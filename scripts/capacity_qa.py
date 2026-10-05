"""Capacity acceptance on NEW private demo volumes. Never reuse a deployment."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import platform
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.local_stack import generate
from scripts.local_backup import Stack,create,restore,run

SEED='''import os,json,uuid,copy,hashlib
import psycopg
from psycopg.rows import dict_row
from policy_releases import prepare,activate
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as c:
 for n in range(1000):
  oid='RF-'+str(3000+n)
  c.execute('INSERT INTO rf_orders VALUES (%s,%s,%s,%s,%s,%s)',(oid,'demo',1000,2,False,'delivered'))
  rid=str(uuid.uuid4());status='awaiting_approval' if n%2 else 'refunded'
  c.execute("INSERT INTO rf_runs(id,ticket,order_id,mode,status,created_at) VALUES (%s,'synthetic historical fixture',%s,'demo',%s,now()-interval '2 days')",(rid,oid,status))
 p=prepare();p['id']='capacity-fixture-v1'
 # Separate distractor document keeps executable policy clauses unchanged.
 d=copy.deepcopy(p['documents'][0]);d.update(id='capacity-filler',source='capacity-filler.md',title='Warehouse bin identifiers',body='Synthetic warehouse bins.',sha256=hashlib.sha256(b'capacity-filler').hexdigest())
 d['governance']['document_sha256']=d['sha256'];p['documents'].append(d)
 for n in range(100):p['chunks'].append(dict(chunk_id='capacity-filler-'+str(n),document_id=d['id'],position=n,text='Synthetic warehouse bin identifier '+str(n),line_start=n+1,line_end=n+1))
 generation=c.execute('SELECT generation FROM rf_policy_head').fetchone()['generation']
 activate(c,payload=p,expected_generation=generation,actor='capacity-qa',reason='Synthetic capacity corpus',mode='demo')
 print(json.dumps({t:c.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('rf_orders','rf_runs','rf_knowledge_chunks')}))
'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True);p.add_argument('--image',required=True)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--port',type=int,default=8056)
    a=p.parse_args()
    progress=a.report.with_suffix('.progress.json')
    if a.report.exists() or progress.exists():raise ValueError('Use new evidence path')
    work=generate(a.project,a.image,a.port,orders=True,capacity=True);s=Stack(work);target=None
    report={'passed':False,'checks':[],'samples':{'submit':[],'read':[],'auto':[]},'polls':[],
            'projects':[a.project],'model_api_calls':0,'real_refunds':0,'workers':3,'slot_allocation':{'alpha':2,'beta':1},
            'machine':{'os':platform.platform(),'processor':platform.processor()},'started_at':datetime.now(timezone.utc).isoformat()}
    port=a.port;stop=threading.Event();monitor=None;tokens={};accepted=[]
    report['accepted']=accepted
    def save_progress():
        progress.parent.mkdir(parents=True,exist_ok=True)
        temporary=progress.with_suffix('.tmp')
        temporary.write_text(json.dumps({**report,'partial':True},indent=2),encoding='utf-8')
        temporary.replace(progress)
    def check(name,ok):
        report['checks'].append({'check':name,'passed':bool(ok)});print(json.dumps(report['checks'][-1]),flush=True)
        save_progress()
        if not ok:raise AssertionError(name)
    def api(path,token=None,body=None,expected=200,kind=None,headers=None):
        h={'Content-Type':'application/json',**(headers or {})}
        if token:h['Authorization']='Bearer '+token
        req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,headers=h,data=json.dumps(body).encode() if body is not None else None)
        start=time.monotonic()
        try:
            with urllib.request.urlopen(req,timeout=20) as r:code,data=r.status,json.load(r)
        except urllib.error.HTTPError as e:code,data=e.code,json.load(e)
        except Exception:
            if kind:report['samples'][kind].append({'seconds':time.monotonic()-start,'status':'transport_error'})
            raise
        if kind:report['samples'][kind].append({'seconds':time.monotonic()-start,'status':code})
        if code!=expected:raise AssertionError(path+' expected '+str(expected)+' got '+str(code))
        return data
    def login(name,pw):return api('/api/session',body={'username':name,'password':pw})['token']
    def sql(stack,w,query):
        source='import os,json,psycopg\nwith psycopg.connect(os.environ["DATABASE_URL"]) as c:\n r=c.execute('+repr(query)+')\n data=r.fetchall() if r.description else []\nprint(json.dumps(data,default=str))'
        return json.loads(stack.cmd('run','--rm','--no-deps','-T',w+'-migrate','python','-c',source))
    def poll():
        while not stop.is_set():
            sample={'time':time.time(),'workspaces':{}}
            try:
                for w in ('alpha','beta'):
                    query="SELECT (SELECT count(*) FROM rf_runs WHERE status='running'),(SELECT count(*) FROM rf_runs WHERE status='queued'),(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()),(SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=56300 AND granted)"
                    row=sql(s,w,query)[0];sample['workspaces'][w]=dict(zip(('running','queued','connections','slots'),row))
                report['polls'].append(sample)
            except Exception as e:report.setdefault('monitor_errors',[]).append(type(e).__name__)
            stop.wait(.2)
    def submit(n,w='alpha',order=None):
        started=time.monotonic()
        result=api('/api/runs',tokens[w],{'order_id':order or 'RF-'+str(3000+n),'ticket':'申请退款'},202,'submit')
        item={'id':result['id'],'workspace':w,'submitted_monotonic':started};accepted.append(item);return item
    def wait(item):
        end=time.monotonic()+180
        while time.monotonic()<end:
            data=api('/api/runs/'+item['id'],tokens[item['workspace']])
            if data['job']['status']=='failed':raise AssertionError('worker_failed:'+item['id'])
            if data['job']['status']=='done':
                item['terminal']=data['status'];item['seconds']=time.monotonic()-item['submitted_monotonic'];return data
            time.sleep(.25)
        raise AssertionError('worker_timeout:'+item['id'])
    try:
        s.cmd('run','--rm','--no-deps','-T','identity-init');s.cmd('up','-d','--wait','--wait-timeout','180')
        report['docker']=json.loads(run(['docker','info','--format','{{json .}}']))
        report['docker']={k:report['docker'][k] for k in ('NCPU','MemTotal','ServerVersion','OperatingSystem')}
        report['image_id']=run(['docker','image','inspect','--format','{{.Id}}',a.image]).decode().strip()
        workers=['alpha-worker','alpha-extra-worker','beta-worker'];s.cmd('stop',*workers)
        for w in ('alpha','beta'):
            report.setdefault('fixtures',{})[w]=json.loads(s.cmd('run','--rm','--no-deps','-T',w+'-migrate','python','-c',SEED))
        check('each workspace has >=1000 orders/history and >=100 active chunks',all(v['rf_orders']>=1000 and v['rf_runs']>=1000 and v['rf_knowledge_chunks']>=100 for v in report['fixtures'].values()))
        manager=login('maintainer',(work/'bootstrap.txt').read_text());pw=secrets.token_urlsafe(24)
        for name,role,w in [('operatora','operator','alpha'),('reviewera','reviewer','alpha'),('admina','admin','alpha'),('adminb','admin','beta')]:
            api('/api/accounts',manager,{'username':name,'password':pw,'role':role,'workspace':w},201)
        tokens.update(alpha=login('admina',pw),beta=login('adminb',pw));op=login('operatora',pw);reviewer=login('reviewera',pw)
        api('/api/accounts',manager,{'username':'sixth','password':pw,'role':'operator','workspace':'beta'},409)
        check('five active accounts including maintenance enforced',True)
        api('/api/runs',reviewer,{'order_id':'RF-3000','ticket':'申请退款'},403)
        api('/api/metrics',op,expected=403);api('/api/runs?workspace=beta',op,expected=403)
        check('role and workspace selector denied under capacity mode',True)
        monitor=threading.Thread(target=poll,daemon=True);monitor.start()
        with ThreadPoolExecutor(max_workers=10) as pool:burst=list(pool.map(lambda n:submit(n,'beta' if n>=7 else 'alpha'),range(10)))
        check('ten requests accepted and persist queued before workers',all(api('/api/runs/'+r['id'],tokens[r['workspace']])['status']=='queued' for r in burst))
        s.cmd('start',*workers)
        for item in burst:wait(item)
        check('burst all accepted jobs reach terminal',all(r['terminal']=='refunded' for r in burst))
        for n in range(10,40):
            item=submit(n);wait(item);report['samples']['auto'].append({'seconds':item['seconds'],'status':item['terminal']})
            if n%10==9:
                print(json.dumps({'automatic_samples':len(report['samples']['auto'])}),flush=True)
                save_progress()
        check('30 automatic flows complete with slot available',len(report['samples']['auto'])==30 and all(r['status']=='refunded' for r in report['samples']['auto']))
        # Batch at most 10/11 seconds, below configured rate limit; excess tests are separate.
        for start in range(40,98,10):
            batch=[submit(n,'beta' if n%3==0 else 'alpha') for n in range(start,min(start+10,98))]
            for item in batch:wait(item)
            print(json.dumps({'accepted_so_far':len(accepted)}),flush=True)
            save_progress()
            time.sleep(1)
        pending=submit(98,order='RF-1004');wait(pending)
        duplicate=submit(99,order='RF-3010');wait(duplicate)
        check('100th ticket accepted and duplicate new ticket consumes quota',len(accepted)==100 and pending['terminal']=='awaiting_approval' and duplicate['terminal']=='already_refunded')
        api('/api/runs',op,{'order_id':'RF-3101','ticket':'申请退款'},429)
        api('/api/runs',tokens['beta'],{'order_id':'RF-3101','ticket':'申请退款'},429)
        check('101st refused across both workspaces',True)
        api('/api/runs/'+pending['id']+'/approval',op,{'approved':True,'reason':'unauthorized'},403)
        before=s.helper('identity-init','identity-export')['snapshot']['capacity_admissions']
        api('/api/runs/'+pending['id']+'/approval',reviewer,{'approved':True,'reason':'synthetic capacity approval'},202);wait(pending)
        after=s.helper('identity-init','identity-export')['snapshot']['capacity_admissions']
        check('approval resumes at full quota without new admission',before==after and pending['terminal']=='refunded')
        api('/api/runs/'+pending['id'],tokens['beta'],expected=404)
        check('foreign workspace cannot read run',True)
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda n:api('/api/runs' if n%2 else '/api/runs/'+accepted[n%len(accepted)]['id'],tokens[accepted[n%len(accepted)]['workspace']],kind='read'),range(100)))
        # Actual private backend submission still calls shared admission.
        source='import httpx,json; r=httpx.post("http://alpha-api:8000/api/runs",headers={"Authorization":"Bearer "+'+repr(tokens['alpha'])+'},json={"order_id":"RF-3101","ticket":"synthetic"}); print(r.status_code)'
        direct=int(s.cmd('exec','-T','gateway','python','-c',source))
        check('direct private backend cannot bypass full deployment quota',direct==429)
        stop.set();monitor.join(30)
        check('runtime observed all three slots with queued work and never over allocation',bool(report['polls']) and any(sum(x['slots'] for x in p['workspaces'].values())==3 and sum(x['queued'] for x in p['workspaces'].values())>=7 for p in report['polls']) and all(p['workspaces']['alpha']['slots']<=2 and p['workspaces']['beta']['slots']<=1 for p in report['polls']))
        report['accepted']=accepted
        report['metrics']={}
        for kind,limit in [('submit',2),('read',1),('auto',30)]:
            samples=report['samples'][kind];times=sorted(r['seconds'] for r in samples);p95=times[math.ceil(.95*len(times))-1]
            report['metrics'][kind]={'count':len(times),'p95_seconds':p95,'max_seconds':max(times),'failures':sum(r['status'] not in (200,202,'refunded') for r in samples)}
        for kind,limit in [('submit',2),('read',1),('auto',30)]:
            check(kind+' p95 within target with zero failure',report['metrics'][kind]['p95_seconds']<=limit and report['metrics'][kind]['failures']==0)
        report['connection_peaks']={w:max(p['workspaces'][w]['connections'] for p in report['polls']) for w in ('alpha','beta')}
        report['attempts']={w:sql(s,w,"SELECT r.id,r.created_at,a.created_at,a.elapsed_ms,a.success,a.error_type FROM rf_runs r JOIN rf_job_attempts a ON a.run_id=r.id ORDER BY r.created_at") for w in ('alpha','beta')}
        check('all 101 execution attempts succeeded including approval',sum(len(v) for v in report['attempts'].values())==101 and all(r[4] for v in report['attempts'].values() for r in v))
        # Login rate test is after all required logins, not a password leak probe.
        for n in range(11):
            try:api('/api/session',body={'username':'missing'+str(n),'password':'invalid'},expected=401,headers={'X-Forwarded-For':str(n)})
            except AssertionError as e:
                if 'got 429' not in str(e):raise
                report['login_limited_at']=n+1;break
        check('HTTP login limit rejects unknown users despite spoofed forwarding',report.get('login_limited_at',100)<=11)
        bundle=work/'backups'/'capacity';manifest=create(work,bundle)
        check('joint backup captures global quota and rate tables',manifest['capacity'] and manifest['identity']['capacity_admissions']['count']==100)
        restored,result=restore(bundle,a.project+'-r',port+1);target=Stack(restored);port+=1
        report['projects'].append(target.project);report['restore']=result
        # Restore intentionally preserves active throttles as well as quota.
        delay=61-time.time()%60;time.sleep(min(60,delay))
        token=login('admina',pw)
        api('/api/runs',token,{'order_id':'RF-3101','ticket':'申请退款'},429)
        check('fresh-volume restore retains daily quota and three-worker allocation',target.meta['capacity'] and 'alpha-extra-worker' in target.config['services'])
        report['passed']=True
    except BaseException as error:
        report['error_type']=type(error).__name__
        if isinstance(error,AssertionError):report['failure']=str(error)
        raise
    finally:
        stop.set()
        if monitor:monitor.join(30)
        s.cmd('stop')
        if target:target.cmd('stop')
        report['stopped_volumes_preserved']=True;report['finished_at']=datetime.now(timezone.utc).isoformat()
        a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(report['checks']),'metrics':report['metrics']}))

if __name__=='__main__':main()
