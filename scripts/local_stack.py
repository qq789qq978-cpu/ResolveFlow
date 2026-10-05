"""Generate a loopback-only personal demo deployment, with per-service secret files."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.accounts_stack import generate as accounts_generate
from secret_entrypoint import ALLOWED


def protect(path):
    """Private host directory; children inherit the restricted Windows ACL."""
    if os.name == 'nt':
        account = subprocess.check_output(['whoami'], text=True).strip()
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', account + ':(OI)(CI)F'],
                       check=True, capture_output=True)
    else:
        os.chmod(path, 0o700)


def private_file(path, content):
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as f:
        f.write(content)


def generate(project, image, port, *, orders=False, capacity=False):
    work = accounts_generate(project, image, port)
    protect(work)
    config = json.loads((work/'compose.json').read_text())
    config['x-resolveflow-local'] = {'version': 1, 'project': project, 'image': image, 'port': port}
    for workspace in ('alpha', 'beta'):
        env = config['services'][workspace+'-worker']['environment']
        config['services'][workspace+'-monitor'] = {
            'image': image, 'command': ['python', 'monitor.py'],
            'environment': {'READONLY_DATABASE_URL': env['READONLY_DATABASE_URL'],
                            'RF_ALERT_STARTUP_GRACE_SECONDS': '0'},
            'networks': [workspace+'-data'],
            'depends_on': {workspace+'-api': {'condition': 'service_healthy'}},
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}},
            'healthcheck': {'test': ['CMD','python','-c',
                "import json,time;from pathlib import Path;d=json.loads(Path('/tmp/resolveflow-monitor-health.json').read_text());assert time.time()-d['time']<20 and d['available']"],
                'interval': '5s', 'timeout': '3s', 'retries': 20}}
    if orders:
        from scripts.order_source_stack import configure
        configure(config,work,image)
    if capacity:
        from copy import deepcopy
        config['x-resolveflow-local']['capacity'] = True
        for name in ('gateway','identity-init','alpha-api','beta-api','alpha-worker','beta-worker'):
            config['services'][name]['environment']['RF_CAPACITY_ENABLED'] = '1'
        for w,slots in (('alpha',2),('beta',1)):
            config['services'][w+'-worker']['environment']['RF_EXECUTION_SLOTS'] = str(slots)
            for suffix in ('api','monitor'):
                config['services'][w+'-'+suffix]['environment']['RF_EXPECTED_WORKERS'] = str(slots)
        extra = deepcopy(config['services']['alpha-worker'])
        extra['volumes'] = ['alpha-extra-worker:/data']
        config['services']['alpha-extra-worker'] = extra
        config['volumes']['alpha-extra-worker'] = {}
    secret_dir = work/'secrets'; secret_dir.mkdir()
    config['secrets'] = {}
    for name, service in config['services'].items():
        env = service.get('environment', {})
        if name.endswith('-db'):
            key = name+'-password'
            private_file(secret_dir/key, env.pop('POSTGRES_PASSWORD'))
            # Files bind-mounted by Compose must be readable to the container UID.
            # The enclosing host directory remains private (0700 / Windows ACL).
            os.chmod(secret_dir/key, 0o644)
            config['secrets'][key] = {'file': str(secret_dir/key)}
            service['secrets'] = [{'source': key, 'target': 'pg-password'}]
            env['POSTGRES_PASSWORD_FILE'] = '/run/secrets/pg-password'
            continue
        values = {k: env.pop(k) for k in list(env) if k in ALLOWED}
        private_file(secret_dir/(name+'.json'), json.dumps(values))
        os.chmod(secret_dir/(name+'.json'), 0o644)
        config['secrets'][name] = {'file': str(secret_dir/(name+'.json'))}
        service['secrets'] = [{'source': name, 'target': 'runtime.json'}]
        service['entrypoint'] = ['python', 'secret_entrypoint.py']
        if name.endswith('-api'):
            service['command'] = ['uvicorn','operations:app','--host','0.0.0.0','--port','8000','--workers','1','--no-access-log']
        if name.endswith('-worker'):
            service['healthcheck']['test'] = ['CMD', 'python', 'secret_entrypoint.py', 'python', 'worker_health.py']
    (work/'compose.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    return work


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', required=True); p.add_argument('--image', required=True)
    p.add_argument('--port', type=int, default=8053)
    p.add_argument('--orders',action='store_true',help='Enable private synthetic sources and admin synchronization')
    p.add_argument('--capacity',action='store_true',help='Enable deployment quota, rate limits and 2+1 execution slots')
    args = p.parse_args()
    work = generate(args.project, args.image, args.port,orders=args.orders,capacity=args.capacity)
    print(json.dumps({'private_configuration': str(work), 'started': False}))


if __name__ == '__main__': main()
