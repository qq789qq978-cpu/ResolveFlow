---
name: refund-handling
description: 调查退款或退货工单，查询订单与退款政策，形成带证据的建议。
---

# 退款处理

使用 lookup_order 获取当前订单，使用 search_policy 查询退款政策。
核对签收状态、签收天数、是否使用。citations使用本次检索到的完整chunk_id，quotes逐项记录相同chunk_id及逐字原文quote；退款处理必须引用资格规则整段，不以审批或台账段落代替。
订单不存在、政策缺失、未签收或描述冲突时建议 escalate。退款诉求有充分政策证据时输出 refund 建议，并说明每项条件的事实，不替代规则引擎作最终授权。
已签收前提下，签收不超过7天且未使用将自动模拟退款；两项都不满足自动拒绝；仅满足一项交人工审批。依据 refund-v2，不声称真实款项到账。最终分流由确定性代码执行。
输出符合 Proposal 的 action、reason、citation_schema=2、evidence_status、citations、quotes。只部分有据标记partial，无依据标记insufficient，两者均escalate；没有引用时两个列表都为空。金额来自订单，不从用户文本取值。咨询到账、费用等不等于请求执行退款。
