"""Two isolated restores plus real API/Worker approval recovery, demo only."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.database_backup import create_backup, run, utc, write_json
from scripts.database_restore import restore_backup, target_names, wait_database, ensure_unused


def python_in(project, source):
    return json.loads(run(['docker','exec',project+'-resolveflow-1','python','-c',source]))


def api(project, path, body=None, role='operator', expected=200):
    source = '''
import json,os,urllib.request,urllib.error
path,body,role = PAYLOAD
key={'operator':'APP_API_KEY','reviewer':'REVIEWER_API_KEY','admin':'ADMIN_API_KEY'}[role]
request=urllib.request.Request('http://127.0.0.1:8000/api'+path,
 headers={'X-API-Key':os.environ[key],'Content-Type':'application/json'},
 data=json.dumps(body).encode() if body is not None else None)
try:
 with urllib.request.urlopen(request,timeout=15) as response:
  print(json.dumps({'status':response.status,'body':json.load(response)}))
except urllib.error.HTTPError as error:
 print(json.dumps({'status':error.code}))
'''.replace('PAYLOAD', repr((path,body,role)))
    result = python_in(project, source)
    if result['status'] != expected:
        raise RuntimeError('Unexpected API status')
    return result.get('body')


def wait_run(project, rid):
    deadline = time.monotonic()+90
    while time.monotonic()<deadline:
        row = api(project,'/runs/'+rid)
        if row['status'] not in ('queued','running','approval_queued','retrying') and row['job']['status']=='done':
            return row
        if row['status']=='failed':
            raise RuntimeError('Restored QA job failed')
        time.sleep(.3)
    raise TimeoutError('QA job timeout')


def start_runtime(restored):
    project = restored['target']['project']
    run(['docker','start',restored['target']['db']])
    wait_database(restored['target']['db'])
    env_file = ROOT/'work/restores'/project/'runtime.env'
    # Reject queued/live restored work before starting any Worker. The exercise
    # generates its own synthetic cases and cannot spend model tokens.
    guard = '''import os,psycopg,json
with psycopg.connect(os.environ['DATABASE_URL']) as c:
 assert c.execute("SELECT count(*) FROM rf_runs WHERE mode <> 'demo'").fetchone()[0]==0
 assert c.execute("SELECT count(*) FROM rf_jobs WHERE status <> 'done'").fetchone()[0]==0
print(json.dumps({'safe':True}))'''
    run(['docker','run','--rm','--network',restored['target']['network'],'--env-file',str(env_file),
         restored['images']['resolveflow'],'python','-c',guard])
    for service in ('resolveflow','worker'):
        command = ['docker','run','--detach','--pull=never','--name',project+'-'+service+'-1',
            '--label','com.docker.compose.project='+project,'--label','com.docker.compose.service='+service,
            '--network',restored['target']['network'],'--env-file',str(env_file),
            '--health-interval','2s','--health-timeout','3s','--health-retries','30']
        if service=='worker':
            command += ['--health-cmd','python worker_health.py']
        command += [restored['images'][service]]
        if service=='worker':command += ['python','worker.py']
        run(command)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            state=run(['docker','inspect','--format','{{.State.Health.Status}}',project+'-'+service+'-1']).decode().strip()
            if state=='healthy':break
            time.sleep(.5)
        else:raise TimeoutError('QA runtime not healthy')


def stop_project(project):
    ids=run(['docker','ps','-q','--filter','label=com.docker.compose.project='+project]).decode().split()
    if ids:run(['docker','stop',*ids],timeout=60)


def exercise(bundle, prefix, report):
    seed, target = prefix+'-seed', prefix+'-resume'
    ensure_unused(target_names(seed));ensure_unused(target_names(target))
    report=Path(report)
    if report.exists():raise ValueError('Use a new report path')
    artifacts=ROOT/'work/restore-qa'/prefix
    artifacts.mkdir(parents=True,exist_ok=False)
    result={'passed':False,'started_at_utc':utc(),'model_api_calls':0,'main_database_writes':0}
    owned=[]
    try:
        print('Restoring a fresh isolated source and preparing two waiting approvals...',flush=True)
        original=restore_backup(bundle,seed,artifacts/'seed-restore.json')
        owned.append(seed)
        start_runtime(original)
        python_in(seed,'''import os,json,psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as c:
 for oid in ('RF-3501','RF-3502'):
  assert c.execute("INSERT INTO rf_orders SELECT %s,owner,amount,days,used,status FROM rf_orders WHERE id='RF-1004' RETURNING id",(oid,)).fetchone()
print(json.dumps({'inserted':2}))''')
        pending=[]
        for order in ('RF-3501','RF-3502'):
            rid=api(seed,'/runs',{'order_id':order,'ticket':'申请退款'},expected=202)['id']
            row=wait_run(seed,rid)
            assert row['status']=='awaiting_approval' and row['approval'] is None
            pending.append(row)
        saved,manifest=create_backup(artifacts/'backups',seed)
        stop_project(seed)
        assert not run(['docker','ps','-q','--filter','label=com.docker.compose.project='+seed]).strip()
        result['seed_restore']=original
        result['pending_backup']={'private_path':str(saved.relative_to(ROOT)).replace('\\','/'),
                                  'archive':manifest['archive']}
        print('Restoring again, checking every table and resuming original run IDs...',flush=True)
        restored=restore_backup(saved,target,artifacts/'pending-restore.json')
        owned.append(target)
        result['pending_restore']=restored
        start_runtime(restored)
        for row in pending:
            assert api(target,'/runs/'+row['id'])==row
        refunds_before=manifest['snapshot']['tables']['rf_refunds']['count']
        results=[]
        # Reject first, then approve. Both use the original restored run ID.
        for row,approved,status in ((pending[1],False,'rejected'),(pending[0],True,'refunded')):
            route='/runs/'+row['id']+'/approval'
            body={'approved':approved,'reason':'Independent backup restore acceptance'}
            api(target,route,body,expected=403)
            api(target,route,body,'reviewer',202)
            api(target,route,body,'reviewer',409)
            api(target,route,{**body,'approved':not approved},'reviewer',409)
            after=wait_run(target,row['id'])
            assert after['status']==status and after['approval']['approved'] is approved
            assert after['id']==row['id']
            assert after['state']['proposal']==row['state']['proposal']
            assert after['state']['evidence']==row['state']['evidence']
            before_trace=[t for t in row['state']['trace'] if t['node']=='investigate']
            assert before_trace and before_trace==[t for t in after['state']['trace'] if t['node']=='investigate']
            assert sum(t['node']=='approval' for t in after['state']['trace'])==1
            results.append({'run_id':row['id'],'order_id':row['order_id'],'approved':approved,
                'status':status,'investigation_unchanged':True,'evidence_and_proposal_unchanged':True,
                'duplicate_and_opposite_approval_status':409,'operator_approval_status':403})
        duplicate=api(target,'/runs',{'order_id':'RF-3501','ticket':'申请退款'},expected=202)['id']
        row=wait_run(target,duplicate)
        if row['status']=='awaiting_approval':
            api(target,'/runs/'+duplicate+'/approval',{'approved':True,'reason':'Idempotency probe'},'reviewer',202)
            row=wait_run(target,duplicate)
        assert row['status']=='already_refunded'
        final=python_in(target,'''import os,json,psycopg
from psycopg.rows import dict_row
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as c:
 count=c.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']
 rows=c.execute("SELECT order_id,run_id FROM rf_refunds WHERE order_id IN ('RF-3501','RF-3502')").fetchall()
 attempts=c.execute('SELECT max(id) AS n FROM rf_job_attempts').fetchone()['n']
 audit=c.execute('SELECT max(id) AS n FROM rf_audit').fetchone()['n']
 checkpoints=c.execute('SELECT count(*) AS n FROM checkpoints').fetchone()['n']
print(json.dumps({'refund_count':count,'fixture_refunds':rows,'max_attempt_id':attempts,'max_audit_id':audit,'checkpoint_count':checkpoints},default=str))''')
        assert final['refund_count']==refunds_before+1
        assert final['fixture_refunds']==[{'order_id':'RF-3501','run_id':pending[0]['id']}]
        assert final['checkpoint_count']>manifest['snapshot']['tables']['checkpoints']['count']
        seq=restored['validation']['sequences']
        assert final['max_attempt_id']>seq['rf_job_attempts_id_seq']['table_max']
        assert final['max_audit_id']>seq['rf_audit_id_seq']['table_max']
        result.update(passed=True,resume_verified=True,resumed=results,duplicate_status=row['status'],
                      final=final,source_stopped_during_resume=True,volumes_preserved=True)
    finally:
        for project in owned:stop_project(project)
        result['completed_at_utc']=utc()
        result['projects_stopped']=owned
        report.parent.mkdir(parents=True,exist_ok=True)
        write_json(report,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle',type=Path)
    parser.add_argument('--prefix',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=exercise(args.bundle,args.prefix,args.report)
        print(json.dumps({'passed':result['passed'],'resumed':len(result['resumed']),'duplicate_status':result['duplicate_status']}))
        return 0
    except Exception as exc:
        print(json.dumps({'passed':False,'error_type':type(exc).__name__}))
        return 1


if __name__=='__main__':raise SystemExit(main())
