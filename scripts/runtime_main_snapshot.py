"""Read-only evidence of main data, schema and container identity (no secrets)."""
import argparse
import ast
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new evidence path')
    root = Path(__file__).resolve().parents[1]
    module = ast.parse((root/'verify_persistence.py').read_text(encoding='utf-8-sig'))
    source = next(ast.literal_eval(n.value) for n in module.body
                  if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SNAPSHOT' for t in n.targets))
    def run(argv):
        return subprocess.check_output(argv, cwd=root).decode('utf-8')
    report = {'tables': json.loads(run(['docker','exec','resolveflow-resolveflow-1','python','-c',source]))}
    source = 'import os,json;from scripts.schema_catalog import snapshot;print(json.dumps(snapshot(os.environ["DATABASE_URL"])))'
    report['catalog'] = json.loads(run(['docker','exec','resolveflow-resolveflow-1','python','-c',source]))
    report['containers'] = {}
    for service in ('db','resolveflow','worker','embedding'):
        item = json.loads(run(['docker','inspect','resolveflow-'+service+'-1']))[0]
        report['containers'][service] = {'id':item['Id'], 'image':item['Image'],
            'started':item['State']['StartedAt'], 'health':item['State'].get('Health',{}).get('Status'),
            'volumes':{m['Destination']:m['Name'] for m in item['Mounts'] if m['Type']=='volume'}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'tables':len(report['tables']), 'healthy':all(c['health']=='healthy' for c in report['containers'].values())}))


if __name__ == '__main__':
    main()
