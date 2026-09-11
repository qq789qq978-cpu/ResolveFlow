"""Versioned deterministic decision; LLM never authorizes a monetary action."""
VERSION='refund-v2'

def decide(order, supported, conflicts):
    valid=(bool(order) and order.get('status')=='delivered'
           and type(order.get('days')) is int and order['days']>=0
           and type(order.get('used')) is bool
           and type(order.get('amount')) is int and order['amount']>0)
    if not valid or not supported or conflicts:
        return {'route':'escalated','policy_version':VERSION,'checks':[],
                'reason':'订单未签收、事实不完整、证据不足或描述冲突，需要人工核查。'}
    checks=[{'name':'签收不超过7天','passed':order['days']<=7},
            {'name':'商品未使用','passed':not order['used']}]
    count=sum(c['passed'] for c in checks)
    route={2:'auto_approved',1:'awaiting_approval',0:'auto_rejected'}[count]
    reason={2:'两项退款条件均满足，自动执行模拟退款。',1:'仅满足一项退款条件，等待人工审批例外处理。',0:'两项退款条件均不满足，自动拒绝退款。'}[count]
    return {'route':route,'policy_version':VERSION,'checks':checks,'reason':reason}
