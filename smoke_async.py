"""Verify an already running API + worker, including RBAC and manual decisions."""
import argparse
import json
import os
from pathlib import Path
import time
import httpx
from dotenv import load_dotenv

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8003')
    args=parser.parse_args()
    load_dotenv(Path(__file__).with_name('.env'),encoding='utf-8-sig')
    keys={role:{'X-API-Key':os.environ[name]} for role,name in [('operator','APP_API_KEY'),('reviewer','REVIEWER_API_KEY'),('admin','ADMIN_API_KEY')]}
    result=[]
    with httpx.Client(base_url=args.url,timeout=10) as client:
        assert client.get('/api/runs').status_code==401
        assert client.get('/api/metrics',headers=keys['operator']).status_code==403
        def wait(rid):
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                response=client.get('/api/runs/'+rid,headers=keys['operator'])
                response.raise_for_status()
                row=response.json()
                if row['status'] not in {'queued','running','retrying','approval_queued'}:return row
                time.sleep(1)
            raise TimeoutError('Worker did not finish the queued run')
        for order,expected in [('RF-1001',{'refunded','already_refunded'}),('RF-1002',{'auto_rejected'}),('RF-1004',{'awaiting_approval'})]:
            start=time.monotonic()
            response=client.post('/api/runs',headers=keys['operator'],json={'order_id':order,'ticket':'申请退款'})
            assert response.status_code==202
            enqueue_ms=round((time.monotonic()-start)*1000)
            rid=response.json()['id']
            row=wait(rid)
            assert row['status'] in expected,(rid,row['status'])
            outcome={'order':order,'run_id':rid,'status':row['status'],'enqueue_ms':enqueue_ms,'elapsed_ms':row['elapsed_ms'],'usage':(row['state'] or {}).get('usage')}
            if row['status']=='awaiting_approval':
                path=f'/api/runs/{rid}/approval'
                body={'approved':False,'reason':'运行验证：拒绝例外退款'}
                assert client.post(path,headers=keys['operator'],json=body).status_code==403
                assert client.post(path,headers=keys['reviewer'],json=body).status_code==202
                assert client.post(path,headers=keys['reviewer'],json=body).status_code==409
                assert wait(rid)['status']=='rejected'
                outcome['human_final']='rejected'
            result.append(outcome)
        metrics=client.get('/api/metrics',headers=keys['admin']).json()
        assert metrics['workers_online']>=1
    report={'passed':True,'url':args.url,'cases':result,'metrics':metrics,'limitations':'Synthetic orders and simulated refunds; not real payment integration.'}
    Path(__file__).with_name('async_live_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'cases':len(result),'enqueue_ms':[r['enqueue_ms'] for r in result]},ensure_ascii=False))

if __name__=='__main__':main()
