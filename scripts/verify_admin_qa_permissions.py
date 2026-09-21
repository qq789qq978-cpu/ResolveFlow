"""Supplement browser acceptance with API authorization checks on localhost QA.

Uses public isolated-demo fixtures only. Never enqueues an authorized retry:
that action is performed in the browser. Writes evidence without auth headers.
"""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID

BASE = 'http://127.0.0.1:8004'
KEYS = {role: 'qa-step11-' + role for role in ('operator', 'reviewer', 'admin')}
KEYS.update(anonymous=None, invalid='invalid-qa-step13')


def request(role, path, body=None):
    headers = {'Content-Type': 'application/json'}
    if KEYS[role]:
        headers['X-API-Key'] = KEYS[role]
    req = Request(BASE + path, headers=headers,
                  data=None if body is None else json.dumps(body).encode())
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['before', 'after'])
    parser.add_argument('run_id', type=UUID)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    status, config = request('admin', '/api/config')
    assert status == 200 and config['mode'] == 'demo' and config['role'] == 'admin'
    path = f'/api/runs/{args.run_id}'
    status, before = request('admin', path)
    assert status == 200 and before['ticket'].startswith('步骤1.3')
    assert before['status'] == ('failed' if args.phase == 'before' else 'auto_rejected')
    checks = []

    def check(role, endpoint, expected, body=None):
        code, payload = request(role, endpoint, body)
        assert code == expected, (role, endpoint, code, expected)
        checks.append({'role': role, 'path': endpoint, 'method': 'GET' if body is None else 'POST',
                       'status': code, 'response': payload})

    for role, expected in [('anonymous', 401), ('invalid', 401), ('operator', 403),
                           ('reviewer', 403), ('admin', 200)]:
        check(role, '/api/metrics', expected)
    for role, expected in [('anonymous', 401), ('invalid', 401), ('operator', 403), ('reviewer', 403)]:
        check(role, path + '/retry', expected, {})
    check('reviewer', '/api/runs', 403, {'order_id': 'RF-1002', 'ticket': 'QA denied creation'})
    check('operator', path + '/approval', 403, {'approved': False, 'reason': 'QA denied approval'})
    check('operator', path + '/review', 403, {'resolution': 'QA denied closure'})
    if args.phase == 'after':
        check('admin', path + '/retry', 409, {})
    status, after = request('admin', path)
    assert status == 200 and before == after, 'Denied operations changed the target run'
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'phase': args.phase, 'run_id': str(args.run_id),
        'checks': checks, 'target_unchanged': True, 'target': after},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{args.phase}: {len(checks)} HTTP checks passed; target unchanged')


if __name__ == '__main__':
    main()
