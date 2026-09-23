"""Read-only alert snapshots. No lease guesses, retries, DDL or business writes."""
from dataclasses import asdict, dataclass
from runtime_db import integer


@dataclass(frozen=True)
class Limits:
    expected_workers:int=1
    offline_seconds:int=20
    running_seconds:int=120
    queued_seconds:int=60
    consecutive_failures:int=3
    failure_window_seconds:int=900

    @classmethod
    def environment(cls):
        return cls(integer('RF_EXPECTED_WORKERS',1,1,64),integer('RF_ALERT_WORKER_OFFLINE_SECONDS',20,10,3600),
                   integer('RF_ALERT_RUNNING_SECONDS',120,2,3600),integer('RF_ALERT_QUEUED_SECONDS',60,2,3600),
                   integer('RF_ALERT_FAILURE_COUNT',3,2,20),integer('RF_ALERT_FAILURE_WINDOW_SECONDS',900,10,86400))


def alert(code,*,run_id=None,severity='warning',**details):
    return {'id':code+(':'+str(run_id) if run_id else ''),'code':code,'severity':severity,
            'run_id':str(run_id) if run_id else None,**details}


def snapshot(store,limits=None):
    limits=limits or Limits.environment()
    with store.connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        # Bound this observer independently from longer business SQL budgets.
        c.execute('SET LOCAL statement_timeout=2000')
        now=c.execute('SELECT now() AS now').fetchone()['now']
        online=c.execute("SELECT count(*) AS n FROM rf_worker_heartbeats WHERE seen_at > now()-(%s * interval '1 second')",(limits.offline_seconds,)).fetchone()['n']
        result=[];truncated_codes=[]
        if online<limits.expected_workers:
            result.append(alert('worker_offline',severity='critical',online=online,expected=limits.expected_workers))
        # rf_runs.running is committed separately from the long rf_jobs row lock.
        # A plain MVCC SELECT observes it without waiting for that job lock.
        for code,status,age,threshold in (
            ('task_stalled',"r.status='running'",'r.updated_at',limits.running_seconds),
            ('queue_delayed',"r.status IN ('queued','approval_queued','retrying')",'j.available_at',limits.queued_seconds)):
            rows=c.execute("SELECT j.run_id,j.kind,extract(epoch FROM now()-"+age+") AS age_seconds FROM rf_jobs j JOIN rf_runs r ON r.id=j.run_id WHERE j.status='queued' AND "+status+" AND "+age+"<=now()-(%s * interval '1 second') ORDER BY "+age+",j.run_id LIMIT 51",(threshold,)).fetchall()
            if len(rows)>50:truncated_codes.append(code)
            result.extend(alert(code,run_id=r['run_id'],kind=r['kind'],age_seconds=max(0,int(r['age_seconds'])),threshold_seconds=threshold) for r in rows[:50])
        rows=c.execute("SELECT run_id,kind,attempts FROM rf_jobs WHERE status='failed' ORDER BY updated_at,run_id LIMIT 51").fetchall()
        if len(rows)>50:truncated_codes.append('job_failed')
        result.extend(alert('job_failed',run_id=r['run_id'],severity='critical',kind=r['kind'],attempts=r['attempts']) for r in rows[:50])
        recent=c.execute('SELECT run_id,success,created_at FROM rf_job_attempts ORDER BY id DESC LIMIT %s',(limits.consecutive_failures,)).fetchall()
        if (len(recent)==limits.consecutive_failures and all(not r['success'] for r in recent)
                and all((now-r['created_at']).total_seconds()<=limits.failure_window_seconds for r in recent)):
            result.append(alert('consecutive_failures',severity='critical',count=len(recent),window_seconds=limits.failure_window_seconds))
    return {'available':True,'checked_at':now.isoformat(),'limits':asdict(limits),
            'workers_online':online,'alerts':sorted(result,key=lambda a:a['id']),
            'truncated':bool(truncated_codes),'truncated_codes':truncated_codes,
            'observation':'running_age_is_a_warning_not_proof_of_deadlock'}


class Transitions:
    """One incident per stable alert id. Unknown polls never resolve known alerts."""
    def __init__(self):self.active={}

    def update(self,current=None):
        if current is None:
            target={**self.active,'monitor_unavailable':alert('monitor_unavailable',severity='critical')}
        else:
            target={a['id']:a for a in current['alerts']}
            # A bounded/truncated result cannot prove omitted incidents recovered.
            if current.get('truncated'):
                incomplete=current.get('truncated_codes',['task_stalled','queue_delayed','job_failed'])
                target={**{k:v for k,v in self.active.items() if v['code'] in incomplete},**target}
        events=[]
        for key,value in target.items():
            if key not in self.active:events.append({'transition':'firing',**value})
        for key,value in self.active.items():
            if key not in target:events.append({'transition':'resolved',**value})
        self.active=target
        return events
