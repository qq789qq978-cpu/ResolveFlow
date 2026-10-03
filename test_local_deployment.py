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
