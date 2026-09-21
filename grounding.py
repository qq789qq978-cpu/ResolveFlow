"""Exact snippet references and conservative action scope; no semantic judge."""
from functools import lru_cache
import re

from rag import read_documents, current_documents
from policy_governance import availability, utcnow

# Bind automatic actions to the reviewed clause, not merely its document ID.
# A policy edit needs an explicit rule/evidence binding review (roadmap 2.5).
ACTION_CHUNKS = {'refund': 'refund-v2:3ad704686fec:0',
                 'reply': 'shipping-v1:872750f16cd0:0'}
SAFE_NO_BASIS = '现有证据不足以支持这项处理，已转人工核查；不会据此承诺退款、到账时间或赔付。'


@lru_cache(maxsize=1)
def trusted_chunks():
    documents, chunks = read_documents()
    docs = {d['id']: d for d in documents}
    return {c['chunk_id']: {**c, 'source': docs[c['document_id']]['source'],
                           'version': docs[c['document_id']]['version'],
                           'document_sha256': docs[c['document_id']]['sha256']} for c in chunks}


def requested_action(ticket):
    """Bound demo/live monetary routing to a direct service request.

    This conservative lexical boundary is not a general intent classifier.
    Other questions can retain quoted evidence but require human interpretation.
    """
    text = ticket.strip().lower()
    if re.search(r'到账|多久|何时|什么时候|几小时|几个工作日|运费|保修|发票|赔付|赔偿|'
                 r'地址|电话|支付账户|银行卡|乐队|海报|\bwhen\b|\bhow long\b', text):
        return None
    if (re.fullmatch(r'退款[。！!]?|退货[。！!]?|refund[.!]?', text)
            or re.search(r'(?:申请|要求|请求|我要|想要|帮我|直接|办理)(?:退款|退货)|'
                         r'(?:退款|退货)(?:申请|办理)|\brefund\s+please\b', text)):
        return 'refund'
    if re.search(r'查询(?:物流|快递|运单)|(?:物流|快递|运单)(?:查询|状态)|快递到哪|'
                 r'\bshipping\s+status\b', text):
        return 'reply'
    return None


def check_grounding(proposal, evidence, *, mode=None, documents=None, now=None):
    """Validate the saved snapshot and verbatim excerpts, never free-text entailment."""
    errors, verified = [], []
    citations = proposal.get('citations', [])
    quotes = proposal.get('quotes', [])
    if proposal.get('citation_schema') != 2:
        errors.append('legacy_document_citations')
    if not citations:
        errors.append('no_citations')
    if not isinstance(citations, list) or any(not isinstance(c, str) for c in citations):
        citations = []
        errors.append('invalid_citations')
    if len(citations) > 8 or len(citations) != len(set(citations)):
        errors.append('duplicate_or_excessive_citations')
    if not isinstance(quotes, list) or any(not isinstance(q, dict) for q in quotes):
        quotes = []
        errors.append('invalid_quotes')
    quote_ids = [q.get('chunk_id') for q in quotes]
    if (any(not isinstance(qid, str) for qid in quote_ids)
            or len(quote_ids) != len(set(str(qid) for qid in quote_ids))
            or set(citations) != set(str(qid) for qid in quote_ids)):
        errors.append('citation_quote_mismatch')
    available = {}
    for item in evidence:
        cid = item.get('chunk_id')
        if cid in available and item != available[cid]:
            errors.append('conflicting_snapshot')
        available[cid] = item
    trusted = trusted_chunks()
    for quote in quotes:
        cid, excerpt = quote.get('chunk_id'), quote.get('quote')
        if not isinstance(cid, str):
            errors.append('invalid_chunk_id')
            continue
        saved, canonical = available.get(cid), trusted.get(cid)
        if not saved or not canonical:
            errors.append('unknown_or_unretrieved_chunk')
            continue
        if any(saved.get(field) != canonical[source] for field, source in (
                ('id', 'document_id'), ('text', 'text'), ('source', 'source'),
                ('version', 'version'), ('document_sha256', 'document_sha256'),
                ('line_start', 'line_start'), ('line_end', 'line_end'))):
            errors.append('snapshot_mismatch')
            continue
        if not isinstance(excerpt, str) or len(excerpt.strip()) < 12 or excerpt not in saved['text']:
            errors.append('quote_not_verbatim')
            continue
        verified.append({**saved, 'quote': excerpt})
    valid = not errors
    now = now if now is not None else utcnow()
    current = {d['id']: d for d in (current_documents() if documents is None else documents)} if verified else {}
    policy_checks = []
    for saved in verified:
        document = current.get(saved['id'], {})
        policy = availability(document, now=now, mode=mode)
        if document.get('sha256') != saved['document_sha256']:
            policy = {**policy, 'usable': False, 'reason': 'policy_removed_or_changed'}
        policy_checks.append({'id': saved['id'], 'chunk_id': saved['chunk_id'], **policy})
        saved['policy'] = policy
        if not policy['usable']:
            errors.append('policy_'+policy['reason'])
    complete = proposal.get('evidence_status') == 'supported'
    if not complete:
        errors.append('partial_or_insufficient_basis')
    return {'schema': 2, 'reference_valid': valid, 'usable': not errors,
            'policy_checks': policy_checks, 'checked_at': now.isoformat(),
            'evidence_status': proposal.get('evidence_status', 'insufficient'),
            'errors': sorted(set(errors)), 'verified': verified,
            'claim_entailment': 'not_assessed'}


def action_supported(action, grounding):
    # A short quote from some other paragraph cannot authorize a refund.
    return grounding['usable'] and any(
        e['chunk_id'] == ACTION_CHUNKS.get(action) and e['quote'] == e['text']
        for e in grounding['verified'])


def demo_suggestion(ticket, evidence):
    action = requested_action(ticket)
    required = ACTION_CHUNKS.get(action)
    matching = next((e for e in evidence if e.get('chunk_id') == required), None)
    if not matching:
        return {'action': 'escalate', 'reason': SAFE_NO_BASIS, 'citation_schema': 2,
                'evidence_status': 'insufficient', 'citations': [], 'quotes': []}
    return {'action': action, 'reason': '已检索到处理规则原文，订单事实仍需程序核验。',
            'citation_schema': 2, 'evidence_status': 'supported', 'citations': [required],
            'quotes': [{'chunk_id': required, 'quote': matching['text']}]}
