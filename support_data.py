import re

POLICIES = [
    {"id": "refund-v2", "text": "退款 refund：已签收且事实完整、无冲突的订单，检查签收不超过7天、商品未使用两项条件；全部满足自动模拟退款，全部不满足自动拒绝，仅满足一项等待人工审批例外处理。未签收、证据不足或事实冲突转人工核查。"},
    {"id": "shipping-v1", "text": "物流 shipping：运输中的订单提供运单状态，签收后按退款政策处理，不自动承诺赔付。"},
    {"id": "unknown-v1", "text": "缺少订单、政策依据不足或不属于售后问题时转人工，不编造事实。"},
]
ORDERS = {
    "RF-1004": {"id": "RF-1004", "owner": "demo", "amount": 15900, "days": 3, "used": True, "status": "delivered"},
    "RF-1001": {"id": "RF-1001", "owner": "demo", "amount": 29900, "days": 3, "used": False, "status": "delivered"},
    "RF-1002": {"id": "RF-1002", "owner": "demo", "amount": 12900, "days": 12, "used": True, "status": "delivered"},
    "RF-1003": {"id": "RF-1003", "owner": "demo", "amount": 8900, "days": 0, "used": False, "status": "shipping"},
}

def retrieve(query: str) -> list[dict]:
    """Small, auditable character-bigram lexical baseline (not vector retrieval)."""
    def tokens(text):
        clean = re.sub(r"\s+", "", text.lower())
        return set(clean[i:i + 2] for i in range(len(clean) - 1))
    q = tokens(query)
    scored = [(len(q & tokens(p["text"])), p) for p in POLICIES]
    return [dict(p, score=s) for s, p in sorted(scored, key=lambda x: -x[0])[:2] if s > 0]

