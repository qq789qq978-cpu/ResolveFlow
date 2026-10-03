"""Explicit database preparation. Runtime API/Worker never migrate the schema."""
import argparse
import json
import os
from pathlib import Path
import re
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from database_state import (CORE_REVISION, VECTOR_REVISION, SchemaNotReady,
                            check_application, require_ready)
from scripts.schema_catalog import CORE, VECTOR, CHECKPOINT, VERSION_TABLE, differences, read_catalog

ROOT = Path(__file__).resolve().parent


def _write_demo(c):
    from support_data import ORDERS
    from rag import sync_index
    from policy_releases import activate, prepare
    for order in ORDERS.values():
        c.execute('INSERT INTO rf_orders VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                  tuple(order[k] for k in ('id','owner','amount','days','used','status')))
    seeded = sync_index(c, only_if_empty=True)
    if seeded['seeded']:
        activate(c, payload=prepare(), expected_generation=0, actor='demo-bootstrap',
                 reason='Initial bundled policy release for a new empty database', mode='demo')


def seed_demo(dsn):
    """Fresh install only. All demo orders, policy and release commit together."""
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        _write_demo(c)


@contextmanager
def migration_session(dsn, schema=None):
    # One session lock covers Alembic, the library's autocommit migrations and
    # demo bootstrap. Use this runner for deployment, not raw Alembic commands.
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as guard:
        schema = schema or guard.execute('SELECT current_schema()').fetchone()[0]
        if not schema or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema) or schema.startswith('pg_') or schema == 'information_schema':
            raise ValueError('Invalid migration schema')
        lock_key = 'resolveflow:migration:'+schema
        acquired = guard.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (lock_key,)).fetchone()[0]
        if not acquired:
            raise SchemaNotReady('Another migration is running; retry after it finishes')
        try:
            yield make_conninfo(dsn, options=f'-c search_path={schema}'), schema
        finally:
            guard.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (lock_key,))


def bootstrap_empty_demo(dsn, *, demo=False, schema=None, profile=None):
    """Explicit recovery after a fresh demo install stopped before seeding.

    No attempt is made to infer an existing database's original mode or intent.
    """
    if not demo:
        raise SchemaNotReady('bootstrap-demo requires MODE=demo')
    with migration_session(dsn, schema) as (scoped, schema):
        require_ready(scoped, profile)
        with psycopg.connect(scoped) as c:
            c.execute('SET LOCAL lock_timeout=5000')
            cat = read_catalog(c, schema)
            names = sorted(cat['application_tables']+cat['langgraph_tables'])
            for name in names:
                c.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(sql.Identifier(name)))
            check_application(read_catalog(c, schema), 'core')
            from checkpoint_state import checkpoint_status
            checkpoint_status(c)
            for name in names:
                if name == 'checkpoint_migrations':
                    continue
                if name == 'rf_policy_head':
                    if c.execute('SELECT singleton,release_id,generation FROM rf_policy_head').fetchall() != [(True,None,0)]:
                        raise SchemaNotReady('Demo bootstrap requires untouched empty policy state')
                elif c.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM {})').format(sql.Identifier(name))).fetchone()[0]:
                    raise SchemaNotReady('Demo bootstrap refuses nonempty data; use check instead of reseeding')
            c.row_factory = dict_row
            _write_demo(c)
        return {**require_ready(scoped, profile), 'action':'demo_bootstrapped', 'demo_seeded':True}


def migrate(dsn, *, profile='core', adopt=False, demo=False, schema=None, orders=False):
    if profile not in ('core','hybrid'):
        raise ValueError('Unknown schema profile')
    with migration_session(dsn, schema) as (scoped, schema):
        engine = create_engine('postgresql+psycopg://', poolclass=NullPool,
                               creator=lambda: psycopg.connect(scoped, connect_timeout=5), hide_parameters=True)
        action = 'unchanged'
        phase = 'application_transaction'
        try:
            with engine.begin() as connection:
                raw = connection.connection.driver_connection
                raw.execute('SET LOCAL lock_timeout=5000')
                if not raw.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE')").fetchone()[0]:
                    raise SchemaNotReady('Migration requires schema owner privileges')
                if os.getenv('RF_ENFORCE_DB_ROLES') == '1' and raw.execute('SELECT current_user').fetchone()[0] != 'rf_migrator':
                    raise SchemaNotReady('Migration requires rf_migrator')
                cat = read_catalog(raw, schema)
                names = cat['tables']
                if VERSION_TABLE not in names and any(t in names for t in ('rf_order_versions','rf_order_events')):
                    raise SchemaNotReady('Cannot adopt unversioned import journal')
                if VERSION_TABLE not in names and cat['application_tables']:
                    if not adopt:
                        raise SchemaNotReady('Unversioned existing database; stop API/Worker and run explicit adopt')
                    # Acquire all application locks before inspecting/stamping.
                    for name in sorted(cat['application_tables']):
                        raw.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(sql.Identifier(name)))
                    cat = read_catalog(raw, schema)
                    actual = 'hybrid' if any(t in cat['tables'] for t in VECTOR) else 'core'
                    baseline = json.loads((ROOT/'migrations/baselines'/f'{actual}.json').read_text())
                    changed = differences(cat, baseline, actual)
                    if changed:
                        raise SchemaNotReady('Legacy baseline mismatch: '+', '.join(changed))
                    cfg = Config(str(ROOT/'alembic.ini'))
                    cfg.attributes.update(connection=connection, schema=schema)
                    command.stamp(cfg, VECTOR_REVISION if actual == 'hybrid' else CORE_REVISION)
                    action = 'adopted'
                    cat = read_catalog(raw, schema)
                elif adopt and VERSION_TABLE not in names:
                    raise SchemaNotReady('No legacy application tables to adopt; run prepare for a new database')
                if cat['application_tables'] or VERSION_TABLE in cat['tables']:
                    # Never bless a stamped-but-drifted or unknown revision.
                    check_application(cat, 'core')
                elif cat['unknown_tables']:
                    raise SchemaNotReady('Unexpected tables in target schema')
                from checkpoint_state import checkpoint_status
                checkpoint_status(raw, allow_partial=True, catalog=cat)
                cfg = Config(str(ROOT/'alembic.ini'))
                cfg.attributes.update(connection=connection, schema=schema)
                target = 'vector@head' if profile == 'hybrid' else 'core@head'
                command.upgrade(cfg, target)
                if orders:
                    command.upgrade(cfg, 'orders@head')
                check_application(read_catalog(raw, schema), profile)
                if not cat['application_tables']:
                    action = 'installed'
                elif profile == 'hybrid' and not any(t in cat['tables'] for t in VECTOR):
                    action += '+vector'
            # PostgresSaver owns its DDL, including concurrent indexes, so it
            # must use its own autocommit connection after the app transaction.
            phase = 'checkpoint_setup'
            with PostgresSaver.from_conn_string(scoped) as saver:
                saver.setup()
            # Existing/adopted data must never be implicitly reseeded on upgrade.
            if demo and action == 'installed':
                phase = 'demo_seed'
                seed_demo(scoped)
            phase = 'readiness'
            if os.getenv('RF_ENFORCE_DB_ROLES') == '1':
                from db_roles import grant_runtime
                with psycopg.connect(scoped) as grants:grant_runtime(grants,schema)
            result = require_ready(scoped, profile)
            return {**result, 'action':action, 'demo_seeded':bool(demo and action == 'installed')}
        except Exception as error:
            error.migration_phase = phase
            raise
        finally:
            engine.dispose()


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env', override=False, encoding='utf-8-sig')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','adopt','check','bootstrap-demo'])
    parser.add_argument('--profile', choices=['core','hybrid'],
                        default='hybrid' if os.getenv('RETRIEVAL_MODE') == 'hybrid' else 'core')
    parser.add_argument('--orders', action='store_true', help='Install the optional synthetic-order journal')
    args = parser.parse_args()
    entry_phase = 'demo_recovery' if args.action == 'bootstrap-demo' else 'preflight'
    try:
        dsn = os.environ['DATABASE_URL']
        if args.action == 'check':
            result = require_ready(dsn, args.profile)
        elif args.action == 'bootstrap-demo':
            result = bootstrap_empty_demo(dsn, demo=os.getenv('MODE') == 'demo',
                                          schema=os.getenv('RF_MIGRATION_SCHEMA'), profile=args.profile)
        else:
            result = migrate(dsn, profile=args.profile, adopt=args.action == 'adopt',
                             demo=os.getenv('MODE') == 'demo', schema=os.getenv('RF_MIGRATION_SCHEMA'), orders=args.orders)
        print(json.dumps(result))
        return 0
    except SchemaNotReady as error:
        print(json.dumps({'ready':False,'reason':str(error),
                          'phase':getattr(error,'migration_phase',entry_phase)}))
        return 1
    except Exception as error:
        # Provider/driver messages can contain connection details; print type only.
        print(json.dumps({'ready':False,'error_type':type(error).__name__,
                          'phase':getattr(error,'migration_phase',entry_phase)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
