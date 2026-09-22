"""Real Compose new-install and pre-migration Worker upgrade acceptance.

Uses only disposable projects, public fixture credentials and demo calls.
Preserves all named volumes. Supply unique project names for every run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = '''
import hashlib,json,os,psycopg
from psycopg import sql
with psycopg.connect(os.environ['DATABASE_URL']) as c:
 c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 names=[r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
 out={}
 for name in names:
  if name in ('rf_worker_heartbeats','rf_schema_version'):continue
  rows=[json.dumps(r[0],sort_keys=True) for r in c.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t').format(sql.Identifier(name)))]
  out[name]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()}
 print(json.dumps(out))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--legacy-image',required=True)
    parser.add_argument('--prefix',default='resolveflow-qa-step32')
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if args.report.exists():parser.error('Choose a new report path')
    work=ROOT/'work'/args.prefix
    work.mkdir(parents=True,exist_ok=True)
    empty=work/'empty.env';empty.write_text('')
    env={**os.environ,'MODE':'demo','RETRIEVAL_MODE':'bm25','OPENAI_API_KEY':'',
         'APP_API_KEY':'qa-migration-operator','REVIEWER_API_KEY':'qa-migration-reviewer',
         'ADMIN_API_KEY':'qa-migration-admin','POSTGRES_PASSWORD':'qa-migration-database',
         'RESOLVEFLOW_IMAGE':args.image,'LEGACY_IMAGE':args.legacy_image,
         'COMPOSE_PROFILES':'','POSTGRES_IMAGE':'pgvector/pgvector:0.8.6-pg17-trixie@sha256:724a4041afdb1750446e3f6b5cfa8f3b0ac5a2cf538ddfa6bfee4f94c2fa85c6'}
    projects=[];result={'passed':False,'model_api_calls':0,'checks':[]}
    def docker(*items,input=None,expected=0):
        r=subprocess.run(['docker',*items],input=input,cwd=ROOT,env=env,text=True,
                         encoding='utf-8',capture_output=True,timeout=200)
        if r.returncode!=expected:
            # Fixture-only logs, no main environment is loaded by these projects.
            (work/'last-error.log').write_text(r.stdout+'\n'+r.stderr,encoding='utf-8')
            raise RuntimeError('QA Docker command failed: '+items[0])
        return r.stdout.strip()
    def compose(project,legacy=False):
        return ['compose','--env-file',str(empty),'--project-directory',str(ROOT),'-p',project,
                '-f',str(ROOT/('tests/fixtures/legacy_compose.yaml' if legacy else 'compose.yaml'))]
    def api(port,path,body=None,role='operator'):
        request=urllib.request.Request(f'http://127.0.0.1:{port}/api'+path,
            headers={'X-API-Key':'qa-migration-'+role,'Content-Type':'application/json'},
            data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(request,timeout=15) as r:return json.load(r)
    def submit(port,order):
        rid=api(port,'/runs',{'order_id':order,'ticket':'申请退款'})['id']
        return wait(port,rid)
    def wait(port,rid):
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            row=api(port,'/runs/'+rid)
            if row['status'] not in ('queued','running','approval_queued','retrying'):return row
            time.sleep(.3)
        raise TimeoutError('QA Worker did not finish')
    def snapshot(project):
        return json.loads(docker(*compose(project),'run','--rm','--no-deps','-T','migrate','python','-',input=SNAPSHOT))
    try:
        fresh=args.prefix+'fresh';projects.append(fresh);env['APP_PORT']='8017'
        assert not docker('ps','-a','--filter','label=com.docker.compose.project='+fresh,'--format','{{.ID}}')
        print('Testing a fresh Compose installation...',flush=True)
        docker(*compose(fresh),'up','--no-build','-d','--wait','--wait-timeout','180')
        installed=json.loads(docker(*compose(fresh),'logs','--no-log-prefix','migrate'))
        assert installed['action']=='installed' and installed['demo_seeded']
        fresh_run=submit(8017,'RF-1001');assert fresh_run['status']=='refunded'
        result['fresh']={'migration':installed,'run_id':fresh_run['id'],'status':fresh_run['status']}
        result['checks'].append('Fresh Compose orders DB -> migrator -> API -> Worker and executes a refund')
        docker(*compose(fresh),'stop')

        legacy=args.prefix+'legacy';projects.append(legacy);env['APP_PORT']='8018'
        assert not docker('ps','-a','--filter','label=com.docker.compose.project='+legacy,'--format','{{.ID}}')
        print('Creating completed and pending work with the old image...',flush=True)
        docker(*compose(legacy,True),'up','--no-build','-d','--wait','--wait-timeout','180')
        completed=[]
        for order,expected in [('RF-1001','refunded'),('RF-1002','auto_rejected')]:
            row=submit(8018,order);assert row['status']==expected;completed.append(row)
        rejected=submit(8018,'RF-1004');assert rejected['status']=='awaiting_approval'
        api(8018,'/runs/'+rejected['id']+'/approval',{'approved':False,'reason':'Legacy rejection fixture'},'reviewer')
        rejected=wait(8018,rejected['id']);assert rejected['status']=='rejected';completed.append(rejected)
        pending=submit(8018,'RF-1004');assert pending['status']=='awaiting_approval'
        docker(*compose(legacy,True),'stop','worker','resolveflow')
        before=snapshot(legacy)
        assert before['rf_refunds']['count']==1 and before['rf_approvals']['count']==1
        refused=json.loads(docker(*compose(legacy),'run','--rm','--no-deps','-T','migrate',expected=1))
        assert 'explicit adopt' in refused['reason']
        assert snapshot(legacy)==before
        print('Adopting the old database and comparing all retained tables...',flush=True)
        adoption=json.loads(docker(*compose(legacy),'run','--rm','--no-deps','-T','migrate','python','db_migrate.py','adopt'))
        assert adoption['action']=='adopted' and not adoption['demo_seeded']
        assert snapshot(legacy)==before
        docker(*compose(legacy),'up','--no-build','-d','--wait','--wait-timeout','180')
        for row in completed+[pending]:assert api(8018,'/runs/'+row['id'])==row
        assert snapshot(legacy)==before
        result['checks'].append('Old refund, approval, audit, jobs, policy and all checkpoint tables preserved during adoption')
        print('Recreating the migrated stack, then resuming the old approval...',flush=True)
        docker(*compose(legacy),'down')
        docker(*compose(legacy),'up','--no-build','-d','--wait','--wait-timeout','180')
        assert snapshot(legacy)==before
        for row in completed+[pending]:assert api(8018,'/runs/'+row['id'])==row
        api(8018,'/runs/'+pending['id']+'/approval',{'approved':True,'reason':'Resume after migration'},'reviewer')
        resumed=wait(8018,pending['id']);assert resumed['status']=='refunded'
        duplicate=submit(8018,'RF-1001');assert duplicate['status']=='already_refunded'
        final=snapshot(legacy);assert final['rf_refunds']['count']==2
        result['checks'].append('Recreation preserves migration state; old pending approval resumes; old refund remains idempotent')
        result['legacy']={'before':before,'after_adoption':before,'preserved_tables':len(before),
                          'adoption':adoption,'old_pending_run':pending['id'],'resumed_status':resumed['status'],
                          'duplicate_status':duplicate['status'],'final':final}
        result['passed']=True
    finally:
        for project in projects:
            docker(*compose(project),'stop')
        result['projects_stopped']=projects
        result['volumes_preserved']=True
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'checks':len(result['checks'])}),flush=True)


if __name__=='__main__':main()
