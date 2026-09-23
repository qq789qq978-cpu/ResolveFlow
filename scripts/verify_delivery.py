"""Verify matching local/remote commit, successful Actions run and its evidence.

Read-only GitHub API calls. Credentials stay in memory (GH_TOKEN/GITHUB_TOKEN or
Git credential manager); never print headers, responses containing credentials,
or raw CI logs. Writes a receipt outside the committed release evidence.
"""
import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPO = 'qq789qq978-cpu/ResolveFlow'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true', help='Show current HEAD run without downloading')
    parser.add_argument('--output', type=Path, default=ROOT / 'validation/github-actions-local.json')
    parser.add_argument('--artifacts', type=Path, default=ROOT / 'work/delivery')
    args = parser.parse_args()
    sha = git('rev-parse', 'HEAD')
    remote = git('ls-remote', 'origin', 'refs/heads/main').split()[0]
    if sha != remote:
        raise RuntimeError('Local HEAD differs from remote main')
    token = os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
    if not token:
        result = subprocess.run(['git', 'credential', 'fill'], cwd=ROOT,
            input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, check=True)
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        token = fields['password']

    def request(path, binary=False):
        url = 'https://api.github.com/repos/' + REPO + path
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        # Artifact redirects are signed; do not forward the GitHub bearer token.
        class NoAuthRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
                if redirected is not None:
                    redirected.remove_header('Authorization')
                return redirected
        with urllib.request.build_opener(NoAuthRedirect()).open(req, timeout=60) as response:
            raw = response.read()
        return raw if binary else json.loads(raw)

    runs = request('/actions/workflows/ci.yml/runs?head_sha=' + sha + '&per_page=30')['workflow_runs']
    runs = [r for r in runs if r['head_sha'] == sha and r['head_branch'] == 'main' and r['event'] == 'push']
    if not runs:
        print(json.dumps({'sha': sha, 'status': 'not_started'}))
        return 2
    run = max(runs, key=lambda row: row['id'])
    summary = {'sha': sha, 'run_id': run['id'], 'url': run['html_url'],
               'status': run['status'], 'conclusion': run['conclusion'], 'attempt': run['run_attempt']}
    if args.status:
        status_jobs = request('/actions/runs/' + str(run['id']) + '/jobs?per_page=100')['jobs']
        summary['active_steps'] = [s['name'] for j in status_jobs for s in j.get('steps', []) if s['status'] == 'in_progress']
        summary['failed_steps'] = [s['name'] for j in status_jobs for s in j.get('steps', []) if s['conclusion'] == 'failure']
        print(json.dumps(summary))
        return 0
    if run['status'] != 'completed' or run['conclusion'] != 'success':
        print(json.dumps(summary))
        return 2
    jobs = request('/actions/runs/' + str(run['id']) + '/jobs?per_page=100')['jobs']
    if not jobs or any(job['conclusion'] != 'success' for job in jobs):
        raise RuntimeError('Not all CI jobs succeeded')
    items = request('/actions/runs/' + str(run['id']) + '/artifacts')['artifacts']
    artifacts = [a for a in items if a['name'] == 'evaluation' and not a['expired']]
    if len(artifacts) != 1:
        raise RuntimeError('Expected exactly one live evaluation artifact')
    raw = request('/actions/artifacts/' + str(artifacts[0]['id']) + '/zip', binary=True)
    archive = zipfile.ZipFile(io.BytesIO(raw))

    def read(suffix):
        names = [n for n in archive.namelist() if n == suffix or n.endswith('/' + suffix)]
        if len(names) != 1:
            raise RuntimeError('Missing or ambiguous artifact: ' + suffix)
        return archive.read(names[0])

    counts = {}
    for kind in ('unit', 'postgres'):
        root = ET.fromstring(read(kind + '-ci.xml'))
        cases = root.findall('.//testcase')
        if not cases or any(case.find(tag) is not None for case in cases for tag in ('failure', 'error', 'skipped')):
            raise RuntimeError('JUnit missing tests or contains unsuccessful cases: ' + kind)
        counts[kind] = len(cases)
    checked = {}
    for name in ('persistence-ci.json', 'roles-ci.json', 'runtime-ci.json',
                 'observability-ci.json', 'stage3-ci.json', 'stage1-ci/summary.json'):
        data = json.loads(read(name))
        if data.get('passed') is not True:
            raise RuntimeError('Artifact did not pass: ' + name)
        checked[name] = {'passed': True, 'checks': len(data['checks']) if isinstance(data.get('checks'), list) else data.get('checks')}
    stage = json.loads(read('stage3-ci.json'))
    if not stage['restore'].get('resume_verified') or not stage['restore'].get('source_stopped_during_resume'):
        raise RuntimeError('Independent original approval recovery was not verified')
    faults = json.loads(read('stage1-ci/summary.json'))
    if len(faults['cases']) != 5 or not all(c['passed'] for c in faults['cases']):
        raise RuntimeError('Five fault scenarios are incomplete')
    for step in ('1.5', '1.6', '1.7', '1.8', '1.9'):
        if json.loads(read('stage1-ci/step-' + step + '.json')).get('passed') is not True:
            raise RuntimeError('Individual fault evidence failed: ' + step)
    # Read the descriptive evaluations too; these are not semantic quality gates.
    for name in ('evaluation_v3.json', 'evaluation_rag.json', 'rag-candidates-ci.json', 'rag-baseline-ci.json'):
        json.loads(read(name))
    args.artifacts.mkdir(parents=True, exist_ok=True)
    artifact_path = args.artifacts / (str(run['id']) + '-attempt-' + str(run['run_attempt']) + '.zip')
    artifact_path.write_bytes(raw)
    receipt = {**summary, 'verified_utc': datetime.now(timezone.utc).isoformat(),
               'local_equals_remote': True, 'passed': True, 'tests': counts,
               'reports': checked, 'artifact_id': artifacts[0]['id'], 'artifact_path': str(artifact_path),
               'jobs': [{'name': j['name'], 'conclusion': j['conclusion'],
                         'steps': [{'name': s['name'], 'conclusion': s['conclusion']} for s in j['steps']]} for j in jobs]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({**summary, 'verified': True, 'tests': counts, 'receipt': str(args.output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
