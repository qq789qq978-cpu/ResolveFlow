"""Local embedding contracts, safe fallback, deadline and fusion behavior."""
import asyncio
import copy
import time

import pytest

from embedding_contract import validate_vector
from policy_releases import context,prepare
from rag import read_documents,rank
import semantic


@pytest.mark.parametrize('vector',[[0.]*384,[1.]*383,[float('nan')]+[0.]*383,[float('inf')]+[0.]*383,[True]+[0.]*383])
def test_reject_invalid_vectors(vector):
    with pytest.raises(ValueError):validate_vector(vector)


def test_rrf_preserves_provenance_and_unions_by_chunk():
    a={'chunk_id':'a','text':'original a','source':'policy.md'}
    b={'chunk_id':'b','text':'original b','source':'policy.md'}
    result=semantic.fuse([a,b],[b,a],.25,2)
    assert [h['chunk_id'] for h in result]==['a','b']
    assert result[0]['text']=='original a' and result[0]['score']==pytest.approx(.75/61+.25/62)
    assert 'retrieval' not in a


def test_cancellable_total_deadline():
    cancelled=[]
    async def slow():
        try:await asyncio.sleep(60)
        finally:cancelled.append(True)
    start=time.monotonic()
    with pytest.raises(semantic.SemanticUnavailable,match='timeout'):semantic._run(slow(),timeout=.05)
    assert time.monotonic()-start<1 and cancelled==[True]


def test_external_encoder_endpoint_is_rejected_before_io(monkeypatch):
    monkeypatch.setenv('EMBEDDING_URL','https://example.com')
    with pytest.raises(semantic.SemanticUnavailable,match='endpoint_not_local'):semantic.encode('fixture')


@pytest.mark.parametrize('reason',['semantic_timeout','vector_batch_missing_or_stale','vector_integrity_mismatch'])
def test_observable_fallback_keeps_current_lexical_evidence(monkeypatch,reason):
    docs,chunks=read_documents();release=context(prepare())
    def fail(*args,**kwargs):raise semantic.SemanticUnavailable(reason)
    monkeypatch.setattr(semantic,'dense_rank',fail)
    hits,meta=semantic.search('refund',docs,chunks,release)
    expected=rank('refund',docs,chunks,release=release)
    # Policy timestamps vary; compare provenance and release identity, not wall time.
    assert [(h['chunk_id'],h['text'],h['release']) for h in hits]==[(h['chunk_id'],h['text'],h['release']) for h in expected]
    assert meta['used']=='bm25' and meta['reason']==reason


def test_invalid_publication_cannot_bypass_to_lexical(monkeypatch):
    docs,chunks=read_documents()
    monkeypatch.setattr(semantic,'dense_rank',lambda *a,**k:pytest.fail('Invalid release cannot encode'))
    hits,meta=semantic.search('refund',docs,chunks,{'valid':False,'reason':'release_missing'})
    assert hits==[] and meta['used']=='none'


def test_api_default_does_not_load_encoder(monkeypatch):
    from rag import search
    monkeypatch.delenv('DATABASE_URL',raising=False);monkeypatch.delenv('RETRIEVAL_MODE',raising=False)
    monkeypatch.setattr(semantic,'_encode',lambda *a,**k:pytest.fail('Default must stay BM25'))
    assert search('refund')['retrieval']['used']=='bm25'
