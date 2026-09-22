"""Isolated step 2.5 CLI publication and real API/Worker acceptance."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from worker_crash_qa import main_fingerprints

PROJECT='resolveflow-qa-step25'
URL='http://127.0.0.1:8014'
API=PROJECT+'-resolveflow-1'


def docker(*args, fail=False):
    p=subprocess.run(['docker',*args],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=150)
    if fail:
        assert p.returncode!=0, 'Expected publication refusal'
        return p.stderr
    if p.returncode: raise RuntimeError('QA Docker operation failed: '+p.stderr[-1000:])
    return p.stdout.strip()


def request(path,data=None,role='operator'):
    req=urllib.request.Request(URL+'/api'+path,data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json','X-API-Key':'qa-step25-'+role})
    with urllib.request.urlopen(req,timeout=10) as response: return json.load(response)


def wait(rid):
    end=time.monotonic()+60
    while time.monotonic()<end:
        row=request('/runs/'+rid)
        if row['status'] not in {'queued','running','retrying','approval_queued'}:return row
        time.sleep(.3)
    raise TimeoutError('QA Worker did not finish')


def cli(command,generation,*extra,fail=False):
    result=docker('exec',API,'python','policy_releases.py',command,
        '--actor','qa-maintainer','--reason','Synthetic release CLI acceptance',
        '--expected-generation',str(generation),*extra,fail=fail)
    return result if fail else json.loads(result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():parser.error('Use a new report path')
    before=main_fingerprints()
    initial=request('/policy-releases',role='admin')
    assert initial['head']['generation']==1 and len(initial['releases'])==1,'Use a new isolated QA project; do not erase old volumes'
    guard="import os;assert os.environ['MODE']=='demo' and os.environ['APP_API_KEY']=='qa-step25-operator';"
    docker('exec',API,'python','-c',guard+"print('isolated demo fixture verified')")
    auth=[]
    for role,expected in [('missing',401),('operator',403),('reviewer',403)]:
        try:request('/policy-releases',role=role)
        except urllib.error.HTTPError as error:assert error.code==expected;auth.append({'role':role,'status':error.code})
        else:raise AssertionError('Unexpected release history access')
    # Reuse the same synthetic bundle builder as the tests; it modifies only /tmp.
    docker('cp','test_policy_releases.py',API+':/tmp/qa_bundle.py')
    docker('exec',API,'python','-c',guard+
        "import sys;sys.path.insert(0,'/tmp');from pathlib import Path;from qa_bundle import bundle;bundle(Path('/tmp'))")
    results=[]
    old=request('/runs',{'order_id':'RF-1004','ticket':'申请退款'})['id']
    pending=wait(old)
    assert pending['status']=='awaiting_approval'
    original_evidence=pending['state']['evidence']
    published=cli('publish',1,'--directory','/tmp/qa-policy-b')
    assert published['generation']==2
    found=request('/knowledge?query='+urllib.parse.quote('申请退款'))
    assert found['release']['token']==published
    assert any(h['id']=='refund-v3' and 'refund' in h['actions'] for h in found['results'])
    request('/runs/'+old+'/approval',{'approved':True,'reason':'Old release approval'},'reviewer')
    old_row=wait(old)
    assert old_row['status']=='escalated' and old_row['state']['evidence']==original_evidence
    results.append({'scenario':'A_approval_after_B_publish','run':old_row})
    new=request('/runs',{'order_id':'RF-1001','ticket':'申请退款'})['id']
    new_row=wait(new)
    assert new_row['status']=='refunded' and new_row['state']['result']['grounding']['release']['token']==published
    results.append({'scenario':'B_refund_with_compatible_rule_v2','run':new_row})
    b_pending=request('/runs',{'order_id':'RF-1004','ticket':'申请退款'})['id']
    assert wait(b_pending)['status']=='awaiting_approval'
    rolled=cli('rollback',2,'--release',initial['head']['release_id'])
    assert rolled['generation']==3 and rolled['id']==initial['head']['release_id']
    docker('compose','-f','compose.qa-release.yaml','restart','worker')
    request('/runs/'+b_pending+'/approval',{'approved':True,'reason':'B approval after rollback'},'reviewer')
    b_row=wait(b_pending)
    assert b_row['status']=='escalated'
    assert request('/runs/'+new)['state']==new_row['state']
    results.append({'scenario':'B_approval_after_A_rollback_and_restart','run':b_row})
    fresh=request('/runs',{'order_id':'RF-1004','ticket':'申请退款'})['id']
    assert wait(fresh)['status']=='awaiting_approval'
    request('/runs/'+fresh+'/approval',{'approved':True,'reason':'Fresh A release approval'},'reviewer')
    fresh_row=wait(fresh)
    assert fresh_row['status']=='refunded'
    results.append({'scenario':'fresh_A_approval','run':fresh_row})
    docker('exec',API,'python','-c',guard+
        "import json;from pathlib import Path;data=json.loads(Path('/tmp/qa-policy-b/governance.json').read_text());"
        "meta=data['policies']['refund-v3'];meta['status']='revoked';Path('/tmp/revoke-b.json').write_text(json.dumps(meta))")
    cli('review',3,'--metadata','/tmp/revoke-b.json')
    history=request('/policy-releases',role='admin')
    failure=cli('rollback',4,'--release','qa-policy-b',fail=True)
    assert 'not currently usable' in failure
    assert request('/policy-releases',role='admin')==history
    stale=cli('rollback',3,'--release',rolled['id'],fail=True)
    assert 'generation changed' in stale
    docker('exec',API,'python','-c',guard+
        "import json;from pathlib import Path;p=Path('/tmp/qa-policy-b/release.json');d=json.loads(p.read_text());"
        "d['id']='incompatible-fixture';d['refund_rule']['version']='unknown-rule';p.write_text(json.dumps(d))")
    incompatible=cli('publish',4,'--directory','/tmp/qa-policy-b',fail=True)
    assert 'executable refund rule' in incompatible
    assert request('/policy-releases',role='admin')==history
    assert all(r['run']['state']['usage']['model_calls']==0 for r in results)
    after=main_fingerprints()
    assert before==after
    report={'passed':True,'project':PROJECT,'url':URL,'image':docker('inspect','--format','{{.Image}}',API),
        'main_before':before,'main_after':after,'results':results,'auth_checks':auth,
        'refused_operations':['rollback_revoked_policy','stale_generation','incompatible_rule'],
        'release_history':history,'old_completed_result_unchanged':True,'model_api_calls':0,'real_payments':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'business_flows':len(results),'refused_operations':3,'auth_checks':3,'main_unchanged':True}))


if __name__=='__main__':main()
