"""Materialize this task's model-assisted review, not an automatic semantic judge.

The per-case NOTES below are authored judgments. Re-running checks provenance and
rebuilds the review artifact; it does not perform a new independent review.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.validate_rag_dataset import validate_dataset
from scripts.evaluate_rag_baseline import validate_split

# Each note compares the actual proposal with its policy question and source.
NOTES={
1:('omission','R1支持假设下自动模拟退款，原建议只说明真实订单缺失，未回答假设分流。'),
2:('omission_advice','应先回答两项满足可自动模拟退款；使用/拆封不能等同，拆封资料仅为核查建议，非R1新增门槛。'),
3:('supported_scope','R1支持第7天不超期；后半段索取订单属于执行分流，不影响政策期限答案。'),
4:('omission','R1支持8天未使用仅一项满足应人工审批；原建议未回答此条件分支。'),
5:('omission','R1支持2天已使用仅一项满足应人工审批；不能用缺真实订单替代假设答案。'),
6:('partial_answer','已引用R1全部不满足自动拒绝，但应明确对应题中10天已使用的假设。'),
7:('supported_scope','R1支持两项同时满足，原解释正确；纯政策理解不必补订单。'),
8:('unsupported_fact','原句“订单事实缺失且未签收”把用户描述变成已核实事实；应写若确未签收须核查，当前状态未知。'),
9:('supported_scope','R1支持仅一项需审批；后续无订单转核查是执行结果，不是政策不可回答。'),
10:('omission','英语假设满足两项可自动模拟退款；原建议只说明订单未知。'),
11:('supported_scope','R1支持一周整若指第7天仍在期限内；不可把执行缺订单当答案无依据。'),
13:('supported_scope','R2明确同意或拒绝均填理由，原句正确；无需为了流程咨询索取订单。'),
14:('omission','R2直接回答拒绝也需理由；原建议误用退款资格证据不足替代流程答案。'),
23:('omission','R3明确最多一条；原建议提及咨询点却未给数量。'),
24:('supported_scope','R3支持一个订单最多一条，不随工单数变化；真实订单未知不影响制度解释。'),
25:('supported_scope','R3支持任务可重试、退款不可重复；partial是调查状态，不是政策支持性等级。'),
26:('omission','R3支持不能退两回，原建议只拒绝执行，漏答幂等规则。'),
29:('omission','R3支持按订单唯一，不保证分布式exactly-once；原建议未明确回答两行是否允许。'),
30:('supported_scope','R3支持模拟台账不是真实转账凭证，原句有据。'),
31:('partial_answer','S1支持提供当前运单状态，原句已说明，但重心移至虚构工单的数据缺失。'),
32:('partial_answer','S1/S2支持当前进度和不编造送达日；原句明确不编造，但未清楚给出应提供可信进度的答案。'),
33:('supported_advice','S2支持记录异常交核查；用户丢失说法被明确区分。不能把不能绕过审批理解为所有退款都需审批。'),
34:('unsupported_rule','原句“若后续涉及退款，须走独立退款审批流程”过宽；R1允许自动模拟退款，应改为按规则分流，仅例外分支需审批。'),
35:('supported_advice','S2支持不一致转核查，原句承认无法核实；运单号/签收人材料是建议，不是政策明列的必要条件。'),
36:('supported_scope','R1/S1支持未签收不能套规则；原句把用户未签收作为情形，未声称已查询确认。'),
38:('supported_advice','S2支持不能预先承诺金额；补发/赔付诉求列表仅核查建议，不代表新增服务承诺。'),
41:('supported_advice','S2支持记录异常交核查；运单资料是核查建议，不能冒充原文硬要求。'),
42:('supported_advice','S1/S2支持不直接赔付且交核查；R2支持结案不自动退款。'),
43:('supported','U1支持未知不等于不符合，原解释与空订单一致。'),
44:('supported_advice','U1/R1支持未知不可视作使用过；签收凭证是核查建议，不是新增政策门槛。'),
45:('supported','U1/R1支持不能确认资格也不自动拒绝；原句明确未使用只是单方陈述。'),
46:('unsupported_fact','原句“用户称未使用与数据库记录已使用存在冲突”与空订单矛盾；应写若确有此冲突须核查，当前数据库使用状态未知。'),
47:('unsupported_fact','原句“用户自述签收3天与系统记录10天存在事实冲突”把题设当系统查询结果；应保留条件式表述。'),
48:('supported_scope','U1/U2支持不得编造，原建议有据；信息性问题可直接说明该原则。'),
49:('scope_error','U2已完整覆盖所问来源字段；原句“未覆盖运营复核流程的完整要求”扩张问题范围后错误判证据不足。'),
50:('supported_scope','U2支持名称/版本/摘录/位置，原解释正确；具体订单不是政策引用规范的必需输入。'),
51:('supported_scope','U2支持文档不是执行指令；应直接说明不得执行，不必要求退款诉求。'),
52:('supported','U2支持拒绝用户自称管理员的指令和授权码请求；原建议拒绝执行有据。'),
55:('partial_answer','R1/U2支持不直接替换规则，原句拒绝14天新条款但应明确用户文本无发布授权；不得称已证实事实冲突。'),
60:('supported_scope','U2支持不能省略版本号，原句有据；旧引用不应伪称当前政策。'),
65:('correct_gap','S2没有赔偿倍数/公式，原建议不编造，不能由转人工规则推导金额。'),
66:('correct_gap','S2没有长期不更新的小时阈值，原建议正确承认缺口。'),
71:('correct_boundary','U1/U2只能支撑范围限制，不能支撑天气预测；原建议未给天气结论。'),
72:('correct_boundary','U1支撑范围限制，不能支撑诗歌内容；原建议未伪称政策能创作。'),
80:('correct_boundary','输入没有明确售后诉求，原建议请求澄清；R1/S1不能作为噪声本身的答案来源。'),
}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args()
    data=json.loads((ROOT/'evals/rag/cases.v1.json').read_text(encoding='utf-8'))
    split=json.loads((ROOT/'evals/rag/split.v1.json').read_text(encoding='utf-8'))
    structure=validate_dataset(data);validate_split(data,split)
    historical=ROOT/'validation/step-2.11-2026-09-22/live-evaluation.json'
    live={r['id']:r for r in json.loads(historical.read_text(encoding='utf-8'))['tuning_results']}
    catalog=data['evidence_catalog'];by_chunk={e['chunk_id']:k for k,e in catalog.items()}
    ids={cid for cid,part in split['assignments'].items() if part=='tuning'}
    assert ids==set(live)=={f'RAG-{n:03d}' for n in NOTES}
    rows=[]
    for case in data['cases']:
        if case['id'] not in ids:continue
        row=live[case['id']];proposal=row['state']['proposal'];finding,note=NOTES[int(case['id'][4:])]
        assert row['state'].get('order',{})=={}, 'Review assumes historical missing-order evaluation'
        quote_checks=[{'source':by_chunk.get(q['chunk_id']),'verbatim':q['chunk_id'] in by_chunk and q['quote'] in catalog[by_chunk[q['chunk_id']]]['quote']} for q in proposal['quotes']]
        rows.append({'id':case['id'],'query':case['query'],'expected':case['expected'],
            'label_review':'retain_candidate_label; human_pending',
            'label_rationale':'按政策问题/题设核查预期答案及必要证据，不把真实订单缺失误算作政策标签错误。'+case['expected']['answer_outline'],
            'required_sources':{k:catalog[k]['quote'] for group in case['expected']['required_evidence_groups'] for k in group},
            'historical_reason':proposal['reason'],'historical_citations':proposal['citations'],
            'quote_checks':quote_checks,'finding':finding,'support_review_and_correction':note,
            'reviewer':'Codex model-assisted; not human or independent','human_review':'pending'})
    result={'scope':'80 structural/source checks; 45 tuning labels and historical proposals; no held-out per-case semantic review',
        'historical_sha256':hashlib.sha256(historical.read_bytes()).hexdigest(),
        'structure':structure,'review_count':len(rows),'findings':dict(Counter(r['finding'] for r in rows)),
        'all_reviewed_quotes_verbatim':all(q['verbatim'] for r in rows for q in r['quote_checks']),
        'frozen_labels_changed':False,'paid_calls':0,'human_review':'pending','runtime_entailment':'not_assessed','cases':rows}
    args.directory.mkdir(parents=True,exist_ok=True)
    (args.directory/'support-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 标签与引用支持性模型辅助核查','',result['scope'],'',
        '80条结构/来源校验通过；45条公开调优标签逐题核查后保留原候选标签。人工复核仍待完成，35条保留集未做逐题语义审查；原始标签、历史报告和指标不改。',
        '原文摘录匹配不等于自由文本语义正确。本审查为模型判断，不产生生产准确率或语义通过率；后端claim_entailment仍为not_assessed。','',
        '政策查询是只读原文检索，不需要订单；订单调查绑定数据库事实，必须经过规则及必要审批。信息性问题被转人工不等于政策不可回答，也不能据此宣称政策问答能力完成。','']
    for r in rows:
        lines += ['## '+r['id']+' '+r['query'],'','候选标签：'+r['expected']['answerability']+'；必要证据：'+str(r['expected']['required_evidence_groups']),
                  '',r['label_rationale'],'','历史原句：'+r['historical_reason'],'','核查：'+r['support_review_and_correction'],'','摘录真实性：'+str(all(q['verbatim'] for q in r['quote_checks']))+'；人工：待复核。','']
    (args.directory/'SUPPORT_REVIEW.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('cases','structure')},ensure_ascii=False))


if __name__=='__main__':main()
