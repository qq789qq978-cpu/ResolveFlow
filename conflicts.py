"""Conservative explicit-claim checks, not a complete natural-language parser."""
import re

def detect_conflicts(ticket, order):
    conflicts = []
    if not order:
        return conflicts
    # Require positive wording: '未使用' must not match '已使用'.
    used_claim = any(t in ticket for t in ['已使用', '已经使用', '用过了', '使用过'])
    late_claim = bool(re.search(r'(超过|超出|大于)\s*(7|七)\s*天',ticket))
    match = re.search(r'签收\s*(\d+)\s*天',ticket)
    if used_claim and not order['used']:
        conflicts.append('用户明确表示已使用，但订单记录为未使用')
    if (late_claim or match and int(match.group(1)) > 7) and order['days'] <= 7:
        conflicts.append('用户描述签收超过七天，但订单记录未超过七天')
    return conflicts
