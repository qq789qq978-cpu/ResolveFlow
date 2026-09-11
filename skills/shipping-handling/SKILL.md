---
name: shipping-handling
description: 调查物流或快递状态工单，依据当前订单事实答复，不承诺未经确认的赔付。
---

# 物流处理

调用 lookup_order 核对订单，调用 search_policy 获取物流政策。
仅说明工具返回的配送状态；缺少运单或预计送达时间时明确未知，不编造日期。
信息足够时输出 reply 并引用物流政策，订单缺失或诉求涉及未覆盖的赔付时输出 escalate。
退款属于另一处理流程，不能通过物流答复绕过审批。
输出符合 Proposal 的 action、reason、citations。
