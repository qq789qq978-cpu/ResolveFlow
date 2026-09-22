"""Real baseline checks in disposable schemas; never adopt the main database."""
import json
import os
from pathlib import Path
import uuid

from alembic import command
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
from sqlalchemy.exc import ProgrammingError

from scripts.schema_catalog import CORE, VECTOR, CHECKPOINT, application, differences, snapshot
from test_migrations import config

pytestmark = pytest.mark.skipif(os.getenv('RUN_PG_TESTS') != '1', reason='Isolated PostgreSQL required')
BASELINES = Path(__file__).parent/'migrations/baselines'


@pytest.fixture
def database(monkeypatch):
    base = os.environ['DATABASE_URL']
    schema = 'migration_test_'+uuid.uuid4().hex
    with psycopg.connect(base) as c:
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    dsn = make_conninfo(base, options=f'-c search_path={schema}')
    monkeypatch.setenv('DATABASE_URL', dsn)
    monkeypatch.setenv('RF_MIGRATION_SCHEMA', schema)
    try:
        yield dsn, schema
    finally:
        with psycopg.connect(base) as c:
            c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def baseline(profile):
    return json.loads((BASELINES/f'{profile}.json').read_text())


def test_core_baseline_and_no_business_seeding(database):
    dsn, schema = database
    command.upgrade(config(), 'core@head')
    cat = snapshot(dsn, schema)
    assert differences(cat, baseline('core'), 'core') == []
    assert cat['revisions'] == ['rf_core_0001']
    assert cat['application_tables'] == list(CORE) and not cat['langgraph_tables']
    with psycopg.connect(dsn) as c:
        for table in CORE:
            count = c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
            assert count == (1 if table == 'rf_policy_head' else 0)
    command.upgrade(config(), 'core@head')
    assert snapshot(dsn, schema) == cat


def test_vector_baseline_and_dependency(database):
    dsn, schema = database
    with psycopg.connect(dsn) as c:
        c.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
    command.upgrade(config(), 'vector@head')
    cat = snapshot(dsn, schema)
    assert differences(cat, baseline('hybrid'), 'hybrid') == []
    assert set(cat['application_tables']) == set(CORE+VECTOR)
    assert cat['revisions'] == ['rf_vector_0001']
    # Dependency is satisfied even when its row is absorbed by the vector head.
    command.upgrade(config(), 'core@head')
    assert snapshot(dsn, schema) == cat


def test_legacy_structure_matches_without_stamp(database):
    from storage import Store
    import jobs
    import semantic
    dsn, schema = database
    store = Store(dsn)
    store.setup()
    jobs.setup(store)
    with store.connect() as c:
        c.execute(semantic.SCHEMA)
    cat = snapshot(dsn, schema)
    assert differences(cat, baseline('hybrid'), 'hybrid') == []
    assert cat['revisions'] == [] and 'rf_schema_version' not in cat['tables']
    with store.connect() as c:
        assert c.execute('SELECT count(*) AS n FROM rf_orders').fetchone()['n'] > 0


def test_existing_tables_reject_baseline_and_rollback(database):
    from storage import Store
    import jobs
    dsn, schema = database
    store = Store(dsn)
    store.setup()
    jobs.setup(store)
    before = snapshot(dsn, schema)
    with store.connect() as c:
        orders = c.execute('SELECT * FROM rf_orders ORDER BY id').fetchall()
    with pytest.raises(ProgrammingError, match='already exists'):
        command.upgrade(config(), 'core@head')
    assert snapshot(dsn, schema) == before
    with store.connect() as c:
        assert c.execute('SELECT * FROM rf_orders ORDER BY id').fetchall() == orders


@pytest.mark.parametrize('ddl,changed',[
    ('DROP INDEX rf_jobs_ready','rf_jobs'),
    ("ALTER TABLE rf_runs ALTER COLUMN created_by SET DEFAULT 'other'",'rf_runs'),
    ('ALTER TABLE rf_orders ADD COLUMN drift TEXT','rf_orders'),
    ('CREATE TABLE unknown_data(id INTEGER)','unknown:unknown_data'),
])
def test_detects_drift(database, ddl, changed):
    dsn, schema = database
    command.upgrade(config(), 'core@head')
    with psycopg.connect(dsn) as c:
        c.execute(ddl)
    assert differences(snapshot(dsn, schema), baseline('core'), 'core') == [changed]


def test_real_langgraph_checkpoint_untouched(database):
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.postgres import PostgresSaver
    dsn, schema = database
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()
        checkpoint = empty_checkpoint()
        saved = saver.put({'configurable':{'thread_id':'migration-test','checkpoint_ns':''}},
                          checkpoint, {'source':'input','step':-1,'parents':{}}, {})
        before = saver.get_tuple(saved)
        cat = snapshot(dsn, schema)
        assert cat['langgraph_tables'] == list(CHECKPOINT)
        with psycopg.connect(dsn) as c:
            rows = {t:c.execute(sql.SQL('SELECT * FROM {} ORDER BY 1,2').format(sql.Identifier(t))).fetchall()
                    for t in CHECKPOINT if t != 'checkpoint_migrations'}
            versions = c.execute('SELECT * FROM checkpoint_migrations ORDER BY v').fetchall()
        command.upgrade(config(), 'core@head')
        after = snapshot(dsn, schema)
        assert {t:after['tables'][t] for t in CHECKPOINT} == cat['tables']
        assert saver.get_tuple(saved) == before
        with psycopg.connect(dsn) as c:
            for t, data in rows.items():
                assert c.execute(sql.SQL('SELECT * FROM {} ORDER BY 1,2').format(sql.Identifier(t))).fetchall() == data
            assert c.execute('SELECT * FROM checkpoint_migrations ORDER BY v').fetchall() == versions
