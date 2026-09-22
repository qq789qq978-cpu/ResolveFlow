"""Real PostgreSQL fault boundaries; every test owns a disposable schema."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from alembic import command
from langgraph.checkpoint.postgres import PostgresSaver
import psycopg
from psycopg import sql
import pytest

from checkpoint_state import checkpoint_status
from database_state import SchemaNotReady, require_ready
import db_migrate
from scripts.schema_catalog import snapshot
from storage import Store
from test_database_upgrade_pg import data
from test_migrations import config
from test_migrations_pg import database, legacy_setup

pytestmark = pytest.mark.skipif(os.getenv('RUN_PG_TESTS') != '1', reason='Isolated PostgreSQL required')


@pytest.mark.parametrize('legacy',[False,True])
def test_application_sql_failure_rolls_back_ddl_stamp_and_rows(database,monkeypatch,legacy):
    dsn,schema=database
    if legacy:legacy_setup(Store(dsn))
    before=snapshot(dsn,schema);rows=data(dsn,schema)
    original=command.upgrade
    def fail(cfg,target):
        original(cfg,target)
        cfg.attributes['connection'].exec_driver_sql('ALTER TABLE rf_orders ADD COLUMN fault TEXT')
        cfg.attributes['connection'].exec_driver_sql('SELECT 1/0')
    with monkeypatch.context() as m:
        m.setattr(command,'upgrade',fail)
        with pytest.raises(Exception) as error:db_migrate.migrate(dsn,adopt=legacy,demo=not legacy)
    assert error.value.migration_phase=='application_transaction'
    assert snapshot(dsn,schema)==before and data(dsn,schema)==rows
    assert db_migrate.migrate(dsn,adopt=legacy,demo=not legacy)['ready']


@pytest.mark.parametrize('count',range(10))
@pytest.mark.parametrize('statement_ahead',[False,True])
def test_every_library_prefix_and_committed_statement_can_resume(database,count,statement_ahead):
    dsn,schema=database
    command.upgrade(config(),'core@head')
    with psycopg.connect(dsn,autocommit=True) as c:
        c.execute(PostgresSaver.MIGRATIONS[0])
        for i in range(count):
            c.execute(PostgresSaver.MIGRATIONS[i]);c.execute('INSERT INTO checkpoint_migrations VALUES(%s)',(i,))
        if statement_ahead:c.execute(PostgresSaver.MIGRATIONS[count])
    with pytest.raises(SchemaNotReady):require_ready(dsn)
    assert db_migrate.migrate(dsn)['ready']
    with psycopg.connect(dsn) as c:assert checkpoint_status(c)['completed']==10


def test_wrong_prefix_structure_does_not_get_blessed(database):
    dsn,schema=database
    command.upgrade(config(),'core@head')
    with psycopg.connect(dsn) as c:
        c.execute(PostgresSaver.MIGRATIONS[0])
        c.execute('INSERT INTO checkpoint_migrations VALUES(0)')
        c.execute('CREATE TABLE checkpoints(wrong TEXT)')
    before=snapshot(dsn,schema)
    with pytest.raises(SchemaNotReady,match='prefix'):db_migrate.migrate(dsn)
    assert snapshot(dsn,schema)==before


def test_cancelled_concurrent_index_requires_repair_before_continue(database):
    dsn,schema=database
    command.upgrade(config(),'core@head')
    with psycopg.connect(dsn,autocommit=True) as c:
        for i in range(6):
            c.execute(PostgresSaver.MIGRATIONS[i]);c.execute('INSERT INTO checkpoint_migrations VALUES(%s)',(i,))
        with psycopg.connect(dsn) as writer:
            writer.execute('LOCK TABLE checkpoints IN ROW EXCLUSIVE MODE')
            c.execute('SET statement_timeout=250')
            with pytest.raises(psycopg.errors.QueryCanceled):c.execute(PostgresSaver.MIGRATIONS[6])
            c.execute('SET statement_timeout=0')
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT indisvalid FROM pg_index WHERE indexrelid='checkpoints_thread_id_idx'::regclass").fetchone()==(False,)
    with pytest.raises(SchemaNotReady,match='Invalid LangGraph index'):db_migrate.migrate(dsn)
    # Explicit maintenance repair; the runner never drops/recreates indexes itself.
    with psycopg.connect(dsn,autocommit=True) as c:c.execute('REINDEX INDEX checkpoints_thread_id_idx')
    assert db_migrate.migrate(dsn)['ready']


def test_seed_failure_is_atomic_and_explicit_empty_demo_recovery(database,monkeypatch):
    import policy_releases
    dsn,schema=database
    def fail(*args,**kwargs):raise RuntimeError('synthetic seed failure')
    with monkeypatch.context() as m:
        m.setattr(policy_releases,'activate',fail)
        with pytest.raises(RuntimeError) as error:db_migrate.migrate(dsn,demo=True)
    assert error.value.migration_phase=='demo_seed'
    rows=data(dsn,schema)
    assert not rows['rf_orders'] and not rows['rf_knowledge_documents'] and not rows['rf_policy_releases']
    assert db_migrate.migrate(dsn,demo=True)['demo_seeded'] is False
    assert db_migrate.bootstrap_empty_demo(dsn,demo=True)['demo_seeded']
    after=data(dsn,schema);assert len(after['rf_orders'])==4
    with pytest.raises(SchemaNotReady,match='nonempty'):db_migrate.bootstrap_empty_demo(dsn,demo=True)
    assert data(dsn,schema)==after


def test_recovery_rejects_live_and_existing_checkpoint_data(database):
    from langgraph.checkpoint.base import empty_checkpoint
    dsn,schema=database
    db_migrate.migrate(dsn)
    with pytest.raises(SchemaNotReady,match='MODE=demo'):db_migrate.bootstrap_empty_demo(dsn)
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.put({'configurable':{'thread_id':'existing','checkpoint_ns':''}},empty_checkpoint(),
                  {'source':'input','step':-1,'parents':{}},{})
    before=data(dsn,schema)
    with pytest.raises(SchemaNotReady,match='nonempty'):db_migrate.bootstrap_empty_demo(dsn,demo=True)
    assert data(dsn,schema)==before


def test_recovery_cli_respects_explicit_profile_and_reports_failure_phase(database,monkeypatch):
    dsn,schema=database
    db_migrate.migrate(dsn)
    monkeypatch.setenv('MODE','demo')
    monkeypatch.setenv('RETRIEVAL_MODE','hybrid')
    args=[sys.executable,'db_migrate.py','bootstrap-demo','--profile','core']
    first=subprocess.run(args,capture_output=True,text=True,timeout=20)
    assert first.returncode==0,first.stdout
    assert json.loads(first.stdout)['demo_seeded']
    before=data(dsn,schema)
    second=subprocess.run(args,capture_output=True,text=True,timeout=20)
    assert second.returncode==1
    result=json.loads(second.stdout)
    assert result['phase']=='demo_recovery' and 'nonempty' in result['reason']
    assert data(dsn,schema)==before


def test_reversible_test_revision_downgrades_without_losing_business_data(database,tmp_path):
    dsn,schema=database
    db_migrate.migrate(dsn,demo=True)
    before=data(dsn,schema)
    target=tmp_path/'migrations';shutil.copytree(Path(__file__).parent/'migrations',target)
    (target/'versions/recovery_probe.py').write_text('''from alembic import op
revision='rf_recovery_probe'
down_revision='rf_core_0001'
branch_labels=None
depends_on=None
def upgrade():op.execute('ALTER TABLE rf_orders ADD COLUMN recovery_probe TEXT')
def downgrade():op.execute('ALTER TABLE rf_orders DROP COLUMN recovery_probe')
''')
    cfg=config();cfg.set_main_option('script_location',str(target))
    command.upgrade(cfg,'core@head')
    with pytest.raises(SchemaNotReady):require_ready(dsn)
    command.downgrade(cfg,'rf_core_0001')
    assert require_ready(dsn)['ready'] and data(dsn,schema)==before
    # Baseline removal is intentionally unsupported and must roll back.
    with pytest.raises(RuntimeError,match='unsupported'):command.downgrade(config(),'base')
    assert require_ready(dsn)['ready'] and data(dsn,schema)==before


@pytest.mark.parametrize('boundary',['application','checkpoint'])
def test_real_process_kill_releases_lock_and_can_continue(database,tmp_path,boundary):
    dsn,schema=database
    signal=tmp_path/'reached.json'
    code='''import os,time,json
from pathlib import Path
from alembic import command
from langgraph.checkpoint.postgres import PostgresSaver
import psycopg
import db_migrate
signal=Path(os.environ['RF_QA_SIGNAL'])
def wait():
 signal.write_text('reached')
 while True:time.sleep(.1)
if os.environ['RF_QA_BOUNDARY']=='application':
 original=command.upgrade
 def blocked(cfg,target):
  original(cfg,target)
  wait()
 command.upgrade=blocked
else:
 def blocked(self):
  with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as c:
   for i in range(3):
    c.execute(PostgresSaver.MIGRATIONS[i]);c.execute('INSERT INTO checkpoint_migrations VALUES(%s)',(i,))
   c.execute(PostgresSaver.MIGRATIONS[3])
  wait()
 PostgresSaver.setup=blocked
db_migrate.migrate(os.environ['DATABASE_URL'],demo=True)
'''
    process=subprocess.Popen([sys.executable,'-c',code],env={**os.environ,'RF_QA_SIGNAL':str(signal),'RF_QA_BOUNDARY':boundary},
                             stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+20
        while not signal.exists() and time.monotonic()<deadline and process.poll() is None:time.sleep(.05)
        assert signal.exists(),'Child did not reach the transaction boundary'
        process.kill();process.wait(timeout=5)
        assert process.returncode != 0
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
    # Poll the session lock because PostgreSQL notices TCP close asynchronously.
    deadline=time.monotonic()+10
    while True:
        try:result=db_migrate.migrate(dsn,demo=True);break
        except SchemaNotReady as error:
            if 'Another migration' not in str(error) or time.monotonic()>deadline:raise
            time.sleep(.05)
    if boundary=='checkpoint':
        assert not result['demo_seeded']
        db_migrate.bootstrap_empty_demo(dsn,demo=True)
    assert require_ready(dsn)['ready'] and len(data(dsn,schema)['rf_orders'])==4
