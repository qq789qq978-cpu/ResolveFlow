"""Small, auditable lexical query expansion; never an execution policy."""
import re
import unicodedata

# Synonyms/translation only, derived from the tuning partition and corpus.
# No case IDs, document IDs, retrieved text rewriting, or query-to-answer lookup.
ALIASES = (
    (('parcel', 'package'), '包裹'),
    (('tracking', 'shipment'), '物流 运单'),
    (('not changed', 'not updated', 'stalled', '停更'), '不更新'),
    (('long time',), '长期'),
    (('ledger',), '台账'),
    (('same order',), '一个订单'),
    (('duplicate', 'two rows', '两次', '两回'), '重复'),
    (('retry', 'retries'), '重试'),
    (('simulated',), '模拟'),
    (('customer', '客户'), '用户'),
    (('自述', '声称'), '描述'),
    (('系统里', '系统记录'), '数据库'),
    (('新版', '版本号'), '版本'),
    (('贴了', '粘贴'), '用户文本'),
)

PROFILES = {
    'original': {'title_weight': 2, 'expand': False},
    'title_once': {'title_weight': 1, 'expand': False},
    'body_only': {'title_weight': 0, 'expand': False},
    'expanded': {'title_weight': 2, 'expand': True},
    'expanded_title_once': {'title_weight': 1, 'expand': True},
    'expanded_body_only': {'title_weight': 0, 'expand': True},
}
DEFAULT_PROFILE = 'expanded'  # Selected on tuning only; frozen before held-out scoring.
EXPANSION_WEIGHT = 0.5


def expansions(query):
    query = unicodedata.normalize('NFKC', query).lower()
    output = []
    for aliases, target in ALIASES:
        if any(re.search(r'(?<![a-z0-9])' + re.escape(a) + r'(?![a-z0-9])', query)
               if a.isascii() else a in query for a in aliases):
            output.append(target)
    return output
