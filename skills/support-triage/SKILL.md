---
name: support-triage
description: 对意图不明确或政策未覆盖的售后工单进行保守分流。
---

# 售后分流

可用 search_policy 检查是否存在适用依据。意图不明、无依据或不属于售后范围时输出 escalate。
reason 说明需要补充的订单信息或诉求，不猜测退款资格。
citations 只能包含实际检索到的政策 ID；没有依据时使用空列表。
