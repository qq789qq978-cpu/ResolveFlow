"""Isolated step 5.3 acceptance, including real combined restore and outage events."""
import argparse
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.local_stack import generate, protect
from scripts.local_backup import Stack, create, verify, restore, run, start_existing
from scripts.local_probe import observe


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True);p.add_argument('--image',required=True)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--port',type=int,default=8053)
    args=p.parse_args()
    if args.report.exists():raise ValueError('Existing report')
    report={'passed':False,'checks':[],'model_api_calls':0,'real_refunds':0,'projects':[args.project]}
    work=generate(args.project,args.image,args.port);source=Stack(work);target=None;probe=None;probe_file=None
    port=args.port
    def check(name,condition):
        report['checks'].append({'check':name,'passed':bool(condition)})
        print(json.dumps({'check':name,'passed':bool(condition)}),flush=True)
        if not condition:raise AssertionError(name)
    def api(path,token=None,body=None,status=200):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=15) as r:code,data=r.status,json.load(r)
        except urllib.error.HTTPError as e:code,data=e.code,json.load(e)
        if code!=status:raise AssertionError(path+' unexpected status '+str(code))
        return data
    def login(name,password):return api('/api/session',body={'username':name,'password':password})['token']
    def wait_for(fn,seconds=100):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if fn():return True
            time.sleep(.5)
        return False
    def wait_run(rid,token):
        assert wait_for(lambda:api('/api/runs/'+rid,token)['job']['status']=='done')
        return api('/api/runs/'+rid,token)
    def new_run(order,token):
        rid=api('/api/runs',token,{'order_id':order,'ticket':'申请退款'},202)['id']
        return wait_run(rid,token)
    def sql(stack,workspace,query):
        text='import os,json,psycopg\nwith psycopg.connect(os.environ["DATABASE_URL"]) as c:\n r=c.execute('+repr(query)+')\n data=r.fetchall() if r.description else []\nprint(json.dumps(data,default=str))'
        return json.loads(stack.cmd('run','--rm','--no-deps','-T',workspace+'-migrate','python','-c',text))
    def monitor_events(stack):
        rows=[]
        for line in stack.cmd('logs','--no-log-prefix','alpha-monitor').decode().splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
        return rows
    try:
        source.cmd('run','--rm','--no-deps','-T','identity-init')
        source.cmd('up','-d','--wait','--wait-timeout','180')
        check('fresh local stack healthy',observe(f'http://127.0.0.1:{port}/health'))
        ids=source.cmd('ps','-q').decode().split()
        inspected=json.loads(run(['docker','inspect',*ids]))
        exposed=[(b['HostIp'],b['HostPort']) for c in inspected for bindings in c['HostConfig']['PortBindings'].values() for b in (bindings or [])]
        check('only loopback gateway published',exposed==[('127.0.0.1',str(port))])
        secret_values=[]
        for f in (work/'secrets').iterdir():
            if f.suffix=='.json':
                for key,value in json.loads(f.read_text()).items():
                    if key=='RF_WORKSPACES':secret_values.extend(v['service_key'] for v in json.loads(value).values())
                    else:secret_values.append(value)
            else:secret_values.append(f.read_text())
        config_text=(work/'compose.json').read_text()
        check('secret values absent compose and Docker Config.Env',all(v not in config_text+json.dumps([c['Config']['Env'] for c in inspected]) for v in secret_values))
        monitor_keys=set(json.loads((work/'secrets/alpha-monitor.json').read_text()))
        check('monitor has only own readonly credential',monitor_keys=={'READONLY_DATABASE_URL'} and 'RF_IDENTITY_SERVICE_KEY' not in json.loads((work/'secrets/alpha-worker.json').read_text()))
        manager=login('maintainer',(work/'bootstrap.txt').read_text());password=secrets.token_urlsafe(24)
        people={}
        for name,role,workspace in [('alice','operator','alpha'),('anna','reviewer','alpha'),('admina','admin','alpha'),('bob','admin','beta')]:
            people[name]=api('/api/accounts',manager,{'username':name,'password':password,'role':role,'workspace':workspace},201)['id']
        a=login('alice',password);reviewer=login('anna',password);admin=login('admina',password);b=login('bob',password)
        auto=new_run('RF-1001',a);pending=new_run('RF-1004',a);beta=new_run('RF-1001',b)
        report['runs']={'auto':auto['id'],'pending':pending['id'],'beta':beta['id']}
        check('real demo refunds and pending checkpoint created',auto['status']==beta['status']=='refunded' and pending['status']=='awaiting_approval')
        source.cmd('up','-d','--no-deps','--force-recreate','--wait','--wait-timeout','100','gateway','alpha-api','alpha-worker')
        check('identity and pending run survive container recreation',api('/api/me',a)['id']==people['alice'] and api('/api/runs/'+pending['id'],a)['status']=='awaiting_approval')
        bundle=work/'backups'/'complete-1'
        source.cmd('stop','beta-api')
        manifest=create(work,bundle)
        check('combined backup contains identity and both complete PG snapshots',len(manifest['workspaces']['alpha']['tables'])>=20 and len(manifest['workspaces']['beta']['tables'])>=20 and manifest['identity']['accounts']['count']==5)
        assert wait_for(lambda:observe(f'http://127.0.0.1:{port}/health'))
        check('source resumes and existing session survives backup',api('/api/me',a)['id']==people['alice'])
        check('backup leaves previously stopped dependency stopped','beta-api' not in source.cmd('ps','--services','--status','running').decode().split())
        # Real quiescence followed by injected export failure exercises restart finally.
        original=Stack.helper
        def fail(*unused,**kw):raise RuntimeError('injected export failure')
        Stack.helper=fail
        try:
            try:create(work,work/'backups'/'incomplete')
            except RuntimeError:pass
        finally:Stack.helper=original
        check('failed backup never gains completion marker',not (work/'backups/incomplete/manifest.json').exists())
        check('failed backup resumes source runtime',wait_for(lambda:observe(f'http://127.0.0.1:{port}/health')) and api('/api/me',a)['id']==people['alice'])
        replica=work/'replica';replica.mkdir();protect(replica)
        shutil.copytree(bundle,replica/'complete-1')
        check('separate local backup copy is readable',verify(replica/'complete-1')['files']==manifest['files'])
        corrupt=work/'corrupt';shutil.copytree(bundle,corrupt);(corrupt/'alpha.dump').write_bytes(b'corrupt')
        rejected=False
        try:restore(corrupt,args.project+'-bad',port+2)
        except ValueError:rejected=True
        check('corrupt bundle refused before target creation',rejected and not (ROOT/'work'/(args.project+'-bad')).exists())
        restored_work,validation=restore(replica/'complete-1',args.project+'-r',port+1)
        target=Stack(restored_work);report['projects'].append(target.project);report['restore_validation']=validation
        port+=1
        check('both business catalogs rows indexes sequences and checkpoints match',all(v['catalog_equal'] and v['indexes_valid'] and v['matched_tables']>=20 for v in validation['workspaces'].values()))
        api('/api/me',a,status=401)
        a=login('alice',password);reviewer=login('anna',password);admin=login('admina',password);b=login('bob',password)
        check('restored passwords and identities preserved but old sessions revoked',api('/api/me',a)['id']==people['alice'] and validation['identity']['old_sessions_revoked'])
        api('/api/runs/'+beta['id'],a,status=404)
        check('restored workspace isolation preserved',True)
        check('original pending approval readable after restore',api('/api/runs/'+pending['id'],a)['status']=='awaiting_approval')
        api('/api/runs/'+pending['id']+'/approval',reviewer,{'approved':True,'reason':'restored approval'},202)
        check('original checkpoint resumes to refund',wait_run(pending['id'],a)['status']=='refunded')
        api('/api/runs/'+pending['id']+'/approval',reviewer,{'approved':True,'reason':'duplicate'},409)
        before=sql(target,'alpha','SELECT * FROM rf_refunds ORDER BY order_id')
        new_run('RF-1001',a)
        check('repeat refund preserves original ledger',before==sql(target,'alpha','SELECT * FROM rf_refunds ORDER BY order_id'))
        target.cmd('stop')
        start_existing(target)
        check('restored deployment restarts without installation jobs',api('/api/me',a)['id']==people['alice'])
        check('fresh target refuses reuse',_refuses(lambda:restore(bundle,target.project,port)))
        # Host observer is separate from Docker, but cannot observe this host powered off.
        probe_file=(work/'probe.jsonl').open('w')
        probe=subprocess.Popen([sys.executable,str(ROOT/'scripts/local_probe.py'),'--url',f'http://127.0.0.1:{port}/health','--interval','1'],stdout=probe_file,stderr=subprocess.DEVNULL)
        assert wait_for(lambda:'local_entry_recovered' in (work/'probe.jsonl').read_text())
        started=time.monotonic();target.cmd('stop','gateway')
        check('host probe observes entry outage within 120 seconds',wait_for(lambda:'local_entry_unavailable' in (work/'probe.jsonl').read_text(),30))
        report['entry_outage_observed_seconds']=round(time.monotonic()-started,3)
        target.cmd('start','gateway')
        check('host probe observes entry recovery',wait_for(lambda:(work/'probe.jsonl').read_text().count('local_entry_recovered')==2,30))
        assert wait_for(lambda:not any(r.get('code')=='worker_offline' for r in api('/api/alerts',admin)['alerts']),30)
        event_offset=len(monitor_events(target))
        started=time.monotonic();target.cmd('stop','alpha-worker')
        check('readonly monitor observes worker offline',wait_for(lambda:any(r.get('code')=='worker_offline' and r.get('transition')=='firing' for r in monitor_events(target)[event_offset:]),40))
        report['worker_offline_observed_seconds']=round(time.monotonic()-started,3)
        queued=api('/api/runs',a,{'order_id':'RF-1002','ticket':'申请退款'},202)['id']
        sql(target,'alpha',"UPDATE rf_jobs SET available_at=now()-interval '2 minutes' WHERE run_id='"+queued+"'")
        check('readonly monitor observes aged queued task',wait_for(lambda:any(r.get('code')=='queue_delayed' and r.get('run_id')==queued and r.get('transition')=='firing' for r in monitor_events(target)[event_offset:]),20))
        target.cmd('start','alpha-worker')
        wait_run(queued,a)
        check('worker recovery clears offline incident',wait_for(lambda:any(r.get('code')=='worker_offline' and r.get('transition')=='resolved' for r in monitor_events(target)[event_offset:]),30))
        report['monitor_events']=monitor_events(target)
        probe.terminate();probe.wait(timeout=10);probe=None;probe_file.close();probe_file=None
        report['entry_events']=[json.loads(line) for line in (work/'probe.jsonl').read_text().splitlines()]
        logs=source.cmd('logs','--no-log-prefix').decode()
        check('source logs contain no generated secret values',all(v not in logs for v in secret_values) and password not in logs and a not in logs)
        report['passed']=True
    except BaseException as error:
        report['error_type']=type(error).__name__
        report['runtime_diagnostics'] = {}
        for item in (source, target):
            if item is None: continue
            try:
                ids = item.cmd('ps','-a','-q').decode().split()
                rows = json.loads(run(['docker','inspect',*ids])) if ids else []
                report['runtime_diagnostics'][item.project] = [
                    {'name':r['Name'], 'status':r['State']['Status'], 'exit_code':r['State']['ExitCode'],
                     'health':r['State'].get('Health',{}).get('Status')} for r in rows]
            except Exception as diagnostic_error:
                report['runtime_diagnostics'][item.project] = {'error_type':type(diagnostic_error).__name__}
        if isinstance(error,AssertionError):report['failure']=str(error)
        raise
    finally:
        if probe:probe.terminate();probe.wait(timeout=10)
        if probe_file:probe_file.close()
        source.cmd('stop')
        if target:target.cmd('stop')
        report['stopped_volumes_preserved']=True
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(report['checks'])}))


def _refuses(fn):
    try:fn()
    except (ValueError,FileExistsError):return True
    return False


if __name__=='__main__':main()
