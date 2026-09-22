"""Document RAG: Markdown ingestion, bounded chunks, BM25 and source citations.

The small corpus is ranked in-process. Production documents/chunks live in
PostgreSQL; isolated tests read bundled documents without a database or model.
This is lexical retrieval, not neural embeddings or a vector database.
"""
from collections import Counter
import hashlib
import math
import os
from pathlib import Path
import re
import unicodedata

import yaml
from bm25_config import DEFAULT_PROFILE, EXPANSION_WEIGHT, PROFILES, expansions
from policy_governance import availability, read_manifest, utcnow

KNOWLEDGE = Path(__file__).parent / 'knowledge'
STOP_WORDS = {'申请','请问','如何','怎么','是否','可以','需要','处理','问题','进行','什么','the','a','an','is','to'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS rf_knowledge_documents (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
 version TEXT NOT NULL, sha256 TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rf_knowledge_chunks (
 chunk_id TEXT PRIMARY KEY,
 document_id TEXT NOT NULL REFERENCES rf_knowledge_documents(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, text TEXT NOT NULL,
 line_start INTEGER NOT NULL, line_end INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS rf_chunks_document ON rf_knowledge_chunks(document_id);
ALTER TABLE rf_knowledge_documents ADD COLUMN IF NOT EXISTS governance JSONB NOT NULL DEFAULT '{}';
"""

def tokens(text):
    text = unicodedata.normalize('NFKC', text).lower()
    result = re.findall(r'[a-z0-9]+', text)
    for sequence in re.findall(r'[\u3400-\u9fff]+', text):
        result.extend(sequence[i:i+2] for i in range(len(sequence)-1))
    return [token for token in result if token not in STOP_WORDS]

def read_documents(directory=KNOWLEDGE):
    directory = Path(directory)
    documents, chunks, seen = [], [], set()
    paths = sorted(directory.rglob('*.md'))
    if not paths or len(paths) > 200:
        raise ValueError('Knowledge directory requires 1 to 200 Markdown documents')
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Knowledge documents must remain inside the source directory')
        if path.stat().st_size > 200_000:
            raise ValueError('Knowledge document exceeds 200 KB')
        content = path.read_text(encoding='utf-8-sig')
        lines = content.splitlines()
        if not lines or lines[0] != '---':
            raise ValueError('Missing document metadata: ' + path.name)
        try:
            end = lines.index('---', 1)
        except ValueError:
            raise ValueError('Unclosed document metadata: ' + path.name) from None
        metadata = yaml.safe_load('\n'.join(lines[1:end]))
        if not isinstance(metadata, dict):
            raise ValueError('Document metadata must be a mapping')
        for key in ('id', 'title', 'version'):
            if not isinstance(metadata.get(key), str) or not metadata[key].strip():
                raise ValueError('Missing string metadata: ' + key)
        doc_id = metadata['id']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', doc_id) or doc_id in seen:
            raise ValueError('Invalid or duplicate document id: ' + doc_id)
        seen.add(doc_id)
        body = '\n'.join(lines[end+1:]).strip()
        if not body:
            raise ValueError('Document body is empty')
        document = {k: metadata[k] for k in ('id', 'title', 'version')}
        document.update(source=path.relative_to(directory).as_posix(), body=body,
                        sha256=hashlib.sha256(content.encode('utf-8')).hexdigest())
        documents.append(document)
        # Paragraph boundaries preserve policy meaning; oversized paragraphs use
        # overlapping windows. Every chunk retains original source line numbers.
        paragraphs, paragraph = [], []
        for number, line in enumerate(lines[end+1:], end+2):
            if not line.strip():
                if paragraph: paragraphs.append(paragraph); paragraph = []
            else:
                paragraph.append((number, line))
        if paragraph: paragraphs.append(paragraph)
        for paragraph in paragraphs:
            text = '\n'.join(line for _, line in paragraph)
            # Standalone headings are attached as metadata via the document title;
            # they are not evidence on their own.
            if all(line.startswith('#') for _, line in paragraph): continue
            for start in range(0, len(text), 440):
                piece = text[start:start+500]
                if not piece.strip(): continue
                position = sum(c['document_id'] == doc_id for c in chunks)
                chunks.append({'chunk_id': f'{doc_id}:{document["sha256"][:12]}:{position}',
                    'document_id': doc_id, 'position': position, 'text': piece,
                    'line_start': paragraph[0][0] + text[:start].count('\n'),
                    'line_end': paragraph[0][0] + text[:start+len(piece)].count('\n')})
                if start+500 >= len(text): break
        if not any(c['document_id'] == doc_id for c in chunks):
            raise ValueError('Document has no evidence paragraphs: ' + doc_id)
    manifest = read_manifest(directory)
    if set(manifest) - seen:
        raise ValueError('Policy governance refers to an unknown document')
    for document in documents:
        metadata = manifest.get(document['id'], {})
        if metadata and metadata['document_sha256'] != document['sha256']:
            raise ValueError('Policy governance hash does not match document: '+document['id'])
        document['governance'] = metadata
    return documents, chunks

def sync_index(connection, directory=KNOWLEDGE, *, only_if_empty=False):
    from policy_releases import SCHEMA as RELEASE_SCHEMA, invalidate
    documents, chunks = read_documents(directory)  # validate before any mutation
    connection.execute(SCHEMA)
    connection.execute(RELEASE_SCHEMA)
    # Schema-local relation lock serializes bootstrap/reindex, including two
    # services starting together. One transaction exposes a complete index.
    connection.execute('LOCK TABLE rf_knowledge_documents IN EXCLUSIVE MODE')
    existing = connection.execute('SELECT count(*) AS n FROM rf_knowledge_documents').fetchone()
    if only_if_empty and existing['n']:
        return {'seeded': False, 'documents': existing['n']}
    invalidate(connection)
    reviews = {r['document_sha256']:r['governance'] for r in connection.execute('SELECT * FROM rf_policy_reviews').fetchall()}
    for document in documents:
        if document['sha256'] in reviews:
            document['governance'] = reviews[document['sha256']]
    replace_index(connection, documents, chunks)
    return {'seeded': True, 'documents': len(documents), 'chunks': len(chunks)}


def replace_index(connection, documents, chunks):
    """Internal writer; caller holds the policy lock and manages release identity."""
    from psycopg.types.json import Jsonb
    for document in documents:
        connection.execute('''INSERT INTO rf_knowledge_documents
            (id,title,source,version,sha256,body,governance) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET title=EXCLUDED.title,source=EXCLUDED.source,
            version=EXCLUDED.version,sha256=EXCLUDED.sha256,body=EXCLUDED.body,
            governance=EXCLUDED.governance''',
            tuple(document[k] for k in ('id','title','source','version','sha256','body'))+(Jsonb(document['governance']),))
    connection.execute('DELETE FROM rf_knowledge_chunks')
    for chunk in chunks:
        connection.execute('INSERT INTO rf_knowledge_chunks VALUES (%s,%s,%s,%s,%s,%s)',
            tuple(chunk[k] for k in ('chunk_id','document_id','position','text','line_start','line_end')))
    connection.execute('DELETE FROM rf_knowledge_documents WHERE NOT (id = ANY(%s))',
                       ([d['id'] for d in documents],))

def rank(query, documents, chunks, top_k=4, *, now=None, mode=None, release=None, profile=DEFAULT_PROFILE):
    if not isinstance(query, str) or len(query) > 4000:
        raise ValueError('Query must be a string of at most 4000 characters')
    if not 1 <= top_k <= 8:
        raise ValueError('top_k must be between 1 and 8')
    now = now if now is not None else utcnow()
    policies = {d['id']: availability(d, now=now, mode=mode) for d in documents}
    documents = [d for d in documents if policies[d['id']]['usable']]
    allowed = {d['id'] for d in documents}
    chunks = [c for c in chunks if c['document_id'] in allowed]
    config = PROFILES[profile]
    query_weights = {term: 1.0 for term in tokens(query)}
    if config['expand']:
        for phrase in expansions(query):
            for term in tokens(phrase):
                query_weights.setdefault(term, EXPANSION_WEIGHT)
    query_tokens = set(query_weights)
    if not query_tokens or not chunks: return []
    documents = {d['id']: d for d in documents}
    # Field weighting is selected only on the frozen tuning partition.
    counts = [Counter(tokens(c['text']) + tokens(documents[c['document_id']]['title'])*config['title_weight']) for c in chunks]
    average = sum(sum(c.values()) for c in counts) / len(counts) or 1
    frequency = Counter(term for count in counts for term in count)
    ranked = []
    for chunk, count in zip(chunks, counts):
        score = 0.0
        for term in sorted(query_tokens & count.keys()):
            df = frequency[term]
            inverse = math.log(1 + (len(chunks)-df+0.5)/(df+0.5))
            tf = count[term]
            score += query_weights[term] * inverse * tf * 2.5 / (tf + 1.5*(0.25+0.75*sum(count.values())/average))
        if score <= 0: continue
        document = documents[chunk['document_id']]
        hit = {'id': document['id'], 'chunk_id': chunk['chunk_id'],
            'text': chunk['text'], 'title': document['title'], 'source': document['source'],
            'version': document['version'], 'document_sha256': document['sha256'],
            'line_start': chunk['line_start'], 'line_end': chunk['line_end'],
            'score': round(score, 6), 'retrieval': 'bm25', 'retrieval_profile': profile,
            'policy': policies[document['id']]}
        if release and release['valid']:
            hit.update(release=release['token'], actions=[a for a,cid in release['payload']['action_chunks'].items() if cid==chunk['chunk_id']])
        ranked.append(hit)
    return sorted(ranked, key=lambda r: (-r['score'], r['chunk_id']))[:top_k]

def current_documents():
    """Reload current governance for every decision; saved evidence is not authority."""
    if os.getenv('DATABASE_URL'):
        from storage import Store
        with Store(os.environ['DATABASE_URL']).connect() as connection:
            return connection.execute('SELECT * FROM rf_knowledge_documents').fetchall()
    return read_documents()[0]


def read_index():
    from policy_releases import active_context, context, prepare
    if os.getenv('DATABASE_URL'):
        from storage import Store
        with Store(os.environ['DATABASE_URL']).connect() as connection:
            # Database failures are propagated, never silently replaced with fixtures.
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            documents = connection.execute('SELECT * FROM rf_knowledge_documents').fetchall()
            chunks = connection.execute('SELECT * FROM rf_knowledge_chunks ORDER BY document_id,position').fetchall()
            release = active_context(connection, documents, chunks)
    else:
        documents, chunks = read_documents()
        release = context(prepare())
    return documents, chunks, release


def search(query, top_k=4):
    documents, chunks, release = read_index()
    now = utcnow()
    return {'results': rank(query, documents, chunks, top_k, now=now, release=release) if release['valid'] else [],
            'release': {k:v for k,v in release.items() if k!='payload'},
            'policies': [{'id': d['id'], 'title': d['title'], 'version': d['version'],
                          'policy': availability(d, now=now)} for d in documents]}


def retrieve(query, top_k=4):
    return search(query, top_k)['results']

def main():
    import argparse
    import json
    from dotenv import load_dotenv
    from storage import Store
    parser = argparse.ArgumentParser(description='Import index and invalidate active release; explicit policy publication is required')
    parser.add_argument('--directory', type=Path, default=KNOWLEDGE)
    args = parser.parse_args()
    load_dotenv(Path(__file__).with_name('.env'), encoding='utf-8-sig')
    with Store(os.environ['DATABASE_URL']).connect() as connection:
        print(json.dumps(sync_index(connection, args.directory)))

if __name__ == '__main__': main()
