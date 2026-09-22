"""Permission adoption and lock semantics in an isolated schema and logins."""
import os
import uuid
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

import db_roles
from db_migrate import migrate
from database_state import SchemaNotReady,require_ready
from scripts.schema_catalog import snapshot
from scripts.backup_snapshot import snapshot_session
from test_migrations_pg import database

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Isolated PostgreSQL required')


@pytest.fixture
def roles_db(database):
    dsn,schema=database
    migrate(dsn,demo=True)
    suffix=uuid.uuid4().hex[:12]
    roles={k:'rf_'+suffix+'_'+k for k in db_roles.ROLES}
    passwords={k:'qa-'+k+'-password-123456789012345' for k in roles}
    with snapshot_session(dsn,public_only=False) as before:pass
    try:
        db_roles.provision(dsn,passwords,schema=schema,roles=roles)
        urls={k:make_conninfo(dsn,user=roles[k],password=passwords[k]) for k in roles}
        yield dsn,schema,roles,passwords,urls,before
    finally:
        with psycopg.connect(dsn) as c:
            admin=c.execute('SELECT current_user').fetchone()[0]
            # Only these random roles own this test schema. Hand it back before
            # the existing database fixture removes its own schema.
            for name in roles.values():
                if c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(name,)).fetchone():
                    c.execute(sql.SQL('REASSIGN OWNED BY {} TO {}').format(sql.Identifier(name),sql.Identifier(admin)))
                    c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(name)))
                    c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(name)))


def test_existing_schema_adoption_preserves_all_data_and_is_repeatable(roles_db):
    dsn,schema,roles,passwords,urls,before=roles_db
    db_roles.provision(dsn,passwords,schema=schema,roles=roles)
    with snapshot_session(urls['app'],public_only=False) as after:
        assert before['tables']==after['tables'] and before['catalog']==after['catalog']
    assert require_ready(urls['readonly'])['ready']
    with psycopg.connect(dsn) as c:
        owners=c.execute("SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','S')",(schema,)).fetchall()
    assert owners==[(roles['migrator'],)]


def test_roles_cannot_run_migration_or_self_provision(roles_db):
    _,schema,roles,passwords,urls,_=roles_db
    for kind in ('app','readonly'):
        with pytest.raises((SchemaNotReady,psycopg.errors.ReadOnlySqlTransaction)):
            migrate(urls[kind])
        with pytest.raises(ValueError,match='administrator'):
            db_roles.provision(urls[kind],passwords,schema=schema,roles=roles)


def test_lock_helper_serializes_policy_updates_without_policy_write_grant(roles_db):
    _,_,_,_,urls,_=roles_db
    with psycopg.connect(urls['app']) as app:
        app.execute('SELECT rf_lock_policy_refund()')
        with psycopg.connect(urls['migrator']) as owner:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                owner.execute('LOCK TABLE rf_knowledge_documents IN EXCLUSIVE MODE NOWAIT')
    with psycopg.connect(urls['migrator']) as owner:
        owner.execute('LOCK TABLE rf_knowledge_documents IN EXCLUSIVE MODE NOWAIT')
    with psycopg.connect(urls['app']) as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):app.execute('UPDATE rf_knowledge_documents SET body=body')


def test_readonly_cannot_bypass_acl_by_turning_readonly_off(roles_db):
    _,_,_,_,urls,_=roles_db
    with psycopg.connect(urls['readonly'],autocommit=True) as c:
        c.execute('SET default_transaction_read_only=off')
        for query in ('DELETE FROM rf_runs WHERE false',"SELECT nextval('rf_audit_id_seq')",'SELECT rf_lock_policy_refund()'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute(query)


def test_mcp_uses_readonly_connection_not_business_password(roles_db,monkeypatch):
    _,_,_,_,urls,_=roles_db
    monkeypatch.setenv('DATABASE_URL','postgresql://invalid:invalid@127.0.0.1:1/invalid')
    monkeypatch.setenv('READONLY_DATABASE_URL',urls['readonly'])
    monkeypatch.setenv('RF_ENFORCE_DB_ROLES','1')
    monkeypatch.setenv('RETRIEVAL_MODE','bm25')
    from mcp_gateway import call_tools
    order=call_tools('RF-1001','demo',[('lookup_order',{})])[0]
    assert order['id']=='RF-1001' and order['amount']==29900
    monkeypatch.delenv('READONLY_DATABASE_URL')
    with pytest.raises(ValueError,match='READONLY_DATABASE_URL'):
        call_tools('RF-1001','demo',[('lookup_order',{})])


def test_app_identity_guard_rejects_owner_and_admin(roles_db,monkeypatch):
    dsn,_,roles,_,urls,_=roles_db
    monkeypatch.setattr(db_roles,'ROLES',roles)
    db_roles.require_app(urls['app'])
    for url in (dsn,urls['migrator'],urls['readonly']):
        with pytest.raises(ValueError):db_roles.require_app(url)


def test_unmanaged_role_collision_rolls_back_provision(database):
    dsn,schema=database
    names={k:'rf_collision_'+uuid.uuid4().hex[:12] for k in db_roles.ROLES}
    passwords={k:'qa-'+k+'-password-123456789012345' for k in names}
    with psycopg.connect(dsn) as c:c.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(names['app'])))
    try:
        with pytest.raises(ValueError,match='unmanaged'):
            db_roles.provision(dsn,passwords,schema=schema,roles=names)
        with psycopg.connect(dsn) as c:
            assert not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(names['migrator'],)).fetchone()
    finally:
        with psycopg.connect(dsn) as c:c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(names['app'])))


def test_repeat_provision_removes_column_grant_drift(roles_db):
    dsn,schema,roles,passwords,urls,_=roles_db
    with psycopg.connect(dsn) as c:
        c.execute(sql.SQL('GRANT UPDATE(body) ON rf_knowledge_documents TO {}').format(sql.Identifier(roles['app'])))
    db_roles.provision(dsn,passwords,schema=schema,roles=roles)
    with psycopg.connect(urls['app']) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute('UPDATE rf_knowledge_documents SET body=body WHERE false')
