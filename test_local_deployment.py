import hashlib
import json
from pathlib import Path
import sqlite3
import pytest

from secret_entrypoint import load
from scripts.local_backup import FILES, exclusive, verify
from scripts.local_backup_worker import identity_snapshot
from scripts.local_probe import validate_url


@pytest.mark.parametrize('values', [{'PATH':'/tmp'}, {'DATABASE_URL':3}, [], {'RF_WORKSPACES':{}}])
def test_secret_loader_rejects_env_injection(tmp_path, values):
    p=tmp_path/'secret.json';p.write_text(json.dumps(values))
    with pytest.raises(ValueError): load(p)


def test_load_secret_only_in_child_environment(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','old')
    p=tmp_path/'secret.json';p.write_text(json.dumps({'DATABASE_URL':'private'}))
    load(p)
    import os
    assert os.environ['DATABASE_URL']=='private'


@pytest.mark.parametrize('url',['https://example.com/health','http://127.0.0.1@evil.test/',
    'http://127.0.0.1/?token=secret','file:///tmp/foo','http://127.0.0.1/#token'])
def test_probe_never_accepts_remote_or_secret_url(url):
    with pytest.raises(ValueError):validate_url(url)


def test_maintenance_mutex(tmp_path):
    with exclusive(tmp_path):
        with pytest.raises(FileExistsError):
            with exclusive(tmp_path):pass
    assert not (tmp_path/'maintenance.lock').exists()


def test_incomplete_and_corrupt_bundle_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):verify(tmp_path)
    manifest={'format':1,'files':{},'workspaces':{'alpha':{},'beta':{}}}
    for name in FILES:
        (tmp_path/name).write_bytes(b'sample')
        manifest['files'][name]=hashlib.sha256(b'sample').hexdigest()
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    assert verify(tmp_path)==manifest
    (tmp_path/'alpha.dump').write_bytes(b'corrupt')
    with pytest.raises(ValueError):verify(tmp_path)
    manifest['files']['../outside']=manifest['files'].pop('alpha.dump')
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):verify(tmp_path)


def test_identity_snapshot_detects_account_and_audit_changes():
    from identity import SCHEMA
    with sqlite3.connect(':memory:') as c:
        c.executescript(SCHEMA)
        before=identity_snapshot(c)
        c.execute("INSERT INTO audit(created_at,action,status) VALUES (1,'test',200)")
        assert identity_snapshot(c)['audit'] != before['audit']
        c.execute('PRAGMA user_version=99')
        with pytest.raises(ValueError): identity_snapshot(c)


@pytest.mark.parametrize('images,configured,accepted',[
    (['same']*5,'same',True),(['same']*4+['different'],'same',False),(['old']*5,'retagged',False)])
def test_backup_refuses_mixed_images_or_retagged_runtime(monkeypatch,images,configured,accepted):
    from scripts import local_backup
    class Stack:
        meta={'image':'demo-tag'}
        def cmd(self,*args):return b'a b c d e'
    def run(args):
        return json.dumps([{'Image':i} for i in images]).encode() if args[1]=='inspect' else configured.encode()
    monkeypatch.setattr(local_backup,'run',run)
    if accepted:assert local_backup.runtime_image(Stack())==configured
    else:
        with pytest.raises(ValueError):local_backup.runtime_image(Stack())


def _restart_fixture(monkeypatch, *, missing=False, health='healthy'):
    from scripts import local_backup as module
    names=['alpha-db','beta-db','gateway','alpha-api','beta-api','alpha-worker','beta-worker','alpha-monitor','beta-monitor']
    maintenance=['alpha-roles','alpha-migrate','identity-init']
    rows=[{'Id':n,'Config':{'Labels':{'com.docker.compose.project':'resolveflow-accounts-fixture',
        'com.docker.compose.service':n}},'State':{'Running':True,'Status':'running','Health':{'Status':health}}}
        for n in names+maintenance if not (missing and n=='beta-worker')]
    calls=[]
    class Stack:
        project='resolveflow-accounts-fixture'
        config={'services':{n:{} for n in names+maintenance}}
        def cmd(self,*args):return ' '.join(r['Id'] for r in rows).encode()
    def fake_run(argv):
        calls.append(argv)
        return json.dumps([r for r in rows if r['Id'] in argv[2:]]).encode() if argv[1]=='inspect' else b''
    monkeypatch.setattr(module,'run',fake_run)
    return module,Stack(),calls


def test_resume_never_replays_completed_installation_jobs(monkeypatch):
    module,stack,calls=_restart_fixture(monkeypatch)
    module.start_existing(stack)
    starts=[c[2:] for c in calls if c[1]=='start']
    assert starts[0]==['alpha-db','beta-db']
    assert starts[1]==['gateway','alpha-api','beta-api']
    assert starts[2]==['alpha-worker','beta-worker','alpha-monitor','beta-monitor']
    assert not any(n in ('alpha-roles','alpha-migrate','identity-init') for c in starts for n in c)


def test_resume_missing_runtime_refuses_before_start(monkeypatch):
    module,stack,calls=_restart_fixture(monkeypatch,missing=True)
    with pytest.raises(ValueError,match='Missing runtime'):module.start_existing(stack)
    assert not any(c[1]=='start' for c in calls)


def test_resume_unhealthy_database_never_starts_business(monkeypatch):
    module,stack,calls=_restart_fixture(monkeypatch,health='unhealthy')
    with pytest.raises(RuntimeError,match='health gate'):module.start_existing(stack)
    assert [c[2:] for c in calls if c[1]=='start']==[['alpha-db','beta-db']]


def test_resume_stuck_database_has_bounded_wait(monkeypatch):
    module,stack,calls=_restart_fixture(monkeypatch,health='starting')
    with pytest.raises(TimeoutError,match='expired'):module.start_existing(stack,timeout=0)
    assert len([c for c in calls if c[1]=='start'])==1
