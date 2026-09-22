"""Explicit PostgreSQL provisioning and reviewed runtime grants; no secret output."""
import argparse
import json
import os
import re
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from scripts.schema_catalog import read_catalog

ROLES={'migrator':'rf_migrator','app':'rf_app','readonly':'rf_readonly'}
PASSWORD_ENV={k:'RF_'+k.upper()+'_PASSWORD' for k in ROLES}
MARKER='ResolveFlow managed database role v1'
WRITES={
    'rf_runs':'INSERT, UPDATE','rf_jobs':'INSERT, UPDATE',
    'rf_approvals':'INSERT','rf_refunds':'INSERT','rf_reviews':'INSERT',
    'rf_audit':'INSERT','rf_job_attempts':'INSERT',
    'rf_worker_heartbeats':'INSERT, UPDATE, DELETE',
    'checkpoints':'INSERT, UPDATE, DELETE','checkpoint_blobs':'INSERT, UPDATE, DELETE',
    'checkpoint_writes':'INSERT, UPDATE, DELETE',
}
RUNTIME_SEQUENCES={'rf_audit_id_seq','rf_job_attempts_id_seq'}


def validate_names(schema,roles):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}',schema) or schema.startswith('pg_') or schema=='information_schema':
        raise ValueError('Invalid schema')
    if set(roles)!=set(ROLES) or len(set(roles.values()))!=3:
        raise ValueError('Three distinct roles required')
    if any(not re.fullmatch(r'rf_[a-z0-9_]{1,59}',name) for name in roles.values()):
        raise ValueError('Invalid managed role name')


def validate_passwords(passwords):
    if set(passwords)!=set(ROLES) or len(set(passwords.values()))!=3:
        raise ValueError('Three distinct passwords required')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{24,128}',p) for p in passwords.values()):
        raise ValueError('Use 24-128 URL-safe password characters')


def grant_runtime(c,schema='public',roles=None):
    roles=roles or ROLES
    validate_names(schema,roles)
    cat=read_catalog(c,schema)
    if cat['unknown_tables']:raise ValueError('Unknown tables; review privileges')
    owner,app,reader=(sql.Identifier(roles[k]) for k in ('migrator','app','readonly'))
    scope=sql.Identifier(schema)
    c.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC,{},{}').format(scope,app,reader))
    c.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {},{}').format(scope,app,reader))
    for table in cat['tables']:
        ident=sql.Identifier(schema,table)
        c.execute(sql.SQL('REVOKE ALL ON TABLE {} FROM PUBLIC,{},{}').format(ident,app,reader))
        columns=sql.SQL(',').join(sql.Identifier(col[0]) for col in cat['tables'][table]['columns'])
        c.execute(sql.SQL('REVOKE ALL ({}) ON TABLE {} FROM PUBLIC,{},{}').format(columns,ident,app,reader))
        c.execute(sql.SQL('GRANT SELECT ON TABLE {} TO {},{}').format(ident,app,reader))
        if table in WRITES:
            c.execute(sql.SQL('GRANT {} ON TABLE {} TO {}').format(sql.SQL(WRITES[table]),ident,app))
    for seq in cat['sequences']:
        ident=sql.Identifier(schema,seq[0])
        c.execute(sql.SQL('REVOKE ALL ON SEQUENCE {} FROM PUBLIC,{},{}').format(ident,app,reader))
        c.execute(sql.SQL('GRANT SELECT ON SEQUENCE {} TO {},{}').format(ident,app,reader))
        if seq[0] in RUNTIME_SEQUENCES:c.execute(sql.SQL('GRANT USAGE ON SEQUENCE {} TO {}').format(ident,app))
    for kind in ('TABLES','SEQUENCES'):
        c.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE ALL ON {} FROM PUBLIC,{},{}').format(owner,scope,sql.SQL(kind),app,reader))
        c.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON {} TO {},{}').format(owner,scope,sql.SQL(kind),app,reader))
    c.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC').format(owner))
    # LOCK SHARE otherwise requires policy write privileges. This fixed no-arg
    # helper grants exactly one lock; callers cannot supply SQL or relation names.
    body=sql.SQL('BEGIN LOCK TABLE {} IN SHARE MODE; END;').format(sql.Identifier(schema,'rf_knowledge_documents')).as_string(c)
    function=sql.Identifier(schema,'rf_lock_policy_refund')
    c.execute(sql.SQL('CREATE OR REPLACE FUNCTION {}() RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS {}').format(function,sql.Literal(body)))
    c.execute(sql.SQL('ALTER FUNCTION {}() OWNER TO {}').format(function,owner))
    c.execute(sql.SQL('REVOKE ALL ON FUNCTION {}() FROM PUBLIC,{},{}').format(function,app,reader))
    c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION {}() TO {}').format(function,app))
    return {'tables':len(cat['tables']),'sequences':len(cat['sequences'])}


def provision(dsn,passwords,*,schema='public',roles=None):
    roles=roles or ROLES
    validate_names(schema,roles);validate_passwords(passwords)
    if conninfo_to_dict(dsn).get('password') in passwords.values():
        raise ValueError('Managed roles must not reuse the administrator password')
    with psycopg.connect(dsn,connect_timeout=5) as c:
        c.execute('SET LOCAL lock_timeout=5000')
        if not c.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0]:
            raise ValueError('Provision requires cluster administrator')
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('resolveflow:migration:'+schema,))
        cat=read_catalog(c,schema)
        if cat['unknown_tables']:raise ValueError('Unknown tables')
        for kind,name in roles.items():
            existing=c.execute("SELECT oid,shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s",(name,)).fetchone()
            if existing:
                if existing[1]!=MARKER:raise ValueError('Existing unmanaged role')
                if c.execute('SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=%s OR roleid=%s)',(existing[0],existing[0])).fetchone()[0]:
                    raise ValueError('Unexpected role membership')
            else:c.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(name)))
            c.execute(sql.SQL('ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(name),sql.Literal(passwords[kind])))
            c.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(name),sql.Literal(MARKER)))
        database=c.execute('SELECT current_database()').fetchone()[0]
        c.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC,{},{},{}').format(sql.Identifier(database),*[sql.Identifier(roles[k]) for k in ROLES]))
        c.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {},{},{}').format(sql.Identifier(database),*[sql.Identifier(roles[k]) for k in ROLES]))
        c.execute(sql.SQL('ALTER ROLE {} IN DATABASE {} SET default_transaction_read_only=on').format(sql.Identifier(roles['readonly']),sql.Identifier(database)))
        c.execute(sql.SQL('ALTER SCHEMA {} OWNER TO {}').format(sql.Identifier(schema),sql.Identifier(roles['migrator'])))
        for table in cat['tables']:
            c.execute(sql.SQL('ALTER TABLE {} OWNER TO {}').format(sql.Identifier(schema,table),sql.Identifier(roles['migrator'])))
        for seq in cat['sequences']:
            c.execute(sql.SQL('ALTER SEQUENCE {} OWNER TO {}').format(sql.Identifier(schema,seq[0]),sql.Identifier(roles['migrator'])))
        result=grant_runtime(c,schema,roles)
    return {'provisioned':True,'roles':roles,**result}


def require_app(dsn):
    with psycopg.connect(dsn,connect_timeout=5) as c:
        row=c.execute('SELECT current_user,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if row[0]!=ROLES['app'] or any(row[1:]):raise ValueError('Runtime requires restricted rf_app')
        if c.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE')").fetchone()[0]:
            raise ValueError('Runtime must not create schema objects')
        if c.execute('SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user))').fetchone()[0]:
            raise ValueError('Runtime must not inherit or switch roles')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['provision','grant'])
    args=parser.parse_args()
    try:
        if args.action=='provision':result=provision(os.environ['DATABASE_URL'],{k:os.environ[v] for k,v in PASSWORD_ENV.items()})
        else:
            with psycopg.connect(os.environ['DATABASE_URL']) as c:result=grant_runtime(c)
        print(json.dumps(result));return 0
    except Exception as exc:
        print(json.dumps({'passed':False,'error_type':type(exc).__name__}));return 1


if __name__=='__main__':raise SystemExit(main())
