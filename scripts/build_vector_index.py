"""Explicit maintenance command: build a complete vector batch for the active release."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from semantic import build_index
from storage import Store


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if args.report.exists():parser.error('Use a new report path')
    report=build_index(Store(os.environ['DATABASE_URL']))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()
