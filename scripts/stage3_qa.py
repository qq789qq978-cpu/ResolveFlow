"""Fresh demo installation, recreation, backup and independent approval recovery.

Uses only checked-in files, a built application image and Docker. Host Python
needs only its standard library. Never loads .env or touches the main project.
All databases/volumes are retained; use a new project and report for each run.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.database_backup import create_backup, utc
from scripts.database_restore import unused_subnet
from scripts.restore_qa import api, wait_run, python_in, exercise
from scripts.migration_qa import SNAPSHOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8025)
    args = parser.parse_args()
    if not re.fullmatch(r'resolveflow-qa-[a-z0-9-]{1,35}', args.project) or args.report.exists():
        parser.error('Use a fresh resolveflow-qa-* project and report')
    work = ROOT / 'work' / args.project
    work.mkdir(parents=True, exist_ok=False)
    empty = work / 'empty.env'
    empty.write_text('')
    first = unused_subnet()
    second = unused_subnet(excluded=[first])
    override = work / 'override.json'
    override.write_text(json.dumps({'networks': {
        'default': {'internal': True, 'ipam': {'config': [{'subnet': first}]}},
        'embedding-private': {'ipam': {'config': [{'subnet': second}]}}}}))
    env = {**os.environ, 'RESOLVEFLOW_IMAGE': args.image, 'MODE': 'demo',
           'RETRIEVAL_MODE': 'bm25', 'COMPOSE_PROFILES': '', 'OPENAI_API_KEY': '',
           'POSTGRES_IMAGE': 'postgres:17', 'POSTGRES_PASSWORD': 'qa39-admin-password',
           'RF_MIGRATOR_PASSWORD': 'qa39-migrator-password-123456',
           'RF_APP_PASSWORD': 'qa39-app-password-1234567890',
           'RF_READONLY_PASSWORD': 'qa39-readonly-password-123456',
           'APP_API_KEY': 'qa39-operator', 'REVIEWER_API_KEY': 'qa39-reviewer',
           'ADMIN_API_KEY': 'qa39-admin', 'APP_PORT': str(args.port)}
    # Explicit defaults keep host tuning out of the release acceptance fixture.
    for key in list(env):
        if key.startswith(('RF_ALERT_', 'RF_DB_', 'RF_TASK_')) or key == 'RF_EXPECTED_WORKERS':
            del env[key]
    compose = ['docker', 'compose', '--env-file', str(empty), '-p', args.project,
               '-f', str(ROOT / 'compose.yaml'), '-f', str(override)]
    report = {'passed': False, 'started_utc': utc(), 'project': args.project,
              'model_api_calls': 0, 'checks': [], 'main_modified': False}

    def command(argv):
        result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=300)
        if result.returncode:
            (work / 'last-error.log').write_bytes(result.stdout + result.stderr)
            raise RuntimeError('Stage-three QA command failed; inspect private work log')
        return result.stdout.decode('utf-8')

    def check(name, condition):
        report['checks'].append({'check': name, 'passed': bool(condition)})
        if not condition:
            raise AssertionError(name)

    def identities():
        rows = {}
        for service in ('db', 'resolveflow', 'worker', 'monitor'):
            template = '{"id":{{json .Id}},"image":{{json .Image}},"mounts":{{json .Mounts}}}'
            row = json.loads(command(['docker', 'inspect', '--format', template,
                                      args.project + '-' + service + '-1']))
            row['volumes'] = {m['Destination']: m['Name'] for m in row.pop('mounts') if m['Type'] == 'volume'}
            rows[service] = row
        return rows

    started = False
    try:
        check('fresh containers and volumes', not any(command(['docker', kind, 'ls', '-q',
              *(['-a'] if kind == 'container' else []), '--filter',
              'label=com.docker.compose.project=' + args.project]).strip() for kind in ('container', 'volume', 'network')))
        started = True
        report['image_id'] = command(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image]).strip()
        command(compose + ['up', '--no-build', '-d', '--wait', '--wait-timeout', '180'])
        migration = json.loads(command(compose + ['logs', '--no-log-prefix', 'migrate']))
        report['migration'] = migration
        check('fresh install explicitly seeds demo', migration['action'] == 'installed' and migration['demo_seeded'])
        report['permissions'] = json.loads(command(compose + ['run', '--rm', '--no-deps', '-T',
            'db-roles', 'python', '-c', (ROOT / 'scripts/roles_probe.py').read_text()]))
        check('restricted roles reject forbidden operations', len(report['permissions']['denials']) == 44)
        for order, expected in [('RF-1001', 'refunded'), ('RF-1002', 'auto_rejected'),
                                ('RF-1004', 'awaiting_approval')]:
            rid = api(args.project, '/runs', {'order_id': order, 'ticket': '申请退款'}, expected=202)['id']
            row = wait_run(args.project, rid)
            check(order + ' routing', row['status'] == expected)
        pending = row
        check('operator cannot approve', api(args.project, '/runs/' + rid + '/approval',
              {'approved': True, 'reason': 'Unauthorized fixture'}, expected=403) is None)
        check('admin can observe healthy installation', not api(args.project, '/alerts', role='admin')['alerts'])
        before = python_in(args.project, SNAPSHOT)
        old = identities()
        command(compose + ['stop'])
        # Existing volumes and networks are reused, all service containers replaced.
        command(compose + ['up', '--no-build', '--force-recreate', '-d', '--wait', '--wait-timeout', '180'])
        after = python_in(args.project, SNAPSHOT)
        new = identities()
        report['recreation'] = {'before': before, 'after': after, 'old': old, 'new': new}
        check('all non-heartbeat non-version tables preserved', before == after)
        check('four service containers replaced, volumes preserved', all(old[s]['id'] != new[s]['id'] and
              old[s]['volumes'] == new[s]['volumes'] for s in old))
        check('pending checkpoint unchanged', api(args.project, '/runs/' + rid) == pending)
        route = '/runs/' + rid + '/approval'
        body = {'approved': False, 'reason': 'Independent recreation acceptance'}
        api(args.project, route, body, 'reviewer', 202)
        check('duplicate approval rejected', api(args.project, route, body, 'reviewer', 409) is None)
        check('original pending work resumes', wait_run(args.project, rid)['status'] == 'rejected')
        duplicate = api(args.project, '/runs', {'order_id': 'RF-1001', 'ticket': '申请退款'}, expected=202)['id']
        check('existing refund remains idempotent', wait_run(args.project, duplicate)['status'] == 'already_refunded')
        bundle, manifest = create_backup(work / 'backups', args.project)
        report['backup'] = {'private_path': str(bundle.relative_to(ROOT)), 'archive': manifest['archive'],
                            'table_count': len(manifest['snapshot']['tables'])}
        command(compose + ['stop'])
        check('backup source offline during recovery', not command(['docker', 'ps', '-q', '--filter',
              'label=com.docker.compose.project=' + args.project]).strip())
        restore_prefix = args.project.replace('resolveflow-qa-', 'resolveflow-restore-', 1)
        restored = exercise(bundle, restore_prefix, work / 'restore.json')
        report['restore'] = restored
        check('independent restore, approval resume and refund uniqueness', restored['passed'] and restored['resume_verified'])
        report['passed'] = True
    finally:
        if started:
            command(compose + ['stop'])
        report['stopped_volume_preserved'] = True
        report['finished_utc'] = utc()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks'])}))


if __name__ == '__main__':
    main()
