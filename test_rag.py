import os
from pathlib import Path
import pytest

from rag import read_documents, rank, retrieve, sync_index

def test_retrieval_returns_source_and_correct_policy(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    results=retrieve('未使用，签收三天，申请退款')
    assert results[0]['id']=='refund-v2'
    assert results[0]['source']=='refund.md'
    assert results[0]['line_start'] > 1
    assert len(results[0]['document_sha256'])==64
    assert '全部满足自动模拟退款' in results[0]['text']

@pytest.mark.parametrize('query',['','天气预报','量子纠缠','zzzzzz'])
def test_unrelated_queries_have_no_evidence(query,monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert retrieve(query)==[]

def write_doc(path, body, doc_id='custom-v1'):
    path.write_text(f"---\nid: {doc_id}\ntitle: 测试政策\nversion: '1'\n---\n{body}",encoding='utf-8')

def test_chunk_bounds_overlap_and_stable_provenance(tmp_path):
    write_doc(tmp_path/'policy.md','退款政策'+('甲乙丙丁'*300))
    docs,chunks=read_documents(tmp_path)
    assert len(chunks)>1 and all(len(c['text'])<=500 for c in chunks)
    assert chunks[0]['text'][-60:]==chunks[1]['text'][:60]
    assert read_documents(tmp_path)==(docs,chunks)
    old_ids={c['chunk_id'] for c in chunks}
    write_doc(tmp_path/'policy.md','退款政策已更新')
    assert old_ids.isdisjoint(c['chunk_id'] for c in read_documents(tmp_path)[1])

def test_duplicate_and_invalid_metadata_rejected(tmp_path):
    write_doc(tmp_path/'one.md','退款政策')
    write_doc(tmp_path/'two.md','物流政策')
    with pytest.raises(ValueError,match='duplicate'):read_documents(tmp_path)
    (tmp_path/'two.md').write_text('no metadata',encoding='utf-8')
    with pytest.raises(ValueError,match='metadata'):read_documents(tmp_path)

def test_query_limits_and_top_k(monkeypatch):
    docs,chunks=read_documents()
    assert len(rank('退款',docs,chunks,top_k=1))==1
    with pytest.raises(ValueError):rank('x'*4001,docs,chunks)
    with pytest.raises(ValueError):rank('退款',docs,chunks,top_k=0)

def test_database_failure_is_not_replaced_by_bundled_documents(monkeypatch):
    monkeypatch.setenv('DATABASE_URL','not-used')
    def fail(*args):raise RuntimeError('DB unavailable')
    monkeypatch.setattr('storage.Store.connect',fail)
    with pytest.raises(RuntimeError,match='DB unavailable'):retrieve('退款')
