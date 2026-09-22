"""Offline backup publication, integrity and failure handling contracts."""
import io
import json
from pathlib import Path
import subprocess

import pytest

from scripts import database_backup as backup


@pytest.fixture
def fake_runtime(monkeypatch):
    state = {'fail': None, 'commands': [], 'exporters': []}
    monkeypatch.setattr(backup, 'container', lambda p, s: {'id': s, 'image_id': 'image', 'started_at': 'now'})

    class Exporter:
        def __init__(self, *args, **kwargs):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO()
            state['exporters'].append(self)
        def wait(self, timeout):
            return 0
        def kill(self):
            pass

    monkeypatch.setattr(backup.subprocess, 'Popen', Exporter)
    monkeypatch.setattr(backup, 'read_line', lambda p, t: {
        'postgres_version_num': 170011, 'database': 'resolveflow', 'snapshot_id': '0001-1'})

    def run(command, *, timeout=600, stdin=None, stdout=None):
        state['commands'].append(command)
        if command[:3] == ['git', 'rev-parse', 'HEAD']:
            return b'a' * 40 + b'\n'
        if command[:2] == ['git', 'status']:
            return b' M scripts/database_backup.py\n'
        if '--version' in command:
            return b'pg_dump (PostgreSQL) 17.11\n'
        if 'pg_dump' in command:
            stdout.write(b'PGDMPcompressed fixture')
            if state['fail'] == 'dump':
                raise backup.BackupError('private password must not leak')
            if state['fail'] == 'timeout':
                raise subprocess.TimeoutExpired(command, timeout)
        if 'pg_restore' in command:
            assert stdin.read().startswith(b'PGDMP')
            assert '--file=/dev/null' in command and '-d' not in command
            if state['fail'] == 'decode':
                raise backup.BackupError('bad compressed block')
        return b''
    monkeypatch.setattr(backup, 'run', run)
    return state


def test_success_and_distinct_bundles_without_overwrite(tmp_path, fake_runtime):
    first, manifest = backup.create_backup(tmp_path)
    second, _ = backup.create_backup(tmp_path)
    assert first != second
    assert backup.verify_bundle(first) == manifest
    assert manifest['source_checkout']['dirty'] is True
    assert manifest['restore_verified'] is False
    assert manifest['archive']['full_decode_passed'] is True
    commands = fake_runtime['commands']
    assert any('--snapshot=0001-1' in command for command in commands)
    assert all(p.stdin.closed and p.stdout.closed for p in fake_runtime['exporters'])


@pytest.mark.parametrize('failure,phase', [('dump', 'dump'), ('timeout', 'dump'), ('decode', 'archive_validation')])
def test_failed_backup_never_published(tmp_path, fake_runtime, failure, phase):
    fake_runtime['fail'] = failure
    with pytest.raises(backup.BackupError):
        backup.create_backup(tmp_path)
    partial, = tmp_path.iterdir()
    assert partial.name.endswith('.partial')
    record = json.loads((partial/'failure.json').read_text())
    assert record['phase'] == phase and record['status'] == 'failed'
    assert 'private password' not in (partial/'failure.json').read_text()
    with pytest.raises(backup.BackupError):
        backup.verify_bundle(partial)
    assert fake_runtime['exporters'][0].stdin.closed


def test_disk_failure_before_publish(tmp_path, fake_runtime, monkeypatch):
    original = backup.write_json
    def write(path, data):
        if path.name == 'manifest.json':
            raise OSError('disk full')
        original(path, data)
    monkeypatch.setattr(backup, 'write_json', write)
    with pytest.raises(backup.BackupError):
        backup.create_backup(tmp_path)
    assert all(p.name.endswith('.partial') for p in tmp_path.iterdir())


def test_container_restart_rejects_backup(tmp_path, fake_runtime, monkeypatch):
    calls = []
    def container(project, service):
        calls.append(service)
        return {'id': service, 'image_id': 'same', 'started_at': str(len(calls))}
    monkeypatch.setattr(backup, 'container', container)
    with pytest.raises(backup.BackupError):
        backup.create_backup(tmp_path)
    assert all(p.name.endswith('.partial') for p in tmp_path.iterdir())


@pytest.mark.parametrize('which', ['database.dump', 'manifest.json', 'missing', 'extra', 'inventory_path', 'header'])
def test_corrupt_or_incomplete_bundle_refused(tmp_path, fake_runtime, which):
    path, _ = backup.create_backup(tmp_path)
    if which in ('database.dump', 'manifest.json'):
        with (path/which).open('ab') as stream:
            stream.write(b'corruption')
    elif which == 'missing':
        (path/'database.dump').unlink()
    elif which == 'extra':
        (path/'failure.json').write_text('{}')
    elif which == 'inventory_path':
        (path/'SHA256SUMS.json').write_text(json.dumps({'../outside': 'a'*64}))
    elif which == 'header':
        (path/'database.dump').write_bytes(b'WRONGarchive')
        manifest = json.loads((path/'manifest.json').read_text())
        manifest['archive'].update(sha256=backup.sha256(path/'database.dump'), bytes=12)
        (path/'manifest.json').write_text(json.dumps(manifest))
        (path/'SHA256SUMS.json').write_text(json.dumps({n: backup.sha256(path/n) for n in ('database.dump', 'manifest.json')}))
    with pytest.raises(backup.BackupError):
        backup.verify_bundle(path)


def test_public_verifier_rejects_partial_even_with_complete_manifest(tmp_path, fake_runtime):
    path, _ = backup.create_backup(tmp_path)
    partial = path.with_name(path.name+'.partial')
    path.rename(partial)
    with pytest.raises(backup.BackupError):
        backup.verify_bundle(partial)


def test_snapshot_exporter_failure_and_timeout():
    class Process:
        stdout = io.BytesIO()
    with pytest.raises(backup.BackupError, match='preparation failed'):
        backup.read_line(Process(), 0.1)


def test_client_major_mismatch(tmp_path, fake_runtime, monkeypatch):
    monkeypatch.setattr(backup, 'read_line', lambda p, t: {'postgres_version_num': 180000})
    with pytest.raises(backup.BackupError):
        backup.create_backup(tmp_path)
    assert not any('pg_dump' in c and '--version' not in c for c in fake_runtime['commands'])


@pytest.mark.parametrize('project', ['--all', 'x;bad', '../resolveflow'])
def test_invalid_project_rejected_before_docker(tmp_path, project):
    with pytest.raises(backup.BackupError):
        backup.create_backup(tmp_path, project)
    assert not list(tmp_path.iterdir())


def test_cli_error_redacts_exception(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr('sys.argv', ['database_backup.py', 'verify', str(tmp_path)])
    def fail(path):
        raise ValueError('postgresql://user:private-secret@host/db')
    monkeypatch.setattr(backup, 'verify_bundle', fail)
    assert backup.main() == 1
    assert 'private-secret' not in capsys.readouterr().out
