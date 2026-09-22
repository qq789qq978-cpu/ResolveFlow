"""New installation and explicit, data-preserving adoption of the V3 baseline."""
import json
import os
import subprocess
import sys

import psycopg
from psycopg import sql
import pytest
from fastapi.testclient import TestClient

from database_state import SchemaNotReady, require_ready
from db_migrate import migrate
from scripts.schema_catalog import snapshot
from storage import Store
from test_migrations_pg import database, legacy_setup

pytestmark = pytest.mark.skipif(os.getenv('RUN_PG_TESTS') != '1', reason='Isolated PostgreSQL required')


def data(dsn, schema):
    cat = snapshot(dsn, schema)
    with psycopg.connect(dsn) as c:
        return {t: sorted(json.dumps(r[0],sort_keys=True) for r in c.execute(
            sql.SQL('SELECT to_jsonb(t) FROM {} t').format(sql.Identifier(t))))
            for t in cat['tables'] if t != 'rf_schema_version'}


def test_fresh_demo_install_and_repeat_preserves_every_row(database):
    dsn, schema = database
    result = migrate(dsn, demo=True)
    assert result['action'] == 'installed' and result['demo_seeded']
    assert len(snapshot(dsn,schema)['tables']) == 20  # 15 + 4 + version
    before = data(dsn,schema)
    assert len(before['rf_orders']) == 4 and len(before['rf_knowledge_chunks']) == 7
    assert migrate(dsn,demo=True)['action'] == 'unchanged'
    Store(dsn).setup()
    assert data(dsn,schema) == before


def test_live_install_does_not_seed_demo_orders_or_policies(database):
    dsn,schema = database
    assert not migrate(dsn)['demo_seeded']
    rows=data(dsn,schema)
    assert not rows['rf_orders'] and not rows['rf_knowledge_documents']
    assert not rows['rf_policy_releases']


@pytest.mark.parametrize('vector',[False,True])
def test_legacy_adoption_preserves_data_and_is_explicit(database, vector):
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.base import empty_checkpoint
    dsn,schema = database
    legacy_setup(Store(dsn),vector=vector)
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()
        saver.put({'configurable':{'thread_id':'legacy','checkpoint_ns':''}},
                  empty_checkpoint(), {'source':'input','step':-1,'parents':{}}, {})
    before=data(dsn,schema)
    profile='hybrid' if vector else 'core'
    with pytest.raises(SchemaNotReady,match='explicit adopt'):
        migrate(dsn,profile=profile,demo=True)
    assert 'rf_schema_version' not in snapshot(dsn,schema)['tables']
    result=migrate(dsn,profile=profile,adopt=True,demo=True)
    assert result['action']=='adopted' and not result['demo_seeded']
    assert data(dsn,schema)==before
    assert migrate(dsn,profile=profile,adopt=True)['action']=='unchanged'
    assert data(dsn,schema)==before


@pytest.mark.parametrize('ddl',[
    'DROP INDEX rf_jobs_ready',
    'ALTER TABLE rf_runs ADD COLUMN unexpected TEXT',
    'DROP TABLE rf_worker_heartbeats',
])
def test_legacy_drift_refuses_stamp_without_data_changes(database,ddl):
    dsn,schema=database
    legacy_setup(Store(dsn))
    with psycopg.connect(dsn) as c:c.execute(ddl)
    before=data(dsn,schema)
    with pytest.raises(SchemaNotReady,match='baseline mismatch'):
        migrate(dsn,adopt=True)
    assert data(dsn,schema)==before
    assert 'rf_schema_version' not in snapshot(dsn,schema)['tables']


def test_core_to_hybrid_keeps_data(database):
    dsn,schema=database
    migrate(dsn,demo=True)
    before=data(dsn,schema)
    with psycopg.connect(dsn) as c:c.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
    assert migrate(dsn,profile='hybrid')['action']=='unchanged+vector'
    after=data(dsn,schema)
    assert {t:after[t] for t in before}==before
    assert not after['rf_policy_vectors']
    assert require_ready(dsn,'hybrid')['ready']


def test_runtime_refuses_unprepared_db_without_creating_tables(database,monkeypatch):
    from operations import create_app
    dsn,schema=database
    for key in ('APP_API_KEY','REVIEWER_API_KEY','ADMIN_API_KEY'):
        monkeypatch.setenv(key,'qa-'+key)
    with pytest.raises(SchemaNotReady):
        with TestClient(create_app()):pass
    worker=subprocess.run([sys.executable,'worker.py'],capture_output=True,timeout=20)
    assert worker.returncode != 0
    assert snapshot(dsn,schema)['tables']=={}


def test_unknown_application_revision_and_checkpoint_revision_rejected(database):
    dsn,schema=database
    migrate(dsn)
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE rf_schema_version SET version_num='future_version'")
    with pytest.raises(SchemaNotReady,match='revision'):
        migrate(dsn)
    with pytest.raises(SchemaNotReady,match='revision'):
        require_ready(dsn)
    with psycopg.connect(dsn) as c:
        c.execute("UPDATE rf_schema_version SET version_num='rf_core_0001'")
        c.execute('INSERT INTO checkpoint_migrations VALUES(999)')
    with pytest.raises(SchemaNotReady,match='LangGraph'):
        migrate(dsn)
    with pytest.raises(SchemaNotReady,match='LangGraph'):
        require_ready(dsn)


def test_concurrent_runner_rejected(database):
    dsn,schema=database
    with psycopg.connect(dsn,autocommit=True) as holder:
        holder.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))',('resolveflow:migration:'+schema,))
        with pytest.raises(SchemaNotReady,match='Another migration'):
            migrate(dsn)
    assert snapshot(dsn,schema)['tables']=={}


def test_runtime_ready_check_works_in_readonly_session(database):
    from psycopg.conninfo import make_conninfo
    dsn,schema=database
    migrate(dsn,demo=True)
    readonly=make_conninfo(dsn,options=f'-c search_path={schema} -c default_transaction_read_only=on')
    before=data(dsn,schema)
    assert require_ready(readonly)['ready']
    Store(readonly).setup()
    assert data(dsn,schema)==before
