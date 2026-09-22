"""Offline frozen RAG baseline. No production IO, model calls, or quality pass claim."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag import rank, read_documents
from bm25_config import DEFAULT_PROFILE
from policy_governance import timestamp
from policy_releases import context, prepare
from scripts.freeze_rag_split import SPLIT, digest, validate_split
from scripts.validate_rag_dataset import DATASET, validate_dataset

TOP_K = 4
ANSWERABLE = {'supported', 'partial'}


def ratio(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}


def demo_proposal(query, hits):
    # Calling only the real investigation node avoids creating Engine databases,
    # executing a refund, or pretending knowledge questions are order fixtures.
    from engine import Engine
    def offline_tools(order_id, owner, calls):
        if calls != [('search_policy', {'query': query}), ('lookup_order', {})]:
            raise ValueError('Unexpected demo tool request')
        return hits, {'owner': owner}
    with patch('engine.call_tools', offline_tools):
        state = Engine.investigate(SimpleNamespace(mode='demo'),
                                   {'ticket': query, 'order_id': 'RAG-OFFLINE', 'owner': 'rag-eval'})
    if state['usage']['model_calls'] != 0:
        raise ValueError('Offline evaluation cannot invoke models')
    return state['proposal']


def score_case(case, hits, proposal, catalog):
    if len(hits) > TOP_K or len({h['chunk_id'] for h in hits}) != len(hits):
        raise ValueError('Retriever returned too many or duplicate chunks')
    by_chunk = {e['chunk_id']: (eid, e) for eid, e in catalog.items()}
    ids, integrity = [], 0
    for hit in hits:
        matched = by_chunk.get(hit['chunk_id'])
        if matched:
            eid, source = matched
            valid = all(hit.get(out) == source[field] for out, field in (
                ('id', 'document_id'), ('text', 'quote'), ('source', 'source'),
                ('version', 'version'), ('document_sha256', 'document_sha256'),
                ('line_start', 'line_start'), ('line_end', 'line_end')))
        else:
            eid, valid = None, False
        # Corrupt text with a valid chunk ID must not get relevance credit.
        ids.append(eid if valid else None)
        integrity += int(valid)
    expected = case['expected']
    groups = expected['required_evidence_groups']
    required = {eid for group in groups for eid in group}
    contextual = required | set(expected['optional_evidence']) | set(expected['guardrail_evidence'])
    positions = [next((i for i, eid in enumerate(ids, 1) if eid in group), None) for group in groups]
    complete = bool(groups) and all(pos is not None for pos in positions)
    first = next((i for i, eid in enumerate(ids, 1) if eid in required), None)
    citations = proposal['citations']
    available_documents = {h['id'] for h, eid in zip(hits, ids) if eid is not None}
    available_chunks = {h['chunk_id'] for h, eid in zip(hits, ids) if eid is not None}
    cited_evidence = {eid for h, eid in zip(hits, ids) if eid is not None
                      and (h['id'] in citations or h['chunk_id'] in citations)}
    citation_complete = bool(groups) and all(set(group) & cited_evidence for group in groups)
    quotes = proposal.get('quotes', [])
    valid_quotes = sum(any(h['chunk_id'] == q.get('chunk_id') and eid is not None
                          and h['chunk_id'] in citations and isinstance(q.get('quote'), str)
                          and len(q['quote'].strip()) >= 12 and q['quote'] in h['text']
                          for h, eid in zip(hits, ids)) for q in quotes)
    return {'id': case['id'], 'category': case['category'], 'answerability': expected['answerability'],
            'retrieved_evidence': ids, 'hits': hits, 'proposal': proposal,
            'groups_hit': sum(p is not None for p in positions), 'groups_total': len(groups),
            'group_recall': sum(p is not None for p in positions) / len(groups) if groups else None,
            'all_required': bool(complete), 'reciprocal_rank': 1 / first if first else 0,
            'complete_evidence_rr': 1 / max(positions) if complete else 0,
            'context_hits': sum(eid in contextual for eid in ids), 'source_valid_hits': integrity,
            'empty_retrieval': not hits, 'demo_escalated': proposal['action'] == 'escalate',
            'citations_count': len(citations),
            'valid_document_citations': sum(c in available_documents for c in citations),
            'chunk_citations': sum(c in available_chunks for c in citations),
            'valid_references': sum(c in available_documents or c in available_chunks for c in citations),
            'quote_count': len(quotes), 'valid_quotes': valid_quotes,
            'cited_required_complete': bool(citation_complete)}


def aggregate(rows):
    answerable = [r for r in rows if r['answerability'] in ANSWERABLE]
    no_answer = [r for r in rows if r['answerability'] not in ANSWERABLE]
    def mean(field, subset):
        return ratio(sum(r[field] for r in subset), len(subset))
    by_type = {}
    for status in ('unsupported', 'out_of_scope'):
        subset = [r for r in no_answer if r['answerability'] == status]
        by_type[status] = {'samples': len(subset),
                           'empty_retrieval_rate': mean('empty_retrieval', subset),
                           'demo_escalation_rate': mean('demo_escalated', subset),
                           'unsupported_citation_rate': ratio(sum(bool(r['citations_count']) for r in subset), len(subset))}
    total_hits = sum(len(r['hits']) for r in rows)
    total_citations = sum(r['citations_count'] for r in rows)
    return {'samples': len(rows), 'categories': dict(Counter(r['category'] for r in rows)),
            'answerability': dict(Counter(r['answerability'] for r in rows)),
            'retrieval': {'required_group_recall_at_4': mean('group_recall', answerable),
                          'all_required_at_4': mean('all_required', answerable),
                          'mrr_at_4': mean('reciprocal_rank', answerable),
                          'complete_evidence_rr_at_4': mean('complete_evidence_rr', answerable),
                          'context_precision_at_4': ratio(sum(r['context_hits'] for r in rows), total_hits),
                          'source_integrity': ratio(sum(r['source_valid_hits'] for r in rows), total_hits)},
            'abstention': {'by_no_answer_type': by_type,
                           'demo_over_escalation_rate': mean('demo_escalated', answerable)},
            'citations': {'document_citation_validity': ratio(sum(r['valid_document_citations'] for r in rows), total_citations),
                          'reference_validity': ratio(sum(r['valid_references'] for r in rows), total_citations),
                          'verbatim_quote_validity': ratio(sum(r['valid_quotes'] for r in rows), sum(r['quote_count'] for r in rows)),
                          'cited_required_coverage': mean('cited_required_complete', answerable),
                          'unsupported_citation_rate': ratio(sum(bool(r['citations_count']) for r in no_answer), len(no_answer)),
                          'chunk_citation_rate': ratio(sum(r['chunk_citations'] for r in rows), total_citations)},
            'answer_quality': {'claim_entailment': 'not_measured', 'answer_completeness': 'not_measured',
                               'partial_answer_boundary': 'not_measured', 'live_model_refusal': 'not_measured',
                               'reason': 'Demo emits a fixed reason, not a policy QA answer or claim-to-chunk mapping.'}}


def source_fingerprints():
    paths = ['rag.py', 'bm25_config.py', 'evals/rag/BM25_PROTOCOL.md', 'engine.py', 'grounding.py', 'policy_governance.py', 'knowledge/governance.json',
             'policy_releases.py','refund_policy.py','knowledge/release.json','evals/rag/RELEASE_PROTOCOL.md',
             'evals/rag/GOVERNANCE_PROTOCOL.md', 'skill_loader.py', 'scripts/evaluate_rag_baseline.py',
             'scripts/freeze_rag_split.py', 'scripts/validate_rag_dataset.py', 'evals/rag/PROTOCOL.md',
             'evals/rag/SNIPPET_PROTOCOL.md']
    paths += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / 'skills').glob('*/SKILL.md'))]
    return {name: hashlib.sha256((ROOT / name).read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()
            for name in paths}


def evaluate(data, split, *, proposer=demo_proposal):
    validate_dataset(data)
    validate_split(data, split)
    documents, chunks = read_documents()
    partitions = {'tuning': [], 'held_out': []}
    for case in data['cases']:
        hits = rank(case['query'], documents, chunks, TOP_K,
                    now=timestamp('2026-09-21T12:00:00Z'), mode='demo', release=context(prepare()))
        proposal = proposer(case['query'], hits)
        partitions[split['assignments'][case['id']]].append(score_case(case, hits, proposal, data['evidence_catalog']))
    return {'completed': True, 'quality_gate': 'not_set; baseline_measurement_only',
            'metric_schema': 2, 'snippet_protocol': 'evals/rag/SNIPPET_PROTOCOL.md',
            'governance_protocol': 'evals/rag/GOVERNANCE_PROTOCOL.md',
            'policy_evaluation_time': '2026-09-21T12:00:00Z', 'policy_evaluation_mode': 'demo',
            'protocol': 'evals/rag/PROTOCOL.md', 'retriever': 'bm25', 'retrieval_profile': DEFAULT_PROFILE,
            'bm25_protocol': 'evals/rag/BM25_PROTOCOL.md', 'top_k': TOP_K,
            'mode': 'offline_retrieval_and_real_demo_investigation_node_with_stubbed_tool_transport',
            'model_api_calls': 0, 'production_db_access': False, 'business_execution': False,
            'dataset_sha256': digest(data), 'split_sha256': digest(split),
            'corpus_sha256': data['corpus']['sha256'], 'sources': source_fingerprints(),
            'splits': {name: aggregate(rows) for name, rows in partitions.items()},
            'tuning_results': partitions['tuning'], 'held_out_details': 'withheld_by_protocol',
            'limitations': split['limitations'] + [
                'Only 3 documents and 7 evidence chunks; 80 model-authored labels awaiting human review.',
                'Citation evidence coverage is not semantic entailment of generated claims.',
                'Demo escalation is not live-model refusal or final business routing.',
                'Report completion is not a quality pass. Step 2.6 selects BM25 on tuning cases only.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Use a new report path; preserve previous measurements')
    data = json.loads(DATASET.read_text(encoding='utf-8'))
    split = json.loads(SPLIT.read_text(encoding='utf-8'))
    report = evaluate(data, split)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'tuning_results'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
