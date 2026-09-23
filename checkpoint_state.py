"""Validate the pinned library's own migration history before letting it resume.

This module never writes checkpoint DDL or edits checkpoint migration records.
"""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from scripts.schema_catalog import CHECKPOINT, read_catalog


def checkpoint_status(c, *, allow_partial=False, catalog=None):
    # Import lazily to keep the shared readiness exception in one place.
    from database_state import SchemaNotReady
    reference = json.loads((Path(__file__).parent/'migrations/baselines/langgraph-3.1.2.json').read_text())
    digest = hashlib.sha256(json.dumps(PostgresSaver.MIGRATIONS).encode()).hexdigest()
    if version('langgraph-checkpoint-postgres') != reference['package_version'] or digest != reference['migrations_sha256']:
        raise SchemaNotReady('LangGraph package changed; review its migration contract before proceeding')
    schema = c.execute('SELECT current_schema()').fetchone()[0]
    # Every task checks checkpoint readiness. Do not acquire metadata locks on
    # unrelated business tables while inspecting only the checkpoint contract.
    # Full deployment readiness still supplies/reads the complete catalog.
    catalog = catalog or read_catalog(c, schema, only_tables=CHECKPOINT)
    tables = {t:catalog['tables'][t] for t in CHECKPOINT if t in catalog['tables']}
    total = len(PostgresSaver.MIGRATIONS)
    if not tables:
        if allow_partial:
            return {'completed':0, 'total':total, 'ready':False}
        raise SchemaNotReady('LangGraph schema missing; run db_migrate.py prepare')
    if 'checkpoint_migrations' not in tables:
        raise SchemaNotReady('LangGraph history missing; inspect or restore instead of guessing versions')
    versions = [r[0] for r in c.execute('SELECT v FROM checkpoint_migrations ORDER BY v')]
    count = len(versions)
    if versions != list(range(count)) or count > total:
        raise SchemaNotReady('Unsupported LangGraph migration history')
    # A statement may commit before its migration version is recorded. Accept
    # precisely that one-step-ahead structure; IF NOT EXISTS makes replay safe.
    candidates = [reference['states'][str(count)]]
    if allow_partial and count < total:
        candidates.append(reference['states'][str(count+1)])
    if tables not in candidates:
        raise SchemaNotReady('LangGraph schema differs from its recorded migration prefix; inspect or restore')
    invalid = c.execute("""SELECT i.relname FROM pg_index x
        JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_class t ON t.oid=x.indrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname=%s AND t.relname=ANY(%s) AND (NOT x.indisvalid OR NOT x.indisready)
        ORDER BY i.relname""", (schema,list(CHECKPOINT))).fetchall()
    if invalid:
        raise SchemaNotReady('Invalid LangGraph index; stop services and REINDEX the inspected index before retrying')
    if count != total and not allow_partial:
        raise SchemaNotReady('LangGraph migration incomplete; run db_migrate.py prepare')
    return {'completed':count, 'total':total, 'ready':count == total}
