"""Compare the preregistered six BM25 profiles on tuning cases only."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bm25_config import ALIASES, EXPANSION_WEIGHT, PROFILES
from policy_governance import timestamp
from policy_releases import context, prepare
from rag import rank, read_documents
from scripts.evaluate_rag_baseline import aggregate, demo_proposal, score_case, source_fingerprints
from scripts.freeze_rag_split import SPLIT, digest, validate_split
from scripts.validate_rag_dataset import DATASET, validate_dataset


def eligible(metrics, baseline):
    abstention, old = metrics['abstention'], baseline['abstention']
    return (metrics['retrieval']['source_integrity']['value'] == 1
            and abstention['demo_over_escalation_rate']['value'] <= old['demo_over_escalation_rate']['value']
            and all(abstention['by_no_answer_type'][kind]['demo_escalation_rate']['value'] >=
                    old['by_no_answer_type'][kind]['demo_escalation_rate']['value']
                    and abstention['by_no_answer_type'][kind]['unsupported_citation_rate']['value'] <=
                    old['by_no_answer_type'][kind]['unsupported_citation_rate']['value']
                    for kind in ('unsupported', 'out_of_scope')))


def objective(metrics):
    return tuple(metrics['retrieval'][key]['value'] for key in (
        'all_required_at_4', 'required_group_recall_at_4', 'complete_evidence_rr_at_4',
        'mrr_at_4', 'context_precision_at_4'))


def tune(data, split):
    validate_dataset(data)
    validate_split(data, split)
    # Filter BEFORE ranking, proposing or scoring; held-out details are never evaluated.
    cases = [c for c in data['cases'] if split['assignments'][c['id']] == 'tuning']
    documents, chunks = read_documents()
    release = context(prepare())
    results = {}
    for name in PROFILES:
        rows, latencies = [], []
        for case in cases:
            start = time.perf_counter()
            hits = rank(case['query'], documents, chunks, now=timestamp('2026-09-21T12:00:00Z'),
                        mode='demo', release=release, profile=name)
            latencies.append((time.perf_counter() - start)*1000)
            rows.append(score_case(case, hits, demo_proposal(case['query'], hits), data['evidence_catalog']))
        results[name] = {'metrics': aggregate(rows), 'rows': rows,
                         'rank_ms_mean_single_pass': sum(latencies)/len(latencies)}
    baseline = results['original']['metrics']
    for result in results.values():
        result['eligible'] = eligible(result['metrics'], baseline)
    selected = max((name for name in PROFILES if results[name]['eligible']),
                   key=lambda name: objective(results[name]['metrics']))
    return {'completed': True, 'protocol': 'evals/rag/BM25_PROTOCOL.md', 'tuning_cases': len(cases),
            'held_out_cases_evaluated': 0, 'model_api_calls': 0, 'production_db_access': False,
            'dataset_sha256': digest(data), 'split_sha256': digest(split),
            'profiles': PROFILES, 'aliases': ALIASES, 'expansion_weight': EXPANSION_WEIGHT,
            'selected': selected, 'selection_objective': list(objective(results[selected]['metrics'])),
            'sources': {**source_fingerprints(), **{name: hashlib.sha256((ROOT/name).read_text(encoding='utf-8-sig').encode()).hexdigest()
                        for name in ['bm25_config.py', 'scripts/tune_bm25.py', 'evals/rag/BM25_PROTOCOL.md']}},
            'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    if args.report.exists(): parser.error('Use a new report path')
    report = tune(json.loads(DATASET.read_text(encoding='utf-8')), json.loads(SPLIT.read_text(encoding='utf-8')))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'selected': report['selected'], 'held_out_cases_evaluated': 0,
                      'candidates': {name: {'eligible': r['eligible'], 'objective': objective(r['metrics'])}
                                     for name, r in report['results'].items()}}))


if __name__ == '__main__': main()
