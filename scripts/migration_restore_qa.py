"""Synthetic irreversible-change rehearsal in new databases on an isolated QA DB.

Never connect to the main project; keep the damaged and restored QA databases.
This is an upgrade recovery rehearsal, not the stage 3.4 backup product.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--project',default='resolveflow-qa-step33')
    parser.add_argument('--prefix',default='rf_recovery33')
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'resolveflow-qa-[a-z0-9-]+',args.project):parser.error('Dedicated QA project required')
    if not re.fullmatch(r'rf_[a-z0-9_]{1,35}',args.prefix):parser.error('Use a short QA database prefix')
    if args.report.exists():parser.error('Use a new report path')
    source=args.prefix+'_damaged';restored=args.prefix+'_restored'
    container=args.project+'-db-1';network=args.project+'_private'
    work=ROOT/'work'/args.prefix;work.mkdir(exist_ok=True)
    backup=work/'before-destructive-change.dump'
    if backup.exists():parser.error('Use a new database prefix')
    def docker(*values,input=None):
        r=subprocess.run(['docker',*values],cwd=ROOT,input=input,capture_output=True,timeout=180)
        if r.returncode:raise RuntimeError('QA Docker operation failed: '+values[0])
        return r.stdout
    labels=json.loads(docker('inspect','--format','{{json .Config.Labels}}',container))
    assert labels['com.docker.compose.project']==args.project
    def python(db,code):
        return docker('run','--rm','-i','--network',network,'-e',
            'DATABASE_URL=postgresql://resolveflow:qa-step28-database@db:5432/'+db,
            '-e','MODE=demo',args.image,'python','-',input=code.encode())
    snapshot_code='''
import hashlib,json,os,psycopg
from psycopg import sql
with psycopg.connect(os.environ['DATABASE_URL']) as c:
 c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 names=[r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
 out={}
 for name in names:
  rows=[json.dumps(r[0],sort_keys=True) for r in c.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t').format(sql.Identifier(name)))]
  out[name]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()}
 print(json.dumps(out))
'''
    def snapshot(db):return json.loads(python(db,snapshot_code))
    docker('exec',container,'createdb','-U','resolveflow',source)
    print('Preparing synthetic data and a real checkpoint in an isolated database...',flush=True)
    python(source,'''import os
from db_migrate import migrate
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.base import empty_checkpoint
dsn=os.environ['DATABASE_URL'];migrate(dsn,demo=True)
with PostgresSaver.from_conn_string(dsn) as saver:
 checkpoint=empty_checkpoint();checkpoint['channel_values']={'recovery_marker':'before-destructive-change'}
 saver.put({'configurable':{'thread_id':'recovery-qa','checkpoint_ns':''}},checkpoint,
           {'source':'input','step':-1,'parents':{}},{})
''')
    before=snapshot(source)
    dump=docker('exec',container,'pg_dump','-U','resolveflow','-d',source,'-Fc')
    backup.write_bytes(dump)
    # Deliberately destructive only in the newly created synthetic QA database.
    docker('exec',container,'psql','-U','resolveflow','-d',source,'-v','ON_ERROR_STOP=1','-c',
           'ALTER TABLE rf_orders DROP COLUMN used; ALTER TABLE rf_orders ADD COLUMN used BOOLEAN NOT NULL DEFAULT FALSE;')
    damaged=snapshot(source)
    assert before['rf_orders']!=damaged['rf_orders']
    refused=json.loads(python(source,'''import os,json
from database_state import require_ready,SchemaNotReady
try:require_ready(os.environ['DATABASE_URL'])
except SchemaNotReady:print(json.dumps({'drift_refused':True}))
else:raise AssertionError('Destructive schema unexpectedly accepted')
'''))
    assert refused['drift_refused']
    print('Restoring into a separate empty database and checking all data...',flush=True)
    docker('exec',container,'createdb','-U','resolveflow',restored)
    docker('exec','-i',container,'pg_restore','-U','resolveflow','-d',restored,'--exit-on-error',input=dump)
    after=snapshot(restored);assert after==before
    ready=json.loads(python(restored,'''import os,json
from database_state import require_ready
from langgraph.checkpoint.postgres import PostgresSaver
dsn=os.environ['DATABASE_URL'];result=require_ready(dsn)
with PostgresSaver.from_conn_string(dsn) as saver:
 row=saver.get_tuple({'configurable':{'thread_id':'recovery-qa','checkpoint_ns':''}})
 assert row.checkpoint['channel_values']=={'recovery_marker':'before-destructive-change'}
result['checkpoint_readable']=True
print(json.dumps(result))
'''))
    report={'passed':True,'project':args.project,'damaged_database':source,'restored_database':restored,
            'backup_private_path':str(backup.relative_to(ROOT)).replace('\\','/'),
            'backup_sha256':hashlib.sha256(dump).hexdigest(),'backup_bytes':len(dump),
            'before':before,'after':after,'restored_tables':len(before),
            'schema_readd_failed_to_restore_values':True,'drift_refused':True,'restored_ready':ready,
            'main_database_accessed':False,'qa_databases_preserved':True,'model_api_calls':0}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'restored_tables':len(before),'checkpoint_readable':True}),flush=True)


if __name__=='__main__':main()
