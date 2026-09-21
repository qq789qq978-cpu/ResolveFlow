"""Scoring arithmetic, abstention boundaries, and frozen split protections."""
import copy
import json
from pathlib import Path

import pytest

from scripts.evaluate_rag_baseline import aggregate, demo_proposal, ratio, score_case
from scripts.freeze_rag_split import SPLIT, digest, public_inventory, validate_split
from scripts.validate_rag_dataset import DATASET


@pytest.fixture
def data():
    return json.loads(DATASET.read_text(encoding='utf-8'))


def hit(data, eid):
    evidence = copy.deepcopy(data['evidence_catalog'][eid])
    evidence['id'] = evidence.pop('document_id')
    evidence['text'] = evidence.pop('quote')
    return evidence


def case(required, status='supported', guardrails=(), optional=()):
    return {'id': 'fixture', 'category': 'fixture', 'expected': {
        'required_evidence_groups': required, 'answerability': status,
        'guardrail_evidence': list(guardrails), 'optional_evidence': list(optional)}}


def proposal(citations=(), action='reply'):
    return {'citations': list(citations), 'action': action, 'reason': 'fixture'}


def test_frozen_family_split_has_no_shared_families_or_missing_cases(data):
    split = json.loads(SPLIT.read_text(encoding='utf-8'))
    validate_split(data, split)
    assert split['counts'] == {'held_out': 35, 'tuning': 45}
    assert len(split['groups']) == 37
    for group in split['groups']:
        assert {split['assignments'][cid] for cid in group['case_ids']} == {group['split']}
        if group['public_overlap']:
            assert group['split'] == 'tuning'
    assert len(split['assignments']) == 80


def test_reject_moving_one_near_duplicate_to_other_split(data):
    split = json.loads(SPLIT.read_text(encoding='utf-8'))
    split['assignments']['RAG-001'] = 'held_out'
    with pytest.raises(ValueError, match='split or source inventory drift'):
        validate_split(data, split)


def test_reject_changed_labels_or_public_queries(data):
    split = json.loads(SPLIT.read_text(encoding='utf-8'))
    data['cases'][0]['query'] += '?'
    with pytest.raises(ValueError, match='drift'):
        validate_split(data, split)
    data['cases'][0]['query'] = data['cases'][0]['query'][:-1]
    public = public_inventory()
    public[0]['query'] += '?'
    with pytest.raises(ValueError, match='drift'):
        validate_split(data, split, public)


def test_json_digest_ignores_whitespace_and_key_order():
    assert digest({'a': 1, 'b': 2}) == digest(json.loads('{\r\n "b":2, "a":1\r\n}'))


def test_and_or_groups_and_ranks_have_distinct_denominators(data):
    row = score_case(case([['R1', 'S1'], ['U1']]),
                     [hit(data, 'S2'), hit(data, 'S1'), hit(data, 'U1')],
                     proposal(['shipping-v1', 'unknown-v1']), data['evidence_catalog'])
    assert row['group_recall'] == 1 and row['all_required']
    assert row['reciprocal_rank'] == .5 and row['complete_evidence_rr'] == pytest.approx(1/3)
    assert row['cited_required_complete']


def test_missing_one_required_group_is_not_complete(data):
    row = score_case(case([['R1'], ['U1']]), [hit(data, 'R1')],
                     proposal(['refund-v2']), data['evidence_catalog'])
    assert row['group_recall'] == .5 and row['reciprocal_rank'] == 1
    assert not row['all_required'] and row['complete_evidence_rr'] == 0
    assert not row['cited_required_complete']


def test_context_is_not_direct_answer_and_nonempty_is_not_refusal(data):
    row = score_case(case([], 'unsupported', guardrails=['R3']), [hit(data, 'R3')],
                     proposal(['refund-v2'], action='refund'), data['evidence_catalog'])
    assert row['context_hits'] == 1 and row['group_recall'] is None
    assert row['valid_document_citations'] == 1 and not row['cited_required_complete']
    report = aggregate([row])
    assert report['retrieval']['all_required_at_4']['value'] is None
    unsupported = report['abstention']['by_no_answer_type']['unsupported']
    assert unsupported['empty_retrieval_rate']['value'] == 0
    assert unsupported['demo_escalation_rate']['value'] == 0
    assert unsupported['unsupported_citation_rate']['value'] == 1


def test_empty_hits_do_not_imply_demo_escalation(data):
    row = score_case(case([], 'out_of_scope'), [], proposal(['invented'], action='reply'), data['evidence_catalog'])
    assert row['empty_retrieval'] and not row['demo_escalated']
    assert row['valid_document_citations'] == 0


def test_optional_hits_do_not_inflate_required_ranking(data):
    row = score_case(case([['R1']], optional=['R2']), [hit(data, 'R2')],
                     proposal(['refund-v2']), data['evidence_catalog'])
    assert row['context_hits'] == 1 and row['reciprocal_rank'] == 0
    assert not row['cited_required_complete']


def test_matching_document_id_does_not_prove_required_chunk_was_retrieved(data):
    row = score_case(case([['R1']]), [hit(data, 'R3')], proposal(['refund-v2']), data['evidence_catalog'])
    assert row['valid_document_citations'] == 1 and row['chunk_citations'] == 0
    assert not row['cited_required_complete']


def test_corrupt_quote_cannot_get_relevance_or_source_credit(data):
    corrupt = hit(data, 'R1')
    corrupt['text'] = 'invented text'
    row = score_case(case([['R1']]), [corrupt], proposal(['refund-v2']), data['evidence_catalog'])
    assert row['source_valid_hits'] == 0 and row['group_recall'] == 0
    assert row['valid_document_citations'] == 0


@pytest.mark.parametrize('count', [2, 5])
def test_duplicate_or_excessive_hits_rejected(data, count):
    with pytest.raises(ValueError, match='too many or duplicate'):
        score_case(case([['R1']]), [hit(data, 'R1')]*count, proposal(), data['evidence_catalog'])


def test_macro_recall_is_not_micro_and_no_citations_is_not_success(data):
    a = score_case(case([['R1'], ['U1']]), [hit(data, 'R1')], proposal(), data['evidence_catalog'])
    b = score_case(case([['S1']]), [hit(data, 'S1')], proposal(), data['evidence_catalog'])
    summary = aggregate([a, b])
    assert summary['retrieval']['required_group_recall_at_4']['value'] == .75
    assert summary['citations']['document_citation_validity']['value'] is None
    assert summary['citations']['cited_required_coverage']['value'] == 0
    assert summary['answer_quality']['claim_entailment'] == 'not_measured'
    assert ratio(0, 0)['value'] is None


def test_actual_demo_probe_uses_no_engine_storage_or_model(data, monkeypatch):
    import engine
    def forbidden(*args, **kwargs):
        raise AssertionError('No engine initialization, model or real tool transport allowed')
    monkeypatch.setattr(engine.Engine, '__init__', forbidden)
    monkeypatch.setattr(engine, 'build_model', forbidden)
    monkeypatch.setattr(engine, 'call_tools', forbidden)
    output = demo_proposal('refund please', [hit(data, 'R1')])
    assert output['action'] == 'refund' and output['citations'] == ['refund-v2']
    assert demo_proposal('zzzzzz', [])['action'] == 'escalate'
    assert engine.call_tools is forbidden  # Temporary stub is always restored.
