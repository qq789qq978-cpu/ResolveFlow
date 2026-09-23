"""Step 3.8 real Worker outage, stalled SQL, failures, recovery and safe logs."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.database_restore import unused_subnet
from scripts.restore_qa import api, wait_run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True);parser.add_argument('--image',required=True)
    parser.add_argument('--report',type=Path,required=True);parser.add_argument('--port',default='8023')
    args=parser.parse_args()
    if not args.project.startswith('resolveflow-qa-') or args.report.exists():parser.error('Use fresh QA project/report')
    work=ROOT/'work'/args.project;work.mkdir(exist_ok=False)
    empty=work/'empty.env';empty.write_text('')
    first=unused_subnet();second=unused_subnet(excluded=[first])
    override=work/'override.json'
    override.write_text(json.dumps({'networks':{'default':{'internal':True,'ipam':{'config':[{'subnet':first}]}},
        'embedding-private':{'ipam':{'config':[{'subnet':second}]}}}}))
    env={**os.environ,'RESOLVEFLOW_IMAGE':args.image,'MODE':'demo','RETRIEVAL_MODE':'bm25','COMPOSE_PROFILES':'',
         'POSTGRES_IMAGE':'postgres:17','POSTGRES_PASSWORD':'qa38-admin-password',
         'RF_MIGRATOR_PASSWORD':'qa38-migrator-password-123456','RF_APP_PASSWORD':'qa38-app-password-1234567890',
         'RF_READONLY_PASSWORD':'qa38-readonly-password-123456','APP_API_KEY':'qa38-operator',
         'REVIEWER_API_KEY':'qa38-reviewer','ADMIN_API_KEY':'qa38-admin','OPENAI_API_KEY':'',
         'APP_PORT':args.port,'RF_EXPECTED_WORKERS':'2','RF_ALERT_WORKER_OFFLINE_SECONDS':'10',
         'RF_ALERT_RUNNING_SECONDS':'2','RF_ALERT_QUEUED_SECONDS':'2','RF_ALERT_FAILURE_COUNT':'3',
         'RF_ALERT_FAILURE_WINDOW_SECONDS':'300','RF_ALERT_POLL_SECONDS':'1','RF_ALERT_STARTUP_GRACE_SECONDS':'5',
         'RF_TASK_TIMEOUT_SECONDS':'12','RF_DB_STATEMENT_TIMEOUT_MS':'30000','RF_DB_LOCK_TIMEOUT_MS':'5000',
         'RF_DB_IDLE_TRANSACTION_TIMEOUT_MS':'90000'}
    compose=['docker','compose','--env-file',str(empty),'-p',args.project,'-f',str(ROOT/'compose.yaml'),'-f',str(override)]
    report={'passed':False,'project':args.project,'image':args.image,'model_api_calls':0,'checks':[],
            'started_at':datetime.now(timezone.utc).isoformat()}
    def command(argv):
        p=subprocess.run(argv,cwd=ROOT,env=env,capture_output=True,timeout=240)
        if p.returncode:
            (work/'last-error.log').write_bytes(p.stdout+p.stderr)
            raise RuntimeError('QA command failed; inspect private work log')
        return p.stdout.decode('utf-8')
    def sql(query):return command(['docker','exec',args.project+'-db-1','psql','-U','resolveflow','-d','resolveflow','-At','-v','ON_ERROR_STOP=1','-c',query]).strip()
    def require(label,condition=True):
        report['checks'].append({'check':label,'passed':bool(condition)})
        if not condition:raise AssertionError(label)
    def wait(fn,seconds=65):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            value=fn()
            if value:return value
            time.sleep(.3)
        raise TimeoutError('QA condition timed out')
    def alerts():return api(args.project,'/alerts',role='admin')
    def codes():return {a['code'] for a in alerts()['alerts']}
    def logs(service):
        p=subprocess.run(['docker','logs',args.project+'-'+service],capture_output=True,check=True)
        lines=(p.stdout+p.stderr).decode('utf-8').splitlines();rows=[]
        for line in lines:
            try:row=json.loads(line)
            except ValueError:continue
            if isinstance(row,dict) and 'event' in row:rows.append(row)
        return rows
    def transitions(code,state):return [r for r in logs('monitor-1') if r.get('code')==code and r.get('transition')==state]
    def gate(enabled):
        if enabled:sql('CREATE FUNCTION qa_alert_gate() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep (25); RETURN NEW; END $$; CREATE TRIGGER qa_alert_gate BEFORE INSERT ON rf_refunds FOR EACH ROW EXECUTE FUNCTION qa_alert_gate()')
        else:sql('DROP TRIGGER IF EXISTS qa_alert_gate ON rf_refunds; DROP FUNCTION IF EXISTS qa_alert_gate()')
    try:
        require('fresh isolated project',not command(['docker','ps','-aq','--filter','label=com.docker.compose.project='+args.project]).strip())
        report['image_id']=command(['docker','image','inspect',args.image,'--format','{{.Id}}']).strip()
        command(compose+['up','--no-build','-d','--scale','worker=2','--wait','--wait-timeout','180'])
        wait(lambda: alerts()['workers_online']==2)
        require('admin only alerts',api(args.project,'/alerts',role='operator',expected=403) is None and api(args.project,'/alerts',role='reviewer',expected=403) is None)
        require('healthy system has no alerts',codes()==set())
        role=command(['docker','exec',args.project+'-monitor-1','python','-c',
            "import os,psycopg;\nwith psycopg.connect(os.environ['READONLY_DATABASE_URL']) as c:print(c.execute('SELECT current_user').fetchone()[0])"]).strip()
        require('observer uses readonly DB role',role=='rf_readonly')

        # The independent observer must work even while no browser is polling.
        previous_firing=len(transitions('worker_offline','firing'))
        previous_resolved=len(transitions('worker_offline','resolved'))
        command(['docker','kill',args.project+'-worker-2'])
        wait(lambda:alerts()['workers_online']==1 and len(transitions('worker_offline','firing'))>previous_firing)
        report['offline']=alerts();require('partial Worker loss detected',report['offline']['workers_online']==1 and 'worker_offline' in codes())
        count=len(transitions('worker_offline','firing'))
        for _ in range(3):alerts()
        require('unchanged incident is not re-fired',len(transitions('worker_offline','firing'))==count)
        command(['docker','start',args.project+'-worker-2']);wait(lambda:alerts()['workers_online']==2)
        wait(lambda:len(transitions('worker_offline','resolved'))>previous_resolved);require('Worker recovery recorded')

        command(compose+['stop','worker'])
        queued=api(args.project,'/runs',{'order_id':'RF-1002','ticket':'申请退款'},expected=202)['id']
        wait(lambda:'queue_delayed' in codes());require('due queued task detected without Workers')
        command(compose+['start','worker']);require('queued task resumes',wait_run(args.project,queued)['status']=='auto_rejected')
        wait(lambda:'queue_delayed' not in codes())

        pending=api(args.project,'/runs',{'order_id':'RF-1004','ticket':'申请退款 qa38-private-ticket-sentinel'},expected=202)['id']
        require('approval saved before fault',wait_run(args.project,pending)['status']=='awaiting_approval')
        # An intentionally old human wait must not be classified as stalled work.
        sql("UPDATE rf_runs SET updated_at=now()-interval '1 day' WHERE id='"+pending+"'")
        require('human approval wait excluded',not any(a.get('run_id')==pending for a in alerts()['alerts']))
        gate(True)
        api(args.project,'/runs/'+pending+'/approval',{'approved':True,'reason':'qa38-private-reason-sentinel'},'reviewer',202)
        wait(lambda:sql("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE 'rf-task-%' AND wait_event='PgSleep'")!='0')
        blocked=wait(lambda:next((a for a in alerts()['alerts'] if a['code']=='task_stalled' and a['run_id']==pending),None))
        report['stalled']=blocked
        require('live heartbeats do not conceal a blocked task',alerts()['workers_online']==2)
        wait(lambda:transitions('task_stalled','firing'));require('independent observer sees blocked task')
        failed=wait(lambda:(r if (r:=api(args.project,'/runs/'+pending))['status']=='failed' else None))
        report['failure']={'run_id':pending,'attempts':failed['job']['attempts'],'error_type':failed['job']['last_error']}
        require('three actual task deadlines exhaust retries',failed['job']['attempts']==3 and failed['job']['last_error']=='TaskDeadlineExceeded')
        wait(lambda:'consecutive_failures' in codes() and 'job_failed' in codes())
        wait(lambda:transitions('consecutive_failures','firing'));require('real consecutive failures trigger independent alert')
        require('approval preserved and no partial refund',failed['approval']['approved'] and sql('SELECT count(*) FROM rf_refunds')=='0')
        gate(False)
        api(args.project,'/runs/'+pending+'/retry',{},'operator',403)
        api(args.project,'/runs/'+pending+'/retry',{},'admin',202)
        require('same approved checkpoint recovers',wait_run(args.project,pending)['status']=='refunded')
        wait(lambda:transitions('consecutive_failures','resolved'))
        require('failure alerts recover after success',not {'consecutive_failures','job_failed','task_stalled'} & codes())
        replay=api(args.project,'/runs',{'order_id':'RF-1004','ticket':'申请退款'},expected=202)['id']
        require('replay still requires approval',wait_run(args.project,replay)['status']=='awaiting_approval')
        api(args.project,'/runs/'+replay+'/approval',{'approved':True,'reason':'QA replay'},'reviewer',202)
        require('refund idempotency intact',wait_run(args.project,replay)['status']=='already_refunded' and sql('SELECT count(*) FROM rf_refunds')=='1')

        command(compose+['stop','db']);wait(lambda:transitions('monitor_unavailable','firing'))
        require('DB outage recorded as unknown, not healthy')
        api(args.project,'/alerts',role='admin',expected=503)
        command(compose+['start','db'])
        wait(lambda:subprocess.run(['docker','exec',args.project+'-db-1','pg_isready','-U','resolveflow'],capture_output=True).returncode==0)
        wait(lambda:transitions('monitor_unavailable','resolved'));require('observer recovers after DB restart')
        wait(lambda:alerts()['workers_online']==2 and not codes())

        rows=[r for service in ('resolveflow-1','worker-1','worker-2','monitor-1') for r in logs(service)]
        selected=[r for r in rows if r.get('run_id')==pending]
        started=[r for r in selected if r['event']=='job_started']
        require('attempt IDs unique across retries and resume',len({r['attempt_id'] for r in started})==len(started) and len(started)>=5)
        queued_events=[r for r in selected if r['event']=='job_enqueued']
        require('API request linked through durable run id',bool(queued_events) and all(any(r['event']=='http_finished' and r.get('request_id')==q['request_id'] for r in selected) for q in queued_events))
        tasks=[r for r in selected if r['event']=='task_started']
        require('Worker and SQL task tokens correlated',bool(tasks) and all('worker_id' in r and 'attempt_id' in r and 'task_token' in r for r in tasks))
        require('finished events follow started attempts',all(any(s['attempt_id']==r['attempt_id'] for s in started) for r in selected if r['event']=='job_finished'))
        raw=[]
        for service in ('resolveflow-1','worker-1','worker-2','monitor-1'):
            p=subprocess.run(['docker','logs',args.project+'-'+service],capture_output=True,check=True)
            raw.append((p.stdout+p.stderr).decode('utf-8'))
        text='\n'.join(raw)
        require('no credentials, ticket or approval text in service logs',not any(secret in text for secret in ['qa38-private-ticket-sentinel','qa38-private-reason-sentinel','qa38-admin-password',env['RF_APP_PASSWORD'],env['RF_READONLY_PASSWORD'],env['APP_API_KEY']]))
        report['events']=rows
        report['final_alerts']=alerts();report['passed']=True
    finally:
        command(compose+['stop'])
        report['stopped_volume_preserved']=True;report['finished_at']=datetime.now(timezone.utc).isoformat()
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(report['checks'])}))


if __name__=='__main__':main()
