---
name: support-triage
description: 对意图不明确或政策未覆盖的售后工单进行保守分流。
---

# 售后分流

可用 search_policy 检查是否存在适用依据。意图不明、无依据或不属于售后范围时输出 escalate。
reason 说明需要补充的订单信息或诉求，不猜测退款资格。
输出citation_schema=2。citations只能包含本次实际检索到的完整chunk_id；quotes逐项记录相同chunk_id和逐字原文quote。部分有据标记evidence_status=partial，无依据标记insufficient，均输出escalate；没有依据时citations、quotes都为空。不要把候选原文的存在声称为完整支持整个问题。
