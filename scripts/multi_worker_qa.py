"""Step 1.9: two real Workers, stalled refund, concurrent idempotency, MCP timeout.

Dedicated demo project on 8010. Preserves all volumes/history, no paid calls.
Run with --baseline for the pre-fix busy admin retry reproduction only.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request

import worker_crash_qa as qa


def distinct_worker_clients(rows, workers):
    """Count container identities, not all interfaces of each container."""
    if len(rows) != len(workers):
        return False
    represented = set()
    for row in rows:
        address = row['client_addr']
        matches = {w['id'] for w in workers if address and any(
            n.get('IPAddress') == address for n in w['networks'].values())}
        if len(matches) != 1:
            return False
        represented.update(matches)
    return represented == {w['id'] for w in workers}


def admin(path, body=None, timeout=5):
    start = time.monotonic()
    request = urllib.request.Request(qa.URL + path, headers={
        'X-API-Key': qa.PUBLIC_ENV['ADMIN_API_KEY'], 'Content-Type': 'application/json'},
        data=None if body is None else json.dumps(body).encode())
    try:
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            result = {'status': response.status, 'body': json.load(response)}
    except (TimeoutError, OSError) as error:
        result = {'error_type': type(error).__name__}
    return {**result, 'seconds': round(time.monotonic() - start, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Use a new report path')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    qa.PROJECT = 'resolveflow-qa-step19'
    qa.API, qa.WORKER = qa.PROJECT + '-resolveflow-1', qa.PROJECT + '-worker-1'
    workers = [qa.WORKER, qa.PROJECT + '-worker-2']
    qa.URL = 'http://127.0.0.1:8010'
    qa.PUBLIC_ENV = {**qa.PUBLIC_ENV, 'APP_PORT': '8010', 'APP_API_KEY': 'qa-step19-operator',
                     'REVIEWER_API_KEY': 'qa-step19-reviewer', 'ADMIN_API_KEY': 'qa-step19-admin',
                     'POSTGRES_PASSWORD': 'qa-step19-database'}
    environment = {**os.environ, **qa.PUBLIC_ENV}
    report = {'step': '1.9', 'baseline': args.baseline, 'project': qa.PROJECT,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'checks': [], 'cleanup_errors': []}
    observer = compose = env_path = None

    def require(condition, label):
        report['checks'].append({'check': label, 'passed': bool(condition)})
        if not condition:
            raise AssertionError(label)

    def submit(order):
        return qa.api('/api/runs', {'order_id': order, 'ticket': '申请退款'})['id']

    def done(rid, seconds=60):
        # API details are separate READ COMMITTED reads; a transition can expose
        # the previous run status alongside the freshly committed job status.
        return qa.wait_for(lambda: (r if (r := qa.api('/api/runs/' + rid))['job']['status'] == 'done'
            and r['status'] not in {'queued', 'running', 'retrying', 'approval_queued'} else None), seconds)

    def blocked(count):
        return qa.wait_for(lambda: (rows if len(rows := [r for r in observer.call('multi_activity')
            if r['operation'] == 'refund_insert' and r['wait_event'] == 'advisory']) == count else None), 25)

    try:
        report['main_before'] = qa.main_fingerprints()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as empty:
            env_path = Path(empty.name)
        compose = ('compose', '--env-file', str(env_path), '-f', str(qa.ROOT / 'compose.yaml'), '-p', qa.PROJECT)
        print('Starting independent two-Worker demo...', flush=True)
        qa.docker(*compose, 'up', '--scale', 'worker=2', '--no-build', '--pull', 'never', '-d', '--wait',
                  '--wait-timeout', '180', env=environment, timeout=210)
        report['workers_before'] = [qa.container(w) for w in workers]
        require(all(w['command'] == ['python', 'worker.py'] and w['project'] == qa.PROJECT
                    and w['health'] == 'healthy' for w in report['workers_before']), 'two real healthy QA Workers')
        require(qa.api('/health')['mode'] == 'demo', 'demo mode')
        addresses = {n['IPAddress'] for w in report['workers_before'] for n in w['networks'].values()}
        observer = qa.Observer()
        report['qa_before'] = observer.call('fingerprints')

        # Refund SQL has no task-wide deadline; hold longer than the MCP envelope.
        order = observer.call('multi_seed')['id']
        gate = observer.call('multi_gate', order_id=order)
        stalled = report['stalled'] = {'order_id': order, 'gate': gate, 'run_id': submit(order)}
        stalled['blocked'] = blocked(1)
        require(stalled['blocked'][0]['client_addr'] in addresses, 'real Worker is waiting in refund SQL')
        start = time.monotonic()
        stalled['retry_http'] = admin('/api/runs/' + stalled['run_id'] + '/retry', {}, timeout=3)
        if args.baseline:
            require('error_type' in stalled['retry_http'] and stalled['retry_http']['seconds'] >= 2.5,
                    'baseline admin retry blocks behind active job lock')
        else:
            require(stalled['retry_http']['status'] == 409 and stalled['retry_http']['seconds'] < 2,
                    'busy admin retry promptly returns HTTP 409')
        stalled['other_run_id'] = submit('RF-1002')
        require(done(stalled['other_run_id'])['status'] == 'auto_rejected', 'second Worker completes unrelated job while first is stalled')
        if not args.baseline:
            time.sleep(max(0, 35 - (time.monotonic() - start)))
        stalled['observed_seconds'] = round(time.monotonic() - start, 3)
        stalled['snapshot'] = observer.call('snapshot', run_id=stalled['run_id'])
        stalled['activity'] = observer.call('multi_activity')
        stalled['metrics'] = admin('/api/metrics')
        stalled['health'] = [qa.container(w)['health'] for w in workers]
        require(stalled['metrics']['body']['workers_online'] == 2 and set(stalled['health']) == {'healthy'},
                'both heartbeats and health checks stay green while one task is stalled')
        require(not stalled['snapshot']['job_lock_available'] and stalled['snapshot']['job']['attempts'] == 0
                and stalled['snapshot']['graph_next'] == ['execute'], 'stalled run retains job lock and pending execute checkpoint')
        require(not stalled['snapshot']['refunds'], 'blocked refund has not committed')
        if not args.baseline:
            require(any(r['operation'] == 'queue_claim' and r['state'] == 'idle in transaction'
                        and float(r['transaction_seconds']) >= 30 for r in stalled['activity']),
                    'queue transaction remains open beyond 30 seconds without an overall task deadline')
        observer.call('multi_release')
        stalled['completion_http'] = done(stalled['run_id'])
        require(stalled['completion_http']['status'] == 'refunded', 'releasing external SQL blocker completes original job')
        stalled['final'] = observer.call('snapshot', run_id=stalled['run_id'])
        require(stalled['final']['job']['attempts'] == 1 and stalled['final']['audit'] == [{'action': 'create'}],
                'busy retry request neither resets nor duplicates work')
        observer.call('multi_cleanup')

        if not args.baseline:
            # Both real workers reach the same order's refund boundary concurrently.
            order = observer.call('multi_seed')['id']
            concurrent = report['concurrent'] = {'order_id': order, 'gate': observer.call('multi_gate', order_id=order)}
            concurrent['run_ids'] = [submit(order), submit(order)]
            concurrent['blocked'] = blocked(2)
            require(distinct_worker_clients(concurrent['blocked'], report['workers_before']),
                    'two distinct Worker containers concurrently execute different jobs for the same order')
            concurrent['before'] = [observer.call('snapshot', run_id=rid) for rid in concurrent['run_ids']]
            require(all(not s['job_lock_available'] and s['job']['attempts'] == 0 for s in concurrent['before']),
                    'both active job rows are locked and cannot be claimed by another Worker')
            observer.call('multi_release')
            results = [done(rid)['status'] for rid in concurrent['run_ids']]
            require(sorted(results) == ['already_refunded', 'refunded'], 'concurrent same-order requests produce one refund and one duplicate result')
            concurrent['after'] = [observer.call('snapshot', run_id=rid) for rid in concurrent['run_ids']]
            require(all(s['job']['attempts'] == 1 and len(s['attempts']) == 1 and s['attempts'][0]['success']
                        and len(s['refunds']) == 1 for s in concurrent['after']), 'each concurrent job commits one successful attempt and sees one ledger row')
            require(concurrent['after'][0]['refunds'] == concurrent['after'][1]['refunds'], 'both jobs see the identical unique refund')
            observer.call('multi_cleanup')

            print('Testing server-side MCP SQL timeout and retry exhaustion...', flush=True)
            for w in workers:
                qa.docker('pause', w)
            timeout_case = report['timeout'] = {'run_id': submit('RF-1002')}
            timeout_case['gate'] = observer.call('gate', table='rf_orders')
            for w in workers:
                qa.docker('unpause', w)
            timeout_case['blocked'] = observer.call('blocked', run_id=timeout_case['run_id'])
            start = time.monotonic()
            failed = qa.wait_for(lambda: (r if (r := qa.api('/api/runs/' + timeout_case['run_id']))['job']['status'] == 'failed' else None), 145)
            timeout_case['seconds_until_failed'] = round(time.monotonic() - start, 3)
            timeout_case['failed'] = observer.call('snapshot', run_id=timeout_case['run_id'])
            require(failed['job']['attempts'] == 3 and len(timeout_case['failed']['attempts']) == 3,
                    'real MCP timeout consumes exactly three ordinary attempts then fails')
            require(all(not a['success'] and 9000 <= a['elapsed_ms'] < 15000 for a in timeout_case['failed']['attempts']),
                    'each observed server-side SQL timeout finishes before the 15-second MCP read deadline')
            attempts = timeout_case['failed']['attempts']
            timeout_case['retry_gaps_seconds'] = [round((datetime.fromisoformat(b['created_at'])
                - datetime.fromisoformat(a['created_at'])).total_seconds() - a['elapsed_ms']/1000, 3)
                for a,b in zip(attempts, attempts[1:])]
            require(all(gap >= delay-0.2 for gap,delay in zip(timeout_case['retry_gaps_seconds'], (2,4))),
                    'slow failures wait at least 2 then 4 seconds before the next retry')
            require(timeout_case['failed']['job_lock_available'], 'exhausted timeout releases job lock')
            timeout_case['activity_after_failure'] = observer.call('multi_activity')
            require(not any(r['operation'] == 'order_read' and r['blockers'] for r in timeout_case['activity_after_failure']),
                    'MCP cleanup leaves no blocked order-read backend after exhaustion')
            observer.call('unlock')
            timeout_case['retry_http'] = admin('/api/runs/' + timeout_case['run_id'] + '/retry', {})
            require(timeout_case['retry_http']['status'] == 202, 'failed task can be retried after blocker is removed')
            require(done(timeout_case['run_id'])['status'] == 'auto_rejected', 'admin retry resumes original checkpoint to completion')
            timeout_case['recovered'] = observer.call('snapshot', run_id=timeout_case['run_id'])
            require(timeout_case['recovered']['audit'] == [{'action': 'create'}, {'action': 'retry'}]
                    and len(timeout_case['recovered']['attempts']) == 4, 'retry history retains three failures and one success')
            ids = [stalled['run_id'], stalled['other_run_id'], *concurrent['run_ids'], timeout_case['run_id']]
            require(all(observer.call('snapshot', run_id=rid)['graph_state']['usage']['model_calls'] == 0 for rid in ids), 'all five runs use zero model calls')
            report['workers_after'] = [qa.container(w) for w in workers]
            require(all(a['pid'] == b['pid'] and a['started'] == b['started'] for a,b in zip(report['workers_before'], report['workers_after'])),
                    'neither Worker restarted during the scenarios')
        report['gate_cleanup'] = observer.call('multi_cleanup')
        require(report['gate_cleanup'] == {'trigger_count': 0, 'function': None}, 'all temporary test triggers/functions removed')
        report['qa_after'] = observer.call('fingerprints')
        report['main_after'] = qa.main_fingerprints()
        require(report['main_before'] == report['main_after'], 'main 13 business/checkpoint/RAG tables unchanged')
        report['passed'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        print('QA failed: ' + type(error).__name__, flush=True)
    finally:
        actions = [lambda: observer.close() if observer else None]
        for w in workers:
            actions.append(lambda w=w: qa.docker('unpause', w) if compose and qa.container(w)['state'] == 'paused' else None)
        actions.append(lambda: qa.docker(*compose, 'stop', env=environment, timeout=75) if compose else None)
        for action in actions:
            try:
                action()
            except Exception as error:
                report['cleanup_errors'].append(type(error).__name__)
        if env_path:
            env_path.unlink(missing_ok=True)
        if compose:
            report['qa_final'] = {w: qa.container(w)['state'] for w in [*workers, qa.API, qa.PROJECT + '-db-1']}
            if set(report['qa_final'].values()) != {'exited'}:
                report['cleanup_errors'].append('QA services did not all stop')
        report['passed'] = report['passed'] and not report['cleanup_errors']
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(args.report)}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
