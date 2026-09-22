"""Real grants/denials through separately authenticated connections; QA only."""
import json
import os
import psycopg
from psycopg.conninfo import make_conninfo


def inspect_roles(dsn,passwords):
    urls={kind:make_conninfo(dsn,user='rf_'+kind,password=password) for kind,password in passwords.items()}
    result={'passed':False,'denials':[],'identities':{},'privileges':{}}
    for kind,url in urls.items():
        with psycopg.connect(url) as c:
            result['identities'][kind]=c.execute('SELECT current_user,session_user').fetchone()
            flags=c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
            assert not any(flags)
            result['privileges'][kind]={'elevated_flags':list(flags),
                'schema_create':c.execute("SELECT has_schema_privilege(current_user,'public','CREATE')").fetchone()[0],
                'db_create':c.execute("SELECT has_database_privilege(current_user,current_database(),'CREATE')").fetchone()[0],
                'db_temp':c.execute("SELECT has_database_privilege(current_user,current_database(),'TEMP')").fetchone()[0]}
    def denied(kind,query):
        with psycopg.connect(urls[kind],autocommit=True) as c:
            c.execute('SET default_transaction_read_only=off')
            c.execute('BEGIN')
            try:c.execute(query)
            except psycopg.errors.InsufficientPrivilege:
                result['denials'].append({'role':kind,'statement':query,'sqlstate':'42501'})
            else:raise AssertionError('Unexpected privilege: '+kind)
            finally:c.execute('ROLLBACK')
    for kind in ('app','readonly'):
        for query in ('CREATE TABLE public.qa_forbidden(id int)',
                      'CREATE TEMP TABLE qa_forbidden(id int)',
                      'ALTER TABLE rf_runs ADD COLUMN qa_forbidden int',
                      'DROP TABLE rf_reviews', 'TRUNCATE rf_jobs',
                      "UPDATE rf_orders SET days=days WHERE false",
                      "UPDATE rf_knowledge_documents SET body=body WHERE false",
                      "UPDATE rf_policy_head SET generation=generation WHERE false",
                      "UPDATE rf_schema_version SET version_num=version_num WHERE false",
                      "INSERT INTO checkpoint_migrations(v) VALUES(1000)",
                      "SELECT setval('rf_audit_id_seq',1,false)",
                      'SET ROLE rf_migrator','SET ROLE resolveflow',
                      'CREATE ROLE qa_forbidden','ALTER ROLE rf_app SUPERUSER'):
            denied(kind,query)
    for query in ("UPDATE rf_runs SET status=status WHERE false",
                  "INSERT INTO rf_worker_heartbeats(worker_id) VALUES('forbidden')",
                  "SELECT nextval('rf_audit_id_seq')",'SELECT rf_lock_policy_refund()'):
        denied('readonly',query)
    for query in ('DELETE FROM rf_audit WHERE false','DELETE FROM rf_refunds WHERE false',
                  "SELECT nextval('rf_policy_events_id_seq')"):
        denied('app',query)
    for query in ('CREATE ROLE qa_forbidden','ALTER ROLE rf_migrator SUPERUSER','SET ROLE resolveflow'):
        denied('migrator',query)
    with psycopg.connect(urls['readonly']) as c:
        assert c.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        for table in ('rf_runs','rf_audit','checkpoints','checkpoint_writes','rf_schema_version'):
            c.execute('SELECT count(*) FROM '+table).fetchone()
    with psycopg.connect(urls['app']) as c:
        c.execute('SELECT rf_lock_policy_refund()')
    with psycopg.connect(urls['migrator']) as c:
        c.execute('CREATE TABLE qa_future_grants(id BIGSERIAL PRIMARY KEY)')
        c.execute('CREATE FUNCTION qa_future_function() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$')
        c.commit()
        try:
            with psycopg.connect(urls['readonly']) as reader:reader.execute('SELECT count(*) FROM qa_future_grants')
            denied('app','INSERT INTO qa_future_grants DEFAULT VALUES')
            denied('readonly','INSERT INTO qa_future_grants DEFAULT VALUES')
            denied('app','SELECT qa_future_function()')
            denied('readonly','SELECT qa_future_function()')
            c.execute('ALTER TABLE qa_future_grants ADD COLUMN reviewed int')
        finally:
            c.execute('DROP TABLE qa_future_grants');c.execute('DROP FUNCTION qa_future_function()')
    result['passed']=True
    return result


if __name__=='__main__':
    print(json.dumps(inspect_roles(os.environ['DATABASE_URL'],
        {k:os.environ['RF_'+k.upper()+'_PASSWORD'] for k in ('migrator','app','readonly')})))
