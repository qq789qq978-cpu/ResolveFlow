"""Read-only restored-database acceptance; no migration, seeding or setval."""
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver

from scripts.backup_snapshot import snapshot_session


def sequence_checks(c, catalog, schema):
    result = {}
    for name, table, column, _, start, increment, maximum, minimum, cache, cycle in catalog['sequences']:
        if not table or not column or increment != 1 or cycle:
            raise ValueError('Unsupported sequence contract')
        last, called = c.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(
            sql.Identifier(schema, name))).fetchone()
        high = c.execute(sql.SQL('SELECT max({}) FROM {}').format(
            sql.Identifier(column), sql.Identifier(schema, table))).fetchone()[0]
        next_value = last + increment if called else last
        safe = minimum <= next_value <= maximum and (high is None or next_value > high)
        result[name] = {'last_value': last, 'is_called': called, 'table_max': high,
                        'next_value': next_value, 'safe': safe}
        if not safe:
            raise ValueError('Unsafe restored sequence: '+name)
    return result


def validate_restore(dsn, manifest, *, public_only=True):
    expected = manifest['snapshot']
    if expected['fingerprint_format'] != 'jsonb-text-utf8-C-sort-u64be-length-sha256-v1':
        raise ValueError('Unsupported fingerprint format')
    with snapshot_session(dsn, public_only=public_only) as actual:
        for key in ('schema', 'profile', 'catalog', 'tables', 'checkpoint_migrations'):
            if actual[key] != expected[key]:
                raise ValueError('Restored '+key+' differs from backup')
        if actual['postgres_version_num'] // 10000 != expected['postgres_version_num'] // 10000:
            raise ValueError('PostgreSQL major mismatch')
        with psycopg.connect(dsn) as c:
            c.execute('SET TRANSACTION READ ONLY')
            sequences = sequence_checks(c, actual['catalog'], actual['schema'])
            invalid = c.execute("SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid "
                                "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s "
                                "AND (NOT i.indisvalid OR NOT i.indisready)", (actual['schema'],)).fetchone()[0]
            if invalid:
                raise ValueError('Invalid restored index')
        checkpoints = []
        # Force even the library's read connection to reject SQL writes.
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as c:
            c.execute('SET default_transaction_read_only=on')
            saver = PostgresSaver(c)
            for row in c.execute('SELECT id,status,state FROM rf_runs ORDER BY id').fetchall():
                if not row['state']:
                    continue  # Never-started queued jobs need not have a checkpoint.
                checkpoint = saver.get_tuple({'configurable': {'thread_id': str(row['id'])}})
                if checkpoint is None:
                    raise ValueError('Stored run has no readable checkpoint')
                checkpoints.append({'run_id': str(row['id']), 'status': row['status'],
                                    'checkpoint_id': checkpoint.checkpoint['id']})
        return {'matched_tables': len(actual['tables']), 'table_fingerprints': actual['tables'],
                'catalog_equal': True, 'indexes_valid': True,
                'revisions': actual['catalog']['revisions'],
                'checkpoint_migrations': actual['checkpoint_migrations'],
                'sequences': sequences, 'readable_checkpoints': checkpoints}


def main():
    try:
        manifest = json.load(sys.stdin)
        for package, expected in manifest['snapshot']['packages'].items():
            if version(package) != expected:
                raise ValueError('Runtime package differs from backup')
        for path, expected in manifest['snapshot']['runtime_source_sha256'].items():
            if path not in ('engine.py', 'storage.py', 'db_migrate.py', 'requirements.lock', 'db_roles.py', 'mcp_gateway.py', 'runtime_db.py', 'task_runtime.py', 'worker_task.py', 'worker.py', 'jobs.py'):
                raise ValueError('Unexpected runtime file')
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise ValueError('Runtime source differs from backup')
        result = validate_restore(os.environ['DATABASE_URL'], manifest)
        print(json.dumps({'passed': True, **result}))
        return 0
    except Exception as exc:
        print(json.dumps({'passed': False, 'error_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
