"""Real-calendar local trial. Private state/credentials stay under ignored work/.

No simulated dates, volume deletion, in-place restore or external notifications.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.local_backup import Stack, create, verify, runtime_image, run
from scripts.local_stack import generate, protect, private_file
from scripts.local_probe import observe, NoRedirect

HK=timezone(timedelta(hours=8))
SERVICES=('gateway','alpha-api','beta-api','alpha-worker','alpha-extra-worker','beta-worker',
          'alpha-monitor','beta-monitor','alpha-source','beta-source','alpha-db','beta-db')

def now():return datetime.now(timezone.utc)
def stamp():return now().strftime('%Y%m%dT%H%M%S%fZ')
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)

@contextmanager
def lock(work):
    # Kernel lock is released after process death; never delete another lock file.
    with (Path(work)/'trial.lock').open('a+b') as f:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f,fcntl.LOCK_UN)

def load(work):
    work=Path(work).resolve()
    if work.parent != (ROOT/'work').resolve():raise ValueError('Trial must be directly under project work/')
    state=read(work/'trial-state.json');s=Stack(work)
    if (state['format']!=1 or state['project']!=s.project or not s.project.startswith('resolveflow-accounts-trial')
        or not s.meta.get('orders') or not s.meta.get('capacity')):raise ValueError('Not a registered isolated trial')
    return work,state,s

class API:
    def __init__(self,port):
        self.base='http://127.0.0.1:'+str(int(port))
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def call(self,path,token=None,body=None,status=200):
        h={'Content-Type':'application/json'}
        if token:h['Authorization']='Bearer '+token
        req=urllib.request.Request(self.base+path,headers=h,data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(req,timeout=20) as r:code,data=r.status,json.load(r)
        except urllib.error.HTTPError as e:code,data=e.code,json.load(e)
        if code!=status:raise AssertionError('HTTP status '+str(code)+' expected '+str(status))
        return data
    def login(self,name,password):return self.call('/api/session',body={'username':name,'password':password})['token']
    def wait(self,rid,token):
        end=time.monotonic()+200
        while time.monotonic()<end:
            d=self.call('/api/runs/'+rid,token)
            if d['job']['status']=='failed':raise AssertionError('Worker job failed')
            if d['job']['status']=='done':return d
            time.sleep(.5)
        raise TimeoutError('Worker completion deadline')

def sql(stack,w,query,params=()):
    source='import os,json,psycopg\nwith psycopg.connect(os.environ["DATABASE_URL"]) as c:\n r=c.execute('+repr(query)+','+repr(params)+')\n rows=r.fetchall() if r.description else []\nprint(json.dumps(rows,default=str))'
    return json.loads(stack.cmd('run','--rm','--no-deps','-T',w+'-migrate','python','-c',source))

def healthy(stack):
    ids=stack.cmd('ps','-q',*SERVICES).decode().split()
    rows=json.loads(run(['docker','inspect',*ids])) if ids else []
    return len(rows)==12 and all(c['State'].get('Health',{}).get('Status')=='healthy' for c in rows)

def wait_healthy(stack):
    end=time.monotonic()+120
    while time.monotonic()<end:
        if healthy(stack):return
        time.sleep(2)
    raise TimeoutError('Services not healthy')

def copy_bundle(bundle,dest):
    bundle=Path(bundle).resolve();dest=Path(dest).resolve()
    if dest==bundle or bundle in dest.parents:raise ValueError('Independent destination required')
    manifest=verify(bundle)
    if any(p.is_symlink() for p in bundle.iterdir()):raise ValueError('No symlink backup copies')
    dest.parent.mkdir(parents=True,exist_ok=True);protect(dest.parent)
    shutil.copytree(bundle,dest);protect(dest)
    assert verify(dest)==manifest
    return hashlib.sha256((dest/'manifest.json').read_bytes()).hexdigest()

def backup(work,state,s):
    tag=stamp();bundle=work/'backups'/tag;mirror=Path(state['mirror'])/tag
    begin=now();manifest=create(work,bundle);digest=copy_bundle(bundle,mirror);wait_healthy(s)
    item={'id':tag,'created_at':manifest['created_at'],'finished_at':now().isoformat(),
          'day':begin.astimezone(HK).date().isoformat(),'bundle':str(bundle),'copy':str(mirror),
          'manifest_sha256':digest,'image_id':manifest['image_id'],'passed':True,'separate_directory':True,
          'offsite':False,'maintenance_seconds':(now()-begin).total_seconds(),
          'accounts':manifest['identity']['accounts']['count'],
          'admissions':manifest['identity']['capacity_admissions']['count']}
    state['backups'].append(item);write(work/'trial-state.json',state)
    write(Path(state['evidence'])/'backups'/f'{tag}.json',item)
    return item

def observation_summary(rows):
    gaps=[];unavailable=[]
    for prev,cur in zip(rows,rows[1:]):
        seconds=(datetime.fromisoformat(cur['time'])-datetime.fromisoformat(prev['time'])).total_seconds()
        if seconds>150:gaps.append({'from':prev['time'],'to':cur['time'],'seconds':seconds,'meaning':'unobserved_not_proven_downtime'})
    for row in rows:
        if not row['available']:unavailable.append(row['time'])
    return {'samples':len(rows),'first':rows[0]['time'] if rows else None,'last':rows[-1]['time'] if rows else None,
            'unavailable_samples':unavailable,'unobserved_gaps':gaps,'scope':'same_host_samples_not_24x7_or_external_SLA'}

def summarize_observer(work,state):
    rows=[]
    for p in sorted((work/'observations').glob('*.jsonl')):
        for line in p.read_text(encoding='utf-8').splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass # Partial final append is retried at the next tick.
    result=observation_summary(rows);write(Path(state['evidence'])/'observation-summary.json',result)
    return result

def ensure_observer(work,state):
    path=work/'observer.json'
    if path.exists():
        old=read(path)
        if time.time()-old.get('heartbeat',0)<150:return
    token=secrets.token_hex(16)
    write(path,{'token':token,'heartbeat':0,'requested_at':now().isoformat()})
    kwargs={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,'cwd':str(ROOT)}
    if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP
    else:kwargs['start_new_session']=True
    subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'observe','--work',str(work),'--lease',token],**kwargs)

def observer(work,lease):
    work,state,s=load(work);path=work/'observer.json';out=work/'observations';out.mkdir(exist_ok=True)
    while not (work/'observer-stop').exists():
        if read(path)['token']!=lease:return
        d={'time':now().isoformat(),'available':observe('http://127.0.0.1:'+str(s.meta['port'])+'/health')}
        with (out/(now().astimezone(HK).date().isoformat()+'.jsonl')).open('a',encoding='utf-8') as f:f.write(json.dumps(d)+'\n')
        if read(path)['token']!=lease:return
        write(path,{'token':lease,'heartbeat':time.time(),'time':d['time'],'pid':os.getpid()})
        for _ in range(60):
            if (work/'observer-stop').exists():return
            time.sleep(1)

def assess(state,days,current):
    today=current.astimezone(HK).date();valid={d['day'] for d in days if d.get('passed')}
    first=datetime.fromisoformat(state['started_at']).astimezone(HK).date()
    end=max(today,first+timedelta(days=6))
    required=[(end-timedelta(days=n)).isoformat() for n in reversed(range(7))]
    elapsed=(current-datetime.fromisoformat(state['started_at'])).total_seconds()
    calendar=all(day in valid for day in required) and elapsed>=6*86400
    backups=state['backups'];times=[datetime.fromisoformat(b['created_at']) for b in backups]
    gaps=[{'from':a.isoformat(),'to':b.isoformat(),'hours':(b-a).total_seconds()/3600} for a,b in zip(times,times[1:]) if (b-a).total_seconds()>86400]
    latest_age=(current-times[-1]).total_seconds()/3600 if times else None
    # Missing days/backup gaps must be disclosed. Only a fresh continuous window can qualify.
    window_start=datetime.fromisoformat(required[0]+'T00:00:00+08:00')
    window_gaps=[g for g in gaps if datetime.fromisoformat(g['to'])>=window_start]
    backups_ok=len(backups)>=7 and latest_age is not None and 0<=latest_age<=24 and not window_gaps
    drill=state.get('final_drill',{})
    drill_ok=drill.get('passed',False) and drill.get('backup_id')==(backups[-1]['id'] if backups else None)
    return {'passed':calendar and backups_ok and drill_ok,'observed_successful_dates':sorted(valid),
            'required_dates':required,'successful_days':len(valid),'calendar_requirement_met':calendar,
            'backup_requirement_met':backups_ok,'successful_backup_count':len(backups),
            'backup_gaps_over_24h':gaps,'latest_backup_age_hours':latest_age,
            'final_drill_current':drill_ok,'next':'final_drill' if calendar and backups_ok and not drill_ok else 'continue_real_daily_observation'}

def status(work,state=None):
    work,stored,s=load(work);state=state or stored
    evidence=Path(state['evidence']);days=[read(p) for p in sorted((evidence/'days').glob('*.json'))]
    result=dict(state['completion']) if state.get('completed_at') and state.get('completion',{}).get('passed') else assess(state,days,now())
    result.update(project=s.project,checked_at=now().isoformat(),completed_at=state.get('completed_at'),
        url='http://127.0.0.1:'+str(s.meta['port']),model_api_calls=0,real_refunds=0,
        public_access=False,trial_started_at=state['started_at'])
    write(evidence/'status.json',result);return result

def daily(work,state,s):
    today=now().astimezone(HK).date().isoformat();evidence=Path(state['evidence'])
    finished=evidence/'days'/f'{today}.json'
    if finished.exists() and read(finished).get('passed'):return False
    p=work/'day-progress'/f'{today}.json'
    d=read(p) if p.exists() else {'day':today,'started_at':now().isoformat(),'passed':False,'runs':{}}
    api=API(s.meta['port']);creds=read(work/'trial-accounts.json');tokens={w:api.login('admin'+w[0],creds['password']) for w in ('alpha','beta')}
    assert api.call('/api/runs/'+state['pending_run'],tokens['alpha'])['status']=='awaiting_approval'
    offset=(now().astimezone(HK).date()-datetime.fromisoformat(state['started_at']).astimezone(HK).date()).days
    if not 0<=offset<1000:raise ValueError('Trial date outside supported order range')
    oid='RF-'+str(7000+offset)
    for w in ('alpha','beta'):
        fixture=work/(w+'-orders.json');data=read(fixture)
        item={'event_id':str(uuid.uuid5(uuid.NAMESPACE_URL,state['project']+w+today)),
              'workspace':w,'source':'synthetic-v1','synthetic':True,'version':1,'id':oid,
              'owner':'demo','currency':'CNY','amount':1000,'days':2,'used':False,'status':'delivered'}
        if oid in data['orders'] and data['orders'][oid]!=item:raise ValueError('Existing daily order conflicts')
        data['orders'][oid]=item
        # Preserve the bind-mounted file inode.
        fixture.write_text(json.dumps(data),encoding='utf-8')
        api.call('/api/order-sync',tokens[w],{'order_id':oid})
        for kind,expected in (('auto','refunded'),('duplicate','already_refunded')):
            key=w+'-'+kind
            if key not in d['runs']:
                d['runs'][key]={'submission_requested_at':now().isoformat()};write(p,d)
                r=api.call('/api/runs',tokens[w],{'order_id':oid,'ticket':'申请退款'},202)
                d['runs'][key]['id']=r['id'];write(p,d)
            entry=d['runs'][key]
            if 'id' not in entry:raise RuntimeError('Unknown prior submission: reconcile before retry')
            r=api.wait(entry['id'],tokens[w]);assert r['status']==expected
            entry['status']=r['status'];write(p,d)
        assert sql(s,w,'SELECT count(*),sum(amount) FROM rf_refunds WHERE order_id=%s',(oid,))==[[1,1000]]
        foreign='beta' if w=='alpha' else 'alpha'
        api.call('/api/runs/'+d['runs'][w+'-auto']['id'],tokens[foreign],status=404)
        alerts=api.call('/api/alerts',tokens[w]);assert alerts['available'] and not alerts['alerts']
        assert alerts['workers_online']>=({'alpha':2,'beta':1}[w])
        d.setdefault('alerts',{})[w]=alerts
    item=backup(work,state,s)
    d.update(passed=True,finished_at=now().isoformat(),order_id=oid,backup_id=item['id'],
             checks=['two_workspace_auto_refunds','duplicate_ledger_unchanged','cross_workspace_denied',
                     'three_workers_observed','no_active_alerts','joint_backup_and_independent_copy'],
             model_api_calls=0,real_refunds=0)
    write(p,d);write(finished,d);return True

def tick(work):
    work,state,s=load(work)
    with lock(work):
        attempt={'started_at':now().isoformat(),'passed':False}
        try:
            if status(work,state)['passed']:return status(work,state)
            ensure_observer(work,state)
            assert runtime_image(s)==state['image_id'];wait_healthy(s)
            for p in (work/'day-progress').glob('*.json'):
                if any('id' not in r for r in read(p)['runs'].values()):
                    raise RuntimeError('Unreconciled prior submission; manual investigation required')
            for b in state['backups']:
                original=verify(Path(b['bundle']));replica=verify(Path(b['copy']))
                assert original==replica
                assert hashlib.sha256((Path(b['copy'])/'manifest.json').read_bytes()).hexdigest()==b['manifest_sha256']
            for w in ('alpha','beta'):
                if sql(s,w,"SELECT count(*) FROM rf_jobs WHERE status='failed'")[0][0]:
                    raise RuntimeError('Unresolved failed source jobs require investigation')
            fresh=daily(work,state,s)
            if not fresh and (not state['backups'] or (now()-datetime.fromisoformat(state['backups'][-1]['created_at'])).total_seconds()>=20*3600):backup(work,state,s)
            summarize_observer(work,state);attempt.update(passed=True,new_day=fresh)
        except Exception as e:
            attempt['error_type']=type(e).__name__
            raise
        finally:
            attempt['finished_at']=now().isoformat();write(Path(state['evidence'])/'ticks'/f'{stamp()}.json',attempt)
        return status(work,state)

def resume(work):
    work,state,s=load(work)
    with lock(work):
        if runtime_image(s)!=state['image_id']:raise ValueError('Trial runtime image changed')
        # Start existing containers only. Never replay initialization or migration.
        for services in (('alpha-db','beta-db'),('alpha-source','beta-source','gateway','alpha-api','beta-api'),
                         ('alpha-worker','alpha-extra-worker','beta-worker','alpha-monitor','beta-monitor')):
            s.cmd('start','--wait','--wait-timeout','180',*services)
        ensure_observer(work,state)
    return tick(work)

def initialize(project,image,port,evidence,mirror):
    if not project.startswith('resolveflow-accounts-trial'):raise ValueError('Dedicated trial project required')
    evidence=Path(evidence).resolve();mirror=Path(mirror).resolve()
    if evidence.exists() and (evidence/'status.json').exists():raise ValueError('Existing trial evidence')
    if mirror.exists():raise ValueError('New independent backup directory required')
    work=generate(project,image,port,orders=True,capacity=True);s=Stack(work)
    state={'format':1,'project':project,'started_at':now().isoformat(),'evidence':str(evidence),
           'mirror':str(mirror),'backups':[],'image_id':run(['docker','image','inspect','--format','{{.Id}}',image]).decode().strip()}
    write(work/'trial-state.json',state);pw=secrets.token_urlsafe(32)
    private_file(work/'trial-accounts.json',json.dumps({'password':pw}))
    s.cmd('run','--rm','--no-deps','-T','identity-init');s.cmd('up','-d','--wait','--wait-timeout','180')
    api=API(port);manager=api.login('maintainer',(work/'bootstrap.txt').read_text())
    for name,role,w in [('operatora','operator','alpha'),('reviewera','reviewer','alpha'),('admina','admin','alpha'),('adminb','admin','beta')]:
        api.call('/api/accounts',manager,{'username':name,'password':pw,'role':role,'workspace':w},201)
    admin=api.login('admina',pw)
    r=api.call('/api/runs',admin,{'order_id':'RF-1004','ticket':'申请退款'},202)
    state['pending_run']=r['id'];write(work/'trial-state.json',state)
    assert api.wait(r['id'],admin)['status']=='awaiting_approval'
    ensure_observer(work,state)
    write(evidence/'initialization.json',{'passed':True,'project':project,'started_at':state['started_at'],
          'pending_run':r['id'],'image_id':state['image_id'],'url':api.base,'public_access':False})
    return tick(work)

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    q=sub.add_parser('init');q.add_argument('--project',required=True);q.add_argument('--image',required=True)
    q.add_argument('--port',type=int,default=8057);q.add_argument('--evidence',type=Path,required=True);q.add_argument('--mirror',type=Path,required=True)
    for name in ('tick','status','observe','resume'):
        q=sub.add_parser(name);q.add_argument('--work',type=Path,required=True)
        if name=='observe':q.add_argument('--lease',required=True)
    a=p.parse_args()
    try:
        if a.action=='init':result=initialize(a.project,a.image,a.port,a.evidence,a.mirror)
        elif a.action=='tick':result=tick(a.work)
        elif a.action=='status':result=status(a.work)
        elif a.action=='resume':result=resume(a.work)
        else:observer(a.work,a.lease);return 0
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as e:
        print(json.dumps({'passed':False,'error_type':type(e).__name__}));return 1

if __name__=='__main__':raise SystemExit(main())
