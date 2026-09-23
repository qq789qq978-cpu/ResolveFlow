"""Frozen, real CPU cross-encoder experiment. Tuning only; network must be off."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():parser.error('Use a new report')
    from scripts.assess_reranker import digest,validate_dataset,validate_split
    from scripts.evaluate_rag_baseline import aggregate,score_case
    dataset=json.loads((ROOT/'evals/rag/cases.v1.json').read_text())
    split=json.loads((ROOT/'evals/rag/split.v1.json').read_text())
    cached=json.loads((ROOT/'validation/step-2.8-2026-09-22/tuning.json').read_text())
    validate_dataset(dataset);validate_split(dataset,split)
    assert cached['dataset_sha256']==digest(dataset) and cached['split_sha256']==digest(split)
    cases=[c for c in dataset['cases'] if split['assignments'][c['id']]=='tuning']
    manifest=json.loads((args.model/'manifest.json').read_text())
    assert manifest['revision']=='1427fd652930e4ba29e8149678df786c240d8825'
    for name,sha in manifest['sha256'].items():assert hashlib.sha256((args.model/name).read_bytes()).hexdigest()==sha
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    import torch
    from transformers import AutoTokenizer,AutoModelForSequenceClassification
    torch.set_num_threads(2)
    start=time.monotonic()
    tokenizer=AutoTokenizer.from_pretrained(args.model,local_files_only=True,trust_remote_code=False)
    model=AutoModelForSequenceClassification.from_pretrained(args.model,local_files_only=True,trust_remote_code=False,use_safetensors=True).eval()
    loaded=time.monotonic()-start
    original={name:{r['id']:r for r in cached['candidates'][name]['rows']} for name in ('0.25','bm25','dense')}
    assert all(set(rows)=={c['id'] for c in cases} for rows in original.values())
    results={k:[] for k in ('baseline','reranked_top4','reranked_union')}
    details=[];times=[];pairs=0;truncated=0
    for case in cases:
        cid=case['id'];base=original['0.25'][cid]
        pool={h['chunk_id']:h for h in original['bm25'][cid]['hits']+original['dense'][cid]['hits']}
        hits=list(pool.values());scores={}
        started=time.monotonic()
        for pos in range(0,len(hits),8):
            batch=hits[pos:pos+8]
            texts=[(case['query'],h['text']) for h in batch]
            lengths=tokenizer(texts,truncation=False)['input_ids']
            truncated+=sum(len(ids)>512 for ids in lengths)
            encoded=tokenizer(texts,padding=True,truncation=True,max_length=512,return_tensors='pt')
            with torch.inference_mode():values=model(**encoded).logits.flatten().tolist()
            scores.update({h['chunk_id']:v for h,v in zip(batch,values)})
            pairs+=len(batch)
        elapsed=time.monotonic()-started;times.append(elapsed)
        rank=lambda values:sorted(values,key=lambda h:(-scores[h['chunk_id']],h['chunk_id']))[:4]
        selections={'baseline':base['hits'],'reranked_top4':rank(base['hits']),'reranked_union':rank(hits)}
        for name,selected in selections.items():results[name].append(score_case(case,selected,base['proposal'],dataset['evidence_catalog']))
        details.append({'id':cid,'seconds':elapsed,'scores':scores,'selected':{k:[h['chunk_id'] for h in v] for k,v in selections.items()}})
    metrics={k:aggregate(v) for k,v in results.items()}
    assert metrics['baseline']==cached['candidates']['0.25']['metrics']
    report={'completed':True,'kind':'actual_local_cross_encoder','model':manifest,'partition':'tuning','samples':45,
        'held_out_evaluated':0,'paid_calls':0,'pairs_scored':pairs,'truncated_pairs':truncated,
        'parameters':{'max_length':512,'batch_size':8,'threads':2,'device':'cpu','input':'query + original chunk text'},
        'cold_load_seconds':loaded,'latency_seconds':{'p50':statistics.median(times),'p95':sorted(times)[int(.95*(len(times)-1))]},
        'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        'metrics':metrics,'tuning_details':details,'generation_quality_evaluated':False,
        'protocol_sha256':hashlib.sha256((ROOT/'RAG_SUPPLEMENT_PLAN.md').read_bytes()).hexdigest(),
        'limitations':['Model-authored labels pending human review.','Cached candidate pools, not an end-to-end live retrieval latency.','No held-out generalization or generated answer quality claim.']}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('completed','pairs_scored','truncated_pairs','cold_load_seconds','latency_seconds','peak_rss_mib')}))
    print(json.dumps({k:v['retrieval'] for k,v in metrics.items()}))

if __name__=='__main__':main()
