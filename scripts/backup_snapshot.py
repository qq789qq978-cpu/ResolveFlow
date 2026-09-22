"""Private snapshot protocol, executed inside the running API container.

No credentials or business row values are emitted. The exporter stays alive
until pg_dump has consumed its snapshot. Imported by PostgreSQL tests as well.
"""
from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import select
import sys

import psycopg
from psycopg import sql

from database_state import check_application, check_checkpoints
from db_migrate import migration_session
from scripts.schema_catalog import read_catalog


def table_fingerprint(c, schema, table):
    """SHA256 of UTF-8 JSONB text rows, C-sorted, each length-prefixed (8 bytes)."""
    digest = hashlib.sha256()
    count = 0
    with c.cursor(name='backup_rows') as cursor:
        cursor.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {} t '
                               'ORDER BY to_jsonb(t)::text COLLATE "C"').format(sql.Identifier(schema, table)))
        for (row,) in cursor:
            raw = row.encode('utf-8')
            digest.update(len(raw).to_bytes(8, 'big'))
            digest.update(raw)
            count += 1
    return {'count': count, 'sha256': digest.hexdigest()}


@contextmanager
def snapshot_session(dsn, timeout=600, *, public_only=True):
    # Acquire the migration lock BEFORE establishing the MVCC snapshot.
    with migration_session(dsn) as (locked_dsn, schema):
        with psycopg.connect(locked_dsn, connect_timeout=5) as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            c.execute("SET LOCAL TIME ZONE 'UTC'")
            c.execute(sql.SQL('SET LOCAL statement_timeout = {}').format(sql.Literal(timeout * 1000)))
            c.execute('SET LOCAL lock_timeout=5000')
            schemas = [r[0] for r in c.execute("SELECT nspname FROM pg_namespace WHERE "
                       "nspname !~ '^pg_' AND nspname <> 'information_schema' ORDER BY nspname")]
            if public_only and (schema != 'public' or schemas != ['public']):
                raise ValueError('Backup supports the Compose public schema only')
            catalog = read_catalog(c, schema)
            profile = check_application(catalog, 'core')
            check_checkpoints(c)
            for table in catalog['tables']:
                c.execute(sql.SQL('LOCK TABLE {} IN ACCESS SHARE MODE').format(sql.Identifier(schema, table)))
            snapshot_id, timestamp, database, server_version, server_num = c.execute(
                "SELECT pg_export_snapshot(), clock_timestamp(), current_database(), "
                "current_setting('server_version'), current_setting('server_version_num')").fetchone()
            tables = {table: table_fingerprint(c, schema, table) for table in catalog['tables']}
            sequences = {}
            for seq in catalog['sequences']:
                last, called = c.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(
                    sql.Identifier(schema, seq[0]))).fetchone()
                sequences[seq[0]] = {'observed_last_value': last, 'observed_is_called': called}
            metadata = {
                'database': database, 'schema': schema, 'profile': profile,
                'snapshot_id': snapshot_id, 'snapshot_observed_at_utc': timestamp.isoformat(),
                'postgres_version': server_version, 'postgres_version_num': int(server_num),
                'catalog': catalog, 'tables': tables,
                'fingerprint_format': 'jsonb-text-utf8-C-sort-u64be-length-sha256-v1',
                'checkpoint_migrations': [r[0] for r in c.execute('SELECT v FROM checkpoint_migrations ORDER BY v')],
                'sequences_observed': sequences,
                'sequence_consistency': 'Sequences are not MVCC; observed values may differ from the dump during writes.',
            }
            yield metadata


def main():
    timeout = int(sys.argv[1])
    try:
        with snapshot_session(os.environ['DATABASE_URL'], timeout) as metadata:
            metadata['packages'] = {p: version(p) for p in (
                'langgraph-checkpoint-postgres', 'psycopg', 'alembic', 'SQLAlchemy')}
            metadata['runtime_source_sha256'] = {
                p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                for p in ('engine.py', 'storage.py', 'db_migrate.py', 'requirements.lock')}
            print(json.dumps(metadata), flush=True)
            # EOF, a wrong acknowledgement or timeout all release the lock/snapshot.
            if not select.select([sys.stdin], [], [], timeout + 60)[0]:
                raise TimeoutError('Snapshot consumer timeout')
            if sys.stdin.readline().strip() != 'dump-complete':
                raise ValueError('Dump did not complete')
        return 0
    except Exception as exc:
        print(json.dumps({'error_type': type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
