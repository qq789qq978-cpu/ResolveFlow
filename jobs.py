"""Durable PostgreSQL queue. A job row lock is held for the whole execution.

On process death PostgreSQL rolls back the transaction and releases the lock;
another worker resumes the same LangGraph thread. No lease expiry guesses.
Business refunds independently have a unique order key to tolerate replay.
"""
import json
import logging
import time
import psycopg
from psycopg.types.json import Jsonb


def enqueue(store, run_id, ticket, order_id, mode, model, actor):
    with store.connect() as c:
        if not c.execute('SELECT id FROM rf_orders WHERE id=%s', (order_id,)).fetchone():
            raise KeyError(order_id)
        c.execute("INSERT INTO rf_runs(id,ticket,order_id,mode,model,status,created_by) VALUES (%s,%s,%s,%s,%s,'queued',%s)", (run_id,ticket,order_id,mode,model,actor))
        c.execute("INSERT INTO rf_jobs(run_id,kind) VALUES (%s,'investigate')", (run_id,))
        c.execute("INSERT INTO rf_audit(run_id,actor,action) VALUES (%s,%s,'create')", (run_id,actor))

def queue_approval(store, run_id, approved, actor, reason):
    with store.connect() as c:
        row = c.execute('SELECT status FROM rf_runs WHERE id=%s FOR UPDATE', (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        if row['status'] != 'awaiting_approval':
            raise ValueError('当前工单不允许审批，或审批已经提交')
        c.execute('INSERT INTO rf_approvals VALUES (%s,%s,%s,%s,DEFAULT)', (run_id,approved,actor,reason))
        c.execute("INSERT INTO rf_jobs(run_id,kind) VALUES (%s,'approval') ON CONFLICT(run_id) DO UPDATE SET kind='approval',status='queued',attempts=0,available_at=now(),last_error=NULL,updated_at=now()", (run_id,))
        c.execute("UPDATE rf_runs SET status='approval_queued',updated_at=now() WHERE id=%s", (run_id,))
        c.execute("INSERT INTO rf_audit(run_id,actor,action) VALUES (%s,%s,'approve' || %s)", (run_id,actor,':yes' if approved else ':no'))

def retry(store, run_id, actor):
    with store.connect() as c:
        try:
            # A Worker keeps this lock across external calls. Never wait for
            # that task to finish just to reject an ineligible manual retry.
            job = c.execute('SELECT * FROM rf_jobs WHERE run_id=%s FOR UPDATE NOWAIT', (run_id,)).fetchone()
        except psycopg.errors.LockNotAvailable:
            raise ValueError('任务正在处理或状态更新中，请稍后查询状态') from None
        if not job:
            raise KeyError(run_id)
        if job['status'] != 'failed':
            raise ValueError('只能重试已耗尽自动重试次数的任务')
        c.execute("UPDATE rf_jobs SET status='queued',attempts=0,available_at=now(),last_error=NULL,updated_at=now() WHERE run_id=%s", (run_id,))
        c.execute("UPDATE rf_runs SET status=%s,error=NULL,updated_at=now() WHERE id=%s", ('approval_queued' if job['kind']=='approval' else 'queued',run_id))
        c.execute("INSERT INTO rf_audit(run_id,actor,action) VALUES (%s,%s,'retry')", (run_id,actor))

def details(store, run_id):
    with store.connect() as c:
        return {'job': c.execute('SELECT * FROM rf_jobs WHERE run_id=%s', (run_id,)).fetchone(),
                'audit': c.execute('SELECT actor,action,created_at FROM rf_audit WHERE run_id=%s ORDER BY id', (run_id,)).fetchall()}

def metrics(store):
    with store.connect() as c:
        counts = c.execute('SELECT status,count(*) AS count FROM rf_runs GROUP BY status').fetchall()
        attempts = c.execute('SELECT count(*) AS total, count(*) FILTER(WHERE NOT success) AS failed, round(avg(elapsed_ms)) AS avg_ms, percentile_cont(0.95) WITHIN GROUP(ORDER BY elapsed_ms) AS p95_ms FROM rf_job_attempts').fetchone()
        workers = c.execute("SELECT count(*) AS online FROM rf_worker_heartbeats WHERE seen_at > now()-interval '20 seconds'").fetchone()
        usage = c.execute("SELECT COALESCE(sum((state->'usage'->>'input_tokens')::bigint),0) AS input_tokens, COALESCE(sum((state->'usage'->>'output_tokens')::bigint),0) AS output_tokens FROM rf_runs").fetchone()
        return {'runs': {r['status']:r['count'] for r in counts}, 'attempts':attempts, 'usage':usage, 'workers_online':workers['online'], 'refund_execution':'simulated'}

def heartbeat(store, worker_id):
    with store.connect() as c:
        c.execute('INSERT INTO rf_worker_heartbeats(worker_id) VALUES (%s) ON CONFLICT(worker_id) DO UPDATE SET seen_at=now()', (worker_id,))

def process_one(store, engine, run_id=None, max_attempts=3, retry_delay=2):
    # READ COMMITTED plus SKIP LOCKED lets multiple workers handle different runs.
    # Lock only rf_jobs; graph checkpoints and refund inserts use other connections.
    with store.connect() as c:
        job = c.execute("SELECT * FROM rf_jobs WHERE status='queued' AND available_at<=now() AND (%s::uuid IS NULL OR run_id=%s) ORDER BY available_at FOR UPDATE SKIP LOCKED LIMIT 1", (run_id,run_id)).fetchone()
        if not job:
            return False
        rid = str(job['run_id'])
        row = store.get(rid)
        started = time.monotonic()
        with store.connect() as update:
            update.execute("UPDATE rf_runs SET status='running',updated_at=now() WHERE id=%s", (rid,))
        try:
            # Keep the queue row lock in the outer transaction. A failed SQL
            # statement rolls back this savepoint before we record a retry.
            with c.transaction():
                if getattr(engine,'mode',row['mode']) != row['mode']:
                    raise ValueError('Worker mode differs from queued run')
                result = engine.recover(rid, row['ticket'], row['order_id'], row['approval'] if job['kind']=='approval' else None)
                elapsed = int((time.monotonic()-started)*1000)
                c.execute("UPDATE rf_runs SET state=%s,status=%s,error=NULL,elapsed_ms=%s,updated_at=now() WHERE id=%s", (Jsonb(result['state']),result['state']['result']['status'],elapsed,rid))
                c.execute("UPDATE rf_jobs SET status='done',attempts=attempts+1,last_error=NULL,updated_at=now() WHERE run_id=%s", (rid,))
            success, error_type = True, None
        except Exception as error:
            if isinstance(error,(psycopg.OperationalError,psycopg.InterfaceError,psycopg.errors.IdleInTransactionSessionTimeout)) and not isinstance(
                    error,(psycopg.errors.QueryCanceled,psycopg.errors.LockNotAvailable)):
                # Actual disconnects roll back the claim; bounded SQL/lock
                # failures consume attempts rather than retrying forever.
                raise
            elapsed = int((time.monotonic()-started)*1000)
            error_type = type(error).__name__  # Never persist raw provider responses / keys.
            exhausted = job['attempts']+1 >= max_attempts
            # now() is the transaction START; after a slow call its retry
            # deadline can already be in the past. Back off from failure time.
            c.execute("UPDATE rf_jobs SET status=%s,attempts=attempts+1,last_error=%s,available_at=clock_timestamp()+(%s * interval '1 second'),updated_at=clock_timestamp() WHERE run_id=%s", ('failed' if exhausted else 'queued',error_type,retry_delay*2**job['attempts'],rid))
            c.execute("UPDATE rf_runs SET status=%s,error=%s,updated_at=now() WHERE id=%s", ('failed' if exhausted else 'retrying','任务失败：'+error_type,rid))
            success = False
        c.execute('INSERT INTO rf_job_attempts(run_id,kind,success,error_type,elapsed_ms) VALUES (%s,%s,%s,%s,%s)', (rid,job['kind'],success,error_type,elapsed))
        logging.getLogger('resolveflow').info(json.dumps({'event':'job_finished','run_id':rid,'kind':job['kind'],'success':success,'error_type':error_type,'elapsed_ms':elapsed}))
    return True
