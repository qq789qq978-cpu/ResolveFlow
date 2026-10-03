"""Real two-workspace personal-account acceptance. Fresh demo resources only.

No .env, old backups, live models or main resources are used. Volumes/networks
are retained; all services are stopped even on failure. Credentials stay in work/.
"""
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.accounts_stack import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8052)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Refusing existing evidence')
    report = {'passed': False, 'project': args.project, 'checks': [], 'model_api_calls': 0,
              'real_refunds': 0, 'main_modified': False}
    work = generate(args.project, args.image, args.port)
    config = json.loads((work / 'compose.json').read_text())
    compose = ['docker', 'compose', '--env-file', str(work / 'empty.env'), '-p', args.project,
               '-f', str(work / 'compose.json')]
    def command(argv, timeout=300):
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=timeout)
        if result.returncode:
            with (work / 'failures.log').open('ab') as output:
                output.write(result.stdout + result.stderr)
            raise RuntimeError('QA command failed; details retained privately')
        return result.stdout.decode('utf-8')
    def check(name, condition):
        report['checks'].append({'check': name, 'passed': bool(condition)})
        if not condition:
            raise AssertionError(name)
    def execute(service, source):
        return json.loads(command(compose + ['exec', '-T', service, 'python', '-c', source]))
    def api(path, token=None, body=None, expected=200):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request('http://127.0.0.1:' + str(args.port) + path, headers=headers,
                    data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status, data = response.status, json.load(response)
        except urllib.error.HTTPError as error:
            status, data = error.code, json.load(error)
        if status != expected:
            raise AssertionError(f'{path.split("?")[0]} expected {expected}, got {status}')
        return data
    def login(name, password):
        return api('/api/session', body={'username': name, 'password': password})['token']
    def wait_run(token, rid):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            row = api('/api/runs/' + rid, token)
            if row['job']['status'] == 'done':
                return row
            if row['status'] == 'failed':
                raise AssertionError('Demo job failed')
            time.sleep(.4)
        raise TimeoutError('Demo job did not finish')
    def create_run(token, order):
        row = api('/api/runs', token, {'order_id': order, 'ticket': '申请退款'}, 202)
        return wait_run(token, row['id'])
    def sql(workspace, query, params=()):
        # This explicit QA maintenance container has credentials only for its
        # own new cluster. Never use a host DSN or any existing deployment.
        source = 'import os,json,psycopg\nfrom psycopg.rows import dict_row\n' + \
                 'with psycopg.connect(os.environ["DATABASE_URL"],row_factory=dict_row) as c:\n' + \
                 ' r=c.execute(' + repr(query) + ',' + repr(params) + ')\n' + \
                 ' rows=r.fetchall() if r.description else []\nprint(json.dumps(rows,default=str))'
        return json.loads(command(compose + ['run', '--rm', '--no-deps', '-T', workspace + '-migrate', 'python', '-c', source]))
    started = False
    try:
        for kind in ('container', 'volume', 'network'):
            check('fresh ' + kind, not command(['docker', kind, 'ls', '-q',
                  *(['-a'] if kind == 'container' else []), '--filter', 'label=com.docker.compose.project=' + args.project]).strip())
        started = True
        report['image_id'] = command(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image]).strip()
        command(compose + ['run', '--rm', '--no-deps', '-T', 'identity-init'])
        command(compose + ['up', '-d', '--wait', '--wait-timeout', '180'])
        manager = login('maintainer', (work / 'bootstrap.txt').read_text())
        password = secrets.token_urlsafe(24)
        accounts = {}
        for name, role, workspace in [('alice','operator','alpha'), ('anna','reviewer','alpha'),
                                      ('admina','admin','alpha'), ('bob','admin','beta')]:
            accounts[name] = api('/api/accounts', manager, {'username': name, 'password': password,
                                  'role': role, 'workspace': workspace}, 201)['id']
        tokens = {name: login(name, password) for name in accounts}
        a, reviewer, admin, b = (tokens[n] for n in ('alice','anna','admina','bob'))
        check('five active personal accounts include separate maintainer',
              len(api('/api/accounts', manager)['accounts']) == 5)
        api('/api/accounts', manager, {'username':'overflow','password':password,'role':'operator','workspace':'alpha'},409)
        check('sixth active account refused', True)
        for path in ['/api/orders','/api/knowledge?query=refund','/api/runs','/api/metrics','/api/alerts']:
            api(path, expected=401); api(path, manager, expected=403)
        check('anonymous and maintenance identity have no business access', True)
        for path in ['/api/accounts','/api/metrics','/api/alerts','/api/policy-releases','/api/access-audit']:
            api(path, a, expected=403)
        api('/api/accounts', admin, expected=403)
        check('operator and tenant-admin cannot manage platform accounts', True)
        sql('beta', "UPDATE rf_orders SET amount=54321 WHERE id='RF-1001'")
        sql('beta', "INSERT INTO rf_orders SELECT 'RF-9001',owner,amount,days,used,status FROM rf_orders WHERE id='RF-1001'")
        check('order lists never mix workspaces', all(r['id'] != 'RF-9001' for r in api('/api/orders', a))
              and any(r['id'] == 'RF-9001' for r in api('/api/orders', b)))
        api('/api/runs', a, {'order_id':'RF-9001','ticket':'申请退款'},404)
        check('cannot enqueue another workspace order', True)
        first = create_run(a, 'RF-1001'); second = create_run(b, 'RF-1001')
        report['runs'] = {'alpha_auto': first['id'], 'beta_auto': second['id']}
        check('same order ID yields two isolated real Worker refunds', first['status'] == second['status'] == 'refunded'
              and first['state']['order']['amount'] != second['state']['order']['amount'])
        check('individual creator recorded in business audit', first['created_by'] == 'account:' + accounts['alice']
              and first['audit'][0]['actor'] == 'account:' + accounts['alice'])
        api('/api/runs/' + second['id'], a, expected=404)
        api('/api/runs/' + first['id'], b, expected=404)
        for suffix, body in [('approval',{'approved':True,'reason':'cross-workspace'}),
                             ('review',{'resolution':'cross-workspace'}), ('retry',{})]:
            api('/api/runs/' + second['id'] + '/' + suffix, admin, body, 404)
        check('cross-workspace run read, approval, review and retry refused', True)
        for path in ['checkpoints','refunds','workspaces/beta/orders']:
            api('/api/' + path, a, expected=404)
        check('no raw checkpoint/refund or arbitrary workspace endpoint', True)
        pending = create_run(a, 'RF-1004'); pending_id = pending['id']
        report['runs']['alpha_pending'] = pending_id
        check('original checkpoint awaits personal approval', pending['status'] == 'awaiting_approval')
        api('/api/runs/' + pending_id + '/approval', a, {'approved':True,'reason':'forbidden'},403)
        api('/api/runs/' + pending_id + '/retry', reviewer, {},403)
        check('operator cannot approve, reviewer cannot retry', True)
        legacy = execute('alpha-api', '''import json,os,uuid
from storage import Store
from jobs import enqueue
rid=str(uuid.uuid4())
enqueue(Store(os.environ['DATABASE_URL']),rid,'申请退款','RF-1004','demo','demo','operator')
print(json.dumps(rid))''')
        check('legacy role-code record retained without invented account identity',
              wait_run(a,legacy)['status']=='awaiting_approval' and api('/api/runs/'+legacy,a)['created_by']=='operator')
        report['runs']['legacy_pending']=legacy
        # Recreate both workspace runtimes and gateway while original approval is
        # pending; identities, sessions, checkpoints and refund rows must persist.
        before = sql('alpha', 'SELECT count(*) AS n FROM checkpoints WHERE thread_id=%s', (pending_id,))
        command(compose + ['up','-d','--no-deps','--force-recreate','--wait','--wait-timeout','100',
                           'gateway','alpha-api','alpha-worker','beta-api','beta-worker'])
        check('personal session survives process recreation', api('/api/me', a)['id'] == accounts['alice'])
        check('original pending state and checkpoint preserved', api('/api/runs/' + pending_id,a)['status'] == 'awaiting_approval'
              and before == sql('alpha', 'SELECT count(*) AS n FROM checkpoints WHERE thread_id=%s', (pending_id,)))
        api('/api/runs/' + pending_id + '/approval', reviewer, {'approved':True,'reason':'personal approval fixture'},202)
        api('/api/runs/' + pending_id + '/approval', reviewer, {'approved':True,'reason':'duplicate'},409)
        approved = wait_run(a, pending_id)
        check('same pending run resumes with named reviewer', approved['status'] == 'refunded'
              and approved['approval']['actor'] == 'account:' + accounts['anna'])
        api('/api/runs/'+legacy+'/approval',reviewer,{'approved':True,'reason':'legacy approval resume'},202)
        check('legacy checkpoint resumes and existing refund stays unique',
              wait_run(a,legacy)['status']=='already_refunded'
              and sql('alpha',"SELECT run_id FROM rf_refunds WHERE order_id='RF-1004'")[0]['run_id']==pending_id)
        duplicate = create_run(a, 'RF-1001')
        check('refund replay keeps original ledger identity', duplicate['status'] == 'already_refunded'
              and sql('alpha', "SELECT run_id FROM rf_refunds WHERE order_id='RF-1001'")[0]['run_id'] == first['id'])
        check('checkpoint and refund IDs absent in other workspace',
              sql('beta', 'SELECT count(*) AS n FROM checkpoints WHERE thread_id=%s', (pending_id,))[0]['n'] == 0
              and sql('beta', 'SELECT count(*) AS n FROM rf_refunds WHERE run_id=%s', (pending_id,))[0]['n'] == 0)
        # Distinct valid policy bundles, using the existing maintenance release
        # path; action clauses and refund-rule compatibility remain unchanged.
        policy = '''import os,json,psycopg
from psycopg.rows import dict_row
from policy_releases import prepare,activate
p=prepare();p['id']='beta-workspace-release'
for d in p['documents']:d['title']='BETA-ONLY '+d['title']
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as c:
 head=c.execute('SELECT generation FROM rf_policy_head').fetchone()
 activate(c,payload=p,expected_generation=head['generation'],actor='qa-maintainer',reason='workspace policy canary',mode='demo')
print(json.dumps({'ok':True}))'''
        command(compose + ['run','--rm','--no-deps','-T','beta-migrate','python','-c',policy])
        knowledge_a = api('/api/knowledge?query=' + urllib.parse.quote('退款'), a)
        knowledge_b = api('/api/knowledge?query=' + urllib.parse.quote('退款'), b)
        check('knowledge retrieval uses only own published bundle', knowledge_a['results'] and knowledge_b['results']
              and 'BETA-ONLY' not in json.dumps(knowledge_a) and 'BETA-ONLY' in json.dumps(knowledge_b))
        mcp = '''import json
from mcp_gateway import call_tools
print(json.dumps(call_tools('RF-1001','demo',[('lookup_order',{}),('search_policy',{'query':'退款'})]),ensure_ascii=False))'''
        mcp_a = execute('alpha-worker', mcp); mcp_b = execute('beta-worker', mcp)
        check('real MCP subprocess uses own order and policy database', mcp_a[0]['amount'] != mcp_b[0]['amount']
              and 'BETA-ONLY' not in json.dumps(mcp_a) and 'BETA-ONLY' in json.dumps(mcp_b))
        # Obtain IP without credentials. Test both DNS and direct IP reachability.
        inspect = json.loads(command(['docker','inspect',args.project + '-beta-db-1']))[0]
        beta_ip = next(iter(inspect['NetworkSettings']['Networks'].values()))['IPAddress']
        denied = execute('alpha-worker', '''import socket,json
out=[]
for host in ['beta-db',HOST]:
 try:
  with socket.create_connection((host,5432),timeout=2):out.append(False)
 except OSError:out.append(True)
print(json.dumps(out))'''.replace('HOST', repr(beta_ip)))
        check('Worker cannot reach other PG cluster by DNS or IP', denied == [True,True])
        direct = execute('alpha-api', '''import httpx,json
r=httpx.get('http://127.0.0.1:8000/api/orders',headers={'Authorization':'Bearer '+TOKEN},trust_env=False)
s=httpx.get('http://127.0.0.1:8000/api/orders',headers={'X-API-Key':'admin'},trust_env=False)
print(json.dumps([r.status_code,s.status_code]))'''.replace('TOKEN', repr(b)))
        check('direct backend rejects other workspace session and shared-key bypass', direct == [403,401])
        # Rotate role, disable/reenable and reset; check previously issued tokens
        # against both public gateway and directly against a workspace backend.
        api('/api/accounts/' + accounts['alice'], manager, {'role':'reviewer'})
        api('/api/orders', a, expected=401)
        newer = login('alice', password)
        api('/api/runs', newer, {'order_id':'RF-1002','ticket':'申请退款'},403)
        check('role update revokes old session and new role immediately enforced', True)
        api('/api/accounts/' + accounts['alice'], manager, {'enabled':False})
        api('/api/orders', newer, expected=401)
        api('/api/session', body={'username':'alice','password':password},expected=401)
        api('/api/accounts/' + accounts['alice'], manager, {'enabled':True})
        api('/api/orders', newer, expected=401)
        check('disable stops login and reenable never resurrects old sessions', True)
        fresh = login('alice', password)
        changed = secrets.token_urlsafe(24)
        api('/api/accounts/' + accounts['alice'], manager, {'password':changed})
        api('/api/orders', fresh, expected=401)
        api('/api/session', body={'username':'alice','password':password},expected=401)
        fresh = login('alice', changed)
        api('/api/logout', fresh, {})
        api('/api/orders', fresh, expected=401)
        check('password reset and logout revoke server sessions', True)
        audit = api('/api/access-audit', admin)
        check('tenant admin audit scoped and contains role denials', all(r['workspace']=='alpha' for r in audit)
              and any(r['status']==403 for r in audit))
        global_audit = api('/api/access-audit', manager)
        check('audit excludes all test credentials and request text',
              all(value not in json.dumps(global_audit) for value in [password,changed,manager,b,'申请退款']))
        report['audit_sample'] = global_audit[:12]
        # Single-volume identity store persists account cap and revocations.
        check('all active accounts still capped at five after recreation',
              sum(r['enabled'] for r in api('/api/accounts',manager)['accounts']) == 5)
        command(compose + ['stop','gateway'])
        unavailable = execute('alpha-api', '''import httpx,json
r=httpx.get('http://127.0.0.1:8000/api/orders',headers={'Authorization':'Bearer '+TOKEN},timeout=10,trust_env=False)
print(json.dumps(r.status_code))'''.replace('TOKEN',repr(admin)))
        check('identity outage fails closed at backend', unavailable == 503)
        report['passed'] = True
    except BaseException as error:
        report['failure_type'] = type(error).__name__
        # Assertion messages contain only test names/status, never credential data.
        if isinstance(error, (AssertionError, TimeoutError)):
            report['failure'] = str(error)
        raise
    finally:
        if started:
            command(compose + ['stop'])
        report['stopped_volumes_preserved'] = True
        report['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks'])}))


if __name__ == '__main__':
    main()
