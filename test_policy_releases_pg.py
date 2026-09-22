"""Isolated PostgreSQL release publication/rollback and Worker recovery tests."""
import copy
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

import jobs
from policy_releases import activate, prepare, review, status
from rag import retrieve, sync_index
from test_jobs import system, headers, submit
from test_policy_releases import bundle

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Isolated PostgreSQL required')


def publish(store,payload,expected=1):
    with store.connect() as c:
        return activate(c,payload=payload,expected_generation=expected,actor='qa-maintainer',reason='Synthetic release acceptance')


def snapshot(store):
    with store.connect() as c:
        return {t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in
            ['rf_policy_releases','rf_policy_head','rf_policy_events','rf_policy_reviews','rf_knowledge_documents','rf_knowledge_chunks']}


def test_publish_rollback_and_restart_preserve_release_history(system,tmp_path):
    store,engine,client=system
    first=retrieve('退款')[0]['release']
    second=publish(store,prepare(bundle(tmp_path)))
    store.setup()
    assert retrieve('退款')[0]['release']==second
    with store.connect() as c:
        third=activate(c,release_id=first['id'],expected_generation=2,actor='qa',reason='Rollback fixture')
    assert third['id']==first['id'] and third['sha256']==first['sha256'] and third['generation']==3
    with store.connect() as c:
        history=status(c)
    assert len(history['releases'])==2
    assert [e['action'] for e in history['events']]==['publish','publish','rollback']
    assert client.get('/api/policy-releases').status_code==401
    assert client.get('/api/policy-releases',headers=headers()).status_code==403
    assert client.get('/api/policy-releases',headers=headers('reviewer')).status_code==403
    assert client.get('/api/policy-releases',headers=headers('admin')).json()['head']['generation']==3


def test_returning_to_same_release_does_not_revive_old_approval(system,tmp_path):
    store,engine,client=system
    rid=submit(client)
    jobs.process_one(store,engine,rid)
    saved=store.get(rid)['state']['evidence']
    first=saved[0]['release']
    publish(store,prepare(bundle(tmp_path)))
    with store.connect() as c: activate(c,release_id=first['id'],expected_generation=2,actor='qa',reason='Rollback before resume')
    assert client.post('/api/runs/'+rid+'/approval',headers=headers('reviewer'),json={'approved':True,'reason':'Old approval fixture'}).status_code==202
    jobs.process_one(store,engine,rid)
    row=store.get(rid)
    assert row['status']=='escalated' and row['state']['evidence']==saved
    assert 'release_changed_or_missing' in row['state']['result']['grounding']['errors']
    with store.connect() as c: assert c.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==0
    fresh=submit(client)
    jobs.process_one(store,engine,fresh)
    assert store.get(fresh)['status']=='awaiting_approval'
    client.post('/api/runs/'+fresh+'/approval',headers=headers('reviewer'),json={'approved':True,'reason':'Current release fixture'})
    jobs.process_one(store,engine,fresh)
    assert store.get(fresh)['status']=='refunded'


@pytest.mark.parametrize('state',['revoked','expired'])
def test_rollback_never_restores_old_approval_metadata(system,tmp_path,state):
    from datetime import datetime, timezone
    store,engine,client=system
    first=prepare()
    doc=next(d for d in first['documents'] if d['id']=='refund-v2')
    publish(store,prepare(bundle(tmp_path)))
    metadata=copy.deepcopy(doc['governance'])
    if state=='revoked': metadata['status']='revoked'
    else: metadata['effective_until']=datetime.now(timezone.utc).isoformat()
    with store.connect() as c:
        review(c,doc['sha256'],metadata,actor='qa',reason='Invalidate inactive policy',expected_generation=2)
    before=snapshot(store)
    with pytest.raises(ValueError,match='not currently usable'):
        with store.connect() as c: activate(c,release_id=first['id'],expected_generation=3,actor='qa',reason='Rejected rollback fixture')
    assert snapshot(store)==before
    assert retrieve('退款')[0]['release']['id']=='qa-policy-b'


def test_failed_activation_rolls_back_index_head_archive_and_audit(system,tmp_path,monkeypatch):
    store,engine,client=system
    payload=prepare(bundle(tmp_path))
    before=snapshot(store)
    def broken(c,documents,chunks):
        c.execute('DELETE FROM rf_knowledge_chunks')
        raise RuntimeError('Injected publish failure')
    monkeypatch.setattr('rag.replace_index',broken)
    with pytest.raises(RuntimeError,match='Injected'): publish(store,payload)
    assert snapshot(store)==before

def test_mixed_pdf_candidate_cannot_partially_replace_or_publish(system,tmp_path):
    store,engine,client=system
    candidate=bundle(tmp_path)
    (candidate/'extra.PDF').write_bytes(b'%PDF-1.4\nunsupported input fixture')
    before=snapshot(store)
    # Raw import and governed publication both reject before any state change.
    with pytest.raises(ValueError,match='PDF policy import is not supported'):
        with store.connect() as c:sync_index(c,candidate)
    assert snapshot(store)==before
    with pytest.raises(ValueError,match='PDF policy import is not supported'):
        publish(store,prepare(candidate))
    assert snapshot(store)==before
    assert retrieve('退款')[0]['release']['generation']==1


def test_concurrent_publish_requires_current_generation(system,tmp_path):
    store,engine,client=system
    b=prepare(bundle(tmp_path))
    c=prepare(bundle(tmp_path,release_id='qa-policy-c',version='4'))
    def attempt(payload):
        try: publish(store,payload);return 'published'
        except ValueError as error:
            assert 'generation changed' in str(error)
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool: outcomes=list(pool.map(attempt,[b,c]))
    assert sorted(outcomes)==['published','stale']
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rf_policy_releases').fetchone()['n']==2


def test_raw_import_invalidates_release_and_restart_cannot_republish_it(system):
    store,engine,client=system
    with store.connect() as c: sync_index(c)
    store.setup()
    assert retrieve('退款')==[]
    with store.connect() as c:
        assert status(c)['head']=={'singleton':True,'release_id':None,'generation':2}
    publish(store,prepare(),expected=2)
    assert retrieve('退款')


def test_raw_reimport_cannot_reapprove_a_revoked_policy(system):
    store,engine,client=system
    payload=prepare()
    doc=next(d for d in payload['documents'] if d['id']=='refund-v2')
    metadata={**doc['governance'],'status':'revoked'}
    with store.connect() as c:
        review(c,doc['sha256'],metadata,actor='qa',reason='Revoke before raw reimport',expected_generation=1)
    with store.connect() as c: sync_index(c)
    before=snapshot(store)
    with pytest.raises(ValueError,match='not currently usable'):
        publish(store,payload,expected=3)
    assert snapshot(store)==before


def test_rule_code_mismatch_and_index_drift_fail_closed(system,monkeypatch):
    store,engine,client=system
    from policy_releases import rule_contract
    contract=rule_contract()
    monkeypatch.setattr('policy_releases.rule_contract',lambda:{**contract,'sha256':'0'*64})
    assert retrieve('退款')==[]
    with pytest.raises(ValueError,match='executable refund rule'):
        publish(store,prepare())
    monkeypatch.setattr('policy_releases.rule_contract',rule_contract)
    with store.connect() as c: c.execute("UPDATE rf_knowledge_chunks SET text='tampered' WHERE document_id='refund-v2'")
    assert retrieve('退款')==[]


def test_release_and_document_versions_are_immutable(system,tmp_path):
    store,engine,client=system
    before=snapshot(store)
    payload=prepare(bundle(tmp_path))
    payload['id']=prepare()['id']
    with pytest.raises(ValueError,match='Release ids are immutable'): publish(store,payload)
    assert snapshot(store)==before
    payload['id']='new-id'
    original=next(d for d in prepare()['documents'] if d['id']=='refund-v2')
    # Same document id with changed bytes is forbidden, even under a new release.
    next(d for d in payload['documents'] if d['id']=='refund-v3')['id']=original['id']
    with pytest.raises(ValueError,match='document versions are immutable'): publish(store,payload)
    assert snapshot(store)==before


def test_transaction_gate_blocks_publication_after_engine_validation(system,tmp_path,monkeypatch):
    store,engine,client=system
    payload=prepare(bundle(tmp_path))
    original=store.refund
    def publish_then_refund(*args):
        publish(store,payload)
        return original(*args)
    monkeypatch.setattr(store,'refund',publish_then_refund)
    rid=submit(client,'RF-1001')
    jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='escalated'
    with store.connect() as c: assert c.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==0


def test_changed_code_cannot_reuse_a_published_rule_version(system,monkeypatch):
    store,engine,client=system
    payload=prepare()
    payload['id']='updated-rule-fixture'
    changed={'version':'refund-v2','sha256':'f'*64}
    # Model a deliberate future code deployment with a new binding but an
    # incorrectly reused version label. Existing release history must reject it.
    monkeypatch.setattr('policy_releases.RULE_BINDING',changed)
    monkeypatch.setattr('policy_releases.rule_contract',lambda:changed)
    payload['refund_rule']=changed
    before=snapshot(store)
    with pytest.raises(ValueError,match='refund rule versions are immutable'):publish(store,payload)
    assert snapshot(store)==before
