"""Restore a trusted backup to a NEW, isolated local Docker database only.

No existing database, container, network or volume is reused or deleted.
The database is stopped after verification; start it explicitly for a QA demo.
"""
import argparse
import json
import ipaddress
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.database_backup import BackupError, run, utc, verify_bundle, write_json


def target_names(project):
    if not re.fullmatch(r'resolveflow-restore-[a-z0-9][a-z0-9-]{0,30}', project):
        raise BackupError('A new resolveflow-restore-* project is required')
    return {'project': project, 'db': project+'-db-1', 'network': project+'_private',
            'volume': project+'_postgres-data'}


def ensure_unused(names):
    for kind, name in (('container', names['db']), ('network', names['network']), ('volume', names['volume'])):
        command = ['docker', kind, 'ls'] + (['-a'] if kind == 'container' else [])
        field = '{{.Names}}' if kind == 'container' else '{{.Name}}'
        if name in run(command+['--format', field]).decode().splitlines():
            raise BackupError('Restore target already exists')
    if run(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project='+names['project']]).strip():
        raise BackupError('Restore project already has containers')


def image_ids(manifest):
    images = {service: manifest['runtime'][service]['image_id'] for service in ('db', 'resolveflow', 'worker')}
    if images['resolveflow'] != images['worker']:
        raise BackupError('Backup runtime image mismatch')
    for value in images.values():
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
            raise BackupError('Immutable local image ID required')
        actual = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', value]).decode().strip()
        if actual != value:
            raise BackupError('Required local image is unavailable')
    return images


def unused_subnet():
    # Many preserved QA networks can exhaust Docker's default address pools.
    # Allocate a small isolated subnet without deleting any historical network.
    ids = run(['docker','network','ls','-q']).decode().split()
    used = []
    if ids:
        for line in run(['docker','network','inspect','--format','{{json .IPAM.Config}}',*ids]).decode().splitlines():
            for config in json.loads(line) or []:
                if config.get('Subnet'):
                    used.append(ipaddress.ip_network(config['Subnet']))
    for candidate in ipaddress.ip_network('10.240.0.0/12').subnets(new_prefix=24):
        if not any(candidate.overlaps(existing) for existing in used if existing.version == 4):
            return str(candidate)
    raise BackupError('No isolated QA subnet available')


def wait_database(name, timeout=90):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        state = json.loads(run(['docker', 'inspect', '--format',
            '{"status":{{json .State.Status}},"health":{{json .State.Health.Status}}}', name]))
        if state['status'] != 'running':
            raise BackupError('Isolated database exited')
        if state['health'] == 'healthy':
            return
        time.sleep(.5)
    raise BackupError('Isolated database startup timed out')


def validation_source():
    snapshot = (ROOT/'scripts/backup_snapshot.py').read_text(encoding='utf-8')
    validator = (ROOT/'scripts/restore_validation.py').read_text(encoding='utf-8')
    # Source transfer avoids rebuilding or modifying the version-matched image.
    return ('import sys,types\nm=types.ModuleType("scripts.backup_snapshot")\n'
            'sys.modules[m.__name__]=m\nexec('+repr(snapshot)+',m.__dict__)\n'
            'exec('+repr(validator)+',{"__name__":"__main__"})')


def restore_backup(bundle, project, report):
    names = target_names(project)
    report = Path(report).resolve()
    if report.exists():
        raise BackupError('Use a new report path')
    # Verify bytes and metadata before creating or writing any database.
    manifest = verify_bundle(bundle)
    if project == manifest['compose_project']:
        raise BackupError('Cannot restore onto the source project')
    ensure_unused(names)
    images = image_ids(manifest)
    work = ROOT/'work/restores'/project
    work.mkdir(parents=True, exist_ok=False, mode=0o700)
    env_file = work/'runtime.env'
    password = secrets.token_hex(24)
    with env_file.open('x', encoding='utf-8') as stream:
        stream.write('POSTGRES_USER=resolveflow\nPOSTGRES_DB=resolveflow\nPOSTGRES_PASSWORD='+password+'\n')
        stream.write('DATABASE_URL=postgresql://resolveflow:'+password+'@db:5432/resolveflow\n')
        stream.write('MODE=demo\nRETRIEVAL_MODE=bm25\nOPENAI_API_KEY=\n')
        for key in ('APP_API_KEY','REVIEWER_API_KEY','ADMIN_API_KEY'):
            stream.write(key+'='+secrets.token_hex(24)+'\n')
    os.chmod(env_file, 0o600)
    result = {'passed': False, 'started_at_utc': utc(), 'target': names,
              'archive_sha256': manifest['archive']['sha256'], 'images': images,
              'source_project': manifest['compose_project'], 'existing_resources_reused': False,
              'migration_executed': False, 'source_modified': False, 'resume_verified': False}
    created_db = False
    phase = 'create_target'
    try:
        label = 'com.docker.compose.project='+project
        resource_token = secrets.token_hex(16)
        result['subnet'] = unused_subnet()
        run(['docker','network','create','--internal','--subnet',result['subnet'],'--label',label,names['network']])
        run(['docker','volume','create','--label',label,'--label','resolveflow.restore-id='+resource_token,names['volume']])
        owner = run(['docker','volume','inspect','--format',
                     '{{index .Labels "resolveflow.restore-id"}}',names['volume']]).decode().strip()
        if owner != resource_token:
            raise BackupError('Volume was created by another operation')
        run(['docker','run','--detach','--pull=never','--name',names['db'],
             '--label',label,'--label','com.docker.compose.service=db',
             '--network',names['network'],'--network-alias','db','--env-file',str(env_file),
             '--mount','type=volume,source='+names['volume']+',target=/var/lib/postgresql/data',
             '--health-cmd','pg_isready -h 127.0.0.1 -U resolveflow -d resolveflow',
             '--health-interval','2s','--health-timeout','3s','--health-retries','30',images['db']])
        created_db = True
        wait_database(names['db'])
        phase = 'restore'
        empty = run(['docker','exec',names['db'],'psql','-U','resolveflow','-d','resolveflow','-Atc',
                     "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                     "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"]).decode().strip()
        if empty != '0':
            raise BackupError('New database is unexpectedly nonempty')
        # The target was just initialized from template1. No --clean, no --create,
        # no database name from the archive, and no application process is started.
        with (Path(bundle)/'database.dump').open('rb') as stream:
            run(['docker','exec','-i',names['db'],'pg_restore','-U','resolveflow','-d','resolveflow',
                 '--single-transaction','--exit-on-error','--no-owner','--no-privileges'], stdin=stream)
        phase = 'validate'
        # Pass the private manifest via stdin; never include credentials in arguments.
        payload = work/'validation-input.json'
        write_json(payload, manifest)
        with payload.open('rb') as stream:
            checked = json.loads(run(['docker','run','--rm','-i','--pull=never',
                '--network',names['network'],'--env-file',str(env_file),
                images['resolveflow'],'python','-c',validation_source()],stdin=stream))
        if not checked['passed']:
            raise BackupError('Restored database verification failed')
        result.update(passed=True, validation=checked)
    except BaseException as exc:
        result.update(failed_phase=phase, error_type=type(exc).__name__)
        raise
    finally:
        # Successful and failed copies remain available; neither is automatically
        # put into service or removed. Preserve all volume contents for inspection.
        if created_db:
            try:
                run(['docker','stop',names['db']], timeout=60)
                result['database_stopped'] = True
            except Exception:
                result['database_stopped'] = False
                result['passed'] = False
        result['completed_at_utc'] = utc()
        result['volume_preserved'] = True
        report.parent.mkdir(parents=True, exist_ok=True)
        write_json(report, result)
    if not result['passed']:
        raise BackupError('Restore did not finish cleanly')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--project', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = restore_backup(args.bundle, args.project, args.report)
        print(json.dumps({'passed': True, 'matched_tables': result['validation']['matched_tables'],
                          'project': args.project, 'database_stopped': True, 'resume_verified': False}))
        return 0
    except Exception as exc:
        print(json.dumps({'passed': False, 'error_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
