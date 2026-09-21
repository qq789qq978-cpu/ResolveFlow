"""PostgreSQL integration tests: isolated schema, no live business mutation."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from engine import Engine
from storage import Store
from operations import create_app
import jobs

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Set RUN_PG_TESTS=1 for PostgreSQL integration tests')

@pytest.fixture
def system(monkeypatch,tmp_path):
    load_dotenv(Path(__file__).with_name('.env'),encoding='utf-8-sig')
    base=os.environ['DATABASE_URL']
    schema='test_'+uuid.uuid4().hex
    with psycopg.connect(base) as c:c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    url=make_conninfo(base,options=f'-c search_path={schema}')
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setenv('MODE','demo')
    for name,value in [('APP_API_KEY','test-operator'),('REVIEWER_API_KEY','test-reviewer'),('ADMIN_API_KEY','test-admin')]:monkeypatch.setenv(name,value)
    store=Store(url)
    store.setup();jobs.setup(store)
    engine=Engine(str(tmp_path),'demo',repository=store)
    try:
        with TestClient(create_app()) as client:yield store,engine,client
    finally:
        engine.close()
        # Only the random schema created above is removed; never public/data.
        with psycopg.connect(base) as c:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

def headers(role='operator'):return {'X-API-Key':'test-'+role}
def submit(client,order='RF-1004',ticket='申请退款'):
    r=client.post('/api/runs',headers=headers(),json={'order_id':order,'ticket':ticket})
    assert r.status_code==202
    assert r.json()['status']=='queued'
    return r.json()['id']

def test_roles_and_async_manual_flow(system):
    store,engine,c=system
    assert c.get('/api/orders').status_code==401
    assert c.get('/api/metrics',headers=headers()).status_code==403
    assert c.post('/api/runs',headers=headers('reviewer'),json={'order_id':'RF-1004','ticket':'申请退款'}).status_code==403
    rid=submit(c)
    assert jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='awaiting_approval'
    path=f'/api/runs/{rid}/approval'
    body={'approved':False,'reason':'测试拒绝'}
    assert c.post(path,headers=headers(),json=body).status_code==403
    assert c.post(path,headers=headers('reviewer'),json={**body,'approved':'false'}).status_code==422
    assert c.post(path,headers=headers('reviewer'),json=body).status_code==202
    assert c.post(path,headers=headers('reviewer'),json=body).status_code==409
    assert jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='rejected'
    assert store.get(rid)['approval']['actor']=='reviewer'
    assert c.get('/api/metrics',headers=headers('admin')).json()['attempts']['total']==2

def test_automatic_and_conflict_paths(system):
    store,engine,c=system
    for order,ticket,expected in [('RF-1001','申请退款','refunded'),('RF-1001','申请退款','already_refunded'),('RF-1002','申请退款','auto_rejected'),('RF-1001','商品已使用，申请退款','escalated')]:
        rid=submit(c,order,ticket)
        jobs.process_one(store,engine,rid)
        assert store.get(rid)['status']==expected
        if expected=='escalated':
            path=f'/api/runs/{rid}/review'
            assert c.post(path,headers=headers(),json={'resolution':'已核查'}).status_code==403
            assert c.post(path,headers=headers('reviewer'),json={'resolution':'已核查'}).json()['status']=='closed'
    with store.connect() as conn:assert conn.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==1

def test_retries_exhaustion_and_admin_recovery(system):
    store,engine,c=system
    rid=submit(c,'RF-1002')
    class Broken:
        def recover(self,*args):raise TimeoutError('provider-secret-must-not-be-saved')
    for index in range(3):
        assert jobs.process_one(store,Broken(),rid,retry_delay=0)
        assert store.get(rid)['status']==('failed' if index==2 else 'retrying')
    assert not jobs.process_one(store,engine,rid)
    assert 'provider-secret' not in str(store.get(rid))
    assert c.post(f'/api/runs/{rid}/retry',headers=headers(),json={}).status_code==403
    assert c.post(f'/api/runs/{rid}/retry',headers=headers('admin'),json={}).status_code==202
    jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='auto_rejected'
    assert len(jobs.details(store,rid)['audit'])==2

def test_skip_locked_and_recover_completed_checkpoint(system):
    store,engine,c=system
    rid=submit(c,'RF-1001')
    # Simulate graph/checkpoint committed, but job completion not committed.
    engine.start(rid,'申请退款','RF-1001')
    with store.connect() as lock:
        lock.execute('SELECT run_id FROM rf_jobs WHERE run_id=%s FOR UPDATE',(rid,))
        assert not jobs.process_one(store,engine,rid)
    assert jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='refunded'
    with store.connect() as conn:assert conn.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==1

def test_killed_process_releases_queue_lock(system,tmp_path):
    store,engine,c=system
    rid=submit(c,'RF-1002')
    marker=tmp_path/'locked'
    package=str(Path(psycopg.__file__).resolve().parent.parent)
    code="import site;site.addsitedir("+repr(package)+");import os,psycopg,time;from pathlib import Path;c=psycopg.connect(os.environ['DATABASE_URL']);c.execute('SELECT run_id FROM rf_jobs WHERE run_id=%s FOR UPDATE',("+repr(rid)+",));Path("+repr(str(marker))+").write_text('locked');time.sleep(60)"
    child=subprocess.Popen([sys._base_executable,'-c',code],env=os.environ.copy())
    try:
        deadline=time.monotonic()+10
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.1)
        assert marker.exists()
        assert not jobs.process_one(store,engine,rid)
        child.terminate();child.wait(timeout=10)
        assert jobs.process_one(store,engine,rid)
        assert store.get(rid)['status']=='auto_rejected'
    finally:
        if child.poll() is None:child.kill();child.wait()

def test_refund_commit_before_checkpoint_replay(system,monkeypatch):
    store,engine,c=system
    rid=submit(c,'RF-1001')
    original=store.refund
    calls=[]
    def fail_after_commit(*args):
        result=original(*args)
        calls.append(result)
        if len(calls)==1:raise RuntimeError('Injected interruption after ledger commit')
        return result
    monkeypatch.setattr(store,'refund',fail_after_commit)
    jobs.process_one(store,engine,rid,retry_delay=0)
    assert store.get(rid)['status']=='retrying'
    jobs.process_one(store,engine,rid,retry_delay=0)
    assert store.get(rid)['status']=='already_refunded'
    assert calls==[True,False]
    with store.connect() as conn:assert conn.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==1

def test_rag_postgres_reindex_and_invalid_input_rollback(system,tmp_path):
    from rag import retrieve,sync_index
    store,engine,c=system
    original=retrieve('退款')
    assert original and original[0]['source']=='refund.md'
    assert c.get('/api/knowledge',params={'query':'退款'}).status_code==401
    response=c.get('/api/knowledge',params={'query':'退款'},headers=headers())
    assert response.status_code==200 and response.json()['results'][0]['source']=='refund.md'
    directory=tmp_path/'knowledge';directory.mkdir()
    doc=directory/'replacement.md'
    doc.write_text("---\nid: test-v1\ntitle: 售后政策\nversion: '1'\n---\n保修凭证需要人工核查。",encoding='utf-8')
    with store.connect() as connection:sync_index(connection,directory)
    result=retrieve('保修凭证')
    assert result[0]['id']=='test-v1'
    assert retrieve('退款')==[]  # stale chunks are removed by replacement
    doc.write_text('invalid metadata',encoding='utf-8')
    with pytest.raises(ValueError):
        with store.connect() as connection:sync_index(connection,directory)
    assert retrieve('保修凭证')==result

def test_manual_approval_true_with_document_evidence(system):
    store,engine,c=system
    rid=submit(c,'RF-1004')
    jobs.process_one(store,engine,rid)
    row=store.get(rid)
    assert row['state']['evidence'][0]['chunk_id']
    assert c.post(f'/api/runs/{rid}/approval',headers=headers('reviewer'),json={'approved':True,'reason':'例外审批验收'}).status_code==202
    jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='refunded'
    assert store.get(rid)['state']['result']['reason']=='模拟退款已完成。'
    with store.connect() as connection:
        assert connection.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==1


def test_sql_failure_records_retry_after_savepoint_rollback(system):
    store,engine,client=system
    rid=submit(client,'RF-1002')
    class InvalidResult:
        def recover(self,*args):
            # Real NOT NULL failure in the queue transaction, not a Python error.
            return {'state':{'result':{'status':None}}}
    assert jobs.process_one(store,InvalidResult(),rid,retry_delay=0)
    detail=jobs.details(store,rid)
    assert detail['job']['status']=='queued'
    assert detail['job']['attempts']==1
    assert detail['job']['last_error']=='NotNullViolation'
    assert store.get(rid)['status']=='retrying'
    assert jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='auto_rejected'
    with store.connect() as connection:
        attempts=connection.execute('SELECT success,error_type FROM rf_job_attempts WHERE run_id=%s ORDER BY id',(rid,)).fetchall()
    assert attempts==[{'success':False,'error_type':'NotNullViolation'},{'success':True,'error_type':None}]


def test_dead_checkpoint_connection_does_not_exhaust_job(system,tmp_path):
    store,engine,client=system
    rid=submit(client,'RF-1002')
    pid=engine.graph.checkpointer.conn.info.backend_pid
    with store.connect() as connection:
        assert connection.execute('SELECT pg_terminate_backend(%s,5000) AS stopped',(pid,)).fetchone()['stopped']
    with pytest.raises((psycopg.OperationalError,psycopg.InterfaceError)):
        jobs.process_one(store,engine,rid)
    job=jobs.details(store,rid)['job']
    assert job['status']=='queued' and job['attempts']==0
    with store.connect() as connection:
        assert connection.execute('SELECT count(*) AS n FROM rf_job_attempts WHERE run_id=%s',(rid,)).fetchone()['n']==0
    replacement=Engine(str(tmp_path/'replacement'),'demo',repository=store)
    try:
        assert jobs.process_one(store,replacement,rid)
        assert store.get(rid)['status']=='auto_rejected'
        assert jobs.details(store,rid)['job']['attempts']==1
    finally:
        replacement.close()


def test_api_database_unavailable_is_safe_503(system,monkeypatch):
    store,engine,client=system
    def unavailable():
        raise psycopg.OperationalError('postgresql://private-user:private-password@private-host/db')
    monkeypatch.setattr(client.app.state.store,'connect',unavailable)
    responses=[client.get('/health'),client.get('/api/runs',headers=headers()),
               client.post('/api/runs',headers=headers(),json={'order_id':'RF-1002','ticket':'申请退款'})]
    for response in responses:
        assert response.status_code==503
        assert response.json()=={'detail':'数据库暂时不可用，请稍后查询状态再重试。'}
        assert 'private' not in response.text


@pytest.mark.parametrize('status', ['queued', 'failed'])
def test_admin_retry_does_not_wait_for_another_transaction(system,status):
    store,engine,client=system
    rid=submit(client,'RF-1002')
    with store.connect() as connection:
        connection.execute('UPDATE rf_jobs SET status=%s WHERE run_id=%s',(status,rid))
    before=jobs.details(store,rid)
    responses=[]
    errors=[]
    def request():
        try:
            responses.append(client.post(f'/api/runs/{rid}/retry',headers=headers('admin'),json={}))
        except Exception as error:
            errors.append(error)
    thread=threading.Thread(target=request)
    try:
        with store.connect() as blocker:
            blocker.execute('SELECT * FROM rf_jobs WHERE run_id=%s FOR UPDATE',(rid,))
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive(), 'busy retry waited on the active job transaction'
            assert not errors
            assert responses[0].status_code==409
            assert responses[0].json()=={'detail':'任务正在处理或状态更新中，请稍后查询状态'}
            assert jobs.details(store,rid)==before
    finally:
        # Release the lock before joining even when testing a regressed version.
        thread.join(timeout=5)
    response=client.post(f'/api/runs/{rid}/retry',headers=headers('admin'),json={})
    assert response.status_code==(202 if status=='failed' else 409)
    assert jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='auto_rejected'


def test_slow_failure_backoff_starts_after_failure(system):
    store,engine,client=system
    rid=submit(client,'RF-1002')
    class SlowFailure:
        def recover(self,*args):
            time.sleep(1.2)  # Longer than the requested retry delay.
            raise TimeoutError('synthetic slow failure')
    assert jobs.process_one(store,SlowFailure(),rid,retry_delay=1)
    with store.connect() as connection:
        remaining=connection.execute('SELECT extract(epoch FROM available_at-clock_timestamp()) AS seconds FROM rf_jobs WHERE run_id=%s',(rid,)).fetchone()['seconds']
    assert 0.5 < float(remaining) <= 1
    assert not jobs.process_one(store,engine,rid)


def test_chunk_quotes_survive_durable_approval(system):
    store, engine, client = system
    rid = submit(client)
    jobs.process_one(store, engine, rid)
    before = store.get(rid)['state']
    assert before['result']['grounding']['reference_valid']
    assert before['proposal']['citations'][0].startswith('refund-v2:')
    assert client.post(f'/api/runs/{rid}/approval', headers=headers('reviewer'),
                       json={'approved': True, 'reason': '片段依据审批验收'}).status_code == 202
    jobs.process_one(store, engine, rid)
    after = store.get(rid)
    assert after['status'] == 'refunded'
    assert after['state']['proposal']['quotes'] == before['proposal']['quotes']


def test_legacy_approval_checkpoint_cannot_bypass_new_citation_gate(system):
    store, engine, client = system
    rid = submit(client)
    jobs.process_one(store, engine, rid)
    legacy = {'action': 'refund', 'reason': '旧版待审批记录', 'citations': ['refund-v2']}
    engine.graph.update_state(engine.config(rid), {'proposal': legacy}, as_node='validate')
    # update_state clears the prior interrupt: materialize the legacy approval
    # interrupt again before simulating the already-persisted human decision.
    engine.graph.invoke(None, engine.config(rid))
    assert any(task.interrupts for task in engine.graph.get_state(engine.config(rid)).tasks)
    assert client.post(f'/api/runs/{rid}/approval', headers=headers('reviewer'),
                       json={'approved': True, 'reason': '旧快照防绕过验收'}).status_code == 202
    jobs.process_one(store, engine, rid)
    after = store.get(rid)
    assert after['status'] == 'escalated' and after['state']['proposal'] == legacy
    assert 'legacy_document_citations' in after['state']['result']['grounding']['errors']
    with store.connect() as connection:
        assert connection.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n'] == 0
