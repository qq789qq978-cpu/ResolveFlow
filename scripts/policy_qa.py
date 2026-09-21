"""Step 2.4: isolated HTTP/Worker policy lifecycle acceptance, demo only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from worker_crash_qa import main_fingerprints

PROJECT = 'resolveflow-qa-step24'
URL = 'http://127.0.0.1:8013'


def docker(*args):
    p = subprocess.run(['docker', *args],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=150)
    if p.returncode: raise RuntimeError('Isolated QA Docker operation failed: '+p.stderr[-1000:])
    return p.stdout.strip()


def request(path, data=None, role='operator'):
    req = urllib.request.Request(URL+'/api'+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json','X-API-Key':'qa-step24-'+role})
    with urllib.request.urlopen(req,timeout=10) as response: return json.load(response)


def wait(rid):
    deadline = time.monotonic()+60
    while time.monotonic()<deadline:
        row=request('/runs/'+rid)
        if row['status'] not in {'queued','running','retrying','approval_queued'}: return row
        time.sleep(.3)
    raise TimeoutError('QA Worker did not complete')


def set_policy(change):
    # Hard-coded isolated project and public fixture guard, never use main credentials.
    code = """
import os,json
from datetime import datetime,timezone,timedelta
from psycopg.types.json import Jsonb
from storage import Store
from rag import read_documents
assert os.environ['MODE']=='demo' and os.environ['APP_API_KEY']=='qa-step24-operator'
change=CHANGE
meta=next(d['governance'] for d in read_documents()[0] if d['id']=='refund-v2')
if change in {'draft','revoked'}: meta['status']=change
if change=='expired': meta['effective_until']=datetime.now(timezone.utc).isoformat()
if change=='not_yet_effective': meta['effective_from']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
with Store(os.environ['DATABASE_URL']).connect() as c:
 c.execute("UPDATE rf_knowledge_documents SET governance=%s WHERE id='refund-v2'",(Jsonb(meta),))
 print(json.dumps({'refunds':c.execute("SELECT count(*) AS n FROM rf_refunds WHERE order_id='RF-1004'").fetchone()['n']}))
""".replace('CHANGE', repr(change))
    return json.loads(docker('exec',PROJECT+'-resolveflow-1','python','-c',code))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists(): parser.error('Use a new report path')
    before=main_fingerprints()
    results=[]
    assert set_policy('active')['refunds']==0, 'Use a fresh isolated QA volume'
    for change in ['expired','revoked','not_yet_effective','draft']:
        set_policy('active')
        rid=request('/runs',{'order_id':'RF-1004','ticket':'申请退款'})['id']
        pending=wait(rid)
        assert pending['status']=='awaiting_approval'
        snapshot=pending['state']['evidence']
        set_policy(change)
        found=request('/knowledge?query='+urllib.parse.quote('退款'))
        assert all(hit['id']!='refund-v2' for hit in found['results'])
        policy=next(p['policy'] for p in found['policies'] if p['id']=='refund-v2')
        assert policy['reason']==change and not policy['usable']
        if change=='revoked':
            docker('compose','-f','compose.qa-policy.yaml','restart','worker')
        request('/runs/'+rid+'/approval',{'approved':True,'reason':'Policy lifecycle QA'},'reviewer')
        row=wait(rid)
        assert row['status']=='escalated' and row['approval']['approved']
        assert row['state']['evidence']==snapshot
        assert 'policy_'+change in row['state']['result']['grounding']['errors']
        assert set_policy(change)['refunds']==0
        results.append({'scenario':change+'_while_awaiting_approval','id':rid,'status':row['status'],
                        'result':row['state']['result'],'usage':row['state']['usage'],
                        'snapshot_unchanged':True,'refunds_for_order':0})
    set_policy('active')
    for order,ticket,expected in [('RF-1001','申请退款','refunded'),('RF-1002','申请退款','auto_rejected'),
                                  ('RF-1003','查询物流','answered'),('RF-1004','申请退款','awaiting_approval')]:
        rid=request('/runs',{'order_id':order,'ticket':ticket})['id']
        row=wait(rid)
        assert row['status']==expected
        if expected=='awaiting_approval':
            request('/runs/'+rid+'/approval',{'approved':True,'reason':'Active demo policy QA'},'reviewer')
            row=wait(rid)
            assert row['status']=='refunded'
        assert row['state']['result']['grounding']['usable']
        assert row['state']['usage']['model_calls']==0
        results.append({'scenario':'active_'+order,'id':rid,'status':row['status'],
                        'result':row['state']['result'],'usage':row['state']['usage']})
    after=main_fingerprints()
    assert before==after
    report={'passed':True,'created_at':datetime.now(timezone.utc).isoformat(), 'project':PROJECT,'url':URL,
            'main_before':before,'main_after':after,'results':results,'model_api_calls':0,'real_payments':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'cases':len(results),'main_data_unchanged':True}))


if __name__=='__main__': main()
