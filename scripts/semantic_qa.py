"""Step 2.8 isolated real local encoder/API/Worker acceptance; never erase volumes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

from bm25_qa import docker, fingerprints

ROOT=Path(__file__).resolve().parents[1]
PROJECT='resolveflow-qa-step28final'
API=PROJECT+'-resolveflow-1'
ENCODER=PROJECT+'-embedding-1'
WORKER=PROJECT+'-worker-1'
COMPOSE=['compose','-p',PROJECT,'-f','compose.qa-semantic.yaml']


def request(path,data=None,role='operator'):
    req=urllib.request.Request('http://127.0.0.1:8016/api'+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json','X-API-Key':'qa-step28-'+role})
    with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)


def search(query='ledger'):return request('/knowledge?query='+urllib.parse.quote(query))


def wait(rid):
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        row=request('/runs/'+rid)
        if row['status'] not in {'queued','running','retrying','approval_queued'}:return row
        time.sleep(.3)
    raise TimeoutError('Worker did not finish')


def vectors():
    return json.loads(docker('exec',API,'python','-c',"""
import os,json,hashlib
from storage import Store
with Store(os.environ['DATABASE_URL']).connect() as c:
 out={}
 for table in ('rf_vector_batches','rf_policy_vectors'):
  rows=c.execute('SELECT * FROM '+table).fetchall()
  out[table]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(sorted(json.dumps(r,sort_keys=True,default=str) for r in rows)).encode()).hexdigest()}
print(json.dumps(out))
"""))


def fallback(reason):
    start=time.monotonic();row=search();elapsed=(time.monotonic()-start)*1000
    assert row['retrieval']['used']=='bm25',row['retrieval']
    assert row['retrieval']['reason']==reason,row['retrieval']
    assert row['results'][0]['chunk_id']=='refund-v2:3ad704686fec:2'
    assert all(h['retrieval_fallback']['reason']==reason for h in row['results'])
    return {'reason':reason,'elapsed_ms':elapsed,'response':row}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    assert not args.report.exists(),'Choose a new report path'
    before=fingerprints('resolveflow-resolveflow-1')
    previous=request('/runs')
    assert all(r['status']=='failed' for r in previous) and fingerprints(API)['rf_refunds']['count']==0,'Use a fresh QA project or retain only failed setup attempts'
    docker('exec',API,'python','-c',"import os;assert os.environ['MODE']=='demo' and os.environ['APP_API_KEY']=='qa-step28-operator'")
    initial=search();assert initial['retrieval']['used']=='hybrid'
    assert all(h['retrieval']=='hybrid' and h['release']==initial['release']['token'] for h in initial['results'])
    resources=json.loads(docker('inspect','--format',
        '{"image":{{json .Image}},"memory_limit":{{.HostConfig.Memory}},"nano_cpus":{{.HostConfig.NanoCpus}},"readonly_root":{{.HostConfig.ReadonlyRootfs}},"ports":{{json .NetworkSettings.Ports}},"networks":{{json .NetworkSettings.Networks}},"mounts":{{json .Mounts}}}',ENCODER))
    assert resources['memory_limit']==2147483648 and resources['nano_cpus']==2000000000 and resources['readonly_root']
    assert not resources['ports'] and len(resources['networks'])==1
    assert all(not m['RW'] for m in resources['mounts'] if m['Destination']=='/model')
    resources['network_internal']=json.loads(docker('network','inspect','--format','{{.Internal}}',PROJECT+'_private'))
    assert resources['network_internal']
    runtime=json.loads(docker('exec',ENCODER,'python','-c',"""
import json,socket,urllib.request
from pathlib import Path
out={'health':json.load(urllib.request.urlopen('http://127.0.0.1:8080/health'))}
try:
 socket.create_connection(('1.1.1.1',443),timeout=2).close()
 out['public_tcp_blocked']=False
except OSError:out['public_tcp_blocked']=True
out['memory_peak_bytes']=int(Path('/sys/fs/cgroup/memory.peak').read_text())
out['memory_current_bytes']=int(Path('/sys/fs/cgroup/memory.current').read_text())
print(json.dumps(out))
"""))
    assert runtime['public_tcp_blocked'] and runtime['memory_peak_bytes']<resources['memory_limit']
    # Real simultaneous HTTP calls exercise the encoder's nonblocking capacity limit.
    concurrency=json.loads(docker('exec',API,'python','-c',"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json,urllib.request,urllib.error
b=Barrier(8)
def call(i):
 b.wait()
 try:
  r=urllib.request.urlopen(urllib.request.Request('http://embedding:8080/encode',data=json.dumps({'kind':'query','text':'refund policy '*100}).encode(),headers={'Content-Type':'application/json'}),timeout=10)
  r.read();return r.status
 except urllib.error.HTTPError as e:return e.code
with ThreadPoolExecutor(max_workers=8) as pool:print(json.dumps(list(pool.map(call,range(8)))))
"""))
    assert 200 in concurrency and 503 in concurrency and set(concurrency)<={200,503}
    long_query=search('refund '*550)
    assert long_query['retrieval']['reason']=='encoder_input_rejected'
    failures=[{'reason':'encoder_input_rejected','response':long_query}]
    docker('pause',ENCODER)
    try:
        result=fallback('semantic_timeout');assert 4900<result['elapsed_ms']<7500
        failures.append(result)
    finally:docker('unpause',ENCODER)
    time.sleep(1)
    docker(*COMPOSE,'stop','embedding')
    try:failures.append(fallback('semantic_invalid_or_unreachable'))
    finally:docker(*COMPOSE,'up','-d','--wait','--wait-timeout','150','embedding')
    corrupt="""
import os,json
from storage import Store
with Store(os.environ['DATABASE_URL']).connect() as c:
 row=c.execute('SELECT batch_id,chunk_id,vector_sha FROM rf_policy_vectors ORDER BY chunk_id LIMIT 1').fetchone()
 c.execute("UPDATE rf_policy_vectors SET vector_sha='corrupted' WHERE batch_id=%s AND chunk_id=%s",(row['batch_id'],row['chunk_id']))
print(json.dumps(row))
"""
    row=json.loads(docker('exec',API,'python','-c',corrupt))
    try:failures.append(fallback('vector_batch_incomplete_or_mismatched'))
    finally:
        docker('exec',API,'python','-c',"import os;from storage import Store\nr="+repr(row)+"\nwith Store(os.environ['DATABASE_URL']).connect() as c:c.execute('UPDATE rf_policy_vectors SET vector_sha=%s WHERE batch_id=%s AND chunk_id=%s',(r['vector_sha'],r['batch_id'],r['chunk_id']))")
    assert search()['retrieval']['used']=='hybrid'
    history=request('/policy-releases',role='admin');flows=[];pending=None
    for order,ticket,status in [('RF-1001','申请退款','refunded'),('RF-1001','申请退款','already_refunded'),
                                ('RF-1002','申请退款','auto_rejected'),('RF-1003','查询物流','answered'),
                                ('RF-1004','申请退款','awaiting_approval'),
                                ('RF-1001','My parcel tracking has not changed for a long time.','escalated'),
                                ('RF-1001','zzzzzz','escalated')]:
        rid=request('/runs',{'order_id':order,'ticket':ticket})['id'];run=wait(rid)
        assert run['status']==status,(order,status,run['status'])
        assert run['state']['usage']['model_calls']==0
        assert run['state']['evidence'] and all(h['retrieval']=='hybrid' for h in run['state']['evidence'])
        if status=='awaiting_approval':pending=rid
        flows.append({'expected_status':status,'run':run})
    assert request('/policy-releases',role='admin')==history
    # Persist a queued approval while the Worker is stopped, then recreate all containers.
    docker(*COMPOSE,'stop','worker')
    request('/runs/'+pending+'/approval',{'approved':True,'reason':'Step 2.8 local demo persistence'},'reviewer')
    assert request('/runs/'+pending)['status']=='approval_queued'
    docker(*COMPOSE,'stop','resolveflow')
    # Start only API so fingerprints can be read; no Worker can consume the queued approval.
    docker(*COMPOSE,'up','-d','--wait','--wait-timeout','120','resolveflow')
    persist_before={**fingerprints(API),**vectors()}
    docker(*COMPOSE,'down')
    docker(*COMPOSE,'up','-d','--wait','--wait-timeout','150','db','embedding','resolveflow')
    persist_after={**fingerprints(API),**vectors()}
    assert persist_before==persist_after
    assert search()['retrieval']['used']=='hybrid'
    docker(*COMPOSE,'up','-d','--wait','--wait-timeout','120','worker')
    resumed=wait(pending);assert resumed['status']=='refunded'
    assert fingerprints(API)['rf_refunds']['count']==2
    after=fingerprints('resolveflow-resolveflow-1');assert before==after
    report={'passed':True,'project':PROJECT,'image':docker('inspect','--format','{{.Image}}',API),
        'initial_search':initial,'prior_failed_setup_runs':previous,'resources':resources,'encoder_runtime':runtime,'concurrent_statuses':concurrency,
        'fallback_checks':failures,'business_flows':flows,'queued_approval_resumed':resumed,
        'persistence_before':persist_before,'persistence_after':persist_after,'preserved_tables':19,
        'main_before':before,'main_after':after,'main_unchanged_tables':17,'paid_model_api_calls':0,'real_payments':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'flows':len(flows),'preserved_tables':19,'fallback_checks':len(failures)}))


if __name__=='__main__':main()
