"""Opt-in local embeddings, atomic versioned vector batches and exact hybrid retrieval."""
import asyncio
import hashlib
import json
import math
import os
import time
from urllib.parse import urlsplit

from embedding_contract import CONTRACT_ID, validate_vector, vector_digest
from policy_governance import availability, utcnow



class SemanticUnavailable(Exception):pass


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def batch_id(release):return digest([release['token']['sha256'],CONTRACT_ID])


def chunk_identity(chunk,document):
    return {'chunk_id':chunk['chunk_id'],'document_sha':document['sha256'],
            'text_sha':digest([document['title'],chunk['text']])}


async def _encode(text,*,kind='query',title='',timeout=5):
    import httpx
    url=os.getenv('EMBEDDING_URL','http://embedding:8080')
    parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname not in {'embedding','localhost','127.0.0.1'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
        raise SemanticUnavailable('encoder_endpoint_not_local')
    async with httpx.AsyncClient(trust_env=False,timeout=timeout,follow_redirects=False) as client:
        response=await client.post(url.rstrip('/')+'/encode',json={'kind':kind,'text':text,'title':title})
        if response.status_code==422:raise SemanticUnavailable('encoder_input_rejected')
        if response.status_code!=200:raise SemanticUnavailable('encoder_unavailable')
        data=response.json()
        if data.get('contract')!=CONTRACT_ID:raise SemanticUnavailable('encoder_contract_mismatch')
        validate_vector(data['vector'])
        if type(data.get('tokens')) is not int or not 1<=data['tokens']<=512:
            raise SemanticUnavailable('encoder_token_count')
        return data


def _run(coroutine,timeout=5):
    import httpx
    import psycopg
    try:return asyncio.run(asyncio.wait_for(coroutine,timeout=timeout))
    except SemanticUnavailable:raise
    except (TimeoutError,httpx.TimeoutException):raise SemanticUnavailable('semantic_timeout') from None
    except psycopg.Error:raise SemanticUnavailable('vector_storage_unavailable') from None
    except (httpx.HTTPError,ValueError,KeyError,TypeError):raise SemanticUnavailable('semantic_invalid_or_unreachable') from None


def encode(text,*,kind='query',title='',timeout=5):
    return _run(_encode(text,kind=kind,title=title,timeout=timeout),timeout)


def build_index(store):
    from database_state import require_ready
    require_ready(store.url, profile='hybrid')
    from rag import read_index
    from policy_releases import active_context
    from psycopg.types.json import Jsonb
    documents,chunks,release=read_index()
    if not release['valid']:raise SemanticUnavailable('release_unavailable')
    docs={d['id']:d for d in documents}
    vectors=[];lengths=[]
    for chunk in chunks:
        document=docs[chunk['document_id']]
        if not availability(document)['usable']:raise SemanticUnavailable('policy_unavailable')
        encoded=encode(chunk['text'],kind='passage',title=document['title'])
        vectors.append((chunk_identity(chunk,document),encoded['vector']))
        lengths.append(encoded['tokens'])
    manifest=sorted([{**identity,'vector_sha':vector_digest(v)} for identity,v in vectors],key=lambda r:r['chunk_id'])
    bid=batch_id(release)
    with store.connect() as c:
        c.execute('LOCK TABLE rf_knowledge_documents IN SHARE MODE')
        current_docs=c.execute('SELECT * FROM rf_knowledge_documents').fetchall()
        current=active_context(c,current_docs,c.execute('SELECT * FROM rf_knowledge_chunks').fetchall())
        if not current['valid'] or current['token']!=release['token'] or any(not availability(d)['usable'] for d in current_docs):
            raise SemanticUnavailable('release_changed_during_build')
        # Batch rows, vectors and manifest become visible together or not at all.
        c.execute('DELETE FROM rf_vector_batches WHERE id=%s',(bid,))
        c.execute('INSERT INTO rf_vector_batches(id,release_sha,contract,manifest) VALUES(%s,%s,%s,%s)',
                  (bid,release['token']['sha256'],CONTRACT_ID,Jsonb(manifest)))
        for identity,vector in vectors:
            c.execute('INSERT INTO rf_policy_vectors VALUES(%s,%s,%s,%s,%s::public.vector,%s)',
                      (bid,identity['chunk_id'],identity['document_sha'],identity['text_sha'],json.dumps(vector),vector_digest(vector)))
    return {'batch_id':bid,'contract':CONTRACT_ID,'chunks':len(vectors),'tokens':lengths,'release':release['token']}


def dense_rank(query,documents,chunks,release,top_k=4,*,now=None):
    import psycopg
    from psycopg.rows import dict_row
    from policy_releases import content,context,digest as release_digest
    if not release['valid']:raise SemanticUnavailable('release_unavailable')
    if not os.getenv('DATABASE_URL'):raise SemanticUnavailable('vector_database_not_configured')
    docs={d['id']:d for d in documents}
    expected=sorted([chunk_identity(c,docs[c['document_id']]) for c in chunks],key=lambda r:r['chunk_id'])
    bid=batch_id(release)
    async def query_vectors():
        encoded=await _encode(query)
        async with await psycopg.AsyncConnection.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=5) as c:
            await c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            await c.execute("SET LOCAL statement_timeout='5s'")
            async def rows(sql,args=()):return await (await c.execute(sql,args)).fetchall()
            current_docs=await rows('SELECT * FROM rf_knowledge_documents')
            current_chunks=await rows('SELECT * FROM rf_knowledge_chunks')
            head=await rows('SELECT h.generation,r.id,r.sha256,r.payload FROM rf_policy_head h JOIN rf_policy_releases r ON r.id=h.release_id')
            if (not head or release_digest(head[0]['payload'])!=head[0]['sha256']
                    or context(head[0]['payload'],head[0]['generation'])['token']!=release['token']
                    or content(current_docs,current_chunks)!=content(documents,chunks)):
                raise SemanticUnavailable('release_changed_during_search')
            batch=await rows('SELECT * FROM rf_vector_batches WHERE id=%s',(bid,))
            if not batch or batch[0]['release_sha']!=release['token']['sha256'] or batch[0]['contract']!=CONTRACT_ID:
                raise SemanticUnavailable('vector_batch_missing_or_stale')
            vectors=await rows('SELECT chunk_id,document_sha,text_sha,embedding::text,vector_sha FROM rf_policy_vectors WHERE batch_id=%s ORDER BY chunk_id',(bid,))
            identities=[{k:r[k] for k in ('chunk_id','document_sha','text_sha')} for r in vectors]
            if identities!=expected or batch[0]['manifest']!=[{**i,'vector_sha':r['vector_sha']} for i,r in zip(identities,vectors)]:
                raise SemanticUnavailable('vector_batch_incomplete_or_mismatched')
            for row in vectors:
                if vector_digest(json.loads(row['embedding']))!=row['vector_sha']:
                    raise SemanticUnavailable('vector_integrity_mismatch')
            checked_at=now if now is not None else utcnow()
            policies={d['id']:availability(d,now=checked_at) for d in current_docs}
            allowed=[chunk['chunk_id'] for chunk in chunks if policies[chunk['document_id']]['usable']]
            ranked=await rows('SELECT chunk_id,embedding OPERATOR(public.<=>) %s::public.vector AS distance FROM rf_policy_vectors '
                'WHERE batch_id=%s AND chunk_id=ANY(%s) ORDER BY distance,chunk_id LIMIT %s',
                (json.dumps(encoded['vector']),bid,allowed,top_k))
            return ranked,policies
    # One cancellable five-second budget covers HTTP, DB connect and all queries.
    ranked,policies=_run(query_vectors())
    by_id={c['chunk_id']:c for c in chunks};results=[]
    for row in ranked:
        chunk=by_id[row['chunk_id']];document=docs[chunk['document_id']]
        results.append({'id':document['id'],**{k:chunk[k] for k in ('chunk_id','text','line_start','line_end')},
            **{k:document[k] for k in ('title','source','version')},'document_sha256':document['sha256'],
            'score':1-float(row['distance']),'retrieval':'dense','retrieval_profile':'e5-small',
            'policy':policies[document['id']],'release':release['token'],
            'actions':[a for a,cid in release['payload']['action_chunks'].items() if cid==chunk['chunk_id']]})
    return results


def fuse(lexical,dense,weight=.25,top_k=4):
    if weight not in {.25,.5,.75}:raise ValueError('Unsupported hybrid weight')
    hits={h['chunk_id']:dict(h) for h in dense+lexical};scores={key:0. for key in hits}
    for values,w in [(lexical,1-weight),(dense,weight)]:
        for rank,hit in enumerate(values,1):scores[hit['chunk_id']]+=w/(60+rank)
    for key in hits:hits[key].update(score=scores[key],retrieval='hybrid',retrieval_profile=f'rrf-{weight}',embedding_contract=CONTRACT_ID)
    return sorted(hits.values(),key=lambda h:(-h['score'],h['chunk_id']))[:top_k]


def search(query,documents,chunks,release,top_k=4,*,weight=.25,now=None):
    from rag import rank
    if not isinstance(query,str) or len(query)>4000 or not 1<=top_k<=8:raise ValueError('Invalid retrieval query or top_k')
    if not release['valid']:return [],{'requested':'hybrid','used':'none','reason':release['reason']}
    lexical=rank(query,documents,chunks,top_k,now=now,release=release)
    if not query.strip():return [],{'requested':'hybrid','used':'bm25','reason':'empty_query'}
    try:
        dense=dense_rank(query,documents,chunks,release,top_k,now=now)
        return fuse(lexical,dense,weight,top_k),{'requested':'hybrid','used':'hybrid','weight':weight,'contract':CONTRACT_ID}
    except SemanticUnavailable as error:
        for hit in lexical:hit['retrieval_fallback']={'requested':'hybrid','reason':str(error)}
        return lexical,{'requested':'hybrid','used':'bm25','reason':str(error)}
