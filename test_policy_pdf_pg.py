"""PDF release integration in disposable PostgreSQL schemas."""
import copy
import os
import pytest
import jobs
import semantic
from embedding_contract import CONTRACT_ID
from grounding import check_grounding
from rag import read_index, retrieve
from policy_releases import activate, prepare, review
from test_jobs import system, headers, submit
from test_policy_pdf import candidate
from test_policy_releases_pg import publish, snapshot

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Isolated PostgreSQL required')


def business_snapshot(store):
    with store.connect() as c:
        return {t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in
                ('rf_orders','rf_runs','rf_jobs','rf_approvals','rf_refunds','rf_audit','checkpoints','checkpoint_writes','checkpoint_blobs')}


def test_pdf_roundtrip_readonly_query_and_rollback(system,tmp_path):
    store,engine,client=system
    original=read_index()[2]['token']
    publish(store,prepare(candidate(tmp_path)))
    store.setup()  # restarting cannot replace the PDF release with bundled Markdown
    before=business_snapshot(store)
    assert client.get('/api/knowledge',params={'query':'订单编号 商品照片'}).status_code==401
    for role in ('operator','reviewer','admin'):
        response=client.get('/api/knowledge',params={'query':'订单编号 商品照片'},headers=headers(role))
        assert response.status_code==200
        pdf=next(h for h in response.json()['results'] if h.get('source_type')=='pdf')
        assert pdf['page_start']==pdf['page_end']==1
        docs,chunks,release=read_index()
        proposal={'citation_schema':2,'citations':[pdf['chunk_id']],
                  'quotes':[{'chunk_id':pdf['chunk_id'],'quote':pdf['text']}],'evidence_status':'supported'}
        assert check_grounding(proposal,[pdf],documents=docs,release=release,mode='demo')['usable']
    assert business_snapshot(store)==before
    with store.connect() as c:activate(c,release_id=original['id'],expected_generation=2,actor='qa',reason='PDF rollback')
    assert all(h.get('source_type')!='pdf' for h in retrieve('订单编号 商品照片'))
    assert not check_grounding(proposal,[pdf],mode='demo')['usable']


def test_pdf_revocation_survives_rollback(system,tmp_path):
    store,_,_=system
    first=read_index()[2]['token'];payload=prepare(candidate(tmp_path));publish(store,payload)
    doc=next(d for d in payload['documents'] if d.get('provenance'))
    with store.connect() as c:activate(c,release_id=first['id'],expected_generation=2,actor='qa',reason='Leave PDF release')
    with store.connect() as c:review(c,doc['sha256'],{**doc['governance'],'status':'revoked'},expected_generation=3,actor='qa',reason='Revoke PDF')
    before=snapshot(store)
    with pytest.raises(ValueError,match='not currently usable'):
        with store.connect() as c:activate(c,release_id=payload['id'],expected_generation=4,actor='qa',reason='Must reject revoked PDF')
    assert snapshot(store)==before


def test_pdf_failed_publish_and_corrupt_archive_fail_closed(system,tmp_path,monkeypatch):
    store,_,_=system;payload=prepare(candidate(tmp_path));before=snapshot(store)
    import rag
    original=rag.replace_index
    def fail(c,documents,chunks):
        original(c,documents,chunks)
        raise RuntimeError('Synthetic failure after replacement')
    monkeypatch.setattr(rag,'replace_index',fail)
    with pytest.raises(RuntimeError):publish(store,payload)
    assert snapshot(store)==before
    monkeypatch.setattr(rag,'replace_index',original);publish(store,payload)
    with store.connect() as c:
        c.execute("UPDATE rf_policy_releases SET payload=jsonb_set(payload,'{documents,0,provenance,file_sha256}',to_jsonb('tampered'::text)) WHERE id=%s",(payload['id'],))
    assert retrieve('订单编号 商品照片')==[]


def test_pdf_vectors_carry_pages_and_rebuild_after_release(system,tmp_path,monkeypatch):
    store,_,_=system
    from db_migrate import migrate
    with store.connect() as c:c.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
    migrate(store.url,profile='hybrid')
    async def encode(text,**kwargs):return {'vector':[1.]+[0.]*383,'tokens':10,'contract':CONTRACT_ID}
    monkeypatch.setattr(semantic,'_encode',encode)
    semantic.build_index(store)
    publish(store,prepare(candidate(tmp_path)))
    docs,chunks,release=read_index()
    with pytest.raises(semantic.SemanticUnavailable):semantic.dense_rank('材料',docs,chunks,release)
    semantic.build_index(store)
    hits=semantic.dense_rank('材料',docs,chunks,release,top_k=20)
    pdf=[h for h in hits if h.get('source_type')=='pdf']
    assert {h['page_start'] for h in pdf}=={1,2}
    assert all(h['release']==release['token'] and len(h['original_sha256'])==64 for h in pdf)


def test_pdf_publication_preserves_old_approval_and_refund_idempotency(system,tmp_path):
    store,engine,client=system
    old=submit(client);jobs.process_one(store,engine,old)
    saved=copy.deepcopy(store.get(old)['state']['evidence'])
    publish(store,prepare(candidate(tmp_path)))
    assert client.post('/api/runs/'+old+'/approval',headers=headers(),json={'approved':True,'reason':'No operator permission'}).status_code==403
    assert client.post('/api/runs/'+old+'/approval',headers=headers('reviewer'),json={'approved':True,'reason':'Stale approval'}).status_code==202
    jobs.process_one(store,engine,old)
    assert store.get(old)['status']=='escalated' and store.get(old)['state']['evidence']==saved
    new=submit(client);jobs.process_one(store,engine,new)
    assert store.get(new)['status']=='awaiting_approval'
    assert client.post('/api/runs/'+new+'/approval',headers=headers('reviewer'),json={'approved':True,'reason':'Current approval'}).status_code==202
    jobs.process_one(store,engine,new);assert store.get(new)['status']=='refunded'
    again=submit(client);jobs.process_one(store,engine,again)
    assert store.get(again)['status']=='awaiting_approval'
    assert client.post('/api/runs/'+again+'/approval',headers=headers('reviewer'),json={'approved':True,'reason':'Duplicate attempt'}).status_code==202
    jobs.process_one(store,engine,again)
    assert store.get(again)['status']=='already_refunded'
    with store.connect() as c:assert c.execute('SELECT count(*) AS n FROM rf_refunds').fetchone()['n']==1
