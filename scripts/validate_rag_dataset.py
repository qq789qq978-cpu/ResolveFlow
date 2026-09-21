"""Validate frozen step-2.1 labels, without ranking, model calls or database IO."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag import KNOWLEDGE, read_documents

DATASET = ROOT / 'evals/rag/cases.v1.json'
CATEGORY_COUNTS = {'refund_rules': 12, 'approval': 10, 'idempotency': 8, 'shipping': 12,
                   'facts_and_evidence': 10, 'versions': 8, 'policy_gaps': 10, 'unrelated': 10}
ANSWERABILITY = {'supported', 'partial', 'unsupported', 'out_of_scope'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def normalized_query(query):
    text = unicodedata.normalize('NFKC', query).casefold()
    return ''.join(c for c in text if c.isalnum())


def render_review(data):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    def refs(values):
        return '、'.join(f'[{v}](#{v.lower()})' for v in values) or '无'
    lines = ['# RAG候选查询复核表（80条）', '',
             '来源：cases.v1.json。全部为Codex模型生成并由同一模型自查，80条均待人工复核，未做独立复核。', '',
             '未划分调优/验收集，未运行本集检索指标；答案边界是候选标注，不是已观测模型输出。', '',
             '所需证据组之间为“且”，每组内部为“或”。无直接答案证据不强迫检索器返回空列表；通用限制说明不能冒充所问事实。', '']
    labels = {'supported':'有据可答', 'partial':'只能部分回答', 'unsupported':'政策缺口', 'out_of_scope':'范围外/噪声'}
    for category in CATEGORY_COUNTS:
        lines += ['## '+category, '', '|编号|查询|答案类型 / 所需证据|回答边界|不得声称|人工复核|', '|---|---|---|---|---|---|']
        for case in data['cases']:
            if case['category'] != category:
                continue
            expected = case['expected']
            required = ' 且 '.join('('+' 或 '.join(f'[{v}](#{v.lower()})' for v in group)+')' for group in expected['required_evidence_groups']) or '无直接答案证据'
            if expected['guardrail_evidence']:
                required += '；限制说明：'+refs(expected['guardrail_evidence'])
            if expected['optional_evidence']:
                required += '；可选：'+refs(expected['optional_evidence'])
            row = [case['id'], case['query'], labels[expected['answerability']]+' / '+required,
                   expected['answer_outline'], '；'.join(expected['must_not_claim']), '待复核']
            lines.append('|'+ '|'.join(cell(value) for value in row)+'|')
        lines.append('')
    lines += ['## 冻结证据目录', '']
    for eid, evidence in sorted(data['evidence_catalog'].items()):
        lines += ['### '+eid, '',
                  f"[{evidence['source']}:{evidence['line_start']}](../../knowledge/{evidence['source']}#L{evidence['line_start']})；文档 `{evidence['document_id']}`，版本 `{evidence['version']}`，片段 `{evidence['chunk_id']}`。", '',
                  '> '+evidence['quote'].replace('\n','\n> '), '']
    return '\n'.join(lines)


def validate_dataset(data, directory=KNOWLEDGE):
    require(data['schema_version'] == 1, 'Unsupported schema version')
    require(data['dataset_id'] == 'resolveflow-rag-candidates-v1', 'Unexpected dataset id')
    documents, chunks = read_documents(directory)
    manifest = [{key: d[key] for key in ('id', 'title', 'source', 'version', 'sha256')} for d in documents]
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':')).encode()).hexdigest()
    require(data['corpus'] == {'sha256': digest, 'documents': manifest}, 'Corpus snapshot drift')
    by_document = {d['id']: d for d in documents}
    by_chunk = {c['chunk_id']: c for c in chunks}
    catalog = data['evidence_catalog']
    require(set(catalog) == {'R1', 'R2', 'R3', 'S1', 'S2', 'U1', 'U2'}, 'Evidence catalog ids differ')
    require({e['chunk_id'] for e in catalog.values()} == set(by_chunk), 'Evidence chunk coverage differs')
    for eid, evidence in catalog.items():
        chunk = by_chunk.get(evidence['chunk_id'])
        require(chunk is not None, eid + ': missing chunk')
        doc = by_document[chunk['document_id']]
        expected = {'document_id': doc['id'], 'source': doc['source'], 'version': doc['version'],
                    'document_sha256': doc['sha256'], 'chunk_id': chunk['chunk_id'],
                    'line_start': chunk['line_start'], 'line_end': chunk['line_end'], 'quote': chunk['text']}
        require(evidence == expected, eid + ': quote, location or version mismatch')
    policy = data['annotation_policy']
    require(policy['origin'] == 'synthetic_model_authored'
            and policy['semantic_review'] == 'same_model_self_review'
            and policy['human_review'] == 'pending'
            and policy['independent_review'] == 'not_performed', 'Unsubstantiated dataset review claim')
    require(policy['project_model_api_calls'] == 0, 'This dataset version is offline')
    cases = data['cases']
    require(len(cases) == 80, 'Expected exactly 80 candidates')
    ids, queries = set(), set()
    categories, outcomes, tags, groups = Counter(), Counter(), Counter(), Counter()
    for index, case in enumerate(cases, 1):
        cid = case['id']
        require(cid == f'RAG-{index:03d}' and cid not in ids, 'Duplicate or unordered case id')
        ids.add(cid)
        query = case['query']
        require(isinstance(query, str) and 0 < len(query.strip()) <= 4000, cid + ': invalid query')
        canonical = normalized_query(query)
        require(bool(canonical) and canonical not in queries, cid + ': duplicate normalized query')
        queries.add(canonical)
        require(case['category'] in CATEGORY_COUNTS, cid + ': unknown category')
        require(case['split'] == 'unassigned', cid + ': step 2.2 split is not assigned yet')
        require(re.fullmatch(r'[a-z][a-z0-9-]+', case['leakage_group']) is not None, cid + ': missing leakage group')
        require(isinstance(case['tags'], list) and all(isinstance(t,str) and t for t in case['tags'])
                and len(case['tags']) == len(set(case['tags'])), cid + ': invalid tags')
        expected = case['expected']
        status = expected['answerability']
        require(status in ANSWERABILITY, cid + ': unknown answerability')
        required = expected['required_evidence_groups']
        require(isinstance(required, list), cid + ': evidence groups must be a list')
        references = set()
        for group in required:
            require(isinstance(group, list) and bool(group) and len(group) == len(set(group)), cid + ': empty/duplicate evidence group')
            require(all(e in catalog for e in group), cid + ': unknown evidence reference')
            references.update(group)
        require((status in {'supported', 'partial'}) == bool(required), cid + ': answerability/evidence mismatch')
        for field in ('optional_evidence', 'guardrail_evidence'):
            values = expected[field]
            require(isinstance(values, list) and len(values) == len(set(values))
                    and all(e in catalog for e in values), cid + ': invalid ' + field)
        require(not references.intersection(expected['optional_evidence']), cid + ': redundant optional evidence')
        require(not references.intersection(expected['guardrail_evidence']), cid + ': answer/guardrail evidence overlap')
        require(isinstance(expected['answer_outline'], str) and bool(expected['answer_outline'].strip()), cid + ': missing answer outline')
        require(isinstance(expected['must_not_claim'], list) and bool(expected['must_not_claim'])
                and all(isinstance(s, str) and s.strip() for s in expected['must_not_claim']), cid + ': missing forbidden claims')
        annotation = case['annotation']
        require(annotation['query_origin'] == 'synthetic_model_authored'
                and annotation['label_origin'] == 'model_reading_frozen_corpus'
                and annotation['author'] == 'Codex', cid + ': missing model provenance')
        require(annotation['created_on'] == data['created_on']
                and annotation['corpus_sha256'] == digest, cid + ': annotation snapshot mismatch')
        require(annotation['review_status'] == 'same_model_self_reviewed'
                and annotation['human_review_status'] == 'pending'
                and annotation['independent_review_status'] == 'not_performed', cid + ': unsubstantiated review claim')
        require(annotation['basis_evidence'] == sorted(references | set(expected['optional_evidence'])), cid + ': annotation basis differs')
        absence = annotation['absence_checked_against']
        require(absence == (list(by_document) if status in {'partial','unsupported','out_of_scope'} else []), cid + ': missing absence scope')
        categories[case['category']] += 1
        outcomes[status] += 1
        tags.update(case['tags'])
        groups[case['leakage_group']] += 1
    require(dict(categories) == CATEGORY_COUNTS, 'Category coverage differs')
    return {'passed': True, 'dataset_id': data['dataset_id'], 'cases': len(cases),
            'categories': dict(categories), 'answerability': dict(outcomes), 'tags': dict(tags),
            'leakage_groups': len(groups), 'multi_case_groups': {k:v for k,v in groups.items() if v>1},
            'documents': len(documents), 'evidence_units': len(catalog), 'corpus_sha256': digest,
            'review': {'same_model_self_reviewed': 80, 'human_pending': 80, 'independent_reviewed': 0},
            'split': 'unassigned', 'retrieval_executed': False, 'model_api_calls': 0,
            'scope': 'Structural and source integrity checks; not semantic label correctness or retrieval quality.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DATASET)
    parser.add_argument('--knowledge', type=Path, default=KNOWLEDGE)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--review-md', type=Path, help='Render the validated candidates for review')
    args = parser.parse_args()
    if args.report and args.report.exists():
        parser.error('Use a new report path; keep historical evidence')
    try:
        data = json.loads(args.dataset.read_text(encoding='utf-8'))
        report = validate_dataset(data, args.knowledge)
        report['dataset_sha256'] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
        if args.review_md:
            args.review_md.parent.mkdir(parents=True, exist_ok=True)
            args.review_md.write_text(render_review(data), encoding='utf-8')
    except (ValueError, KeyError, TypeError, OSError) as error:
        report = {'passed': False, 'error_type': type(error).__name__, 'error': str(error)}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
