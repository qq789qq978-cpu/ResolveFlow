"""Read-only MVCC alerts and correlation across real queue transactions."""
from datetime import timedelta
import json
import logging
import os
import threading
import uuid
import pytest
import psycopg
import jobs
from alerts import Limits, snapshot
from observability import correlation
from test_jobs import system, headers, submit

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Isolated PostgreSQL required')


def codes(data):return {a['code'] for a in data['alerts']}


def test_alert_endpoint_permissions_offline_and_recovery(system):
    store,_,client=system
    assert client.get('/api/alerts').status_code==401
    for role in ('operator','reviewer'):assert client.get('/api/alerts',headers=headers(role)).status_code==403
    assert 'worker_offline' in codes(client.get('/api/alerts',headers=headers('admin')).json())
    jobs.heartbeat(store,'qa-worker')
    assert 'worker_offline' not in codes(snapshot(store))
    with store.connect() as c:c.execute("UPDATE rf_worker_heartbeats SET seen_at=now()-interval '21 seconds'")
    assert 'worker_offline' in codes(snapshot(store))


def test_locked_running_job_is_observable_without_waiting(system):
    store,engine,client=system;rid=submit(client)
    jobs.heartbeat(store,'qa-worker')
    with store.connect() as c:c.execute("UPDATE rf_runs SET status='running',updated_at=now()-interval '125 seconds' WHERE id=%s",(rid,))
    with store.connect() as held:
        held.execute('SELECT * FROM rf_jobs WHERE run_id=%s FOR UPDATE',(rid,))
        report=snapshot(store)
        assert 'task_stalled' in codes(report) and 'worker_offline' not in codes(report)
        assert next(a for a in report['alerts'] if a['code']=='task_stalled')['run_id']==rid
    jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='awaiting_approval'
    with store.connect() as c:c.execute("UPDATE rf_runs SET updated_at=now()-interval '1 day' WHERE id=%s",(rid,))
    assert not {'task_stalled','queue_delayed'} & codes(snapshot(store))


def test_due_queue_excludes_backoff_future_and_human_waits(system):
    store,_,client=system;rid=submit(client)
    with store.connect() as c:c.execute("UPDATE rf_jobs SET available_at=now()-interval '65 seconds' WHERE run_id=%s",(rid,))
    assert 'queue_delayed' in codes(snapshot(store))
    with store.connect() as c:
        c.execute("UPDATE rf_jobs SET available_at=now()+interval '1 hour' WHERE run_id=%s",(rid,))
        c.execute("UPDATE rf_runs SET status='retrying' WHERE id=%s",(rid,))
    assert 'queue_delayed' not in codes(snapshot(store))


def test_failures_are_recent_consecutive_and_recover_without_erasing_history(system):
    store,engine,client=system;rid=submit(client,'RF-1002')
    class Broken:
        def recover(self,*args):raise RuntimeError('private-error-detail')
    for _ in range(3):jobs.process_one(store,Broken(),rid,retry_delay=0)
    assert {'consecutive_failures','job_failed'}<=codes(snapshot(store))
    healthy=submit(client,'RF-1002');jobs.process_one(store,engine,healthy)
    assert 'consecutive_failures' not in codes(snapshot(store)) and 'job_failed' in codes(snapshot(store))
    jobs.retry(store,rid,'admin');jobs.process_one(store,engine,rid)
    assert 'job_failed' not in codes(snapshot(store))
    with store.connect() as c:assert c.execute('SELECT count(*) AS n FROM rf_job_attempts WHERE NOT success').fetchone()['n']==3


def test_expired_failure_burst_not_reported_and_snapshot_no_business_writes(system):
    store,_,client=system;rid=submit(client)
    with store.connect() as c:
        for _ in range(3):c.execute("INSERT INTO rf_job_attempts(run_id,kind,success,elapsed_ms,created_at) VALUES (%s,'investigate',false,1,now()-interval '1 hour')",(rid,))
    def data():
        with store.connect() as c:return {t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in ('rf_runs','rf_jobs','rf_approvals','rf_refunds','rf_audit','rf_job_attempts')}
    before=data();assert 'consecutive_failures' not in codes(snapshot(store));assert before==data()


def test_logs_bridge_request_queue_attempt_without_text_or_keys(system,caplog):
    store,engine,client=system;caplog.set_level(logging.INFO,logger='resolveflow')
    secret='private-customer-sentinel'
    response=client.post('/api/runs',headers={**headers(),'X-Request-ID':'untrusted-request-sentinel'},json={'order_id':'RF-1002','ticket':secret})
    assert response.status_code==202;rid=response.json()['id'];request_id=response.headers['X-Request-ID']
    uuid.UUID(request_id)
    jobs.process_one(store,engine,rid,worker_id='qa-worker')
    client.get('/api/knowledge',params={'query':secret},headers=headers())
    rows=[json.loads(r.message) for r in caplog.records if r.name=='resolveflow']
    queued=next(r for r in rows if r['event']=='job_enqueued')
    start=next(r for r in rows if r['event']=='job_started');end=next(r for r in rows if r['event']=='job_finished')
    assert queued['request_id']==request_id and queued['run_id']==start['run_id']==rid
    assert start['attempt_id']==end['attempt_id'] and start['worker_id']=='qa-worker'
    own=json.dumps(rows)
    assert secret not in own and 'test-operator' not in own and 'untrusted-request-sentinel' not in own


def test_infrastructure_failure_never_logs_finished(system,caplog):
    store,_,client=system;rid=submit(client);caplog.set_level(logging.INFO,logger='resolveflow')
    class Disconnected:
        def recover(self,*args):raise psycopg.OperationalError('private-dsn')
    with pytest.raises(psycopg.OperationalError):jobs.process_one(store,Disconnected(),rid,worker_id='qa-worker')
    rows=[json.loads(r.message) for r in caplog.records if r.name=='resolveflow']
    assert any(r['event']=='job_interrupted' and r['run_id']==rid for r in rows)
    assert not any(r['event']=='job_finished' for r in rows)
    assert store.get(rid)['status']=='running' and jobs.details(store,rid)['job']['attempts']==0
    first=store.get(rid)['updated_at']
    with pytest.raises(psycopg.OperationalError):jobs.process_one(store,Disconnected(),rid,worker_id='qa-worker')
    assert store.get(rid)['updated_at']==first
    with store.connect() as c:c.execute("UPDATE rf_runs SET updated_at=now()-interval '125 seconds' WHERE id=%s",(rid,))
    with pytest.raises(psycopg.OperationalError):jobs.process_one(store,Disconnected(),rid,worker_id='qa-worker')
    assert 'task_stalled' in codes(snapshot(store))


def test_actual_commit_failure_never_claims_completion(system,caplog):
    store,engine,client=system;rid=submit(client,'RF-1002');caplog.set_level(logging.INFO,logger='resolveflow')
    with store.connect() as c:
        c.execute("CREATE FUNCTION qa_commit_error() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic commit failure'; END $$")
        c.execute('CREATE CONSTRAINT TRIGGER qa_commit_error AFTER INSERT ON rf_job_attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION qa_commit_error()')
    with pytest.raises(psycopg.errors.RaiseException):jobs.process_one(store,engine,rid,worker_id='qa-worker')
    rows=[json.loads(r.message) for r in caplog.records if r.name=='resolveflow']
    assert not any(r['event']=='job_finished' for r in rows)
    assert any(r['event']=='job_interrupted' and r['run_id']==rid for r in rows)
    assert jobs.details(store,rid)['job']['attempts']==0


def test_bounded_snapshot_identifies_only_incomplete_categories(system):
    store,_,_=system
    with store.connect() as c:
        for _ in range(51):
            rid=str(uuid.uuid4())
            c.execute("INSERT INTO rf_runs(id,ticket,order_id,mode,model,status,created_by) VALUES (%s,'synthetic','RF-1002','demo','demo','queued','operator')",(rid,))
            c.execute("INSERT INTO rf_jobs(run_id,kind,available_at) VALUES (%s,'investigate',now()-interval '65 seconds')",(rid,))
    report=snapshot(store)
    assert report['truncated'] and report['truncated_codes']==['queue_delayed']
    assert len([a for a in report['alerts'] if a['code']=='queue_delayed'])==50
    assert 'worker_offline' in codes(report)
