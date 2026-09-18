"""Small labeled lexical retrieval evaluation; no model calls or production writes."""
import json
from pathlib import Path
from rag import read_documents,rank

CASES=[
    ('申请退款','refund-v2'),
    ('商品未使用，签收第7天','refund-v2'),
    ('人工审批例外退款需要理由吗','refund-v2'),
    ('重复退款防护如何处理','refund-v2'),
    ('查询物流运单状态','shipping-v1'),
    ('包裹丢失怎么办','shipping-v1'),
    ('物流长期不更新','shipping-v1'),
    ('缺少订单，事实不完整','unknown-v1'),
    ('政策证据需要文档来源位置','unknown-v1'),
    ('天气预报',None),
    ('量子纠缠',None),
    ('zzzzzz',None),
]

def main():
    documents,chunks=read_documents()
    results=[]
    for query,expected in CASES:
        hits=rank(query,documents,chunks,top_k=4)
        ids=[h['id'] for h in hits]
        position=next((i+1 for i,value in enumerate(ids) if value==expected),None)
        results.append({'query':query,'expected':expected,'retrieved_ids':ids,
            'correct':bool(position) if expected else not hits,
            'reciprocal_rank':1/position if position else 0})
    relevant=[r for r in results if r['expected']]
    unrelated=[r for r in results if not r['expected']]
    report={'retriever':'bm25','mode':'offline','documents':len(documents),'chunks':len(chunks),
        'samples':len(results),'passed':all(r['correct'] for r in results),
        'recall_at_4':sum(r['correct'] for r in relevant)/len(relevant),
        'mrr_at_4':sum(r['reciprocal_rank'] for r in relevant)/len(relevant),
        'unrelated_abstention':sum(r['correct'] for r in unrelated)/len(unrelated),
        'results':results,'limitations':'12 synthetic queries over three bundled documents; not semantic retrieval or production accuracy. No generative-model quality evaluation.'}
    Path(__file__).with_name('evaluation_rag.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)

if __name__=='__main__':main()
