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
