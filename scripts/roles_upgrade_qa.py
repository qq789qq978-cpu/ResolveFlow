"""Adopt a restored legacy database, then resume a real pending approval."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.database_backup import create_backup,run,write_json
from scripts.database_restore import restore_backup,provision_restored_roles,wait_database
from scripts.restore_qa import start_runtime,stop_project,maintenance_in,python_in,api,wait_run
from scripts.migration_qa import SNAPSHOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle',type=Path)
    parser.add_argument('--image',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():parser.error('Use a new report path')
    work=ROOT/'work/role-upgrade'/args.project;work.mkdir(parents=True,exist_ok=False)
    report={'passed':False,'model_api_calls':0}
    restored=None
    try:
        restored=restore_backup(args.bundle,args.project,work/'legacy-restore.json')
        start_runtime(restored)
        # Make the interrupted graph with the OLD image before privilege adoption.
        maintenance_in(restored,'''import os,json,psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as c:
 c.execute("INSERT INTO rf_orders SELECT 'RF-3601',owner,amount,days,used,status FROM rf_orders WHERE id='RF-1004'")
print(json.dumps({'inserted':True}))''')
        rid=api(args.project,'/runs',{'order_id':'RF-3601','ticket':'申请退款'},expected=202)['id']
        pending=wait_run(args.project,rid);assert pending['status']=='awaiting_approval'
        before=python_in(args.project,SNAPSHOT)
        # Remove only the two disposable application containers so they can be
        # recreated with new credentials. Keep their independent PostgreSQL volume.
        for service in ('worker','resolveflow'):
            name=args.project+'-'+service+'-1'
            run(['docker','stop',name]);run(['docker','rm',name])
        candidate=run(['docker','image','inspect','--format','{{.Id}}',args.image]).decode().strip()
        isolated_work=ROOT/'work/restores'/args.project
        assert provision_restored_roles(isolated_work,restored['target'],candidate)
        restored['images']['resolveflow']=candidate;restored['images']['worker']=candidate
        run(['docker','run','--rm','--network',restored['target']['network'],
            '--env-file',str(isolated_work/'maintenance.env'),candidate,'python','db_migrate.py','prepare','--profile','hybrid'])
        start_runtime(restored)
        after=python_in(args.project,SNAPSHOT);assert after==before
        assert api(args.project,'/runs/'+rid)==pending
        probe=(ROOT/'scripts/roles_probe.py').read_text()
        report['permissions']=json.loads(run(['docker','run','--rm','--network',restored['target']['network'],
            '--env-file',str(isolated_work/'admin.env'),candidate,'python','-c',probe]))
        api(args.project,'/runs/'+rid+'/approval',{'approved':True,'reason':'Resume legacy checkpoint with restricted roles'},'reviewer',202)
        final=wait_run(args.project,rid);assert final['status']=='refunded'
        assert [t for t in pending['state']['trace'] if t['node']=='investigate']==[t for t in final['state']['trace'] if t['node']=='investigate']
        bundle,manifest=create_backup(work/'backups',args.project)
        report.update(passed=True,before=before,after_adoption=after,preserved_tables=len(before),
            original_pending_id=rid,resumed_status=final['status'],investigation_unchanged=True,
            restricted_backup=str(bundle.relative_to(ROOT)).replace('\\','/'),backup_sha256=manifest['archive']['sha256'])
    finally:
        if restored:stop_project(args.project)
        report['stopped_volume_preserved']=True
        args.report.parent.mkdir(parents=True,exist_ok=True);write_json(args.report,report)
    print(json.dumps({'passed':True,'preserved_tables':report['preserved_tables'],'resumed_status':report['resumed_status']}))


if __name__=='__main__':main()
