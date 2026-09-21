"""Run stage-one fault regressions serially against isolated demo projects.

Requires the demo resolveflow main stack and the matching resolveflow:local
image. Child harnesses keep their named volumes, stop their QA services and
compare read-only main fingerprints. Do not run this alongside other QA.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CASES = (
    ('1.5', 'worker_crash_qa.py', ('--step', '1.5')),
    ('1.6', 'worker_crash_qa.py', ('--step', '1.6')),
    ('1.7', 'worker_crash_qa.py', ('--step', '1.7')),
    ('1.8', 'database_outage_qa.py', ()),
    ('1.9', 'multi_worker_qa.py', ()),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    args = parser.parse_args()
    reports = args.reports.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    summary_path = reports / 'summary.json'
    if summary_path.exists() or any((reports / f'step-{step}.json').exists() for step,_,_ in CASES):
        parser.error('Use a new report directory; historical evidence is preserved')
    result = {'stage': 1, 'passed': False, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'mode': 'demo', 'cases': [], 'scope': 'Fault regressions 1.5-1.9, not UI or load testing'}
    try:
        for step, script, options in CASES:
            print('Stage-one fault regression: ' + step, flush=True)
            report_path = reports / f'step-{step}.json'
            # Each harness owns bounded waits and cleanup. Preserve its output
            # and let it finish cleanup instead of killing a live lock owner.
            process = subprocess.run([sys.executable, str(ROOT/'scripts'/script), *options,
                                      '--report', str(report_path)], cwd=ROOT)
            report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
            passed = process.returncode == 0 and report.get('passed') is True
            result['cases'].append({'step': step, 'passed': passed, 'exit_code': process.returncode,
                                    'report': report_path.name, 'checks': len(report.get('checks', []))})
            if not passed:
                return 1
        result['passed'] = True
        result['checks'] = sum(case['checks'] for case in result['cases'])
        return 0
    finally:
        result['finished_utc'] = datetime.now(timezone.utc).isoformat()
        summary_path.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'passed': result['passed'], 'summary': str(summary_path)}), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
