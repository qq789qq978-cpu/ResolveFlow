from datetime import datetime,timedelta,timezone
import hashlib
import json
import pytest
from scripts.local_trial import assess,observation_summary,copy_bundle,lock
from scripts import local_stack
from scripts.accounts_stack import configuration
from scripts.local_backup import FILES

BASE=datetime(2026,10,5,10,tzinfo=timezone.utc)

def fixture(days=7):
    backups=[{'id':str(n),'created_at':(BASE+timedelta(days=n)).isoformat()} for n in range(days)]
    state={'started_at':BASE.isoformat(),'backups':backups,'final_drill':{'passed':True,'backup_id':str(days-1)}}
    records=[{'day':(BASE+timedelta(days=n)).date().isoformat(),'passed':True} for n in range(days)]
    return state,records,BASE+timedelta(days=days-1,minutes=5)

def test_seven_real_dates_and_elapsed_time_are_required():
    state,records,end=fixture();assert assess(state,records,end)['passed']
    assert not assess(state,records[:1]*7,BASE+timedelta(hours=1))['passed']
    assert not assess(state,records[:-1],end)['passed']
    # Seven date labels cannot shortcut six elapsed days.
    assert not assess(state,records,BASE+timedelta(days=6)-timedelta(seconds=1))['passed']

def test_failed_day_and_missing_backup_do_not_pass():
    state,records,end=fixture();records[2]['passed']=False
    assert not assess(state,records,end)['passed']
    records[2]['passed']=True;state['backups'].pop(2)
    r=assess(state,records,end);assert not r['passed'] and r['backup_gaps_over_24h']

def test_final_drill_must_use_latest_backup():
    state,records,end=fixture();state['final_drill']['backup_id']='old'
    r=assess(state,records,end);assert r['next']=='final_drill' and not r['passed']
    state['final_drill']['backup_id']='6';state['final_drill']['passed']=False
    assert not assess(state,records,end)['passed']

def test_stale_backup_and_new_calendar_day_need_new_evidence():
    state,records,end=fixture()
    assert not assess(state,records,end+timedelta(days=1))['passed']

def test_single_day_needs_explicit_scope_revision_and_does_not_claim_seven_days():
    state,records,end=fixture(1)
    assert not assess(state,records,end)['passed']
    state['scope_revision']='5.7-single-day-user-authorized'
    with pytest.raises(ValueError):assess(state,records,end)
    state['scope_authorization']={'source':'user','decision':'single-day trial plus recovery drills'}
    r=assess(state,records,end)
    assert r['passed'] and r['required_days']==1 and not r['multi_day_retention_verified']
    state['final_drill']['passed']=False
    assert not assess(state,records,end)['passed']

def test_unknown_scope_cannot_weaken_calendar_gate():
    state,records,end=fixture(1);state['scope_revision']='skip-tests'
    with pytest.raises(ValueError):assess(state,records,end)

def test_observation_gaps_are_unknown_not_claimed_outages():
    rows=[{'time':BASE.isoformat(),'available':True},
          {'time':(BASE+timedelta(hours=12)).isoformat(),'available':False}]
    r=observation_summary(rows)
    assert len(r['unavailable_samples'])==1 and r['unobserved_gaps'][0]['meaning']=='unobserved_not_proven_downtime'

def test_trial_mutex_releases_on_exit(tmp_path):
    with lock(tmp_path):
        with pytest.raises(OSError):
            with lock(tmp_path):pass
    with lock(tmp_path):pass

def test_final_drill_refuses_to_restore_before_seven_days(tmp_path,monkeypatch):
    from scripts import trial_drill
    monkeypatch.setattr(trial_drill,'load',lambda _: (tmp_path,{},None))
    monkeypatch.setattr(trial_drill,'status',lambda *a: {'calendar_requirement_met':False,'backup_requirement_met':True})
    def restore(*a,**kw):raise AssertionError('restore must not be reached')
    monkeypatch.setattr(trial_drill,'restore',restore)
    with pytest.raises(ValueError,match='seven-day'):trial_drill.drill(tmp_path,final=True)

def test_resume_only_starts_existing_runtime_in_dependency_order(tmp_path,monkeypatch):
    from scripts import local_trial
    calls=[]
    class Stack:
        def cmd(self,*a):calls.append(a)
    monkeypatch.setattr(local_trial,'load',lambda _: (tmp_path,{'image_id':'frozen'},Stack()))
    monkeypatch.setattr(local_trial,'runtime_image',lambda _: 'frozen')
    monkeypatch.setattr(local_trial,'ensure_observer',lambda *a:None)
    monkeypatch.setattr(local_trial,'tick',lambda *a: {'passed':False})
    local_trial.resume(tmp_path)
    assert calls[0][-2:]==('alpha-db','beta-db')
    assert all(c[0]=='start' and not any('migrate' in a or 'init' in a for a in c) for c in calls)
    monkeypatch.setattr(local_trial,'runtime_image',lambda _: 'unexpected')
    with pytest.raises(ValueError):local_trial.resume(tmp_path)
    assert len(calls)==3

def test_independent_copy_verifies_content_and_refuses_reuse(tmp_path,monkeypatch):
    from scripts import local_trial
    monkeypatch.setattr(local_trial,'protect',lambda p:None)
    src=tmp_path/'source';src.mkdir();manifest={'format':1,'files':{},'workspaces':{'alpha':{},'beta':{}}}
    for name in FILES:
        (src/name).write_bytes(b'fixture');manifest['files'][name]=hashlib.sha256(b'fixture').hexdigest()
    (src/'manifest.json').write_text(json.dumps(manifest));dst=tmp_path/'mirror'/'first'
    assert copy_bundle(src,dst)==hashlib.sha256((src/'manifest.json').read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):copy_bundle(src,dst)
    with pytest.raises(ValueError):copy_bundle(src,src/'nested')
    (src/'alpha.dump').write_bytes(b'corrupt')
    with pytest.raises(ValueError):copy_bundle(src,tmp_path/'second')
    assert (dst/'alpha.dump').read_bytes()==b'fixture'

@pytest.mark.parametrize('capacity',[False,True])
def test_capacity_monitor_and_api_expect_actual_worker_count(tmp_path,monkeypatch,capacity):
    def generate(*args):
        (tmp_path/'compose.json').write_text(json.dumps(configuration('local-image',8057,'x'*32)))
        return tmp_path
    monkeypatch.setattr(local_stack,'accounts_generate',generate)
    monkeypatch.setattr(local_stack,'protect',lambda p:None)
    local_stack.generate('test','local-image',8057,capacity=capacity)
    config=json.loads((tmp_path/'compose.json').read_text())
    for w,expected in [('alpha',2 if capacity else 1),('beta',1)]:
        for suffix in ('api','monitor'):
            assert int(config['services'][w+'-'+suffix]['environment'].get('RF_EXPECTED_WORKERS','1'))==expected
