"""Lexical expansion improves access to evidence without granting authority."""
import copy
import json

import pytest

from bm25_config import DEFAULT_PROFILE, expansions
from rag import rank, read_documents
from scripts.tune_bm25 import tune
from scripts.freeze_rag_split import SPLIT
from scripts.validate_rag_dataset import DATASET


@pytest.mark.parametrize('query', ['parcel', 'ＰＡＲＣＥＬ', 'The package was misplaced'])
def test_translation_can_retrieve_verbatim_chinese_evidence(query):
    docs,chunks=read_documents()
    hits=rank(query,docs,chunks,mode='demo')
    assert hits and any('包裹' in h['text'] for h in hits)
    originals={c['chunk_id']:c['text'] for c in chunks}
    assert all(h['text']==originals[h['chunk_id']] for h in hits)
    assert all(h['retrieval_profile']==DEFAULT_PROFILE for h in hits)


@pytest.mark.parametrize('query', ['ledgersmith', 'retracking', 'parcelation', 'unknown_xyz'])
def test_english_aliases_do_not_match_inside_unrelated_words(query):
    assert expansions(query)==[]


def test_expansion_does_not_publish_or_approve_evidence():
    docs,chunks=read_documents()
    # A lexical hit without a published token is not an execution credential.
    hits=rank('ledger',docs,chunks,mode='demo')
    assert hits and all('release' not in h and 'actions' not in h for h in hits)
    unavailable=copy.deepcopy(docs)
    for doc in unavailable: doc['governance']['status']='revoked'
    assert rank('parcel ledger',unavailable,chunks,mode='demo')==[]


def test_original_profile_remains_available_for_control():
    docs,chunks=read_documents()
    assert rank('ledger',docs,chunks,mode='demo',profile='original')==[]
    assert rank('ledger',docs,chunks,mode='demo')
    assert rank('refund',docs,chunks,mode='demo',profile='original')


def test_tuning_never_executes_held_out_queries(monkeypatch):
    import scripts.tune_bm25 as tuning
    data=json.loads(DATASET.read_text(encoding='utf-8'))
    split=json.loads(SPLIT.read_text(encoding='utf-8'))
    allowed={c['query'] for c in data['cases'] if split['assignments'][c['id']]=='tuning'}
    seen=[]
    def guarded(query,*args,**kwargs):
        assert query in allowed
        seen.append(query)
        return rank(query,*args,**kwargs)
    monkeypatch.setattr(tuning,'rank',guarded)
    monkeypatch.setattr(tuning,'demo_proposal',lambda query,hits: {'action':'escalate','citations':[]})
    result=tune(data,split)
    assert set(seen)==allowed and len(seen)==6*45
    assert result['held_out_cases_evaluated']==0
