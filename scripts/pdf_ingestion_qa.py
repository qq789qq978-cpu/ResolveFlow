"""Real PDF CLI, local embedding, restricted API and Worker in a fresh Compose QA."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.database_restore import unused_subnet
from scripts.restore_qa import api, wait_run, python_in
from scripts.roles_qa import PASSWORDS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--model-directory',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not args.project.startswith('resolveflow-qa-') or args.report.exists():parser.error('Use a new QA project/report')
    work=ROOT/'work'/args.project;work.mkdir(exist_ok=False)
    empty=work/'empty.env';empty.write_text('')
    first=unused_subnet();second=unused_subnet(excluded=[first])
    override=work/'override.json'
    override.write_text(json.dumps({'networks':{'default':{'internal':True,'ipam':{'config':[{'subnet':first}]}},
        'embedding-private':{'ipam':{'config':[{'subnet':second}]}}}}))
    env={**os.environ,'RESOLVEFLOW_IMAGE':args.image,'POSTGRES_IMAGE':'pgvector/pgvector:0.8.6-pg17-trixie',
         'MODE':'demo','RETRIEVAL_MODE':'hybrid','COMPOSE_PROFILES':'semantic','POSTGRES_PASSWORD':'qa-pdf-admin',
         'APP_API_KEY':'qa-pdf-operator','REVIEWER_API_KEY':'qa-pdf-reviewer','ADMIN_API_KEY':'qa-pdf-admin',
         'OPENAI_API_KEY':'','APP_PORT':'8022','RF_EMBEDDING_MODEL_DIR':str(args.model_directory.resolve()),
         **{'RF_'+k.upper()+'_PASSWORD':v for k,v in PASSWORDS.items()}}
    compose=['docker','compose','--env-file',str(empty),'-p',args.project,'-f',str(ROOT/'compose.yaml'),'-f',str(override)]
    def command(values,required=True):
        p=subprocess.run(values,cwd=ROOT,env=env,capture_output=True,timeout=240)
        if p.returncode and required:
            (work/'last-error.log').write_bytes(p.stdout+p.stderr)
            raise RuntimeError('PDF QA command failed; inspect private work log')
        return p
    def maintenance(values,required=True):
        return command(compose+['run','--rm','--no-deps','-T','--user','0','-v',str(work)+':/stage',
            '-v',str(ROOT/'tests/fixtures/policies')+':/fixtures:ro','migrate','python',*values],required)
    def status():return json.loads(maintenance(['policy_releases.py','status']).stdout)
    def snapshot():
        return python_in(args.project,"import os,json;from storage import Store;\nwith Store(os.environ['DATABASE_URL']).connect() as c:\n print(json.dumps({t:c.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ['rf_runs','rf_jobs','rf_approvals','rf_refunds']}))")
    result={'passed':False,'project':args.project,'image':args.image,'model_api_calls':0,'checks':[]}
    def check(name,condition):
        assert condition,name
        result['checks'].append(name)
    try:
        check('fresh_isolated_project',not command(['docker','ps','-aq','--filter','label=com.docker.compose.project='+args.project]).stdout.strip())
        command(compose+['up','--no-build','-d','--wait','db'])
        command(['docker','exec',args.project+'-db-1','psql','-U','resolveflow','-d','resolveflow',
                 '-v','ON_ERROR_STOP=1','-c','CREATE EXTENSION vector WITH SCHEMA public'])
        command(compose+['up','--no-build','-d','--wait','--wait-timeout','180'])
        result['permissions']=json.loads(command(compose+['run','--rm','--no-deps','-T','db-roles','python','-c',(ROOT/'scripts/roles_probe.py').read_text()]).stdout)
        original=status()['head'];before=snapshot()
        maintenance(['policy_pdf.py','/fixtures/materials.pdf','--directory','/stage/draft','--id','materials-pdf-v1','--title','PDF材料与时限样例','--version','1'])
        inspection=json.loads((work/'draft/inspection.json').read_text());result['inspection']=inspection
        check('parse_is_draft_no_publication',inspection['review_status']=='draft' and status()['head']==original)
        candidate=work/'candidate';shutil.copytree(ROOT/'knowledge',candidate)
        for p in (work/'draft').glob('*.pdf*'):shutil.copyfile(p,candidate/p.name)
        governance=json.loads((candidate/'governance.json').read_text())
        governance['policies']['materials-pdf-v1']={**governance['policies']['refund-v2'],'document_sha256':inspection['document_sha256'],
             'review_note':'Synthetic PDF QA fixture only; not human-reviewed live policy'}
        (candidate/'governance.json').write_text(json.dumps(governance))
        manifest=json.loads((candidate/'release.json').read_text());manifest['id']='pdf-compose-qa-v1'
        (candidate/'release.json').write_text(json.dumps(manifest))
        published=json.loads(maintenance(['policy_releases.py','publish','--directory','/stage/candidate',
            '--expected-generation','1','--actor','qa-maintainer','--reason','Synthetic PDF ingestion acceptance']).stdout)
        result['published']=published
        result['vectors']=json.loads(maintenance(['scripts/build_vector_index.py','--report','/stage/vectors.json']).stdout)
        for query,page in [('订单编号 商品照片',1),('没有承诺退款到账时限',2)]:
            hits=api(args.project,'/knowledge?'+urlencode({'query':query}))
            check('real_hybrid_page_'+str(page),hits['retrieval']['used']=='hybrid' and any(h.get('source_type')=='pdf' and h['page_start']==page for h in hits['results']))
            result.setdefault('queries',[]).append(hits)
        check('knowledge_has_no_business_writes',snapshot()==before)
        broken=work/'broken';shutil.copytree(candidate,broken);(broken/'materials-pdf-v1.pdf').write_bytes(b'%PDF-1.4\nbroken')
        failed=maintenance(['policy_releases.py','publish','--directory','/stage/broken','--expected-generation','2','--actor','qa','--reason','Malformed candidate must fail'],False)
        check('bad_pdf_preserves_active_release',failed.returncode!=0 and status()['head']['generation']==2)
        info=api(args.project,'/runs',{'order_id':'RF-1002','ticket':'订单编号 商品照片'},expected=202)['id']
        row=wait_run(args.project,info)
        check('worker_checkpoint_keeps_pdf_source',row['status']=='escalated' and any(h.get('page_start')==1 for h in row['state']['evidence']))
        result['information_run']=info
        business=[]
        for order,expected in [('RF-1001','refunded'),('RF-1002','auto_rejected'),('RF-1004','awaiting_approval')]:
            rid=api(args.project,'/runs',{'order_id':order,'ticket':'申请退款'},expected=202)['id'];row=wait_run(args.project,rid)
            check(order+'_routing',row['status']==expected)
            if expected=='awaiting_approval':
                api(args.project,'/runs/'+rid+'/approval',{'approved':True,'reason':'No operator approval'},'operator',403)
                api(args.project,'/runs/'+rid+'/approval',{'approved':True,'reason':'Reviewed synthetic QA'},'reviewer',202)
                check('reviewer_resume',wait_run(args.project,rid)['status']=='refunded')
                api(args.project,'/runs/'+rid+'/approval',{'approved':True,'reason':'Duplicate'},'reviewer',409)
            business.append(rid)
        result['business_runs']=business
        rid=api(args.project,'/runs',{'order_id':'RF-1001','ticket':'申请退款'},expected=202)['id']
        check('refund_idempotency',wait_run(args.project,rid)['status']=='already_refunded')
        maintenance(['policy_releases.py','rollback','--release',original['release_id'],'--expected-generation','2','--actor','qa','--reason','PDF rollback acceptance'])
        hits=api(args.project,'/knowledge?'+urlencode({'query':'订单编号 商品照片'}))
        check('rollback_removes_pdf_from_current_results',not any(h.get('source_type')=='pdf' for h in hits['results']))
        check('old_pdf_snapshot_retained',any(h.get('page_start')==1 for h in api(args.project,'/runs/'+info)['state']['evidence']))
        result['passed']=True
    finally:
        command(compose+['stop'])
        result['stopped_volume_preserved']=True
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(result['checks']),'denials':len(result['permissions']['denials'])}))


if __name__=='__main__':main()
