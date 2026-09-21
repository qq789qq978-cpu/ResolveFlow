"""Freeze query families before scoring. No ranking, model, or database calls."""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_rag_dataset import DATASET, normalized_query, validate_dataset

SPLIT = ROOT / 'evals/rag/split.v1.json'

# Reviewed by the same authoring model, not an independent human review.
# Keep contrastive variants of one intent together, even if labels differ.
MERGES = {
    'refund-eligibility': ['refund-both-conditions', 'refund-deadline-boundary',
                           'refund-one-condition', 'refund-no-conditions'],
    'refund-idempotence': ['refund-unique-order', 'refund-retry', 'refund-duplicate-meaning'],
    'real-money-boundary': ['refund-simulated', 'gap-refund-arrival'],
    'lost-compensation': ['shipping-lost', 'shipping-compensation', 'gap-compensation-formula'],
    'stale-tracking': ['shipping-stale', 'gap-stale-threshold'],
    'incomplete-facts': ['facts-missing-order', 'facts-unknown-days', 'facts-unknown-use'],
    'conflicting-facts': ['facts-user-vs-db', 'shipping-status-conflict'],
    'untrusted-instructions': ['instruction-vs-data', 'version-user-claim'],
    'available-versions': ['version-current', 'version-historical', 'version-future'],
    'version-lifecycle': ['version-effective-date', 'version-cross-docs'],
}

# IDs below index the frozen public-query inventory (one-based).
EXPOSURE = {
    'refund-eligibility': ['rag:01', 'rag:02', 'business:02', 'business:04', 'business:06', 'legacy:01'],
    'refund-before-delivery': ['business:07', 'legacy:08'],
    'approval-reason': ['rag:03'],
    'refund-idempotence': ['rag:04'],
    'shipping-in-transit': ['rag:05', 'business:11', 'legacy:03', 'legacy:07'],
    'lost-compensation': ['rag:06'],
    'stale-tracking': ['rag:07'],
    'incomplete-facts': ['rag:08', 'business:08', 'legacy:04'],
    'conflicting-facts': ['business:09', 'business:10'],
    'evidence-provenance': ['rag:09'],
    'untrusted-instructions': ['business:12', 'legacy:09'],
    'ood-weather': ['rag:10'],
    'ood-poetry': ['legacy:05'],
    'ood-noise': ['rag:12'],
}


def digest(value):
    """Canonical JSON digest; invariant to Git's newline conversion."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def public_inventory(root=ROOT):
    result = []
    for prefix, filename in [('rag', 'evaluate_rag.py'), ('business', 'evaluate_v3.py')]:
        tree = ast.parse((root / filename).read_text(encoding='utf-8-sig'))
        values = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'CASES' for t in node.targets))
        for index, row in enumerate(values, 1):
            result.append({'id': f'{prefix}:{index:02d}', 'source': filename,
                           'query': row[0 if prefix == 'rag' else 1]})
    for index, row in enumerate(json.loads((root / 'eval_cases.json').read_text(encoding='utf-8')), 1):
        result.append({'id': f'legacy:{index:02d}', 'source': 'eval_cases.json', 'query': row['ticket']})
    return result


def similarity(a, b):
    def grams(text):
        text = normalized_query(text)
        return {text[i:i+2] for i in range(len(text)-1)}
    x, y = grams(a), grams(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def build_split(data, public):
    aliases = {member: family for family, members in MERGES.items() for member in members}
    families = {}
    for case in data['cases']:
        family = aliases.get(case['leakage_group'], case['leakage_group'])
        families.setdefault(family, []).append(case)
    inventory = {row['id']: row for row in public}
    assert set(EXPOSURE) <= set(families)
    groups, assignments = [], {}
    for family, cases in sorted(families.items()):
        references = EXPOSURE.get(family, [])
        assert all(ref in inventory for ref in references)
        side = 'tuning' if references else 'held_out'
        groups.append({'family': family, 'original_groups': sorted({c['leakage_group'] for c in cases}),
                       'case_ids': [c['id'] for c in cases], 'split': side,
                       'public_overlap': references,
                       'review_basis': 'same_intent_or_contrastive_variant_of_public_query' if references
                       else 'distinct_question_intent_after_same_model_review; shared_policy_is_not_independence'})
        assignments.update({c['id']: side for c in cases})
    # Lexical screen supplements the manual intent review; it cannot find all paraphrases.
    cross_pairs = []
    public_pairs = []
    cases = data['cases']
    for i, a in enumerate(cases):
        for b in cases[i+1:]:
            if assignments[a['id']] != assignments[b['id']] and similarity(a['query'], b['query']) >= .25:
                cross_pairs.append([a['id'], b['id']])
        if assignments[a['id']] == 'held_out':
            for b in public:
                if similarity(a['query'], b['query']) >= .25:
                    public_pairs.append([a['id'], b['id']])
    return {'schema_version': 1, 'split_id': 'resolveflow-rag-split-v1',
            'dataset_sha256': digest(data), 'corpus_sha256': data['corpus']['sha256'],
            'created_on': '2026-09-21', 'frozen_before_baseline': True,
            'method': 'public_exposure_blocked_family_split; all exposed families tuning, remaining held_out',
            'review': 'same_model_only; human_pending; not_blind_or_externally_independent',
            'public_inventory': public, 'public_inventory_sha256': digest(public),
            'groups': groups, 'assignments': assignments,
            'counts': dict(Counter(assignments.values())),
            'lexical_screen': {'normalized_bigram_jaccard_threshold': .25,
                               'cross_split_pairs': cross_pairs,
                               'held_out_public_pairs': public_pairs},
            'limitations': ['Shared corpus and author; no independent human labels.',
                            'Public repository labels are visible; held_out means excluded from tuning only.',
                            'Three enumerated public evaluation files reviewed; not a proof of no prior model exposure.',
                            'Exposure-based allocation is not random or stratified; report per-split counts.',
                            'After baseline exposure, do not tune on held-out individual failures.']}


def validate_split(data, split, public=None):
    expected = build_split(data, public if public is not None else public_inventory())
    if split != expected:
        raise ValueError('Frozen split or source inventory drift; do not silently reassign held-out queries')
    if split['lexical_screen']['cross_split_pairs'] or split['lexical_screen']['held_out_public_pairs']:
        raise ValueError('Unresolved lexical overlap requires review before freezing')
    return split


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=SPLIT)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Frozen manifests cannot be overwritten; use a new version')
    data = json.loads(DATASET.read_text(encoding='utf-8'))
    validate_dataset(data)
    split = build_split(data, public_inventory())
    validate_split(data, split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(split, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'counts': split['counts'], 'families': len(split['groups']),
                      'split_sha256': digest(split), 'retrieval_executed': False}))


if __name__ == '__main__':
    main()
