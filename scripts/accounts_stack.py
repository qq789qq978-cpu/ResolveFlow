"""Generate a new private two-workspace demo stack; never reads .env or starts it.

Each workspace has a distinct PG cluster, passwords, private data network and
Worker. Only the personal gateway is bound to loopback. HTTPS is step 5.3.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def configuration(image, port, bootstrap):
    services, networks, volumes, routes = {}, {'entry': {}}, {'identity-data': {}}, {}
    health = {'test': ['CMD', 'python', '-c',
              "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"],
              'interval': '3s', 'timeout': '3s', 'retries': 40}
    hardened = {'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
                'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}
    for workspace in ('alpha', 'beta'):
        db, api, worker = (workspace + '-' + s for s in ('db', 'api', 'worker'))
        front, data = workspace + '-front', workspace + '-data'
        # Allocate nonoverlapping subnets even on hosts retaining many QA networks.
        networks[front] = {'internal': True}
        networks[data] = {'internal': True}
        passwords = {name: secrets.token_urlsafe(32) for name in ('owner', 'migrator', 'app', 'readonly')}
        service_key = secrets.token_urlsafe(32)
        routes[workspace] = {'url': 'http://' + api + ':8000', 'service_key': service_key}
        volumes[db] = {}; volumes[worker] = {}
        services[db] = {'image': 'postgres:17', 'environment': {'POSTGRES_USER': 'resolveflow',
                'POSTGRES_DB': 'resolveflow', 'POSTGRES_PASSWORD': passwords['owner']},
                'networks': [data], 'volumes': [db + ':/var/lib/postgresql/data'],
                'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -h 127.0.0.1 -U resolveflow -d resolveflow'],
                                'interval': '3s', 'timeout': '3s', 'retries': 40}}
        def dsn(role):
            return 'postgresql://' + ('resolveflow' if role == 'owner' else 'rf_' + role) + ':' + passwords[role] + '@' + db + '/resolveflow'
        services[workspace + '-roles'] = {'image': image, 'command': ['python', 'db_roles.py', 'provision'],
            'environment': {'DATABASE_URL': dsn('owner'), **{'RF_' + k.upper() + '_PASSWORD': passwords[k]
                            for k in ('migrator', 'app', 'readonly')}},
            'networks': [data], 'depends_on': {db: {'condition': 'service_healthy'}}, 'healthcheck': {'disable': True}}
        services[workspace + '-migrate'] = {'image': image, 'command': ['python', 'db_migrate.py', 'prepare'],
            'environment': {'DATABASE_URL': dsn('migrator'), 'RF_ENFORCE_DB_ROLES': '1', 'MODE': 'demo', 'RETRIEVAL_MODE': 'bm25'},
            'networks': [data], 'depends_on': {workspace + '-roles': {'condition': 'service_completed_successfully'}},
            'healthcheck': {'disable': True}}
        env = {'DATABASE_URL': dsn('app'), 'READONLY_DATABASE_URL': dsn('readonly'),
               'RF_ENFORCE_DB_ROLES': '1', 'MODE': 'demo', 'RETRIEVAL_MODE': 'bm25', 'MODEL_NAME': 'demo',
               'OPENAI_API_KEY': '', 'RF_WORKSPACE_ID': workspace, 'DATA_DIR': '/data'}
        services[api] = {**hardened, 'image': image,
            'environment': {**env, 'RF_AUTH_MODE': 'personal', 'RF_IDENTITY_URL': 'http://gateway:8000',
                            'RF_IDENTITY_SERVICE_KEY': service_key},
            'networks': [front, data], 'healthcheck': health,
            'depends_on': {workspace + '-migrate': {'condition': 'service_completed_successfully'}}}
        services[worker] = {**hardened, 'image': image, 'init': True, 'command': ['python', 'worker.py'],
            'environment': env, 'networks': [data], 'volumes': [worker + ':/data'],
            'depends_on': {api: {'condition': 'service_healthy'}},
            'healthcheck': {'test': ['CMD', 'python', 'worker_health.py'], 'interval': '5s', 'timeout': '3s', 'retries': 30}}
    identity_env = {'RF_IDENTITY_DB': '/data/identity.sqlite3', 'RF_WORKSPACES': json.dumps(routes)}
    services['identity-init'] = {**hardened, 'image': image, 'profiles': ['maintenance'],
        'command': ['python', 'identity.py', 'init'], 'environment': {**identity_env, 'RF_BOOTSTRAP_PASSWORD': bootstrap},
        'network_mode': 'none', 'volumes': ['identity-data:/data'], 'healthcheck': {'disable': True}}
    services['gateway'] = {**hardened, 'image': image,
        'command': ['uvicorn', 'identity_gateway:app', '--host', '0.0.0.0', '--port', '8000', '--no-access-log'],
        'environment': identity_env, 'volumes': ['identity-data:/data'],
        'networks': ['entry', 'alpha-front', 'beta-front'], 'ports': [f'127.0.0.1:{port}:8000'], 'healthcheck': health}
    return {'services': services, 'networks': networks, 'volumes': volumes}


def generate(project, image, port):
    if not re.fullmatch(r'resolveflow-accounts-[a-z0-9-]{1,30}', project) or not 1024 <= port <= 65535:
        raise ValueError('Use a fresh resolveflow-accounts-* project and an unprivileged port')
    work = ROOT / 'work' / project
    # Refuse preserved Docker resources even if a private configuration directory
    # was moved away. Reusing a named volume must be an explicit maintenance step.
    for kind in ('container', 'volume', 'network'):
        if subprocess.check_output(['docker', kind, 'ls', '-q',
                *(['-a'] if kind == 'container' else []), '--filter',
                'label=com.docker.compose.project=' + project]).strip():
            raise ValueError('Project already owns Docker resources; choose a new name')
    work.mkdir(parents=True, exist_ok=False)
    os.chmod(work, 0o700)
    bootstrap = secrets.token_urlsafe(24)
    config = configuration(image, port, bootstrap)
    from scripts.database_restore import unused_subnet
    used = []
    for item in config['networks'].values():
        subnet = unused_subnet(excluded=used); used.append(subnet)
        item['ipam'] = {'config': [{'subnet': subnet}]}
    for name, content in [('compose.json', json.dumps(config, indent=2)),
                          ('empty.env', ''), ('bootstrap.txt', bootstrap)]:
        file = work / name
        fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content)
    return work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--port', type=int, default=8052)
    args = parser.parse_args()
    work = generate(args.project, args.image, args.port)
    print(json.dumps({'private_configuration': str(work), 'started': False, 'credentials_printed': False}))


if __name__ == '__main__':
    # Direct script execution also resolves project-owned scripts imports.
    import sys
    sys.path.insert(0, str(ROOT))
    main()
