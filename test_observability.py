"""Alert incident transitions and sensitive-data/correlation boundaries."""
import json
import logging
import uuid
import pytest
from alerts import Limits, Transitions, alert
from observability import correlation, identify, event


def test_correlations_are_scoped_and_payloads_never_logged(caplog):
    caplog.set_level(logging.INFO,logger='resolveflow')
    rid=str(uuid.uuid4())
    with correlation(request_id=rid):
        identify(run_id=rid)
        event('job_enqueued',ticket='secret-ticket',reason='secret-reason',api_key='secret-key',error_type='TimeoutError',route='/api/runs')
    event('worker_started')
    rows=[json.loads(r.message) for r in caplog.records if r.name=='resolveflow']
    assert rows[0]['request_id']==rows[0]['run_id']==rid
    assert 'request_id' not in rows[1] and 'run_id' not in rows[1]
    assert 'secret' not in caplog.text


def test_unknown_polls_do_not_resolve_incidents_or_repeat_firing():
    t=Transitions();a=alert('task_stalled',run_id=str(uuid.uuid4()))
    assert [e['transition'] for e in t.update({'alerts':[a]})]==['firing']
    assert t.update({'alerts':[{**a,'age_seconds':1000}]})==[]
    assert [e['code'] for e in t.update(None)]==['monitor_unavailable']
    assert t.update(None)==[] and a['id'] in t.active
    assert {e['code'] for e in t.update({'alerts':[]})}=={'task_stalled','monitor_unavailable'}
    assert t.active=={}


def test_truncation_cannot_claim_omitted_alerts_recovered():
    t=Transitions();a=alert('job_failed',run_id=str(uuid.uuid4()))
    t.update({'alerts':[a]})
    assert t.update({'alerts':[],'truncated':True})==[] and a['id'] in t.active
    assert t.update({'alerts':[]})[0]['transition']=='resolved'


def test_complete_categories_can_recover_while_another_category_is_truncated():
    t=Transitions();a=alert('job_failed',run_id=str(uuid.uuid4()));b=alert('worker_offline')
    t.update({'alerts':[a,b]})
    events=t.update({'alerts':[],'truncated':True,'truncated_codes':['job_failed']})
    assert [(e['code'],e['transition']) for e in events]==[('worker_offline','resolved')]
    assert set(t.active)=={a['id']}


@pytest.mark.parametrize('name,value',[('RF_EXPECTED_WORKERS','0'),('RF_ALERT_RUNNING_SECONDS','0'),('RF_ALERT_FAILURE_COUNT','1'),('RF_ALERT_WORKER_OFFLINE_SECONDS','x')])
def test_invalid_alert_configuration_rejected(monkeypatch,name,value):
    monkeypatch.setenv(name,value)
    with pytest.raises(ValueError):Limits.environment()
