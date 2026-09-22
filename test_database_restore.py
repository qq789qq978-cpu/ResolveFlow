"""Restore destination guards and failure containment without Docker."""
import json
from pathlib import Path

import pytest

from scripts import database_restore as restore
from scripts.database_backup import BackupError


@pytest.mark.parametrize('project',['resolveflow','resolveflow-qa-step33','../resolveflow','resolveflow-restore-'])
def test_only_new_restore_namespace(project):
    with pytest.raises(BackupError):restore.target_names(project)


@pytest.mark.parametrize('kind',['container','network','volume'])
def test_existing_resource_refused(monkeypatch,kind):
    names=restore.target_names('resolveflow-restore-test')
    key={'container':'db','network':'network','volume':'volume'}[kind]
    monkeypatch.setattr(restore,'run',lambda cmd: (names[key]+'\n').encode() if cmd[1]==kind else b'')
    with pytest.raises(BackupError,match='already exists'):restore.ensure_unused(names)


def test_network_allocation_skips_overlapping_subnets(monkeypatch):
    def run(cmd):
        if cmd[2]=='ls':return b'n1\nn2\n'
        return b'[{"Subnet":"10.240.0.0/23"}]\n[{"Subnet":"fd00::/64"}]\n'
    monkeypatch.setattr(restore,'run',run)
    assert restore.unused_subnet()=='10.240.2.0/24'


def test_mutable_image_refused_before_docker(monkeypatch):
    manifest={'runtime':{s:{'image_id':'resolveflow:latest'} for s in ('db','resolveflow','worker')}}
    with pytest.raises(BackupError,match='Immutable'):restore.image_ids(manifest)


@pytest.fixture
def pipeline(tmp_path,monkeypatch):
    images={s:'sha256:'+'a'*64 for s in ('db','resolveflow','worker')}
    manifest={'compose_project':'resolveflow','archive':{'sha256':'b'*64},'runtime':{}}
    monkeypatch.setattr(restore,'ROOT',tmp_path)
    monkeypatch.setattr(restore,'verify_bundle',lambda p:manifest)
    monkeypatch.setattr(restore,'ensure_unused',lambda n:None)
    monkeypatch.setattr(restore,'image_ids',lambda m:images)
    monkeypatch.setattr(restore,'unused_subnet',lambda:'10.240.0.0/24')
    monkeypatch.setattr(restore,'wait_database',lambda n:None)
    monkeypatch.setattr(restore,'validation_source',lambda:'validator')
    state={'commands':[],'failure':None}
    def run(cmd,**kwargs):
        state['commands'].append(cmd)
        if cmd[1:3]==['volume','create']:
            state['token']=next(x.split('=',1)[1] for x in cmd if x.startswith('resolveflow.restore-id='))
        if cmd[1:3]==['volume','inspect']:
            return b'another-owner' if state['failure']=='volume_race' else state['token'].encode()
        if 'psql' in cmd:
            return b'1\n' if state['failure']=='nonempty' else b'0\n'
        if 'pg_restore' in cmd and state['failure']=='restore':raise BackupError('private error')
        if 'validator' in cmd:
            if state['failure']=='validate':raise BackupError('private error')
            return b'{"passed":true,"matched_tables":22}'
        if cmd[1]=='stop' and state['failure']=='stop':raise BackupError('private error')
        return b''
    monkeypatch.setattr(restore,'run',run)
    bundle=tmp_path/'bundle';bundle.mkdir();(bundle/'database.dump').write_bytes(b'PGDMPfixture')
    state.update(bundle=bundle,report=tmp_path/'result.json')
    return state


def test_restore_is_transactional_and_never_starts_application(pipeline):
    result=restore.restore_backup(pipeline['bundle'],'resolveflow-restore-test',pipeline['report'])
    assert result['passed'] and result['database_stopped'] and not result['resume_verified']
    restore_cmd=next(cmd for cmd in pipeline['commands'] if 'pg_restore' in cmd)
    assert all(option in restore_cmd for option in ('--single-transaction','--exit-on-error','--no-owner','--no-privileges'))
    assert not any('--clean' in cmd or 'db_migrate.py' in cmd or 'worker.py' in cmd for cmd in pipeline['commands'])
    assert result['volume_preserved'] and not result['source_modified']


@pytest.mark.parametrize('failure',['restore','validate','stop'])
def test_failure_stops_own_database_preserves_volume_and_reports(pipeline,failure):
    pipeline['failure']=failure
    with pytest.raises(BackupError):
        restore.restore_backup(pipeline['bundle'],'resolveflow-restore-test',pipeline['report'])
    saved=json.loads(pipeline['report'].read_text())
    assert not saved['passed'] and saved['volume_preserved']
    assert any(cmd[1]=='stop' for cmd in pipeline['commands'])
    assert not any('rm' in cmd or 'prune' in cmd for cmd in pipeline['commands'])
    assert 'private error' not in pipeline['report'].read_text()


def test_failed_bundle_validation_creates_no_target(pipeline,monkeypatch):
    def reject(p):raise BackupError('corrupt')
    monkeypatch.setattr(restore,'verify_bundle',reject)
    with pytest.raises(BackupError):
        restore.restore_backup(pipeline['bundle'],'resolveflow-restore-test',pipeline['report'])
    assert pipeline['commands']==[] and not pipeline['report'].exists()


def test_report_never_overwritten(pipeline):
    pipeline['report'].write_text('prior')
    with pytest.raises(BackupError):
        restore.restore_backup(pipeline['bundle'],'resolveflow-restore-test',pipeline['report'])
    assert pipeline['report'].read_text()=='prior' and pipeline['commands']==[]


@pytest.mark.parametrize('failure',['volume_race','nonempty'])
def test_unowned_volume_or_nonempty_database_never_restored(pipeline,failure):
    pipeline['failure']=failure
    with pytest.raises(BackupError):
        restore.restore_backup(pipeline['bundle'],'resolveflow-restore-test',pipeline['report'])
    assert not any('pg_restore' in cmd for cmd in pipeline['commands'])
    if failure=='volume_race':
        assert not any(cmd[1] in ('run','stop') for cmd in pipeline['commands'])
