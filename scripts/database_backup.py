"""Create/verify private PostgreSQL custom-format backup bundles (stdlib host).

Usage: python scripts/database_backup.py create
       python scripts/database_backup.py verify work/backups/<backup-id>
No .env is read on the host; authenticated connections stay inside containers.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]


class BackupError(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def run(command, *, timeout=600, stdin=None, stdout=None):
    result = subprocess.run(command, cwd=ROOT, stdin=stdin, stdout=stdout or subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        # Docker/PG error output may contain private SQL or connection strings.
        raise BackupError('External command failed')
    return result.stdout


def container(project, service):
    ids = run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project='+project,
               '--filter', 'label=com.docker.compose.service='+service]).decode().split()
    if len(ids) != 1:
        raise BackupError('Exactly one running '+service+' container is required')
    # Never inspect Config.Env or print the full inspection result.
    return json.loads(run(['docker', 'inspect', '--format',
        '{"id":{{json .Id}},"image_id":{{json .Image}},"started_at":{{json .State.StartedAt}}}', ids[0]]))


def read_line(process, timeout):
    messages = queue.Queue()
    def receive():
        try:
            messages.put(process.stdout.readline())
        except Exception:
            messages.put(b'')
    threading.Thread(target=receive, daemon=True).start()
    try:
        line = messages.get(timeout=timeout)
    except queue.Empty:
        raise BackupError('Snapshot preparation timed out') from None
    if not line:
        raise BackupError('Snapshot preparation failed')
    return json.loads(line)


def verify_bundle(path, *, allow_partial=False):
    path = Path(path)
    if (path.name.endswith('.partial') and not allow_partial) or not path.is_dir():
        raise BackupError('Incomplete backup bundle')
    expected = {'database.dump', 'manifest.json', 'SHA256SUMS.json'}
    if {p.name for p in path.iterdir()} != expected or any(p.is_symlink() for p in path.iterdir()):
        raise BackupError('Unexpected bundle contents')
    sums = json.loads((path/'SHA256SUMS.json').read_text(encoding='utf-8'))
    if set(sums) != {'database.dump', 'manifest.json'}:
        raise BackupError('Invalid checksum inventory')
    for name in sums:
        if not re.fullmatch('[0-9a-f]{64}', str(sums[name])) or sha256(path/name) != sums[name]:
            raise BackupError('Checksum mismatch: '+name)
    manifest = json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format_version') != 1 or manifest.get('status') != 'complete':
        raise BackupError('Unsupported or incomplete manifest')
    if manifest['archive']['sha256'] != sums['database.dump'] or manifest['archive']['bytes'] != (path/'database.dump').stat().st_size:
        raise BackupError('Archive metadata mismatch')
    with (path/'database.dump').open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise BackupError('Not a PostgreSQL custom archive')
    return manifest


def create_backup(output, project='resolveflow', timeout=600):
    if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,62}', project):
        raise BackupError('Invalid Compose project name')
    if not 10 <= timeout <= 3600:
        raise BackupError('Timeout must be between 10 and 3600 seconds')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12]
    partial = output/(name+'.partial')
    final = output/name
    partial.mkdir(mode=0o700)
    phase = 'preflight'
    exporter = None
    try:
        started = utc()
        runtime = {service: container(project, service) for service in ('db', 'resolveflow', 'worker')}
        if runtime['resolveflow']['image_id'] != runtime['worker']['image_id']:
            raise BackupError('API and Worker images differ')
        base_commit = run(['git', 'rev-parse', 'HEAD']).decode().strip()
        dirty = bool(run(['git', 'status', '--porcelain', '--untracked-files=normal']).strip())
        db = runtime['db']['id']
        pg_dump_version = run(['docker', 'exec', db, 'pg_dump', '--version']).decode().strip()
        pg_restore_version = run(['docker', 'exec', db, 'pg_restore', '--version']).decode().strip()
        phase = 'snapshot'
        source = (ROOT/'scripts/backup_snapshot.py').read_text(encoding='utf-8')
        exporter = subprocess.Popen(['docker', 'exec', '-i', runtime['resolveflow']['id'],
            'python', '-u', '-c', source, str(timeout)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=ROOT)
        metadata = read_line(exporter, timeout)
        major = metadata['postgres_version_num'] // 10000
        for tool_version in (pg_dump_version, pg_restore_version):
            if int(re.search(r'PostgreSQL\) (\d+)', tool_version)[1]) != major:
                raise BackupError('PostgreSQL client/server major versions differ')
        phase = 'dump'
        archive = partial/'database.dump'
        with archive.open('xb') as stream:
            run(['docker', 'exec', db, 'pg_dump', '-U', 'resolveflow', '-d', metadata['database'],
                 '--format=custom', '--no-owner', '--no-privileges', '--lock-wait-timeout=5s',
                 '--snapshot='+metadata['snapshot_id']], timeout=timeout, stdout=stream)
            stream.flush()
            os.fsync(stream.fileno())
        exporter.stdin.write(b'dump-complete\n')
        exporter.stdin.flush()
        exporter.stdin.close()
        if exporter.wait(timeout=10):
            raise BackupError('Snapshot exporter failed')
        phase = 'archive_validation'
        # Full SQL rendering decodes compressed table blocks; --list alone does not.
        # Output is discarded INSIDE the DB container, never executed against a DB.
        with archive.open('rb') as stream:
            run(['docker', 'exec', '-i', db, 'pg_restore', '--exit-on-error',
                 '--file=/dev/null'], stdin=stream, timeout=timeout)
        if {s: container(project, s) for s in runtime} != runtime:
            raise BackupError('Containers changed during backup')
        phase = 'publish'
        manifest = {
            'format_version': 1, 'status': 'complete', 'started_at_utc': started,
            'completed_at_utc': utc(), 'compose_project': project,
            'runtime': runtime, 'source_checkout': {'base_commit': base_commit, 'dirty': dirty},
            'tool_sha256': {p: sha256(ROOT/'scripts'/p) for p in ('database_backup.py', 'backup_snapshot.py')},
            'pg_dump_version': pg_dump_version, 'pg_restore_version': pg_restore_version,
            'snapshot': metadata,
            'archive': {'format': 'PostgreSQL custom', 'file': 'database.dump',
                        'bytes': archive.stat().st_size, 'sha256': sha256(archive),
                        'full_decode_passed': True},
            'scope': 'One complete database; no cluster roles, ACLs, external model files, images or secrets.',
            'restore_verified': False,
        }
        write_json(partial/'manifest.json', manifest)
        write_json(partial/'SHA256SUMS.json', {n: sha256(partial/n) for n in ('database.dump', 'manifest.json')})
        verify_bundle(partial, allow_partial=True)
        partial.rename(final)
        return final, manifest
    except BaseException as exc:
        # Incomplete files are retained for investigation, never reused or pruned.
        if partial.exists():
            write_json(partial/'failure.json', {'status': 'failed', 'phase': phase,
                       'failed_at_utc': utc(), 'error_type': type(exc).__name__})
        raise BackupError('Backup failed at '+phase+'; incomplete bundle retained') from None
    finally:
        if exporter is not None:
            if exporter.stdin and not exporter.stdin.closed:
                exporter.stdin.close()  # EOF makes remote code release snapshot and lock.
            try:
                exporter.wait(timeout=10)
            except subprocess.TimeoutExpired:
                exporter.kill()
                exporter.wait()
            exporter.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create')
    create.add_argument('--output', type=Path, default=ROOT/'work/backups')
    create.add_argument('--project', default='resolveflow')
    create.add_argument('--timeout', type=int, default=600)
    verify = commands.add_parser('verify')
    verify.add_argument('bundle', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'create':
            path, manifest = create_backup(args.output, args.project, args.timeout)
        else:
            path = args.bundle
            manifest = verify_bundle(path)
        print(json.dumps({'passed': True, 'bundle': str(path),
                          'archive_bytes': manifest['archive']['bytes'],
                          'archive_sha256': manifest['archive']['sha256'],
                          'restore_verified': False}))
        return 0
    except Exception as exc:
        # Deliberately omit exception text: parsing/IO errors can contain private data.
        print(json.dumps({'passed': False, 'error_type': type(exc).__name__,
                          'action': 'Inspect private .partial/failure.json if present; do not restore incomplete bundles.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
