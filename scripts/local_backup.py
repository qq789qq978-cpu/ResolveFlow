"""Quiesced identity + two-workspace backup; restore only to newly generated volumes.

Trusted local backups only. SHA256 detects accidental corruption, not malicious
replacement. This tool never deletes backups/volumes and never reads .env.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.local_stack import generate, protect

FILES = {'identity.sqlite3', 'alpha.dump', 'beta.dump'}


def run(argv, data=None, timeout=300):
    r = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
    if r.returncode:
        # Raw stderr may include SQL/credentials; never echo it or CLI arguments.
        raise RuntimeError('Maintenance command failed (exit '+str(r.returncode)+')')
    return r.stdout


class Stack:
    def __init__(self, work):
        self.work = Path(work).resolve()
        self.config = json.loads((self.work/'compose.json').read_text())
        self.meta = self.config['x-resolveflow-local']
        self.project = self.meta['project']
        if not re.fullmatch(r'resolveflow-accounts-[a-z0-9-]{1,30}', self.project):
            raise ValueError('Invalid project')
        self.argv = ['docker', 'compose', '--env-file', str(self.work/'empty.env'), '-p', self.project,
                     '-f', str(self.work/'compose.json')]

    def cmd(self, *args, data=None):
        return run(self.argv+list(args), data)

    def helper(self, service, action, payload=None):
        return json.loads(self.cmd('run','--rm','--no-deps','-T',service,
            'python','scripts/local_backup_worker.py',action,
            data=json.dumps(payload).encode() if payload is not None else None))


@contextmanager
def exclusive(work):
    path = Path(work)/'maintenance.lock'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
        yield
    finally:
        path.unlink()


def verify(bundle):
    bundle = Path(bundle)
    manifest = json.loads((bundle/'manifest.json').read_text())
    if manifest['format'] != 1 or set(manifest['files']) != FILES or set(manifest['workspaces']) != {'alpha','beta'}:
        raise ValueError('Unsupported bundle')
    for name, expected in manifest['files'].items():
        p = bundle/name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError('Backup checksum mismatch')
    return manifest


def runtime_image(stack):
    services = ('gateway','alpha-api','beta-api','alpha-worker','beta-worker')
    ids = stack.cmd('ps','-a','-q',*services).decode().split()
    if len(ids) != len(services):
        raise ValueError('Missing deployed runtime container')
    containers = json.loads(run(['docker','inspect',*ids]))
    images = {item['Image'] for item in containers}
    configured = run(['docker','image','inspect','--format','{{.Id}}',stack.meta['image']]).decode().strip()
    if images != {configured}:
        raise ValueError('Mixed runtime images or moved image tag; preserve the deployed image first')
    return configured


def create(work, bundle):
    stack = Stack(work); bundle = Path(bundle)
    bundle.mkdir(parents=True, exist_ok=False); protect(bundle)
    with exclusive(stack.work):
        running = stack.cmd('ps','--services','--status','running').decode().split()
        if not {'alpha-db','beta-db'} <= set(running):
            raise ValueError('Both databases must be running')
        paused = [s for s in running if s not in ('alpha-db','beta-db')]
        resume_ids = stack.cmd('ps','-q',*paused).decode().split() if paused else []
        manifest = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'source_project': stack.project, 'workspaces': {}, 'files': {},
                    'quiesced_services': paused, 'image': stack.meta['image']}
        manifest['image_id'] = runtime_image(stack)
        try:
            if paused: stack.cmd('stop','-t','30',*paused)
            identity = stack.helper('identity-init','identity-export')
            (bundle/'identity.sqlite3').write_bytes(base64.b64decode(identity['data']))
            manifest['identity'] = identity['snapshot']
            for workspace in ('alpha','beta'):
                manifest['workspaces'][workspace] = stack.helper(workspace+'-migrate','business-snapshot')
                dump = stack.cmd('exec','-T',workspace+'-db','pg_dump','-U','resolveflow','-d','resolveflow','-Fc')
                (bundle/(workspace+'.dump')).write_bytes(dump)
        finally:
            # docker start does not recursively start dependencies that were
            # already stopped by the operator before this maintenance window.
            if resume_ids: run(['docker','start',*resume_ids])
        for name in FILES:
            os.chmod(bundle/name, 0o600)
            manifest['files'][name] = hashlib.sha256((bundle/name).read_bytes()).hexdigest()
        # Completion marker is written only after all components and service restart succeed.
        (bundle/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        verify(bundle)
    return manifest


def restore(bundle, project, port):
    bundle = Path(bundle); manifest = verify(bundle)
    actual_id = run(['docker','image','inspect','--format','{{.Id}}',manifest['image_id']]).decode().strip()
    if actual_id != manifest['image_id']:
        raise ValueError('Backup image unavailable')
    # generate refuses existing configuration, containers, networks AND volumes.
    work = generate(project, manifest['image_id'], port)
    stack = Stack(work); result = {'passed': False, 'project': project, 'workspaces': {}}
    with exclusive(work):
        try:
            stack.cmd('up','-d','--wait','alpha-db','beta-db')
            for workspace in ('alpha','beta'):
                stack.cmd('run','--rm','--no-deps','-T',workspace+'-roles')
                # --clean applies exclusively to the newly allocated cluster.
                stack.cmd('exec','-T',workspace+'-db','pg_restore','--clean','--if-exists',
                          '--exit-on-error','--single-transaction','-U','resolveflow','-d','resolveflow',
                          data=(bundle/(workspace+'.dump')).read_bytes())
                result['workspaces'][workspace] = stack.helper(workspace+'-migrate','business-verify',
                                                               {'snapshot': manifest['workspaces'][workspace]})
            result['identity'] = stack.helper('identity-init','identity-import',
                {'data': base64.b64encode((bundle/'identity.sqlite3').read_bytes()).decode(), 'snapshot':manifest['identity']})
            # A restored installation must never depend on fresh-install jobs.
            # Compose start follows dependencies even when those jobs were only
            # executed with run --rm. Keep maintenance commands explicit.
            for workspace in ('alpha','beta'):
                for suffix in ('roles','migrate'):
                    stack.config['services'][workspace+'-'+suffix]['profiles'] = ['maintenance']
                stack.config['services'][workspace+'-api']['depends_on'] = {
                    workspace+'-db': {'condition':'service_healthy'}}
            stack.config['x-resolveflow-local']['restored'] = True
            (work/'compose.json').write_text(json.dumps(stack.config,indent=2),encoding='utf-8')
            # Start only runtimes; do not replay initialization/migrations on restored data.
            stack.cmd('up','-d','--no-deps','--wait','--wait-timeout','180',
                'gateway','alpha-api','beta-api','alpha-worker','beta-worker','alpha-monitor','beta-monitor')
            result['passed'] = True
        except BaseException:
            stack.cmd('stop')
            raise
        finally:
            (work/'restore-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return work, result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='action',required=True)
    c = subs.add_parser('create'); c.add_argument('--work',type=Path,required=True); c.add_argument('--bundle',type=Path,required=True)
    v = subs.add_parser('verify'); v.add_argument('--bundle',type=Path,required=True)
    r = subs.add_parser('restore'); r.add_argument('--bundle',type=Path,required=True); r.add_argument('--project',required=True); r.add_argument('--port',type=int,default=8054)
    args = p.parse_args()
    try:
        if args.action == 'create': create(args.work,args.bundle)
        elif args.action == 'verify': verify(args.bundle)
        else: restore(args.bundle,args.project,args.port)
        print(json.dumps({'passed':True,'action':args.action})); return 0
    except Exception as error:
        print(json.dumps({'passed':False,'error_type':type(error).__name__})); return 1


if __name__ == '__main__': raise SystemExit(main())
