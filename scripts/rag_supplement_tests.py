"""Run the CI unit/PG lists against a fresh isolated database; preserve its volume."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.database_restore import unused_subnet


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-directory',type=Path,required=True)
    parser.add_argument('--image',default='resolveflow:rag-supplement-tests')
    parser.add_argument('--focused',action='store_true')
    parser.add_argument('--unit-files',nargs='+',help='Optional focused pytest files instead of the CI unit list')
    parser.add_argument('--postgres-files',nargs='+',help='Optional focused pytest files instead of the CI PG list')
    args=parser.parse_args();out=args.report_directory.resolve();out.mkdir(parents=True,exist_ok=True)
    project='resolveflow-qa-rag-'+uuid.uuid4().hex[:8]
    def run(argv,**kwargs):return subprocess.run(argv,check=True,cwd=ROOT,**kwargs)
    summary={'project':project,'passed':False,'volume_preserved':True,'model_api_calls':0}
    try:
        run(['docker','network','create','--internal','--subnet',unused_subnet(),project],capture_output=True)
        run(['docker','run','-d','--name',project+'-db','--network',project,
             '-e','POSTGRES_USER=qa','-e','POSTGRES_PASSWORD=synthetic-qa-only','-e','POSTGRES_DB=qa',
             '-v',project+'-data:/var/lib/postgresql/data','pgvector/pgvector:0.8.6-pg17-trixie'],capture_output=True)
        import time
        for _ in range(60):
            if subprocess.run(['docker','exec',project+'-db','pg_isready','-U','qa','-d','qa'],capture_output=True).returncode==0:break
            time.sleep(1)
        else:raise RuntimeError('QA database not ready')
        ci=(ROOT/'.github/workflows/ci.yml').read_text()
        commands=re.findall(r'python -m pytest (.+?) -q --junitxml=validation/(unit|postgres)-ci.xml',ci)
        for names,kind in commands:
            if args.focused:names='test_policy_pdf.py' if kind=='unit' else 'test_policy_pdf_pg.py'
            selected=args.unit_files if kind=='unit' else args.postgres_files
            if selected:names=' '.join(selected)
            command=['docker','run','--rm','--init','--user','0','--network',project,
                     '-v',str(ROOT)+':/qa:ro','-v',str(out)+':/evidence','-w','/qa',
                     '-e','MODE=demo','-e','OPENAI_API_KEY=', '-e','RETRIEVAL_MODE=bm25']
            if kind=='postgres':command+=['-e','RUN_PG_TESTS=1','-e','DATABASE_URL=postgresql://qa:synthetic-qa-only@'+project+'-db/qa']
            command += [args.image,'python','-m','pytest',*names.split(),'-q','-p','no:cacheprovider','--junitxml=/evidence/'+kind+'.xml']
            with (out/(kind+'.log')).open('w',encoding='utf-8') as log:
                run(command,stdout=log,stderr=subprocess.STDOUT)
        summary['passed']=True
    finally:
        subprocess.run(['docker','stop',project+'-db'],capture_output=True)
        summary['stopped']=True
        (out/'run.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))


if __name__=='__main__':main()
