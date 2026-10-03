import concurrent.futures
import os
import uuid
import psycopg
from psycopg.conninfo import make_conninfo
import pytest
from db_migrate import migrate
from database_state import require_ready,SchemaNotReady
from order_sync import OrderSnapshot,SyncError,apply_snapshot
from storage import Store
from test_order_sync import payload
from test_migrations_pg import database
from scripts.backup_snapshot import snapshot_session
from test_jobs import system,submit,headers
import jobs

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Isolated PostgreSQL required')


@pytest.fixture
def store(database):
    dsn,schema=database
    migrate(dsn,demo=True,orders=True)
    return Store(dsn)


def item(**kw):return OrderSnapshot.model_validate(payload(**kw))
def apply(store,x):return apply_snapshot(store,x,'alpha','account:test')


def test_optional_upgrade_preserves_old_data_and_repeat_is_noop(database):
    dsn,schema=database;migrate(dsn,demo=True)
    with snapshot_session(dsn,public_only=False) as before:pass
    migrate(dsn,orders=True)
    with snapshot_session(dsn,public_only=False) as after:pass
    assert all(after['tables'][name]==row for name,row in before['tables'].items() if name!='rf_schema_version')
    assert after['catalog']['revisions']==['rf_orders_0001']
    migrate(dsn,orders=True)
    assert require_ready(dsn)['ready']


def test_duplicate_stale_and_equal_version_semantics(store):
    x=item();assert apply(store,x)['outcome']=='applied'
    assert apply(store,x)['outcome']=='duplicate'
    assert apply(store,x.model_copy(update={'event_id':uuid.uuid4()}))['outcome']=='unchanged'
    newer=item(version=3,days=8);assert apply(store,newer)['outcome']=='applied'
    assert apply(store,item(version=2))['outcome']=='ignored_stale'
    assert store.order('RF-2001','demo')['days']==8
    with store.connect() as c:assert c.execute('SELECT count(*) AS n FROM rf_order_events').fetchone()['n']==4


@pytest.mark.parametrize('change',[{'amount':101},{'status':'shipping'},{'days':2}])
def test_state_or_immutable_field_conflict_rolls_back(store,change):
    apply(store,item())
    with pytest.raises(SyncError):apply(store,item(version=2,**change))
    with store.connect() as c:
        assert c.execute('SELECT version FROM rf_order_versions').fetchone()['version']==1
        assert c.execute('SELECT count(*) AS n FROM rf_order_events').fetchone()['n']==1


def test_id_collision_version_collision_scope_and_unmanaged_orders(store):
    x=item();apply(store,x)
    for y in [x.model_copy(update={'days':4}),item(days=4),item(workspace='beta'),item(id='RF-1001')]:
        with pytest.raises(SyncError):apply(store,y)
    assert store.order('RF-2001','demo')['days']==3


def test_concurrent_duplicate_and_out_of_order_snapshots(store):
    x=item()
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        rows=list(pool.map(lambda _:apply(store,x),range(8)))
    assert sum(r['outcome']=='applied' for r in rows)==1
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda v:apply(store,item(version=v,days=v+3)),[2,5,4,3]))
    with store.connect() as c:assert c.execute('SELECT version FROM rf_order_versions').fetchone()['version']==5
    assert store.order('RF-2001','demo')['days']==8


def test_injected_journal_failure_rolls_back_order_and_version(store):
    with store.connect() as c:
        c.execute("CREATE FUNCTION reject_import() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RAISE EXCEPTION ''qa failure''; END'")
        c.execute('CREATE TRIGGER reject_import BEFORE INSERT ON rf_order_events FOR EACH ROW EXECUTE FUNCTION reject_import()')
    with pytest.raises(psycopg.errors.RaiseException):apply(store,item())
    assert not store.order('RF-2001','demo')
    with store.connect() as c:assert c.execute('SELECT count(*) AS n FROM rf_order_versions').fetchone()['n']==0


def test_journal_drift_and_missing_revision_refused(store):
    with store.connect() as c:c.execute('ALTER TABLE rf_order_events ADD COLUMN surprise text')
    with pytest.raises(SchemaNotReady):require_ready(store.url)


def test_optional_orders_and_vector_branches_coexist(database):
    dsn,schema=database
    with psycopg.connect(dsn) as c:c.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
    migrate(dsn,profile='hybrid',orders=True)
    assert require_ready(dsn,'hybrid')['revisions']==['rf_orders_0001','rf_vector_0001']


@pytest.mark.parametrize('changes',["days=15", "amount=amount+1", "status='cancelled'"])
def test_refund_checks_current_facts_after_investigation(system,monkeypatch,changes):
    store,engine,client=system
    original=store.refund
    def change_then_refund(*args,**kwargs):
        with store.connect() as c:c.execute('UPDATE rf_orders SET '+changes+" WHERE id='RF-1001'")
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'refund',change_then_refund)
    rid=submit(client,'RF-1001');jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='escalated'
    with store.connect() as c:assert c.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==0


def test_import_waits_for_same_order_execution_lock(store):
    import threading
    apply(store,item())
    started=threading.Event()
    def update():started.set();return apply(store,item(version=2,days=4))
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        with store.connect() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('order:RF-2001',))
            task=pool.submit(update);assert started.wait(1)
            with pytest.raises(concurrent.futures.TimeoutError):task.result(timeout=.15)
            assert store.order('RF-2001','demo')['days']==3
        assert task.result(timeout=5)['outcome']=='applied'
