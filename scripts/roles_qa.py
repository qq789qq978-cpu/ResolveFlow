"""Fresh Compose role isolation and real business acceptance in dedicated QA."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.database_restore import unused_subnet
from scripts.restore_qa import api,wait_run

PASSWORDS={'migrator':'qa36-migrator-password-123456','app':'qa36-app-password-1234567890','readonly':'qa36-readonly-password-123456'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='resolveflow-qa-step36')
    parser.add_argument('--image',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not args.project.startswith('resolveflow-qa-') or args.report.exists():parser.error('Use a new QA target and report')
    work=ROOT/'work'/args.project;work.mkdir(exist_ok=False)
    empty=work/'empty.env';empty.write_text('')
    override=work/'network.json'
    first=unused_subnet()
    # This QA does not start embedding, but the API uses its private network.
    second=unused_subnet(excluded=[first])
    networks={'networks':{'default':{'ipam':{'config':[{'subnet':first}]}},
                         'embedding-private':{'ipam':{'config':[{'subnet':second}]}}}}
    override.write_text(json.dumps(networks))
    env={**os.environ,'RESOLVEFLOW_IMAGE':args.image,'MODE':'demo','RETRIEVAL_MODE':'bm25',
        'COMPOSE_PROFILES':'','POSTGRES_PASSWORD':'qa36-admin-password',
        'APP_API_KEY':'qa36-operator','REVIEWER_API_KEY':'qa36-reviewer','ADMIN_API_KEY':'qa36-admin',
        'OPENAI_API_KEY':'','APP_PORT':'8019',
        **{'RF_'+k.upper()+'_PASSWORD':v for k,v in PASSWORDS.items()}}
    compose=['docker','compose','--env-file',str(empty),'-p',args.project,'-f',str(ROOT/'compose.yaml'),'-f',str(override)]
    def command(values):
        p=subprocess.run(values,cwd=ROOT,env=env,capture_output=True,timeout=240)
        if p.returncode:
            (work/'last-error.log').write_bytes(p.stdout+p.stderr)
            raise RuntimeError('QA command failed')
        return p.stdout
    report={'passed':False,'project':args.project,'model_api_calls':0}
    try:
        assert not command(['docker','ps','-aq','--filter','label=com.docker.compose.project='+args.project]).strip()
        command(compose+['up','--no-build','-d','--wait','--wait-timeout','180'])
        probe=(ROOT/'scripts/roles_probe.py').read_text()
        report['permissions']=json.loads(command(compose+['run','--rm','--no-deps','-T','db-roles','python','-c',probe]))
        results=[]
        for order,expected in [('RF-1001','refunded'),('RF-1002','auto_rejected'),('RF-1004','awaiting_approval')]:
            rid=api(args.project,'/runs',{'order_id':order,'ticket':'申请退款'},expected=202)['id']
            row=wait_run(args.project,rid);assert row['status']==expected
            if expected=='awaiting_approval':
                api(args.project,'/runs/'+rid+'/approval',{'approved':True,'reason':'Restricted DB roles acceptance'},'reviewer',202)
                row=wait_run(args.project,rid);assert row['status']=='refunded'
            results.append({'id':rid,'order':order,'status':row['status']})
        rid=api(args.project,'/runs',{'order_id':'RF-1001','ticket':'申请退款'},expected=202)['id']
        assert wait_run(args.project,rid)['status']=='already_refunded'
        report.update(passed=True,business=results,duplicate_status='already_refunded')
    finally:
        command(compose+['stop'])
        report['stopped_volume_preserved']=True
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'denials':len(report['permissions']['denials'])}))


if __name__=='__main__':main()
