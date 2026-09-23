"""Step 3.7: isolated, restricted-role, two-Worker deadline/recovery acceptance."""
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
from scripts.restore_qa import api,wait_run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--port',default='8020')
    args=parser.parse_args()
    if not args.project.startswith('resolveflow-qa-') or args.report.exists():parser.error('Use a new QA target/report')
    work=ROOT/'work'/args.project;work.mkdir(parents=True,exist_ok=False)
    empty=work/'empty.env';empty.write_text('')
    config=work/'runtime.json'
    first=unused_subnet();second=unused_subnet(excluded=[first])
    override={'networks':{'default':{'ipam':{'config':[{'subnet':first}]}},
        'embedding-private':{'ipam':{'config':[{'subnet':second}]}}},
        'services':{'worker':{'environment':{'RF_TASK_TIMEOUT_SECONDS':'8'},
                            'healthcheck':{'interval':'2s'}}}}
    config.write_text(json.dumps(override))
    env={**os.environ,'RESOLVEFLOW_IMAGE':args.image,'MODE':'demo','RETRIEVAL_MODE':'bm25',
        'COMPOSE_PROFILES':'','POSTGRES_IMAGE':'postgres:17','POSTGRES_PASSWORD':'qa37-admin-only',
        'RF_MIGRATOR_PASSWORD':'qa37-migrator-password-123456',
        'RF_APP_PASSWORD':'qa37-app-password-1234567890',
        'RF_READONLY_PASSWORD':'qa37-readonly-password-123456',
        'APP_API_KEY':'qa37-operator','REVIEWER_API_KEY':'qa37-reviewer','ADMIN_API_KEY':'qa37-admin',
        'OPENAI_API_KEY':'','APP_PORT':args.port,'RF_DB_POOL_MAX':'2',
        'RF_DB_POOL_MAX_WAITING':'2','RF_DB_POOL_TIMEOUT_MS':'400',
        'RF_DB_STATEMENT_TIMEOUT_MS':'1000','RF_DB_LOCK_TIMEOUT_MS':'700',
        'RF_DB_IDLE_TRANSACTION_TIMEOUT_MS':'60000','RF_TASK_TIMEOUT_SECONDS':'8'}
    compose=['docker','compose','--env-file',str(empty),'-p',args.project,'-f',str(ROOT/'compose.yaml'),'-f',str(config)]
    report={'passed':False,'project':args.project,'image':args.image,'started_utc':datetime.now(timezone.utc).isoformat(),
            'checks':[],'model_api_calls':0,'refund_execution':'simulated','limits':{'sql_ms':1000,'lock_ms':700,'task_seconds':8}}
    def command(argv):
        p=subprocess.run(argv,cwd=ROOT,env=env,capture_output=True,timeout=240)
        if p.returncode:
            (work/'last-error.log').write_bytes(p.stdout+p.stderr)
            raise RuntimeError('QA command failed; private log in QA work directory')
        return p.stdout.decode('utf-8')
    def sql(query):
        return command(['docker','exec',args.project+'-db-1','psql','-U','resolveflow','-d','resolveflow','-At','-v','ON_ERROR_STOP=1','-c',query]).strip()
    def require(value,label):
        report['checks'].append({'check':label,'passed':bool(value)})
        if not value:raise AssertionError(label)
    def wait(check,seconds=60):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            result=check()
            if result:return result
            time.sleep(.2)
        raise TimeoutError('QA condition not reached')
    def failed(rid):
        def check():
            row=api(args.project,'/runs/'+rid)
            return row if row['status']=='failed' and row['job']['status']=='failed' else None
        return wait(check)
    def gate(enabled):
        if enabled:
            sql('CREATE FUNCTION qa_runtime_gate() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep (25); RETURN NEW; END $$; '
                'CREATE TRIGGER qa_runtime_gate BEFORE INSERT ON rf_refunds FOR EACH ROW EXECUTE FUNCTION qa_runtime_gate()')
        else:sql('DROP TRIGGER IF EXISTS qa_runtime_gate ON rf_refunds; DROP FUNCTION IF EXISTS qa_runtime_gate()')
    def idle():
        return sql("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE 'rf-task-%'")=='0'
    try:
        require(not command(['docker','ps','-aq','--filter','label=com.docker.compose.project='+args.project]).strip(),'new isolated project')
        command(compose+['up','--no-build','-d','--scale','worker=2','--wait','--wait-timeout','180'])
        report['image_id']=command(['docker','image','inspect','--format','{{.Id}}',args.image]).strip()
        before=[json.loads(command(['docker','inspect',args.project+'-worker-'+str(i)]))[0]['State']['StartedAt'] for i in (1,2)]
        require(api(args.project,'/metrics',role='admin')['workers_online']==2,'two healthy restricted Workers')
        rid=api(args.project,'/runs',{'order_id':'RF-1004','ticket':'申请退款'},expected=202)['id']
        require(wait_run(args.project,rid)['status']=='awaiting_approval','pending approval checkpoint saved')
        gate(True)
        api(args.project,'/runs/'+rid+'/approval',{'approved':True,'reason':'3.7 isolated SQL timeout'},'operator',403)
        api(args.project,'/runs/'+rid+'/approval',{'approved':True,'reason':'3.7 isolated SQL timeout'},'reviewer',202)
        start=time.monotonic();row=failed(rid)
        report['sql_timeout']={'run_id':rid,'seconds':round(time.monotonic()-start,3),'attempts':row['job']['attempts'],'error':row['job']['last_error']}
        require(row['job']['attempts']==3 and row['job']['last_error']=='QueryCanceled','non-MCP refund SQL consumes exactly three attempts')
        require(sql('SELECT count(*) FROM rf_refunds')=='0','timed out refund transaction rolled back')
        require(row['approval']['approved'] is True,'durable approval survives SQL timeouts')
        wait(idle);require(True,'no task SQL remains after SQL retry exhaustion')
        gate(False)
        api(args.project,'/runs/'+rid+'/retry',{},'operator',403)
        api(args.project,'/runs/'+rid+'/retry',{},'admin',202)
        row=wait_run(args.project,rid)
        require(row['status']=='refunded','original approved checkpoint resumes after admin retry')
        require(sum(t.get('event')=='skill_loaded' for t in row['state']['trace'])==1,'approval recovery did not repeat investigation')
        api(args.project,'/runs/'+rid+'/approval',{'approved':False,'reason':'duplicate'},'reviewer',409)
        require(True,'operator permissions and duplicate approval remain enforced')

        # Permit SQL to run longer than the task deadline to distinguish the
        # overall task supervisor from server-side per-statement cancellation.
        override['services']['worker']['environment'].update(RF_DB_STATEMENT_TIMEOUT_MS='30000',RF_DB_LOCK_TIMEOUT_MS='30000',RF_TASK_TIMEOUT_SECONDS='6')
        config.write_text(json.dumps(override))
        command(compose+['up','--no-deps','--no-build','-d','--scale','worker=2','--wait','worker'])
        before=[json.loads(command(['docker','inspect',args.project+'-worker-'+str(i)]))[0]['State']['StartedAt'] for i in (1,2)]
        gate(True)
        rid=api(args.project,'/runs',{'order_id':'RF-1001','ticket':'申请退款'},expected=202)['id']
        wait(lambda: sql("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE 'rf-task-%' AND wait_event='PgSleep'")!='0')
        require(True,'observed actual refund delay inside restricted task SQL')
        long_tx=sql("SELECT count(*) FROM pg_stat_activity WHERE usename='rf_app' AND state='idle in transaction'")
        require(int(long_tx)>0,'queue transaction remains open during task')
        other=api(args.project,'/runs',{'order_id':'RF-1002','ticket':'申请退款'},expected=202)['id']
        require(wait_run(args.project,other)['status']=='auto_rejected','second Worker completes unrelated task while refund is blocked')
        row=failed(rid)
        report['task_deadline']={'run_id':rid,'attempts':row['job']['attempts'],'error':row['job']['last_error'],
            'attempt_elapsed_ms':json.loads(sql("SELECT json_agg(elapsed_ms ORDER BY id) FROM rf_job_attempts WHERE run_id='"+rid+"'"))}
        require(row['job']['attempts']==3 and row['job']['last_error']=='TaskDeadlineExceeded','overall deadline consumes exactly three attempts')
        require(all(5500<=ms<13000 for ms in report['task_deadline']['attempt_elapsed_ms']),'each six-second task ends within bounded cleanup allowance')
        wait(idle);require(True,'no task or MCP database connections remain after deadline')
        require(sql("SELECT count(*) FROM rf_refunds WHERE order_id='RF-1001'")=='0','killed task leaves no partial refund')
        sql("BEGIN; SELECT run_id FROM rf_jobs WHERE run_id='"+rid+"' FOR UPDATE NOWAIT; ROLLBACK")
        require(True,'long queue transaction released after deadline')
        after=[json.loads(command(['docker','inspect',args.project+'-worker-'+str(i)]))[0]['State']['StartedAt'] for i in (1,2)]
        require(before==after,'supervisor Workers did not restart during timeouts')
        gate(False)
        api(args.project,'/runs/'+rid+'/retry',{},'admin',202)
        require(wait_run(args.project,rid)['status']=='refunded','same task resumes after deadline exhaustion')
        duplicate=api(args.project,'/runs',{'order_id':'RF-1001','ticket':'申请退款'},expected=202)['id']
        require(wait_run(args.project,duplicate)['status']=='already_refunded','same order replay is idempotent')
        require(sql("SELECT count(*) FROM rf_refunds WHERE order_id='RF-1001'")=='1','one simulated refund for repeated order')
        require(sql("SELECT coalesce(sum((state->'usage'->>'model_calls')::int),0) FROM rf_runs")=='0','demo model call count remains zero')
        report['passed']=True
    finally:
        command(compose+['stop'])
        report['stopped_volume_preserved']=True
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(report['checks'])}))


if __name__=='__main__':main()
