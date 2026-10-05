"""Read the completed single-day checkpoint; never rewrite it as a seven-day run."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.local_trial import load,status

def checkpoint(work):
    work,state,_=load(work)
    if state.get('scope_revision')!='5.7-single-day-user-authorized' or not state.get('completed_at'):
        raise ValueError('Completed user-authorized single-day checkpoint required')
    result=status(work,state)
    print(json.dumps({'passed':result['passed'],'scope':result['scope_revision'],
                     'days':result['successful_days'],'report':str(Path(state['evidence'])/'REPORT.md')}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True)
    checkpoint(p.parse_args().work)
