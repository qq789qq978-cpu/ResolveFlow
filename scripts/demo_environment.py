"""Start a fresh screenshot demo, or stop it while retaining all volumes.

Private generated credentials live only in ignored work/<project>/demo-env.json.
Never reads .env or reuses an existing project for a fresh demonstration.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.database_restore import unused_subnet


def command(argv, env=None):
    result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=240)
    if result.returncode:
        # Do not emit configuration values or raw container output.
        raise RuntimeError('Command failed: ' + ' '.join(argv[:3]))
    return result.stdout.decode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop'])
    parser.add_argument('--project', required=True)
    parser.add_argument('--port', type=int, default=8026)
    parser.add_argument('--image', default='resolveflow:step39-ci-fix')
    args = parser.parse_args()
    if not re.fullmatch(r'resolveflow-qa-demo-[a-z0-9-]+', args.project):
        parser.error('Use a dedicated resolveflow-qa-demo-* project')
    if not 1024 <= args.port <= 65535 or args.port == 8003:
        parser.error('Use an unoccupied non-main port')
    work = ROOT / 'work' / args.project
    if args.action == 'start':
        # Names also catch resources left without Compose labels.
        for resource in ('volume', 'network'):
            names = command(['docker', resource, 'ls', '--format', '{{.Name}}']).splitlines()
            if any(name.startswith(args.project + '_') for name in names):
                parser.error('Existing named resources must be retained; choose a new project')
        for resource, listing in [('ps', ['-aq']), ('volume', ['ls', '-q']), ('network', ['ls', '-q'])]:
            if command(['docker', resource, *listing, '--filter', 'label=com.docker.compose.project=' + args.project]).strip():
                parser.error('Existing project resources must be retained; choose a new project')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', args.port))
        work.mkdir(parents=True, exist_ok=False)
        (work / 'empty.env').write_text('', encoding='utf-8')
        first = unused_subnet()
        second = unused_subnet(excluded=[first])
        (work / 'network.json').write_text(json.dumps({'networks': {
            'default': {'ipam': {'config': [{'subnet': first}]}},
            'embedding-private': {'ipam': {'config': [{'subnet': second}]}}
        }}), encoding='utf-8')
        values = {name: secrets.token_hex(24) for name in (
            'POSTGRES_PASSWORD', 'RF_MIGRATOR_PASSWORD', 'RF_APP_PASSWORD',
            'RF_READONLY_PASSWORD', 'APP_API_KEY', 'REVIEWER_API_KEY', 'ADMIN_API_KEY')}
        values.update(RESOLVEFLOW_IMAGE=args.image, POSTGRES_IMAGE='postgres:17', MODE='demo', RETRIEVAL_MODE='bm25',
                      COMPOSE_PROFILES='', OPENAI_API_KEY='', APP_PORT=str(args.port))
        (work / 'demo-env.json').write_text(json.dumps(values), encoding='utf-8')
    values = json.loads((work / 'demo-env.json').read_text(encoding='utf-8'))
    # Remove inherited application overrides to keep this demo deterministic.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('RF_', 'OPENAI_', 'COMPOSE_', 'MODEL_', 'RETRIEVAL_', 'SEMANTIC_'))}
    env.update(values)
    compose = ['docker', 'compose', '--env-file', str(work / 'empty.env'), '-p', args.project,
               '-f', str(ROOT / 'compose.yaml'), '-f', str(work / 'network.json')]
    operation = ['up', '--no-build', '-d', '--wait', '--wait-timeout', '180'] if args.action == 'start' else ['stop']
    try:
        command(compose + operation, env)
    except Exception:
        if args.action == 'start':
            command(compose + ['stop'], env)
        raise
    print(json.dumps({'action': args.action, 'project': args.project, 'port': values['APP_PORT'],
                      'mode': 'demo', 'retained_volumes': True}))


if __name__ == '__main__':
    main()
