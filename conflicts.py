"""Conservative explicit-claim checks, not a complete natural-language parser."""
import re

def detect_conflicts(ticket, order):
    conflicts = []
    if not order:
        return conflicts
    # Remove explicit negative phrases before looking for positive substrings.
    negative = r'(?:没有|没|未曾|从未|未)\s*(?:使用|用)(?:过)?'
    unused_claim = bool(re.search(negative, ticket))
    positive_text = re.sub(negative, '', ticket)
    used_claim = any(t in positive_text for t in ['已使用', '已经使用', '用过了', '使用过'])
    late_claim = bool(re.search(r'(超过|超出|大于)\s*(7|七)\s*天',ticket))
    match = re.search(r'签收\s*(\d+)\s*天',ticket)
    if used_claim and order.get('used') is False:
        conflicts.append('用户明确表示已使用，但订单记录为未使用')
    if unused_claim and order.get('used') is True:
        conflicts.append('用户明确表示未使用，但订单记录为已使用')
    if (late_claim or match and int(match.group(1)) > 7) and type(order.get('days')) is int and order['days'] <= 7:
        conflicts.append('用户描述签收超过七天，但订单记录未超过七天')
    return conflicts
