"""Read-only main fingerprints and isolated step-2.3 API/Worker acceptance.

Start compose.qa-grounding.yaml first. No live model calls or main API writes.
Reports never overwrite prior evidence; QA volumes are retained by the caller.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from worker_crash_qa import main_fingerprints


def request(path, data=None, role='operator'):
    req = urllib.request.Request('http://127.0.0.1:8012/api'+path,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={'Content-Type': 'application/json', 'X-API-Key': 'qa-step23-'+role})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def wait(rid):
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        row = request('/runs/'+rid)
        if row['status'] not in {'queued', 'running', 'retrying', 'approval_queued'}:
            return row
        time.sleep(.3)
    raise TimeoutError('Isolated Worker did not finish')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists(): parser.error('Use a new report path')
    before = main_fingerprints()
    results = []
    for order, ticket, allowed in [
        ('RF-1001', '申请退款', {'refunded', 'already_refunded'}),
        ('RF-1002', '申请退款', {'auto_rejected'}),
        ('RF-1004', '申请退款', {'awaiting_approval'}),
        ('RF-1001', '申请退款以后多久能到账？', {'escalated'}),
        ('RF-1003', '查询物流', {'answered'}),
    ]:
        rid = request('/runs', {'order_id': order, 'ticket': ticket})['id']
        row = wait(rid)
        assert row['status'] in allowed, (rid, row['status'])
        state = row['state']
        if row['status'] != 'escalated':
            assert state['result']['grounding']['reference_valid']
            assert all(':' in c for c in state['proposal']['citations'])
            assert state['proposal']['quotes']
        else:
            assert not state['result']['policy_supported']
            assert not state['proposal']['citations']
        outcome = {'id': rid, 'order': order, 'ticket': ticket, 'status': row['status'],
                   'proposal': state['proposal'], 'result': state['result'], 'usage': state['usage']}
        assert state['usage']['model_calls'] == 0
        if row['status'] == 'awaiting_approval':
            quotes = state['proposal']['quotes']
            request('/runs/'+rid+'/approval', {'approved': True, 'reason': '片段引用与持久化审批验收'}, 'reviewer')
            resumed = wait(rid)
            assert resumed['status'] in {'refunded', 'already_refunded'}
            assert resumed['state']['proposal']['quotes'] == quotes
            outcome['after_approval'] = resumed['status']
        results.append(outcome)
    after = main_fingerprints()
    assert before == after, 'Main data changed during isolated acceptance'
    report = {'passed': True, 'created_at': datetime.now(timezone.utc).isoformat(),
              'project': 'resolveflow-qa-step23', 'url': 'http://127.0.0.1:8012',
              'main_before': before, 'main_after': after, 'results': results,
              'model_api_calls': 0, 'real_payments': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'cases': len(results), 'main_data_unchanged': True}))


if __name__ == '__main__': main()
