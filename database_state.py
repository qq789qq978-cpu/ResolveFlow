"""Readiness contract shared by deploy tooling and application processes."""
import json
import os
from pathlib import Path

import psycopg
from scripts.schema_catalog import CHECKPOINT, VECTOR, ORDERS, differences, read_catalog

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
    has_orders = any(t in catalog['tables'] for t in ORDERS)
    expected_revisions = [expected]
    if has_orders:
        contract = json.loads((ROOT/'migrations/baselines/orders.json').read_text())
        if any(catalog['tables'].get(t) != contract['tables'][t] for t in ORDERS):
            raise SchemaNotReady('Order import schema drift')
        expected_revisions = sorted(['rf_orders_0001'] + ([VECTOR_REVISION] if actual_profile == 'hybrid' else []))
    if catalog['revisions'] != expected_revisions:
        raise SchemaNotReady('Application revision missing or incompatible; use the migration command')
    return actual_profile


def check_checkpoints(c):
    from checkpoint_state import checkpoint_status
    return checkpoint_status(c)


def require_checkpoints(dsn):
    from runtime_db import runtime_dsn
    with psycopg.connect(runtime_dsn(dsn)) as c:
        c.execute('SET TRANSACTION READ ONLY')
        check_checkpoints(c)


def require_ready(dsn, profile=None):
    profile = profile or ('hybrid' if os.getenv('RETRIEVAL_MODE','bm25') == 'hybrid' else 'core')
    from runtime_db import runtime_dsn
    with psycopg.connect(runtime_dsn(dsn)) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        schema = c.execute('SELECT current_schema()').fetchone()[0]
        catalog = read_catalog(c, schema)
        actual_profile = check_application(catalog, profile)
        check_checkpoints(c)
        return {'ready':True, 'profile':actual_profile, 'revisions':catalog['revisions']}
