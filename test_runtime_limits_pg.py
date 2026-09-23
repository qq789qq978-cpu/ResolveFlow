import os
import threading
import time
import uuid
from pathlib import Path
import psycopg
from psycopg_pool import PoolTimeout,TooManyRequests
import pytest
from storage import Store
from task_runtime import TaskRunner
from test_jobs import system,submit,headers
from test_migrations_pg import database
from test_db_roles_pg import roles_db
import jobs

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Isolated PostgreSQL required')


def test_pool_reuses_resets_and_rolls_back(database,monkeypatch):
    dsn,_=database
    store=Store(dsn,pooled=True,max_size=1)
    try:
        with store.connect() as c:
            pid=c.info.backend_pid;c.execute("SET application_name='private-request-state'")
        with store.connect() as c:
            assert c.info.backend_pid==pid
            assert c.execute('SHOW application_name').fetchone()['application_name']=='resolveflow'
        with pytest.raises(psycopg.errors.DivisionByZero):
            with store.connect() as c:c.execute('SELECT 1/0')
        with store.connect() as c:assert c.execute('SELECT 42 AS n').fetchone()['n']==42
    finally:store.close()


def test_pool_exhaustion_waiter_cap_and_recovery(database,monkeypatch):
    monkeypatch.setenv('RF_DB_POOL_TIMEOUT_MS','250')
    monkeypatch.setenv('RF_DB_POOL_MAX_WAITING','1')
    store=Store(database[0],pooled=True,max_size=1);errors=[]
    def waiting():
        try:
            with store.connect():pass
        except PoolTimeout:errors.append('timeout')
    try:
        with store.connect():
            thread=threading.Thread(target=waiting);thread.start()
            deadline=time.monotonic()+2
            while store.pool.get_stats()['requests_waiting']!=1 and time.monotonic()<deadline:time.sleep(.01)
            start=time.monotonic()
            with pytest.raises(TooManyRequests):
                with store.connect():pass
            assert time.monotonic()-start<.5
            thread.join(timeout=2);assert errors==['timeout']
        # Pool reset runs on its maintenance thread; returning a connection
        # need not make it available in the same scheduler tick.
        deadline=time.monotonic()+2
        while store.pool.get_stats()['pool_available'] != 1 and time.monotonic()<deadline:time.sleep(.01)
        with store.connect() as c:assert c.execute('SELECT 1 AS n').fetchone()['n']==1
        assert store.pool.get_stats()['pool_size']==1
    finally:store.close()


def test_dead_connection_replaced(database):
    store=Store(database[0],pooled=True,max_size=1)
    try:
        with store.connect() as c:pid=c.info.backend_pid
        with psycopg.connect(database[0],autocommit=True) as admin:admin.execute('SELECT pg_terminate_backend(%s,1000)',(pid,))
        with store.connect() as c:
            assert c.info.backend_pid!=pid
            assert c.execute('SELECT 1 AS n').fetchone()['n']==1
    finally:store.close()


def test_slow_statement_lock_and_idle_timeout(database,monkeypatch):
    monkeypatch.setenv('RF_DB_STATEMENT_TIMEOUT_MS','250')
    monkeypatch.setenv('RF_DB_LOCK_TIMEOUT_MS','150')
    monkeypatch.setenv('RF_DB_IDLE_TRANSACTION_TIMEOUT_MS','1000')
    store=Store(database[0],pooled=True,max_size=1)
    try:
        start=time.monotonic()
        with pytest.raises(psycopg.errors.QueryCanceled):
            with store.connect() as c:c.execute('SELECT pg_sleep (5)')
        assert time.monotonic()-start<2
        with psycopg.connect(database[0]) as lock:
            lock.execute('SELECT pg_advisory_xact_lock(371234)')
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with store.connect() as c:c.execute('SELECT pg_advisory_xact_lock(371234)')
        with pytest.raises(psycopg.errors.IdleInTransactionSessionTimeout):
            with store.connect() as c:
                c.execute('SELECT 1');time.sleep(1.3);c.execute('SELECT 2')
        with store.connect() as c:assert c.execute('SELECT 3 AS n').fetchone()['n']==3
    finally:store.close()


def test_api_pool_full_returns_503_and_recovers(system,monkeypatch):
    store,_,client=system
    pool=client.app.state.store.pool
    from contextlib import ExitStack
    with ExitStack() as held:
        for _ in range(pool.max_size):held.enter_context(pool.connection())
        start=time.monotonic()
        response=client.get('/api/orders',headers=headers())
        assert response.status_code==503 and response.headers['retry-after']=='2'
        assert time.monotonic()-start<4
        assert 'postgresql://' not in response.text
    assert client.get('/api/orders',headers=headers()).status_code==200


def test_api_lock_timeout_rolls_back_then_recovers(system,monkeypatch):
    store,_,client=system
    with psycopg.connect(store.url) as lock:
        lock.execute('LOCK TABLE rf_orders IN ACCESS EXCLUSIVE MODE')
        start=time.monotonic();response=client.get('/api/orders',headers=headers())
        assert response.status_code==503 and time.monotonic()-start<7
    assert client.get('/api/orders',headers=headers()).status_code==200


@pytest.mark.parametrize('error',[psycopg.errors.QueryCanceled,psycopg.errors.LockNotAvailable])
def test_sql_timeouts_consume_retry_budget(system,error):
    store,engine,client=system;rid=submit(client,'RF-1002')
    class Blocked:
        def recover(self,*args):raise error('private SQL must not be stored')
    for _ in range(3):assert jobs.process_one(store,Blocked(),rid,retry_delay=0)
    row=store.get(rid);assert row['status']=='failed'
    assert jobs.details(store,rid)['job']['attempts']==3
    assert 'private SQL' not in str(row)
    jobs.retry(store,rid,'admin');jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='auto_rejected'


def test_real_task_deadline_stops_sql_and_releases_claim(system,tmp_path,monkeypatch):
    store,_,client=system
    monkeypatch.setenv('RF_DB_STATEMENT_TIMEOUT_MS','30000')
    monkeypatch.setenv('RF_DB_LOCK_TIMEOUT_MS','30000')
    monkeypatch.setenv('RF_TASK_TIMEOUT_SECONDS','6')
    rid=submit(client,'RF-1001')
    with psycopg.connect(store.url) as c:
        c.execute('CREATE FUNCTION qa_deadline_gate() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep (25); RETURN NEW; END $$')
        c.execute('CREATE TRIGGER qa_deadline_gate BEFORE INSERT ON rf_refunds FOR EACH ROW EXECUTE FUNCTION qa_deadline_gate()')
    runner=TaskRunner(tmp_path/'task','demo')
    try:
        start=time.monotonic();assert jobs.process_one(store,runner,rid,retry_delay=0)
        assert time.monotonic()-start<13
        detail=jobs.details(store,rid)
        assert detail['job']['last_error']=='TaskDeadlineExceeded' and detail['job']['attempts']==1
        with store.connect() as c:
            assert c.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==0
            assert c.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE application_name LIKE 'rf-task-%'").fetchone()['n']==0
            c.execute('SELECT * FROM rf_jobs WHERE run_id=%s FOR UPDATE NOWAIT',(rid,))
    finally:
        runner.close()
        with psycopg.connect(store.url) as c:
            c.execute('DROP TRIGGER qa_deadline_gate ON rf_refunds');c.execute('DROP FUNCTION qa_deadline_gate()')
    monkeypatch.setenv('RF_TASK_TIMEOUT_SECONDS','60')
    runner=TaskRunner(tmp_path/'task','demo')
    try:
        assert jobs.process_one(store,runner,rid,retry_delay=0)
        assert store.get(rid)['status']=='refunded'
        rid2=submit(client,'RF-1001');jobs.process_one(store,runner,rid2)
        assert store.get(rid2)['status']=='already_refunded'
    finally:runner.close()


def test_mcp_deadline_cleanup_keeps_other_connections(system,tmp_path,monkeypatch):
    store,_,client=system
    monkeypatch.setenv('RF_TASK_TIMEOUT_SECONDS','5')
    rid=submit(client,'RF-1002');runner=TaskRunner(tmp_path/'mcp-task','demo')
    with psycopg.connect(store.url) as lock:
        lock.execute('LOCK TABLE rf_orders IN ACCESS EXCLUSIVE MODE')
        assert jobs.process_one(store,runner,rid,retry_delay=0)
        assert jobs.details(store,rid)['job']['last_error']=='TaskDeadlineExceeded'
        assert lock.execute('SELECT 1').fetchone()[0]==1
        assert lock.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE 'rf-task-%'").fetchone()[0]==0
    runner.close()


def test_reserved_cleanup_works_at_connection_limit_for_both_roles(roles_db,monkeypatch):
    from contextlib import ExitStack
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from task_runtime import cleanup_channels,cleanup_connections
    dsn,_,roles,_,urls,_=roles_db
    monkeypatch.setenv('DATABASE_URL',urls['app'])
    monkeypatch.setenv('READONLY_DATABASE_URL',urls['readonly'])
    token='rf-task-'+uuid.uuid4().hex
    with psycopg.connect(dsn,autocommit=True) as admin, ExitStack() as held:
        guards=held.enter_context(cleanup_channels())
        targets=[]
        for kind in ('app','readonly'):
            # A role limit models denied new connections without changing the
            # shared cluster or interfering with other tests.
            admin.execute(sql.SQL('ALTER ROLE {} CONNECTION LIMIT 3').format(sql.Identifier(roles[kind])))
            targets.append(held.enter_context(psycopg.connect(make_conninfo(urls[kind],application_name=token),autocommit=True)))
            other=held.enter_context(psycopg.connect(make_conninfo(urls[kind],application_name='unrelated-attempt'),autocommit=True))
            with pytest.raises(psycopg.OperationalError):psycopg.connect(urls[kind])
        cleanup_connections(token,guards)
        assert admin.execute('SELECT count(*) FROM pg_stat_activity WHERE application_name=%s',(token,)).fetchone()[0]==0
        assert admin.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='unrelated-attempt'").fetchone()[0]==2
        assert other.execute('SELECT 1').fetchone()[0]==1
        assert all(not c.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0] for c in guards)


def test_checkpoint_startup_obeys_lock_timeout(database,monkeypatch):
    from db_migrate import migrate
    from database_state import require_checkpoints
    dsn,_=database;migrate(dsn,demo=True)
    monkeypatch.setenv('RF_DB_LOCK_TIMEOUT_MS','150')
    with psycopg.connect(dsn) as lock:
        lock.execute('LOCK TABLE checkpoint_migrations IN ACCESS EXCLUSIVE MODE')
        start=time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):require_checkpoints(dsn)
        assert time.monotonic()-start<2


def test_server_connection_exhaustion_is_bounded_and_pool_recovers(database,monkeypatch):
    from contextlib import ExitStack
    monkeypatch.setenv('RF_DB_POOL_TIMEOUT_MS','300')
    store=Store(database[0],pooled=True,max_size=1)
    try:
        with ExitStack() as occupied:
            # Dedicated QA cluster only: consume even the superuser reserve,
            # so this proves server exhaustion, not only a local pool limit.
            for _ in range(200):
                try:occupied.enter_context(psycopg.connect(database[0],autocommit=True,connect_timeout=2))
                except psycopg.OperationalError:break
            else:pytest.fail('QA cluster max_connections must be below 200')
            start=time.monotonic()
            with pytest.raises(PoolTimeout):
                with store.connect():pass
            assert time.monotonic()-start<2
        deadline=time.monotonic()+8
        while True:
            try:
                with store.connect() as c:assert c.execute('SELECT 42 AS n').fetchone()['n']==42
                break
            except (PoolTimeout,TooManyRequests):
                if time.monotonic()>deadline:raise
                time.sleep(.1)
    finally:store.close()


def test_role_readiness_connection_has_runtime_deadlines(roles_db,monkeypatch):
    import db_roles
    _,_,roles,_,urls,_=roles_db
    monkeypatch.setattr(db_roles,'ROLES',roles)
    monkeypatch.setenv('RF_DB_STATEMENT_TIMEOUT_MS','350')
    monkeypatch.setenv('RF_DB_LOCK_TIMEOUT_MS','250')
    # Observe the real connection used by the identity check, not only a DSN
    # string; the original checks still execute with a restricted login.
    original=psycopg.connect
    settings=[]
    def observe(*args,**kwargs):
        c=original(*args,**kwargs)
        settings.append((c.execute('SHOW statement_timeout').fetchone()[0],c.execute('SHOW lock_timeout').fetchone()[0]))
        return c
    monkeypatch.setattr(db_roles.psycopg,'connect',observe)
    db_roles.require_app(urls['app'])
    assert settings==[('350ms','250ms')]
