"""Tuning-only, label-assisted ranking ceiling. No model inference or deployment."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.evaluate_rag_baseline import aggregate, demo_proposal, score_case
from scripts.freeze_rag_split import digest, validate_split
from scripts.validate_rag_dataset import validate_dataset


def best_order(case,hits,proposal,catalog):
    """Exhaustive label oracle, NOT a trainable or deployable reranking model."""
    best=None;key=None;examined=0
    for order in itertools.permutations(hits,min(4,len(hits))):
        row=score_case(case,list(order),proposal,catalog)
        objective=(row['all_required'],row['group_recall'] or 0,
                   row['complete_evidence_rr'],row['reciprocal_rank'],row['context_hits'])
        if key is None or objective>key:best,key=row,objective
        examined+=1
    return best,examined


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():parser.error('Choose a new report path')
    paths=['evals/rag/cases.v1.json','evals/rag/split.v1.json',
           'validation/step-2.8-2026-09-22/tuning.json']
    dataset,split,cached=[json.loads((ROOT/p).read_text(encoding='utf-8')) for p in paths]
    validate_dataset(dataset);validate_split(dataset,split)
    assert cached['phase']=='tuning' and cached['held_out_queries_evaluated']==0
    assert cached['dataset_sha256']==digest(dataset) and cached['split_sha256']==digest(split)
    assert cached['selected_weight']==.25
    # Filter first. Never read a held-out result or compute held-out case metrics.
    cases={c['id']:c for c in dataset['cases'] if split['assignments'][c['id']]=='tuning'}
    rows={name:{r['id']:r for r in cached['candidates'][name]['rows']} for name in ('0.25','bm25','dense')}
    assert all(set(value)==set(cases) for value in rows.values())
    base=[];ordered=[];expanded=[];detail=[];counts=Counter()
    for cid,case in cases.items():
        old=rows['0.25'][cid]
        baseline=score_case(case,old['hits'],old['proposal'],dataset['evidence_catalog'])
        assert baseline==old,'Cached metrics or source identity drift'
        pool={}
        for hit in rows['bm25'][cid]['hits']+rows['dense'][cid]['hits']:
            if hit['chunk_id'] in pool:
                for field in ('text','source','version','document_sha256','line_start','line_end','release'):
                    assert pool[hit['chunk_id']][field]==hit[field]
            pool[hit['chunk_id']]=hit
        assert {h['chunk_id'] for h in old['hits']}<=set(pool)
        small,n1=best_order(case,old['hits'],old['proposal'],dataset['evidence_catalog'])
        wide,n2=best_order(case,list(pool.values()),old['proposal'],dataset['evidence_catalog'])
        # This diagnostic measures the actual demo boundary on label-oracle evidence.
        for row in (small,wide):
            proposal=demo_proposal(case['query'],row['hits'])
            scored=score_case(case,row['hits'],proposal,dataset['evidence_catalog'])
            row.clear();row.update(scored)
        assert len(small['hits'])==len(old['hits'])
        for field in ('all_required','group_recall','context_hits','empty_retrieval'):
            assert small[field]==old[field],'Reordering cannot change set membership'
        base.append(baseline);ordered.append(small);expanded.append(wide)
        counts[len(pool)]+=1
        detail.append({'id':cid,'answerability':old['answerability'],'pool_size':len(pool),
            'orders_examined':{'top4':n1,'union':n2},
            'baseline_rr':old['reciprocal_rank'],'top4_oracle_rr':small['reciprocal_rank'],
            'baseline_complete_rr':old['complete_evidence_rr'],'top4_oracle_complete_rr':small['complete_evidence_rr'],
            'baseline_context_hits':old['context_hits'],'union_oracle_context_hits':wide['context_hits'],
            'baseline_demo_escalated':old['demo_escalated'],'union_oracle_demo_escalated':wide['demo_escalated']})
    metrics={name:aggregate(value) for name,value in [('hybrid_cached',base),('oracle_top4',ordered),('oracle_union_top4',expanded)]}
    assert metrics['hybrid_cached']==cached['candidates']['0.25']['metrics']
    report={'completed':True,'kind':'label_assisted_diagnostic_not_model_evaluation','partition':'tuning',
        'samples':len(cases),'held_out_cases_evaluated':0,'model_inference_calls':0,'paid_model_api_calls':0,
        'oracle_objective':['all_required','group_recall','complete_evidence_rr','reciprocal_rank','context_hits'],
        'metrics':metrics,'candidate_pool_histogram':dict(sorted(counts.items())),
        'cross_encoder_pairs_if_top4':sum(len(r['hits']) for r in base),
        'cross_encoder_pairs_if_union':sum(size*number for size,number in counts.items()),
        'diagnostic_rows':detail,
        'sources':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
        'limitations':['Uses labels to select evidence; unattainable-performance diagnostic, not a tested reranker.',
            'Top4-only reorder cannot recover missing evidence or remove unrelated candidates.',
            'Union selection changes the candidate pool and needs a separate frozen experiment.',
            'Labels are model-generated and pending human review; no independent generalization claim.',
            'No reranker latency, memory, download, API pricing or real generative quality measured.']}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'diagnostic_rows','sources','limitations','metrics'}}))
    print(json.dumps({k:{'retrieval':v['retrieval'],'demo_over_escalation':v['abstention']['demo_over_escalation_rate']} for k,v in metrics.items()}))


if __name__=='__main__':main()
