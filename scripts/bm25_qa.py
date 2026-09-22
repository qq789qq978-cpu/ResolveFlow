"""Step 2.6 isolated API/Worker demo acceptance; existing volumes are preserved."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
PROJECT='resolveflow-qa-step26'
API=PROJECT+'-resolveflow-1'
URL='http://127.0.0.1:8015'


def docker(*args):
    p=subprocess.run(['docker',*args],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=180)
    if p.returncode: raise RuntimeError('Docker operation failed: '+args[0])
    return p.stdout.strip()


def fingerprints(container):
    # Rows may contain credentials/actor labels: output hashes and counts only.
    return json.loads(docker('exec',container,'python','-c',"""
import os,json,hashlib
from storage import Store
from psycopg import sql
assert os.environ['MODE']=='demo'
tables=['rf_orders','rf_runs','rf_jobs','rf_approvals','rf_reviews','rf_refunds','rf_audit','rf_job_attempts','checkpoints','checkpoint_blobs','checkpoint_writes','rf_knowledge_documents','rf_knowledge_chunks','rf_policy_releases','rf_policy_head','rf_policy_reviews','rf_policy_events']
out={}
with Store(os.environ['DATABASE_URL']).connect() as c:
 c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 for table in tables:
  rows=c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
  out[table]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(sorted(json.dumps(r,sort_keys=True,default=str) for r in rows)).encode()).hexdigest()}
print(json.dumps(out))
"""))


def request(path,data=None,role='operator'):
    req=urllib.request.Request(URL+'/api'+path,data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json','X-API-Key':'qa-step26-'+role})
    with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)


def wait(rid):
    end=time.monotonic()+60
    while time.monotonic()<end:
        row=request('/runs/'+rid)
        if row['status'] not in {'queued','running','retrying','approval_queued'}:return row
        time.sleep(.3)
    raise TimeoutError('Worker did not finish')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if args.report.exists():parser.error('Use a new report path')
    before=fingerprints('resolveflow-resolveflow-1')
    fixture=fingerprints(API)
    assert fixture['rf_runs']['count']==0,'Use a fresh QA project; never erase previous evidence volumes'
    docker('exec',API,'python','-c',"import os;assert os.environ['MODE']=='demo' and os.environ['APP_API_KEY']=='qa-step26-operator'")
    searches=[]
    for query,expected in [('ledger','refund-v2:3ad704686fec:2'),
                           ('My parcel tracking has not changed for a long time.','shipping-v1:872750f16cd0:1')]:
        found=request('/knowledge?query='+urllib.parse.quote(query))
        assert found['release']['valid'] and found['results'][0]['chunk_id']==expected
        assert all(h['retrieval_profile']=='expanded' and h['release']==found['release']['token'] for h in found['results'])
        searches.append({'query':query,'response':found})
    assert request('/knowledge?query=zzzzzz')['results']==[]
    history=request('/policy-releases',role='admin')
    rows=[]
    for order,ticket,status in [('RF-1001','申请退款','refunded'),
                                 ('RF-1001','申请退款','already_refunded'),
                                 ('RF-1002','申请退款','auto_rejected'),
                                 ('RF-1003','查询物流','answered'),
                                 ('RF-1004','申请退款','awaiting_approval'),
                                 ('RF-1001','My parcel tracking has not changed for a long time.','escalated')]:
        rid=request('/runs',{'order_id':order,'ticket':ticket})['id']
        row=wait(rid)
        assert row['status']==status,(order,status,row['status'])
        if status=='awaiting_approval':
            request('/runs/'+rid+'/approval',{'approved':True,'reason':'Step 2.6 demo acceptance'},'reviewer')
            row=wait(rid)
            assert row['status']=='refunded'
        assert row['state']['usage']['model_calls']==0
        assert all(h['retrieval_profile']=='expanded' for h in row['state']['evidence'])
        rows.append({'expected_initial_status':status,'run':row})
    assert request('/policy-releases',role='admin')==history
    qa=fingerprints(API)
    assert qa['rf_refunds']['count']==2
    after=fingerprints('resolveflow-resolveflow-1')
    assert before==after
    report={'passed':True,'project':PROJECT,'image':docker('inspect','--format','{{.Image}}',API),
            'knowledge_checks':searches,'unrelated_query_empty':True,'business_flows':rows,
            'main_before':before,'main_after':after,'main_unchanged_tables':17,'qa_tables':qa,
            'release_history_unchanged':True,'model_api_calls':0,'real_payments':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'business_flows':len(rows),'main_unchanged_tables':17}))


if __name__=='__main__':main()
