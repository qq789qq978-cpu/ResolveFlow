"""Frozen local-only semantic experiment. Tune first; held-out evaluation needs selection."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from embedding_contract import CONTRACT_ID
from rag import read_index,rank
from semantic import dense_rank,fuse
from scripts.evaluate_rag_baseline import aggregate,demo_proposal,score_case,source_fingerprints
from scripts.freeze_rag_split import SPLIT,digest,validate_split
from scripts.validate_rag_dataset import DATASET,validate_dataset
from scripts.tune_bm25 import eligible
from policy_governance import timestamp

EVALUATION_TIME=timestamp('2026-09-21T12:00:00Z')


def fingerprints():
    files=['semantic.py','embedding_contract.py','embedding_service.py','embedding/model-manifest.json',
           'embedding/requirements.lock','scripts/evaluate_semantic.py','SEMANTIC_RETRIEVAL.md']
    return {**source_fingerprints(),**{p:hashlib.sha256((ROOT/p).read_text(encoding='utf-8-sig').encode()).hexdigest() for p in files}}


def selected_weight(report):
    candidates=[w for w in (.25,.5,.75) if report['candidates'][str(w)]['eligible']]
    if not candidates:return None
    def objective(w):
        r=report['candidates'][str(w)]['metrics']['retrieval']
        return tuple(r[k]['value'] for k in ['complete_evidence_rr_at_4','mrr_at_4','context_precision_at_4'])
    return max(candidates,key=objective)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['tuning','held-out','performance'])
    parser.add_argument('--selection',type=Path)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if args.report.exists():parser.error('Use a new report path')
    sources=fingerprints()  # Fail before inference if the reproducibility snapshot is incomplete.
    data=json.loads(DATASET.read_text(encoding='utf-8'));split=json.loads(SPLIT.read_text(encoding='utf-8'))
    validate_dataset(data);validate_split(data,split)
    documents,chunks,release=read_index()
    assert release['valid']
    selection=json.loads(args.selection.read_text()) if args.selection else None
    if args.phase!='tuning' and (not selection or selection['selected_weight'] is None):
        parser.error('No eligible tuning candidate; do not evaluate held-out queries')
    if selection and selection['sources']!=sources:parser.error('Sources changed since selection')
    # The split filter runs before any query is encoded or scored.
    partition='held_out' if args.phase=='held-out' else 'tuning'
    cases=[c for c in data['cases'] if split['assignments'][c['id']]==partition]
    rows={name:[] for name in ['bm25','dense','0.25','0.5','0.75']}
    latencies=[]
    if args.phase=='performance':
        for i in range(20):dense_rank(cases[i%len(cases)]['query'],documents,chunks,release)
        for _ in range(3):
            for case in cases:
                start=time.monotonic()
                lexical=rank(case['query'],documents,chunks,release=release)
                dense=dense_rank(case['query'],documents,chunks,release)
                fuse(lexical,dense,selection['selected_weight'])
                latencies.append((time.monotonic()-start)*1000)
        ordered=sorted(latencies)
        report={'completed':True,'phase':'performance','warmup_queries':20,'measured_queries':len(ordered),
                'p95_ms':ordered[math.ceil(.95*len(ordered))-1],'max_ms':max(ordered),
                'mean_ms':sum(ordered)/len(ordered),'errors':0,'latencies_ms':latencies,
                'within_1000_ms':ordered[math.ceil(.95*len(ordered))-1]<=1000}
    else:
        for case in cases:
            lexical=rank(case['query'],documents,chunks,release=release,now=EVALUATION_TIME)
            dense=dense_rank(case['query'],documents,chunks,release,now=EVALUATION_TIME)
            candidates={'bm25':lexical,'dense':dense}
            weights=(.25,.5,.75) if args.phase=='tuning' else (selection['selected_weight'],)
            candidates.update({str(w):fuse(lexical,dense,w) for w in weights})
            for name,hits in candidates.items():
                rows[name].append(score_case(case,hits,demo_proposal(case['query'],hits),data['evidence_catalog']))
        baseline=aggregate(rows['bm25'])
        candidates={name:{'metrics':aggregate(values)} for name,values in rows.items() if values}
        if args.phase=='tuning':
            for name,result in candidates.items():
                result.update(eligible=eligible(result['metrics'],baseline)
                    and result['metrics']['retrieval']['all_required_at_4']['numerator']>=40,
                    rows=rows[name])
            report={'completed':True,'phase':'tuning','candidates':candidates,'held_out_queries_evaluated':0}
            report['selected_weight']=selected_weight(report)
        else:
            candidate=candidates[str(selection['selected_weight'])]['metrics']
            r=candidate['retrieval'];base=baseline['retrieval']
            passed=(r['all_required_at_4']['numerator']>=16 and eligible(candidate,baseline)
                    and all(r[k]['value']>=base[k]['value'] for k in base))
            report={'completed':True,'phase':'held-out','selected_weight':selection['selected_weight'],
                    'candidates':candidates,'quality_gate_passed':passed,'held_out_details':'withheld_by_protocol'}
    report.update(contract=CONTRACT_ID,sources=sources,dataset_sha256=digest(data),split_sha256=digest(split),
                  mode='local_demo',paid_model_api_calls=0,release=release['token'])
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'sources','candidates','latencies_ms'}},ensure_ascii=False))
    if 'candidates' in report:print(json.dumps({name:{'eligible':r.get('eligible'),'retrieval':r['metrics']['retrieval'],
        'abstention':r['metrics']['abstention']} for name,r in report['candidates'].items()}))


if __name__=='__main__':main()
