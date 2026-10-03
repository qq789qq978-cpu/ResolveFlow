"""Real private-source HTTP synchronization and restored approval checks; fresh demo only."""
import argparse
import copy
import json
from pathlib import Path
import secrets
import sys
import time
import urllib.request
import urllib.error
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.local_stack import generate
from scripts.local_backup import Stack,create,restore,run


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True);p.add_argument('--image',required=True)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--port',type=int,default=8057)
    args=p.parse_args()
    if args.report.exists():raise ValueError('Existing report')
    work=generate(args.project,args.image,args.port,orders=True);s=Stack(work);target=None
    config=s.config
    for w in ('alpha','beta'):config['services'][w+'-source']['environment']['RF_SYNTHETIC_QA']='1'
    (work/'compose.json').write_text(json.dumps(config,indent=2))
    report={'passed':False,'checks':[],'projects':[args.project],'model_api_calls':0,'real_refunds':0}
    port=args.port
    def check(name,condition):
        report['checks'].append({'check':name,'passed':bool(condition)});print(json.dumps(report['checks'][-1]),flush=True)
        if not condition:raise AssertionError(name)
    def api(path,token=None,body=None,status=200):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=15) as r:code,data=r.status,json.load(r)
        except urllib.error.HTTPError as e:code,data=e.code,json.load(e)
        if code!=status:raise AssertionError(path+' expected '+str(status)+' got '+str(code))
        return data
    def login(name,pw):return api('/api/session',body={'username':name,'password':pw})['token']
    def sync(oid,token,status=200):return api('/api/order-sync',token,{'order_id':oid},status)
    def fixture(w='alpha'):return json.loads((work/(w+'-orders.json')).read_text())
    def write(data,w='alpha'):(work/(w+'-orders.json')).write_text(json.dumps(data))
    def revise(oid,**kw):
        data=fixture();row=data['orders'][oid];row.update(event_id=str(uuid.uuid4()),version=row['version']+1,**kw);write(data);return row
    def wait_run(rid,token):
        end=time.monotonic()+100
        while time.monotonic()<end:
            data=api('/api/runs/'+rid,token)
            if data['job']['status']=='done' and data['status'] not in ('queued','running','approval_queued','retrying'):return data
            if data['job']['status']=='failed':raise AssertionError('worker failed')
            time.sleep(.5)
        raise AssertionError('worker timeout')
    def new_run(oid,token):return wait_run(api('/api/runs',token,{'order_id':oid,'ticket':'申请退款'},202)['id'],token)
    def sql(stack,w,query):
        text='import os,json,psycopg\nwith psycopg.connect(os.environ["DATABASE_URL"]) as c:\n r=c.execute('+repr(query)+')\n data=r.fetchall() if r.description else []\nprint(json.dumps(data,default=str))'
        return json.loads(stack.cmd('run','--rm','--no-deps','-T',w+'-migrate','python','-c',text))
    try:
        s.cmd('run','--rm','--no-deps','-T','identity-init');s.cmd('up','-d','--wait','--wait-timeout','180')
        report['image_id']=run(['docker','image','inspect','--format','{{.Id}}',args.image]).decode().strip()
        manager=login('maintainer',(work/'bootstrap.txt').read_text());pw=secrets.token_urlsafe(24)
        for name,role,w in [('operatora','operator','alpha'),('reviewera','reviewer','alpha'),('admina','admin','alpha'),('adminb','admin','beta')]:
            api('/api/accounts',manager,{'username':name,'password':pw,'role':role,'workspace':w},201)
        admin=login('admina',pw);op=login('operatora',pw);reviewer=login('reviewera',pw);beta=login('adminb',pw)
        sync('RF-2001',None,401)
        for token in (manager,op,reviewer):sync('RF-2001',token,403)
        check('only workspace admin can synchronize',True)
        check('admin fetches real private HTTP synthetic source',sync('RF-2001',admin)['outcome']=='applied')
        check('same event retry returns duplicate',sync('RF-2001',admin)['outcome']=='duplicate')
        check('other workspace has no imported alpha order',not any(r['id']=='RF-2001' for r in api('/api/orders',beta)))
        sync('RF-2001',beta)
        original=fixture()['orders']['RF-2001']
        data=fixture();data['orders']['RF-2001']['workspace']='beta';write(data)
        check('wrong workspace in source rejected',sync('RF-2001',admin,403)['detail']=='source_scope_mismatch')
        data['orders']['RF-2001']=dict(original);data['orders']['RF-2001'].pop('used');write(data)
        check('missing source fact rejects whole import',sync('RF-2001',admin,502)['detail']=='invalid_source_payload')
        data['orders']['RF-2001']=dict(original);data['orders']['RF-2001']['amount']+=1;write(data)
        check('same event with conflicting content rejected',sync('RF-2001',admin,409)['detail']=='event_id_conflict')
        data['orders']['RF-2001']=dict(original);data['qa_delay_seconds']=4;write(data)
        started=time.monotonic();sync('RF-2001',admin,504);duration=time.monotonic()-started
        check('real source timeout bounded',duration<6);report['source_timeout_seconds']=round(duration,3)
        data.pop('qa_delay_seconds');write(data)
        sync('RF-9999',admin,404)
        check('timeout malformed and missing replies leave imported facts intact',next(r for r in api('/api/orders',op) if r['id']=='RF-2001')['amount']==original['amount'])
        auto=new_run('RF-2001',op)
        check('imported order reaches simulated refund',auto['status']=='refunded')
        before=sql(s,'alpha','SELECT * FROM rf_refunds ORDER BY order_id')
        duplicate=new_run('RF-2001',op)
        check('repeat imported refund preserves ledger',duplicate['status']=='already_refunded' and before==sql(s,'alpha','SELECT * FROM rf_refunds ORDER BY order_id'))
        for oid in ('RF-2002','RF-2003','RF-2004'):sync(oid,admin)
        rejected=new_run('RF-2002',op);shipping=new_run('RF-2003',op);pending=new_run('RF-2004',op)
        check('imported rejection manual review and approval routes',rejected['status']=='auto_rejected' and shipping['status']=='escalated' and pending['status']=='awaiting_approval')
        api('/api/runs/'+shipping['id']+'/review',reviewer,{'resolution':'synthetic shipping investigation'},200)
        check('manual review can close imported shipping case',api('/api/runs/'+shipping['id'],op)['status']=='closed')
        revise('RF-2003',status='delivered',days=1)
        check('shipping to delivered state update imported',sync('RF-2003',admin)['outcome']=='applied' and new_run('RF-2003',op)['status']=='refunded')
        old=fixture()['orders']['RF-2004'].copy();revise('RF-2004',days=12);sync('RF-2004',admin)
        data=fixture();latest=copy.deepcopy(data);data['orders']['RF-2004']={**old,'event_id':str(uuid.uuid4())};write(data)
        check('late older version cannot rewind current facts',sync('RF-2004',admin)['outcome']=='ignored_stale' and next(r for r in api('/api/orders',op) if r['id']=='RF-2004')['days']==12)
        write(latest)
        api('/api/runs/'+pending['id']+'/approval',reviewer,{'approved':True,'reason':'old approval must recheck facts'},202)
        changed=wait_run(pending['id'],op)
        check('changed facts invalidate old approval at refund transaction',changed['status']=='escalated' and not sql(s,'alpha',"SELECT * FROM rf_refunds WHERE order_id='RF-2004'"))
        # Two additional pending cases exercise unchanged approval denial and restored approval.
        data=fixture()
        for oid in ('RF-2104','RF-2105'):
            data['orders'][oid]={**old,'id':oid,'event_id':str(uuid.uuid4())}
        write(data)
        sync('RF-2104',admin);sync('RF-2105',admin)
        denied=new_run('RF-2104',op);recover=new_run('RF-2105',op)
        api('/api/runs/'+denied['id']+'/approval',reviewer,{'approved':False,'reason':'synthetic denial'},202)
        check('unchanged imported order respects human denial',wait_run(denied['id'],op)['status']=='rejected')
        # Test database privileges directly using the runtime's actual credentials.
        source='''import os,json,psycopg
out=[]
for dsn,query in [(os.environ['DATABASE_URL'],"UPDATE rf_orders SET days=days WHERE id='RF-2001'"),
 (os.environ['RF_ORDER_SYNC_DATABASE_URL'],'DELETE FROM rf_refunds'),
 (os.environ['RF_ORDER_SYNC_DATABASE_URL'],'UPDATE rf_approvals SET approved=approved')]:
 try:
  with psycopg.connect(dsn) as c:c.execute(query)
  out.append(False)
 except psycopg.errors.InsufficientPrivilege:out.append(True)
print(json.dumps(out))'''
        rights=json.loads(s.cmd('exec','-T','alpha-api','python','secret_entrypoint.py','python','-c',source))
        check('business role cannot alter orders and importer cannot refund or approve',rights==[True,True,True])
        worker=json.loads((work/'secrets/alpha-worker.json').read_text())
        check('Worker has neither source key nor importer credential',not ({'RF_ORDER_SOURCE_KEY','RF_ORDER_SYNC_DATABASE_URL'}&set(worker)))
        history=api('/api/order-sync',admin)
        check('sync journal records outcomes and personal actor',len(history)>=8 and all(r['actor'].startswith('account:') and r['workspace']=='alpha' for r in history))
        bundle=work/'backups'/'orders'
        manifest=create(work,bundle)
        check('joint backup includes journal and synthetic source fixture',manifest['orders'] and 'synthetic-sources.json' in manifest['files'])
        restored,validation=restore(bundle,args.project+'-r',port+1);target=Stack(restored);port+=1
        report['projects'].append(target.project);report['restore']=validation
        admin=login('admina',pw);op=login('operatora',pw);reviewer=login('reviewera',pw)
        check('restored source retry remains duplicate',sync('RF-2105',admin)['outcome']=='duplicate')
        api('/api/runs/'+recover['id']+'/approval',reviewer,{'approved':True,'reason':'resume original restored order'},202)
        check('original imported approval checkpoint resumes after restore',wait_run(recover['id'],op)['status']=='refunded')
        api('/api/runs/'+recover['id']+'/approval',reviewer,{'approved':True,'reason':'duplicate'},409)
        check('duplicate restored approval refused',True)
        report['runs']={'auto':auto['id'],'rejected':rejected['id'],'manual':shipping['id'],'changed_approval':pending['id'],'denied':denied['id'],'restored':recover['id']}
        report['passed']=True
    except BaseException as error:
        report['error_type']=type(error).__name__
        if isinstance(error,AssertionError):report['failure']=str(error)
        raise
    finally:
        s.cmd('stop')
        if target:target.cmd('stop')
        report['stopped_volumes_preserved']=True
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(report['checks'])}))


if __name__=='__main__':main()
