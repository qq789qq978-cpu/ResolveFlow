"""Snapshot consistency and migration coordination in isolated PG schemas."""
import json
import os

import psycopg
from psycopg import sql
import pytest

from database_state import SchemaNotReady
from db_migrate import migrate, migration_session
from scripts.backup_snapshot import snapshot_session, table_fingerprint
from test_migrations_pg import database

pytestmark = pytest.mark.skipif(os.getenv('RUN_PG_TESTS') != '1', reason='Isolated PostgreSQL required')


def test_exported_snapshot_preserves_old_rows_despite_concurrent_commit(database):
    dsn, schema = database
    migrate(dsn, demo=True)
    with snapshot_session(dsn, public_only=False) as metadata:
        before = metadata['tables']['rf_orders']
        with psycopg.connect(dsn) as writer:
            writer.execute("INSERT INTO rf_orders VALUES ('backup-qa','qa',1,1,FALSE,'delivered')")
        # This is the same PostgreSQL import protocol used by pg_dump --snapshot.
        with psycopg.connect(dsn) as consumer:
            consumer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            consumer.execute(sql.SQL('SET TRANSACTION SNAPSHOT {}').format(sql.Literal(metadata['snapshot_id'])))
            assert table_fingerprint(consumer, schema, 'rf_orders') == before
        with psycopg.connect(dsn) as fresh:
            after = table_fingerprint(fresh, schema, 'rf_orders')
        assert after['count'] == before['count'] + 1 and after['sha256'] != before['sha256']
        assert metadata['catalog']['revisions'] == ['rf_core_0001']
        assert len(metadata['checkpoint_migrations']) == 10
        assert len(metadata['tables']) == 20
        assert 'backup-qa' not in json.dumps(metadata)


def test_backup_blocks_migration_and_releases_after_exception(database):
    dsn, _ = database
    migrate(dsn, demo=True)
    with pytest.raises(RuntimeError, match='consumer failure'):
        with snapshot_session(dsn, public_only=False):
            with pytest.raises(SchemaNotReady, match='Another migration'):
                migrate(dsn)
            raise RuntimeError('consumer failure')
    assert migrate(dsn)['action'] == 'unchanged'


def test_active_migration_refuses_backup(database):
    dsn, _ = database
    migrate(dsn)
    with migration_session(dsn):
        with pytest.raises(SchemaNotReady, match='Another migration'):
            with snapshot_session(dsn, public_only=False):
                pytest.fail('Backup accepted during migration')


def test_public_scope_guard(database):
    dsn, _ = database
    migrate(dsn)
    with pytest.raises(ValueError, match='public schema only'):
        with snapshot_session(dsn):
            pytest.fail('Custom schema accepted by Compose backup')


def test_unknown_structure_refused(database):
    dsn, _ = database
    migrate(dsn)
    with psycopg.connect(dsn) as c:
        c.execute('ALTER TABLE rf_orders ADD COLUMN drift TEXT')
    with pytest.raises(SchemaNotReady, match='drift'):
        with snapshot_session(dsn, public_only=False):
            pytest.fail('Drift accepted')


def test_expired_snapshot_refused(database):
    dsn, _ = database
    migrate(dsn)
    with snapshot_session(dsn, public_only=False) as metadata:
        snapshot_id = metadata['snapshot_id']
    with psycopg.connect(dsn) as consumer:
        consumer.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        with pytest.raises(psycopg.errors.UndefinedObject):
            consumer.execute(sql.SQL('SET TRANSACTION SNAPSHOT {}').format(sql.Literal(snapshot_id)))
