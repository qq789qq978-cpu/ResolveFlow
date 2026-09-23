"""Read-only PostgreSQL schema inventory; no business rows or connection secrets.

Run with DATABASE_URL in the environment. A match is diagnostic, never a stamp.
"""
import argparse
import json
import os
from pathlib import Path
import re

import psycopg
from psycopg import sql

CORE = tuple(sorted('rf_orders rf_runs rf_approvals rf_refunds rf_reviews rf_jobs '
                    'rf_job_attempts rf_worker_heartbeats rf_audit rf_knowledge_documents '
                    'rf_knowledge_chunks rf_policy_releases rf_policy_head rf_policy_reviews '
                    'rf_policy_events'.split()))
VECTOR = ('rf_policy_vectors', 'rf_vector_batches')
CHECKPOINT = ('checkpoint_blobs', 'checkpoint_migrations', 'checkpoint_writes', 'checkpoints')
VERSION_TABLE = 'rf_schema_version'


def snapshot(dsn, schema='public'):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema):
        raise ValueError('Invalid schema name')
    with psycopg.connect(dsn, connect_timeout=5) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute('SET LOCAL statement_timeout = 10000')
        c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        return read_catalog(c, schema)


def read_catalog(c, schema, *, only_tables=None):
    """Read within a caller-owned transaction (also used under migration locks)."""
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema):
        raise ValueError('Invalid schema name')
    c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
    def rows(query, params=()):
        return [list(r) for r in c.execute(query, params).fetchall()]
    def normalize(value):
        if isinstance(value, str):
            return value.replace('"'+schema+'".', '').replace(schema+'.', '').replace('public.vector', 'vector')
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value
    names = [r[0] for r in rows("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (schema,))]
    if only_tables is not None:
        names = [name for name in names if name in only_tables]
    tables = {}
    for name in names:
        rel = c.execute('SELECT to_regclass(%s)::oid', (f'{schema}.{name}',)).fetchone()[0]
        tables[name] = {
            'columns': normalize(rows("""SELECT a.attname,format_type(a.atttypid,a.atttypmod),
                a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (rel,))),
            'constraints': normalize(rows('SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s ORDER BY conname', (rel,))),
            'indexes': normalize(rows('SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=%s AND tablename=%s ORDER BY indexname', (schema,name))),
            'triggers': normalize(rows('SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid=%s AND NOT tgisinternal ORDER BY tgname', (rel,))),
            'row_security': rows('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s', (rel,))[0],
        }
    sequences = normalize(rows("""SELECT s.relname,t.relname,a.attname,format_type(q.seqtypid,NULL),
        q.seqstart,q.seqincrement,q.seqmax,q.seqmin,q.seqcache,q.seqcycle
        FROM pg_class s JOIN pg_namespace n ON n.oid=s.relnamespace JOIN pg_sequence q ON q.seqrelid=s.oid
        LEFT JOIN pg_depend d ON d.objid=s.oid AND d.classid='pg_class'::regclass AND d.deptype IN ('a','i')
        LEFT JOIN pg_class t ON t.oid=d.refobjid
        LEFT JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
        WHERE n.nspname=%s ORDER BY s.relname""", (schema,)))
    return {
        'tables': tables, 'sequences': sequences,
        'application_tables': sorted(set(names) & set(CORE+VECTOR)),
        'langgraph_tables': sorted(set(names) & set(CHECKPOINT)),
        'unknown_tables': sorted(set(names)-set(CORE+VECTOR+CHECKPOINT+(VERSION_TABLE,))),
        'revisions': [r[0] for r in rows('SELECT version_num FROM rf_schema_version ORDER BY version_num')] if VERSION_TABLE in names else [],
        'extensions': rows('SELECT e.extname,e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY e.extname'),
    }


def application(catalog, profile):
    if profile not in ('core','hybrid'):
        raise ValueError('Unknown baseline profile')
    names = CORE + (VECTOR if profile == 'hybrid' else ())
    return {'tables': {name:catalog['tables'].get(name) for name in names},
            'sequences': [s for s in catalog['sequences'] if s[1] in names]}


def differences(catalog, baseline, profile):
    actual = application(catalog, profile)
    changes = [name for name in actual['tables'] if actual['tables'][name] != baseline['tables'].get(name)]
    if actual['sequences'] != baseline['sequences']:
        changes.append('sequences')
    changes.extend('unknown:'+n for n in catalog['unknown_tables'])
    if profile == 'core':
        changes.extend('unexpected:'+n for n in VECTOR if n in catalog['tables'])
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', default='public')
    parser.add_argument('--profile', choices=['core','hybrid'], required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    catalog = snapshot(os.environ['DATABASE_URL'], args.schema)
    changed = differences(catalog, json.loads(args.baseline.read_text(encoding='utf-8')), args.profile)
    args.report.write_text(json.dumps({'matched':not changed, 'differences':changed,
        'profile':args.profile, 'catalog':catalog}, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'matched':not changed, 'differences':changed, 'revisions':catalog['revisions']}))
    return int(bool(changed))


if __name__ == '__main__':
    raise SystemExit(main())
