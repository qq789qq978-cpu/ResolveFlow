"""Immutable policy bundles, audited activation, and executable rule compatibility."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re

from policy_governance import availability, validate_metadata
import refund_policy

ROOT = Path(__file__).resolve().parent
# Explicit demo code boundary inherited from 2.3, not a human policy sign-off. Changing
# an action clause requires code review/deployment as well as a policy release.
ACTION_CLAUSES = {
    'refund': '8a98ee30fa2126dd6684023025877e45097c91a81173c55085eeca5ff004e6e7',
    'reply': 'a23aca9f68feb6c0bbb6d318e5407a0c52577379a62d5238935c2afc1f6e30e4',
}
# Deliberate code-to-clause binding. Updating a manifest alone cannot bless a
# modified refund implementation. A new rule needs a reviewed code deployment.
RULE_BINDING = {'version': 'refund-v2',
                'sha256': '060917416c76d9f174f8f56fa177721a7480d5982675cfade14984311d5322e5'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS rf_policy_releases (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, payload JSONB NOT NULL,
 actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS rf_policy_head (
 singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
 release_id TEXT REFERENCES rf_policy_releases(id), generation BIGINT NOT NULL DEFAULT 0);
INSERT INTO rf_policy_head(singleton) VALUES(TRUE) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS rf_policy_reviews (
 document_sha256 TEXT PRIMARY KEY, governance JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS rf_policy_events (
 id BIGSERIAL PRIMARY KEY, action TEXT NOT NULL, previous_release TEXT,
 target_release TEXT, generation BIGINT NOT NULL, actor TEXT NOT NULL,
 reason TEXT NOT NULL, details JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def rule_contract():
    return {'version': refund_policy.VERSION,
            'sha256': hashlib.sha256(Path(refund_policy.__file__).read_text(encoding='utf-8-sig').encode()).hexdigest()}


def validate_payload(payload):
    if (payload.get('schema') != 1 or payload.get('refund_rule') != rule_contract()
            or payload.get('refund_rule') != RULE_BINDING):
        raise ValueError('Policy release and executable refund rule do not match')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', str(payload.get('id', ''))):
        raise ValueError('Invalid policy release id')
    chunks = {c['chunk_id']: c for c in payload['chunks']}
    bindings = payload.get('action_chunks')
    if not isinstance(bindings, dict) or set(bindings) != set(ACTION_CLAUSES):
        raise ValueError('Release requires explicit refund and reply clause bindings')
    for action, expected in ACTION_CLAUSES.items():
        chunk = chunks.get(bindings[action], {})
        if hashlib.sha256(chunk.get('text', '').encode()).hexdigest() != expected:
            raise ValueError('Policy action clause is incompatible with executable rules: '+action)


def prepare(directory=None):
    from rag import KNOWLEDGE, read_documents
    directory = Path(directory) if directory is not None else KNOWLEDGE
    path = directory/'release.json'
    if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()) or path.stat().st_size > 20_000:
        raise ValueError('Invalid policy release manifest')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate release manifest key')
            result[key] = value
        return result
    manifest = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique)
    if not isinstance(manifest, dict) or set(manifest) != {'schema','id','refund_rule','action_chunks'}:
        raise ValueError('Invalid release manifest fields')
    documents, chunks = read_documents(directory)
    payload = {**manifest, 'documents': sorted(documents, key=lambda d:d['id']),
               'chunks': sorted(chunks, key=lambda c:c['chunk_id'])}
    validate_payload(payload)
    return payload


def content(documents, chunks):
    return {'documents': sorted([{k:d[k] for k in ('id','title','source','version','sha256','body')}
                                 for d in documents], key=lambda d:d['id']),
            'chunks': sorted(chunks, key=lambda c:c['chunk_id'])}


def context(payload, generation=0):
    validate_payload(payload)
    return {'valid': True, 'reason': 'active', 'payload': payload,
            'token': {'id': payload['id'], 'sha256': digest(payload), 'generation': generation,
                      'refund_rule': payload['refund_rule']}}


def active_context(connection, documents, chunks):
    head = connection.execute('SELECT * FROM rf_policy_head WHERE singleton').fetchone()
    if not head or not head['release_id']:
        return {'valid': False, 'reason': 'release_missing', 'token': None, 'payload': None}
    row = connection.execute('SELECT * FROM rf_policy_releases WHERE id=%s', (head['release_id'],)).fetchone()
    try:
        if digest(row['payload']) != row['sha256'] or row['payload']['id'] != row['id']:
            raise ValueError('corrupt release')
        result = context(row['payload'], head['generation'])
        if content(documents, chunks) != content(row['payload']['documents'], row['payload']['chunks']):
            return {**result, 'valid': False, 'reason': 'release_index_drift'}
        return result
    except (ValueError, TypeError, KeyError):
        return {'valid': False, 'reason': 'release_incompatible_or_corrupt', 'token': None, 'payload': None}


def event(connection, action, head, target, actor, reason, details):
    from psycopg.types.json import Jsonb
    connection.execute('''INSERT INTO rf_policy_events
        (action,previous_release,target_release,generation,actor,reason,details)
        VALUES(%s,%s,%s,%s,%s,%s,%s)''',
        (action, head['release_id'], target, head['generation'], actor, reason, Jsonb(details)))


def identity(actor, reason):
    if not isinstance(actor, str) or not actor.strip() or len(actor)>120:
        raise ValueError('A maintenance actor is required')
    if not isinstance(reason, str) or not reason.strip() or len(reason)>1000:
        raise ValueError('A maintenance reason is required')


def capture_reviews(connection):
    # Preserve current governance even if an old maintenance tool edited the
    # active table directly. A rollback must never resurrect an old approval.
    connection.execute('''INSERT INTO rf_policy_reviews SELECT sha256,governance FROM rf_knowledge_documents
        ON CONFLICT(document_sha256) DO UPDATE SET governance=EXCLUDED.governance''')


def invalidate(connection):
    head = connection.execute('SELECT * FROM rf_policy_head WHERE singleton FOR UPDATE').fetchone()
    capture_reviews(connection)
    if head['release_id']:
        head['generation'] += 1
        connection.execute('UPDATE rf_policy_head SET release_id=NULL,generation=%s WHERE singleton', (head['generation'],))
        event(connection, 'import_invalidated', head, None, 'legacy-import',
              'Raw index import requires an explicit compatible release before use', {})


def activate(connection, *, actor, reason, expected_generation, payload=None, release_id=None, mode=None):
    """Caller owns the transaction. All mutation paths lock policy documents first."""
    from psycopg.types.json import Jsonb
    from rag import replace_index
    identity(actor, reason)
    if type(expected_generation) is not int or expected_generation < 0:
        raise ValueError('Expected generation must be a nonnegative integer')
    if (payload is None) == (release_id is None):
        raise ValueError('Supply either a new release payload or an existing rollback id')
    connection.execute('LOCK TABLE rf_knowledge_documents IN EXCLUSIVE MODE')
    head = connection.execute('SELECT * FROM rf_policy_head WHERE singleton FOR UPDATE').fetchone()
    if head['generation'] != expected_generation:
        raise ValueError('Policy generation changed; inspect status before retrying')
    action = 'publish' if payload is not None else 'rollback'
    if payload is None:
        saved = connection.execute('SELECT * FROM rf_policy_releases WHERE id=%s', (release_id,)).fetchone()
        if not saved or digest(saved['payload']) != saved['sha256']:
            raise ValueError('Unknown or corrupt rollback release')
        payload = saved['payload']
    validate_payload(payload)
    fingerprint = digest(payload)
    existing = connection.execute('SELECT * FROM rf_policy_releases WHERE id=%s', (payload['id'],)).fetchone()
    if existing and (existing['sha256'] != fingerprint or existing['payload'] != payload):
        raise ValueError('Release ids are immutable; use a new release id')
    for old in connection.execute('SELECT payload FROM rf_policy_releases').fetchall():
        if (old['payload']['refund_rule']['version']==payload['refund_rule']['version']
                and old['payload']['refund_rule']!=payload['refund_rule']):
            raise ValueError('Published refund rule versions are immutable')
        for old_doc in old['payload']['documents']:
            for doc in payload['documents']:
                if (doc['id'] == old_doc['id'] or (doc['source'],doc['version']) == (old_doc['source'],old_doc['version'])) and doc['sha256'] != old_doc['sha256']:
                    raise ValueError('Published document versions are immutable')
    capture_reviews(connection)
    reviews = {r['document_sha256']:r['governance'] for r in connection.execute('SELECT * FROM rf_policy_reviews').fetchall()}
    documents = copy.deepcopy(payload['documents'])
    for doc in documents:
        doc['governance'] = reviews.get(doc['sha256'], doc['governance'])
        if not availability(doc, mode=mode)['usable']:
            raise ValueError('Release includes a policy that is not currently usable: '+doc['id'])
    if not existing:
        connection.execute('INSERT INTO rf_policy_releases(id,sha256,payload,actor,reason) VALUES(%s,%s,%s,%s,%s)',
                           (payload['id'],fingerprint,Jsonb(payload),actor,reason))
    head['generation'] += 1
    replace_index(connection, documents, payload['chunks'])
    capture_reviews(connection)
    connection.execute('UPDATE rf_policy_head SET release_id=%s,generation=%s WHERE singleton',
                       (payload['id'],head['generation']))
    event(connection, action, head, payload['id'], actor, reason, {'sha256':fingerprint,'refund_rule':payload['refund_rule']})
    return context(payload, head['generation'])['token']


def review(connection, document_sha256, metadata, *, actor, reason, expected_generation):
    """Explicit audited review change, including inactive versions; no publication."""
    from psycopg.types.json import Jsonb
    identity(actor, reason)
    validate_metadata(metadata)
    if metadata['document_sha256'] != document_sha256:
        raise ValueError('Review hash mismatch')
    connection.execute('LOCK TABLE rf_knowledge_documents IN EXCLUSIVE MODE')
    head=connection.execute('SELECT * FROM rf_policy_head WHERE singleton FOR UPDATE').fetchone()
    if type(expected_generation) is not int or head['generation'] != expected_generation:
        raise ValueError('Policy generation changed; inspect status before retrying')
    capture_reviews(connection)
    previous=connection.execute('SELECT governance FROM rf_policy_reviews WHERE document_sha256=%s',(document_sha256,)).fetchone()
    if not previous: raise ValueError('Unknown policy review hash')
    connection.execute('UPDATE rf_policy_reviews SET governance=%s WHERE document_sha256=%s',(Jsonb(metadata),document_sha256))
    connection.execute('UPDATE rf_knowledge_documents SET governance=%s WHERE sha256=%s',(Jsonb(metadata),document_sha256))
    # Review edits invalidate older approvals as well as activation changes.
    head['generation']+=1
    connection.execute('UPDATE rf_policy_head SET generation=%s WHERE singleton',(head['generation'],))
    event(connection,'review',head,head['release_id'],actor,reason,
          {'document_sha256':document_sha256,'before':previous['governance'],'after':metadata})


def status(connection):
    head=connection.execute('SELECT * FROM rf_policy_head WHERE singleton').fetchone()
    return {'head':head,'runtime_refund_rule':rule_contract(),
            'releases':connection.execute('SELECT id,sha256,actor,reason,created_at FROM rf_policy_releases ORDER BY created_at,id').fetchall(),
            'events':connection.execute('SELECT * FROM rf_policy_events ORDER BY id').fetchall()}


def main():
    import argparse
    from dotenv import load_dotenv
    from storage import Store
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('status')
    for name in ['publish','rollback','review']:
        cmd=sub.add_parser(name)
        cmd.add_argument('--actor',required=True)
        cmd.add_argument('--reason',required=True)
        cmd.add_argument('--expected-generation',required=True,type=int)
        if name=='publish': cmd.add_argument('--directory',type=Path,default=ROOT/'knowledge')
        if name=='rollback': cmd.add_argument('--release',required=True)
        if name=='review': cmd.add_argument('--metadata',required=True,type=Path)
    args=parser.parse_args()
    load_dotenv(ROOT/'.env',encoding='utf-8-sig')
    with Store(os.environ['DATABASE_URL']).connect() as connection:
        if args.command=='status': result=status(connection)
        else:
            common=dict(actor=args.actor,reason=args.reason,expected_generation=args.expected_generation)
            if args.command=='publish': result=activate(connection,payload=prepare(args.directory),**common)
            elif args.command=='rollback': result=activate(connection,release_id=args.release,**common)
            else:
                metadata=json.loads(args.metadata.read_text(encoding='utf-8-sig'))
                review(connection,metadata['document_sha256'],metadata,**common)
                result={'review_recorded':True}
        print(json.dumps(result,ensure_ascii=False,default=str))


if __name__=='__main__': main()
