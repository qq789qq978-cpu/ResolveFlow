"""Optional importer credential: order facts/journal only, no refunds or approvals."""
import re
import psycopg
from psycopg import sql

MARKER='ResolveFlow synthetic importer v1'


def grant(c,schema='public',role='rf_sync'):
    ident=sql.Identifier(role);scope=sql.Identifier(schema)
    c.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM {}').format(scope,ident))
    c.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(scope,ident))
    c.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}').format(scope,ident))
    c.execute(sql.SQL('REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}').format(scope,ident))
    for table,privilege in [('rf_orders','SELECT, INSERT, UPDATE'),
                            ('rf_order_versions','SELECT, INSERT, UPDATE'),('rf_order_events','SELECT, INSERT')]:
        if c.execute('SELECT to_regclass(%s)',(schema+'.'+table,)).fetchone()[0]:
            c.execute(sql.SQL('GRANT {} ON {} TO {}').format(sql.SQL(privilege),sql.Identifier(schema,table),ident))


def provision(dsn,password,schema='public',role='rf_sync'):
    if not re.fullmatch(r'[A-Za-z0-9_-]{24,128}',password):raise ValueError('Invalid importer password')
    if not re.fullmatch(r'rf_[a-z0-9_]{1,59}',role):raise ValueError('Invalid importer role')
    with psycopg.connect(dsn) as c:
        if not c.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0]:raise ValueError('Owner required')
        existing=c.execute("SELECT shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s",(role,)).fetchone()
        if existing and existing[0]!=MARKER:raise ValueError('Unmanaged importer role')
        if c.execute('SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s) OR roleid=(SELECT oid FROM pg_roles WHERE rolname=%s))',(role,role)).fetchone()[0]:raise ValueError('Importer role membership forbidden')
        ident=sql.Identifier(role)
        if not existing:c.execute(sql.SQL('CREATE ROLE {}').format(ident))
        c.execute(sql.SQL('ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}').format(ident,sql.Literal(password)))
        c.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(ident,sql.Literal(MARKER)))
        database=c.execute('SELECT current_database()').fetchone()[0]
        c.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),ident))
        grant(c,schema,role)


def refresh(c,schema='public'):
    if c.execute("SELECT 1 FROM pg_roles WHERE rolname='rf_sync' AND shobj_description(oid,'pg_authid')=%s",(MARKER,)).fetchone():
        grant(c,schema)


def require(dsn):
    with psycopg.connect(dsn,connect_timeout=3) as c:
        row=c.execute('SELECT current_user,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if row[0]!='rf_sync' or any(row[1:]):raise ValueError('Restricted importer role required')
        if c.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE')").fetchone()[0]:raise ValueError('Importer must not own schema')
        c.execute('SELECT 1 FROM rf_order_versions LIMIT 1')
