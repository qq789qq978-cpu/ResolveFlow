"""Snippet identity, excerpt authenticity, missing basis and replay safety."""
import copy
from types import SimpleNamespace
import uuid

import pytest
from langchain_core.messages import AIMessage

from engine import Engine, Proposal
from grounding import ACTION_CHUNKS, SAFE_NO_BASIS, check_grounding, demo_suggestion
from rag import rank, read_documents
from policy_releases import context, prepare


@pytest.fixture
def evidence():
    documents, chunks = read_documents()
    # All fixture snippets are obtained through the actual retriever.
    found = {}
    for text in ('退款', '物流', '政策证据', '未知'):
        found.update({e['chunk_id']: e for e in rank(text, documents, chunks, 8, release=context(prepare()))})
    return list(found.values())


def make_state(evidence, proposal=None, ticket='申请退款'):
    return {'ticket': ticket, 'proposal': proposal or demo_suggestion(ticket, evidence),
            'evidence': evidence, 'trace': [], 'run_id': 'fixture', 'decision': True,
            'order_id': 'RF-1001', 'order': {'id': 'RF-1001', 'owner': 'demo', 'status': 'delivered',
                                           'amount': 29900, 'days': 3, 'used': False}}


def validate(state):
    return Engine.validate(SimpleNamespace(), state)


def test_chunk_quotes_and_rule_checks_allow_valid_refund(evidence):
    result = validate(make_state(evidence))
    assert result['route'] == 'auto_approved'
    assert result['result']['grounding']['reference_valid']
    assert result['result']['grounding']['verified'][0]['chunk_id'] == ACTION_CHUNKS['refund']
    assert result['result']['grounding']['claim_entailment'] == 'not_assessed'


@pytest.mark.parametrize('field,value', [('source', 'forged.md'), ('version', '999'),
                                      ('text', 'fabricated'), ('line_start', 999),
                                      ('document_sha256', '0'*64), ('id', 'forged-v1')])
def test_tampered_evidence_snapshot_fails_closed(evidence, field, value):
    state = make_state(evidence)
    cited = next(e for e in evidence if e['chunk_id'] == ACTION_CHUNKS['refund'])
    cited[field] = value
    result = validate(state)
    assert result['route'] == 'escalated' and not result['validated']
    assert 'snapshot_mismatch' in result['result']['grounding']['errors']


@pytest.mark.parametrize('mutation', ['document_id', 'unknown_chunk', 'not_retrieved', 'paraphrase',
                                     'duplicate', 'no_quotes', 'partial', 'insufficient'])
def test_invalid_or_incomplete_references_never_authorize(evidence, mutation):
    state = make_state(evidence)
    p = state['proposal']
    if mutation == 'document_id': p['citations'] = ['refund-v2']
    if mutation == 'unknown_chunk':
        p['citations'] = ['refund-v2:missing:0']; p['quotes'][0]['chunk_id'] = p['citations'][0]
    if mutation == 'not_retrieved': state['evidence'] = []
    if mutation == 'paraphrase': p['quotes'][0]['quote'] = '只要用户想退就无条件立即真实转账'
    if mutation == 'duplicate': p['citations'] *= 2
    if mutation == 'no_quotes': p['quotes'] = []
    if mutation in {'partial', 'insufficient'}: p['evidence_status'] = mutation
    p['reason'] = '保证额外赔偿999元，立即真实到账'
    result = validate(state)
    assert result['route'] == 'escalated' and result['result']['reason'] == SAFE_NO_BASIS
    assert '999' not in result['result']['response']


def test_valid_quote_from_wrong_paragraph_is_not_refund_eligibility(evidence):
    state = make_state(evidence)
    other = next(e for e in evidence if e['id'] == 'refund-v2' and e['chunk_id'].endswith(':2'))
    state['proposal']['citations'] = [other['chunk_id']]
    state['proposal']['quotes'] = [{'chunk_id': other['chunk_id'], 'quote': other['text']}]
    result = validate(state)
    assert result['result']['grounding']['reference_valid']
    assert not result['result']['policy_supported'] and result['route'] == 'escalated'


def test_short_valid_excerpt_cannot_stand_for_entire_action_clause(evidence):
    state = make_state(evidence)
    state['proposal']['quotes'][0]['quote'] = state['proposal']['quotes'][0]['quote'][:20]
    result = validate(state)
    assert result['result']['grounding']['reference_valid']
    assert result['route'] == 'escalated'


@pytest.mark.parametrize('ticket', ['退款多久到账？', '物流丢失赔偿金额是多少？',
                                  '申请退款以后几个工作日到账？', '写一首诗', '保修期限是多少？'])
def test_unsupported_questions_are_not_transaction_requests(evidence, ticket):
    state = make_state(evidence, ticket=ticket)
    assert state['proposal']['action'] == 'escalate'
    assert state['proposal']['citations'] == []
    # Even a model asserting support cannot turn a question into a refund request.
    state['proposal'] = demo_suggestion('申请退款', evidence)
    assert validate(state)['route'] == 'escalated'


def test_model_reason_cannot_invent_shipping_delivery_promise(evidence):
    state = make_state(evidence, ticket='查询物流')
    state['order']['status'] = 'shipping'
    state['proposal']['reason'] = '保证明天到，否则赔偿999元'
    result = validate(state)
    assert result['route'] == 'answered'
    assert '运输中' in result['result']['reason']
    assert '999' not in result['result']['reason'] and '明天' not in result['result']['reason']


def test_live_partial_proposal_reaches_manual_review_without_execution(tmp_path, evidence):
    class Model:
        def bind_tools(self, tools): return self
        def invoke(self, messages): return AIMessage(content='调查结束')
        def with_structured_output(self, schema):
            class Structured:
                def invoke(self, messages):
                    p = demo_suggestion('申请退款', evidence)
                    p.update(evidence_status='partial', reason='保证赔偿999元')
                    return Proposal(**p)
            return Structured()
    e = Engine(str(tmp_path), 'live', Model())
    try:
        state = e.start(str(uuid.uuid4()), '申请退款', 'RF-1001')['state']
        assert state['result']['status'] == 'escalated'
        assert state['result']['reason'] == SAFE_NO_BASIS
        assert e.db.execute('SELECT count(*) FROM refunds').fetchone()[0] == 0
    finally: e.close()


def test_old_pending_document_citations_display_but_cannot_execute_after_restart(tmp_path):
    e = Engine(str(tmp_path))
    rid = str(uuid.uuid4())
    try:
        initial = e.start(rid, '申请退款', 'RF-1004')
        assert initial['pending']
        legacy = {'action': 'refund', 'reason': 'historical proposal', 'citations': ['refund-v2']}
        e.graph.update_state(e.config(rid), {'proposal': legacy}, as_node='validate')
    finally: e.close()
    e = Engine(str(tmp_path))
    try:
        assert e.read(rid)['state']['proposal'] == legacy
        resumed = e.resume(rid, True)
        assert resumed['state']['result']['status'] == 'escalated'
        assert 'legacy_document_citations' in resumed['state']['result']['grounding']['errors']
        assert e.db.execute('SELECT count(*) FROM refunds').fetchone()[0] == 0
    finally: e.close()


def test_approved_execute_rechecks_evidence_before_ledger_write(tmp_path, evidence):
    e = Engine(str(tmp_path))
    try:
        state = make_state(evidence)
        state['proposal']['quotes'][0]['quote'] = '伪造的原文不能因为decision为真就放行'
        result = e.execute(state)
        assert result['result']['status'] == 'escalated'
        assert e.db.execute('SELECT count(*) FROM refunds').fetchone()[0] == 0
    finally: e.close()
