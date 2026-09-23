"""Read-only, credential-free evidence from a dedicated screenshot demo."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.restore_qa import api, python_in


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'resolveflow-qa-demo-[a-z0-9-]+', args.project) or args.output.exists():
        parser.error('Use a dedicated demo project and a new evidence path')
    runs = api(args.project, '/runs')
    details = [api(args.project, '/runs/' + row['id']) for row in runs]
    assert all(row['mode'] == 'demo' for row in details)
    database = python_in(args.project, '''
import os,json,psycopg
from psycopg.rows import dict_row
with psycopg.connect(os.environ['READONLY_DATABASE_URL'],row_factory=dict_row) as c:
 result={table:c.execute('SELECT * FROM '+table).fetchall() for table in ('rf_refunds','rf_approvals')}
 result['checkpoints']=c.execute('SELECT thread_id,count(*) AS count FROM checkpoints GROUP BY thread_id ORDER BY thread_id').fetchall()
 result['mode']=os.environ['MODE']
 result['retrieval_mode']=os.environ['RETRIEVAL_MODE']
 result['model_key_present']=bool(os.environ.get('OPENAI_API_KEY'))
print(json.dumps(result,default=str))
''')
    report = {'captured_at': datetime.now(timezone.utc).isoformat(), 'project': args.project,
              'runs': details, 'database': database,
              'metrics': api(args.project, '/metrics', role='admin'),
              'alerts': api(args.project, '/alerts', role='admin')}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'saved': str(args.output), 'runs': len(runs),
                      'refunds': len(database['rf_refunds']), 'approvals': len(database['rf_approvals'])}))


if __name__ == '__main__':
    main()
