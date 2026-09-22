"""Isolated schema tests with synthetic vectors, not model-quality measurements."""
import copy
import json
import os
import time

import pytest

from embedding_contract import CONTRACT_ID
import semantic
from rag import read_index
from policy_releases import review,activate,prepare
from test_jobs import system,submit,headers
from test_policy_releases import bundle
import jobs

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Isolated pgvector required')


@pytest.fixture
def indexed(system,monkeypatch):
    store,engine,client=system
    async def synthetic(text,**kwargs):
        return {'vector':[1.]+[0.]*383,'tokens':10,'contract':CONTRACT_ID}
    monkeypatch.setattr(semantic,'_encode',synthetic)
    semantic.build_index(store)
    return store,engine,client


def test_exact_database_rank_and_hybrid_source_identity(indexed):
    docs,chunks,release=read_index()
    hits=semantic.dense_rank('fixture',docs,chunks,release)
    assert len(hits)==4 and len({h['chunk_id'] for h in hits})==4
    assert {h['text'] for h in hits} <= {c['text'] for c in chunks}
    assert all(h['release']==release['token'] for h in hits)


@pytest.mark.parametrize('corruption',['missing','vector','identity','manifest','contract'])
def test_corrupt_or_partial_batch_falls_back(indexed,corruption):
    store,_,_=indexed
    with store.connect() as c:
        if corruption=='missing':c.execute('DELETE FROM rf_policy_vectors WHERE chunk_id=(SELECT min(chunk_id) FROM rf_policy_vectors)')
        elif corruption=='vector':c.execute('UPDATE rf_policy_vectors SET embedding=%s::public.vector',(json.dumps([0.,1.]+[0.]*382),))
        elif corruption=='identity':c.execute("UPDATE rf_policy_vectors SET text_sha='corrupt'")
        elif corruption=='manifest':c.execute("UPDATE rf_vector_batches SET manifest='[]'")
        else:c.execute("UPDATE rf_vector_batches SET contract='wrong-model'")
    docs,chunks,release=read_index()
    hits,meta=semantic.search('refund',docs,chunks,release)
    assert hits and meta['used']=='bm25'


@pytest.mark.parametrize('status',['revoked','expired'])
def test_current_governance_filters_before_vector_top_k(indexed,status):
    store,_,_=indexed
    docs,chunks,release=read_index();doc=next(d for d in docs if d['id']=='refund-v2')
    meta=copy.deepcopy(doc['governance'])
    if status=='revoked':meta['status']='revoked'
    else:meta['effective_until']='2026-09-22T00:00:00Z'
    with store.connect() as c:review(c,doc['sha256'],meta,actor='qa',reason='Filter test',expected_generation=1)
    docs,chunks,release=read_index()
    hits=semantic.dense_rank('refund',docs,chunks,release)
    assert len(hits)==4 and all(h['id']!='refund-v2' for h in hits)


def test_failed_rebuild_retains_entire_previous_batch(indexed,monkeypatch):
    store,_,_=indexed
    def snapshot():
        with store.connect() as c:
            return c.execute('SELECT chunk_id,embedding::text FROM rf_policy_vectors ORDER BY chunk_id').fetchall()
    before=snapshot();original=semantic.encode;calls=[]
    def failing(*a,**k):
        calls.append(1)
        if len(calls)==3:raise semantic.SemanticUnavailable('encoder_unavailable')
        return original(*a,**k)
    monkeypatch.setattr(semantic,'encode',failing)
    with pytest.raises(semantic.SemanticUnavailable):semantic.build_index(store)
    assert snapshot()==before


def test_publication_during_encoding_blocks_stale_result(indexed,monkeypatch,tmp_path):
    store,_,_=indexed;docs,chunks,release=read_index();old=semantic._encode
    async def changed(*a,**k):
        with store.connect() as c:activate(c,payload=prepare(bundle(tmp_path)),actor='qa',reason='Mid-query switch',expected_generation=1)
        return await old(*a,**k)
    monkeypatch.setattr(semantic,'_encode',changed)
    with pytest.raises(semantic.SemanticUnavailable,match='release_changed'):semantic.dense_rank('refund',docs,chunks,release)


def test_database_block_is_bounded_by_total_deadline(indexed):
    store,_,_=indexed;docs,chunks,release=read_index()
    with store.connect() as blocker:
        blocker.execute('LOCK TABLE rf_vector_batches IN ACCESS EXCLUSIVE MODE')
        start=time.monotonic()
        with pytest.raises(semantic.SemanticUnavailable,match='timeout'):
            semantic.dense_rank('refund',docs,chunks,release)
        assert 4.5<time.monotonic()-start<6.5


def test_hybrid_old_approval_after_rollback_is_blocked(indexed,monkeypatch,tmp_path):
    store,engine,client=indexed;monkeypatch.setenv('RETRIEVAL_MODE','hybrid')
    # Jobs' real MCP subprocess does not inherit test monkeypatches. Keep tool
    # transport in-process here; actual hybrid MCP is covered by isolated QA.
    import engine as engine_module
    import rag
    def tools(order_id,owner,calls):return rag.retrieve(calls[0][1]['query']),store.order(order_id,owner)
    monkeypatch.setattr(engine_module,'call_tools',tools)
    rid=submit(client)
    jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='awaiting_approval'
    saved=store.get(rid)['state']['evidence']
    with store.connect() as c:activate(c,payload=prepare(bundle(tmp_path)),actor='qa',reason='B',expected_generation=1)
    with store.connect() as c:activate(c,release_id='demo-policy-2026-09-21',actor='qa',reason='A again',expected_generation=2)
    assert client.post('/api/runs/'+rid+'/approval',headers=headers('reviewer'),json={'approved':True,'reason':'Old A'}).status_code==202
    jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='escalated' and store.get(rid)['state']['evidence']==saved
