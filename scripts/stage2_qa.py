"""Repeat the real hybrid/fallback/persistence scenarios in a fresh QA project.

Build resolveflow:stage2-qa from the checkout first. Requires the pinned local E5
model and resolveflow:embedding-step28 image. Port 8016 must be free. No paid API.
The existing 2.8 scenario is reused, including its public synthetic credentials.
"""
import argparse
import json
from pathlib import Path
import re
import sys

import semantic_qa as scenario
from bm25_qa import docker


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='resolveflow-qa-step212')
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'resolveflow-qa-[a-z0-9-]+',args.project):parser.error('Use an isolated resolveflow-qa-* project')
    if args.report.exists():parser.error('Preserve old reports; use a new path')
    scenario.PROJECT=args.project
    scenario.API=args.project+'-resolveflow-1'
    scenario.WORKER=args.project+'-worker-1'
    scenario.ENCODER=args.project+'-embedding-1'
    scenario.COMPOSE=['compose','-p',args.project,'-f','compose.qa-semantic.yaml','-f','compose.qa-stage2.yaml']
    existing=docker('ps','-aq','--filter','label=com.docker.compose.project='+args.project,
        '--filter','label=com.docker.compose.service=resolveflow')
    old_volume=docker('volume','ls','-q','--filter','label=com.docker.compose.project='+args.project,
        '--filter','label=com.docker.compose.volume=agent-data')
    if existing or old_volume:parser.error('Use a fresh QA project; historical application containers and volumes are preserved')
    try:
        docker(*scenario.COMPOSE,'up','-d','--wait','--wait-timeout','180','db','embedding','resolveflow')
        index=json.loads(docker('exec',scenario.API,'python','scripts/build_vector_index.py',
            '--report','/tmp/stage2-vector-index.json'))
        docker(*scenario.COMPOSE,'up','-d','--wait','--wait-timeout','120','worker')
        sys.argv=[sys.argv[0],'--report',str(args.report)]
        scenario.main()
        report=json.loads(args.report.read_text(encoding='utf-8'))
        report.update(stage=2,step='2.12',scenario_source='scripts/semantic_qa.py',vector_index=index,
            scope='Real local encoder, fallback, Worker and rebuild; no new held-out quality run or paid generation')
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    finally:
        docker(*scenario.COMPOSE,'stop')


if __name__=='__main__':main()
