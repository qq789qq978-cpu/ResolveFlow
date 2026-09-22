"""Policy lifecycle boundaries and untrusted/stale evidence fail closed."""
import copy
import json
import uuid

import pytest

from engine import Engine
from grounding import check_grounding, demo_suggestion
from policy_governance import availability, timestamp
from rag import rank, read_documents
from policy_releases import context, prepare

NOW = timestamp('2026-09-21T12:00:00Z')


def reviewed_document():
    doc = next(d for d in read_documents()[0] if d['id'] == 'refund-v2')
    doc['governance'].update(effective_from='2026-09-21T12:00:00Z', effective_until='2026-09-22T12:00:00Z')
    return doc


@pytest.mark.parametrize('at,reason', [
    ('2026-09-21T11:59:59Z','not_yet_effective'),
    ('2026-09-21T12:00:00Z','active'),
    ('2026-09-21T20:00:00+08:00','active'),
    ('2026-09-22T11:59:59.999999Z','active'),
    ('2026-09-22T12:00:00Z','expired'),
])
def test_effective_interval_boundaries(at, reason):
    assert availability(reviewed_document(), now=timestamp(at), mode='demo')['reason'] == reason


@pytest.mark.parametrize('status', ['draft', 'in_review', 'rejected', 'revoked'])
def test_nonapproved_policy_is_excluded_from_ranking(status):
    docs, chunks = read_documents()
    for doc in docs: doc['governance']['status'] = status
    assert rank('退款', docs, chunks, now=NOW, mode='demo') == []


@pytest.mark.parametrize('value', ['2026-09-21', '2026-09-21T12:00:00', '2026-02-30T12:00:00Z',
                                   '2026-09-21T12:00:00+25:00', '2026-09-21T12:00:00+01:99', None, 123])
def test_invalid_dates_are_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        timestamp(value)


@pytest.mark.parametrize('mutation,reason', [
    ('missing','missing_or_invalid_metadata'), ('array','missing_or_invalid_metadata'),
    ('hash','content_changed'), ('future_review','review_in_future'),
    ('reverse','missing_or_invalid_metadata'), ('no_reviewer','missing_or_invalid_metadata'),
])
def test_invalid_metadata_is_unusable(mutation, reason):
    doc = reviewed_document()
    if mutation == 'missing': doc.pop('governance')
    if mutation == 'array': doc['governance'] = [1]
    if mutation == 'hash': doc['governance']['document_sha256'] = '0'*64
    if mutation == 'future_review': doc['governance']['reviewed_at'] = '2099-01-01T00:00:00Z'
    if mutation == 'reverse': doc['governance']['effective_until'] = doc['governance']['effective_from']
    if mutation == 'no_reviewer': doc['governance'].pop('reviewed_by')
    assert availability(doc, now=NOW, mode='demo')['reason'] == reason


def test_demo_approval_is_not_human_or_live_authority():
    doc = reviewed_document()
    assert availability(doc, now=NOW, mode='live')['reason'] == 'demo_only'
    doc['governance']['review_basis'] = 'operator_attested'  # Synthetic test only.
    assert availability(doc, now=NOW, mode='live')['usable']


@pytest.mark.parametrize('change', ['revoked','expired','removed','changed'])
def test_current_policy_overrides_saved_approved_snapshot(change):
    docs, chunks = read_documents()
    evidence = rank('申请退款', docs, chunks, now=NOW, mode='demo', release=context(prepare()))
    proposal = demo_suggestion('申请退款', evidence)
    before = copy.deepcopy(evidence)
    current = copy.deepcopy(docs)
    doc = next(d for d in current if d['id'] == 'refund-v2')
    if change == 'revoked': doc['governance']['status'] = 'revoked'
    if change == 'expired': doc['governance']['effective_until'] = NOW.isoformat()
    if change == 'removed': current.remove(doc)
    if change == 'changed': doc['sha256'] = '0'*64
    result = check_grounding(proposal, evidence, documents=current, now=NOW, mode='demo')
    assert result['reference_valid'] and not result['usable']
    assert result['policy_checks'][0]['usable'] is False
    assert evidence == before  # Historical retrieval snapshot remains intact.


def test_expiry_while_waiting_for_approval_cannot_refund(tmp_path, monkeypatch):
    e = Engine(str(tmp_path), 'demo')
    rid = str(uuid.uuid4())
    try:
        assert e.start(rid, '申请退款', 'RF-1004')['pending']
        docs = read_documents()[0]
        for doc in docs: doc['governance']['effective_until'] = '2026-09-21T01:00:00Z'
        monkeypatch.setattr('grounding.read_index', lambda: (docs, [], context(prepare())))
        monkeypatch.setattr('grounding.utcnow', lambda: NOW)
        result = e.resume(rid, True)['state']['result']
        assert result['status'] == 'escalated'
        assert 'policy_expired' in result['grounding']['errors']
        assert e.db.execute('SELECT count(*) FROM refunds').fetchone()[0] == 0
    finally:
        e.close()


def test_import_requires_hash_binding_and_rejects_duplicate_keys(tmp_path):
    import shutil
    from rag import KNOWLEDGE
    shutil.copytree(KNOWLEDGE, tmp_path/'knowledge')
    directory = tmp_path/'knowledge'
    p = directory/'governance.json'
    meta = json.loads(p.read_text())
    meta['policies']['refund-v2']['document_sha256'] = '0'*64
    p.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='hash'): read_documents(directory)
    p.write_text('{"schema":1,"schema":1,"policies":{}}')
    with pytest.raises(ValueError, match='Duplicate'): read_documents(directory)
    p.unlink()
    docs, chunks = read_documents(directory)
    assert len(chunks) == 7 and rank('退款', docs, chunks, now=NOW) == []
