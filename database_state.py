"""Readiness contract shared by deploy tooling and application processes."""
import json
import os
from pathlib import Path

import psycopg
from scripts.schema_catalog import CHECKPOINT, VECTOR, differences, read_catalog

ROOT = Path(__file__).resolve().parent
CORE_REVISION = 'rf_core_0001'
VECTOR_REVISION = 'rf_vector_0001'


class SchemaNotReady(RuntimeError):
    pass


def check_application(catalog, profile):
    if profile not in ('core', 'hybrid'):
        raise ValueError('Unknown schema profile')
    actual_profile = 'hybrid' if any(t in catalog['tables'] for t in VECTOR) else 'core'
    if profile == 'hybrid' and actual_profile != 'hybrid':
        raise SchemaNotReady('Vector schema required; run db_migrate.py prepare --profile hybrid')
    baseline = json.loads((ROOT/'migrations/baselines'/f'{actual_profile}.json').read_text())
    changed = differences(catalog, baseline, actual_profile)
    if changed:
        raise SchemaNotReady('Application schema drift: '+', '.join(changed))
    expected = VECTOR_REVISION if actual_profile == 'hybrid' else CORE_REVISION
    if catalog['revisions'] != [expected]:
        raise SchemaNotReady('Application revision missing or incompatible; use the migration command')
    return actual_profile


def check_checkpoints(c):
    from langgraph.checkpoint.postgres import PostgresSaver
    names = {r[0] for r in c.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()')}
    if not set(CHECKPOINT) <= names:
        raise SchemaNotReady('LangGraph schema missing; run db_migrate.py prepare')
    versions = [r[0] for r in c.execute('SELECT v FROM checkpoint_migrations ORDER BY v')]
    if versions != list(range(len(PostgresSaver.MIGRATIONS))):
        raise SchemaNotReady('LangGraph revision incompatible; run db_migrate.py prepare with the matching package')


def require_checkpoints(dsn):
    with psycopg.connect(dsn, connect_timeout=5) as c:
        c.execute('SET TRANSACTION READ ONLY')
        check_checkpoints(c)


def require_ready(dsn, profile=None):
    profile = profile or ('hybrid' if os.getenv('RETRIEVAL_MODE','bm25') == 'hybrid' else 'core')
    with psycopg.connect(dsn, connect_timeout=5) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute('SET LOCAL statement_timeout=10000')
        schema = c.execute('SELECT current_schema()').fetchone()[0]
        catalog = read_catalog(c, schema)
        actual_profile = check_application(catalog, profile)
        check_checkpoints(c)
        return {'ready':True, 'profile':actual_profile, 'revisions':catalog['revisions']}
