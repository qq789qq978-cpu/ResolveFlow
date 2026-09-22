"""Recreate only this Compose project, preserving every volume; demo only.

Creates one synthetic waiting-approval run, compares complete database table
fingerprints across recreation, then rejects that run through the normal API.
Never uses down -v. Leaves the stack running and writes a secret-free report.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from dotenv import dotenv_values
import httpx

SNAPSHOT = '''
import json,os
from psycopg import sql
from storage import Store
tables=['rf_orders','rf_runs','rf_jobs','rf_approvals','rf_audit','rf_refunds',
        'rf_reviews','rf_job_attempts','checkpoints','checkpoint_writes',
          'checkpoint_blobs','checkpoint_migrations','rf_knowledge_documents','rf_knowledge_chunks',
          'rf_policy_releases','rf_policy_head','rf_policy_reviews','rf_policy_events',
          'rf_vector_batches','rf_policy_vectors']
result={}
with Store(os.environ['DATABASE_URL']).connect() as c:
    for table in tables:
        if c.execute('SELECT to_regclass(%s) AS relation',(table,)).fetchone()['relation'] is None: continue
        query=sql.SQL("SELECT count(*) AS count, md5(COALESCE(string_agg(to_jsonb(t)::text, E'\\\\n' ORDER BY to_jsonb(t)::text),'')) AS fingerprint FROM {} AS t").format(sql.Identifier(table))
        result[table]=c.execute(query).fetchone()
print(json.dumps(result))
'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',type=Path,default=Path(__file__).parent)
    parser.add_argument('--url',default='http://127.0.0.1:8003')
    parser.add_argument('--report',type=Path,default=Path('validation/persistence.json'))
    args=parser.parse_args()
    root=args.project.resolve()
    configuration={**dotenv_values(root/'.env',encoding='utf-8-sig'),**os.environ}
    docker=os.environ.get('DOCKER_EXE','docker')
    env=dict(os.environ,MODE='demo')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    report={'started_at':datetime.now(timezone.utc).isoformat(),'mode':'demo','passed':False,
            'scope':'Graceful Compose recreation; not a disk-loss or forced-crash test',
            'excluded_dynamic_table':'rf_worker_heartbeats'}
    def save(): args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def command(*values,data=None):
        result=subprocess.run([docker,*values],cwd=root,env=env,input=data,
                              text=True,encoding='utf-8',errors='replace',capture_output=True)
        if result.returncode: raise RuntimeError((result.stdout+result.stderr)[-5000:])
        return result.stdout
    def compose(*values,data=None): return command('compose',*values,data=data)
    def snapshot(): return json.loads(compose('exec','-T','resolveflow','python','-',data=SNAPSHOT))
    def containers():
        result={}
        for service in ('db','resolveflow','worker'):
            cid=compose('ps','-q',service).strip()
            if not cid: raise RuntimeError('Missing container: '+service)
            inspected=json.loads(command('inspect','--format','{{json .}}',cid))
            # Never save inspected Config.Env: it contains secrets.
            result[service]={'id':cid,'health':inspected['State']['Health']['Status'],
                'volumes':{m['Destination']:m['Name'] for m in inspected['Mounts'] if m['Type']=='volume'}}
        return result
    keys={role:{'X-API-Key':configuration[name]} for role,name in
          [('operator','APP_API_KEY'),('reviewer','REVIEWER_API_KEY'),('admin','ADMIN_API_KEY')]}
    with httpx.Client(base_url=args.url,timeout=15,trust_env=False) as client:
        def get_run(rid):
            response=client.get('/api/runs/'+rid,headers=keys['operator'])
            response.raise_for_status()
            return response.json()
        def wait(rid,status):
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                row=get_run(rid)
                if row['status']==status: return row
                if row['status']=='failed': raise RuntimeError('Validation run failed')
                time.sleep(1)
            raise TimeoutError('Timed out waiting for '+status)
        try:
            response=client.get('/health');response.raise_for_status()
            assert response.json()['mode']=='demo','Validation requires an already running demo stack'
            before=containers()
            assert all(c['health']=='healthy' for c in before.values())
            report['initial_tables']=snapshot()
            # Record every preexisting run ID without persisting ticket text or keys.
            original=[]
            offset=0
            while True:
                response=client.get('/api/runs',params={'limit':100,'offset':offset},headers=keys['operator'])
                response.raise_for_status(); rows=response.json()
                original.extend(r['id'] for r in rows)
                if len(rows)<100: break
                offset+=100
            assert client.get('/api/runs').status_code==401
            assert client.get('/api/metrics',headers=keys['operator']).status_code==403
            response=client.post('/api/runs',headers=keys['operator'],json={'order_id':'RF-1004','ticket':'申请退款，容器重建持久化验收'})
            assert response.status_code==202
            rid=response.json()['id'];report['resume_run_id']=rid;save()
            waiting=wait(rid,'awaiting_approval')
            assert waiting['job']['status']=='done'
            report['before_recreation']=snapshot()
            report['containers_before']=before
            save()
            compose('down','--timeout','30')  # preserve all named volumes
            compose('up','-d','--wait','--wait-timeout','180')
            after=containers()
            report['containers_after']=after
            assert all(c['health']=='healthy' for c in after.values())
            assert all(before[s]['id']!=after[s]['id'] for s in before),'Containers were not recreated'
            assert all(before[s]['volumes']==after[s]['volumes'] for s in before),'Volume identity changed'
            report['after_recreation']=snapshot()
            assert report['before_recreation']==report['after_recreation'],'Table contents changed during recreation'
            assert get_run(rid)['status']=='awaiting_approval'
            for original_id in original: get_run(original_id)
            report['original_runs_retained']=original
            body={'approved':False,'reason':'持久化验收：重建后拒绝例外退款'}
            route='/api/runs/'+rid+'/approval'
            assert client.post(route,headers=keys['operator'],json=body).status_code==403
            assert client.post(route,headers=keys['reviewer'],json=body).status_code==202
            assert client.post(route,headers=keys['reviewer'],json=body).status_code==409
            final=wait(rid,'rejected')
            initial_trace=[t for t in waiting['state']['trace'] if t['node']=='investigate']
            final_trace=[t for t in final['state']['trace'] if t['node']=='investigate']
            assert initial_trace==final_trace,'Investigation trace changed on resume'
            assert any(t['node']=='approval' for t in final['state']['trace'])
            report['after_resume']=snapshot()
            assert report['after_resume']['rf_refunds']==report['before_recreation']['rf_refunds']
            assert report['after_resume']['checkpoints']['count']>report['after_recreation']['checkpoints']['count']
            metrics=client.get('/api/metrics',headers=keys['admin']);metrics.raise_for_status()
            assert metrics.json()['workers_online']>=1
            report.update(passed=True,all_table_fingerprints_equal=True,all_container_ids_changed=True,
                all_volume_names_retained=True,resumed_status=final['status'],investigation_trace_unchanged=True,
                refund_ledger_unchanged=True,workers_online=metrics.json()['workers_online'],
                finished_at=datetime.now(timezone.utc).isoformat())
            save()
            print(json.dumps({'passed':True,'original_runs':len(original),'run_id':rid,
                'tables_verified':len(report['before_recreation']),'resumed_status':final['status']}))
        except BaseException as error:
            report['error_type']=type(error).__name__;save()
            raise

if __name__=='__main__': main()
