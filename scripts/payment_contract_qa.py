"""Run an OFFLINE LOCAL MOCK exercise, never provider-sandbox acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from experiments.payment_contract import ContractError,Lab,Ledger,MockProvider,Payment,transaction


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True,help='New resolveflow-payment-* local fixture directory')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    if not re.fullmatch('resolveflow-payment-[a-z0-9-]{1,32}',args.project):p.error('Invalid local fixture name')
    if args.report.exists():p.error('Refuse existing report')
    work=ROOT/'work'/args.project
    if work.exists():p.error('Refuse existing fixture directory')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    work.mkdir(parents=True,mode=0o700)
    if os.name=='nt':
        owner=subprocess.check_output(['whoami'],text=True).strip()
        subprocess.run(['icacls',str(work),'/inheritance:r','/grant:r',owner+':(OI)(CI)F'],check=True,capture_output=True)
    report={'kind':'offline_local_mock_only','provider_sandbox_verified':False,'passed':False,
            'checks':[],'real_refunds':0,'external_payment_api_calls':0,'model_api_calls':0,
            'network_guard':'socket creation forbidden during exercise','fixtures_preserved':str(work.relative_to(ROOT))}
    def check(name,ok):
        report['checks'].append({'name':name,'passed':bool(ok)})
        if not ok:raise AssertionError(name)
    try:
        with patch('socket.socket',side_effect=AssertionError('network forbidden in local mock')):
            secret=secrets.token_bytes(32)
            ledger=Ledger.create(work/'ledger.sqlite3');provider=MockProvider.create(work/'provider.sqlite3',secret)
            lab=Lab(ledger,provider,'alpha','mock-merchant-a')
            payment=Payment('alpha','mock-merchant-a','RF-2001','mock-payment-2001',19900)
            key=ledger.prepare(payment)
            check('intent stable on repeat',key==ledger.prepare(payment))
            check('lost response explicitly unknown',lab.dispatch(key,'after_accept')=='unknown_query_required')
            check('unknown request never resubmitted',lab.dispatch(key)=='query_required_no_resubmit')
            check('query recovers accepted provider fixture',lab.reconcile(key)=='applied')
            pending=provider.query(key)
            success=provider.advance(key,'succeeded')
            check('verified callback reports success',lab.receive(success)=='applied')
            check('same callback applied once',lab.receive(success)=='duplicate')
            check('older callback cannot rewind',lab.receive(pending)=='ignored_stale')
            restarted=Lab(Ledger(ledger.path),MockProvider(provider.path,secret),'alpha','mock-merchant-a')
            check('reopen preserves idempotency',restarted.dispatch(key)=='query_required_no_resubmit')
            check('reconciliation confirms same state',restarted.reconcile(key)=='unchanged')
            try:Lab(ledger,provider,'beta','mock-merchant-a').reconcile(key)
            except ContractError:scoped=True
            else:scoped=False
            check('cross workspace reconciliation refused',scoped)
            check('late contradictory result escalates',lab.receive(provider.advance(key,'failed'))=='needs_review')
            check('discrepancy stays visible',ledger.get(key,'alpha','mock-merchant-a')['state']=='review_required')
            second=Payment('alpha','mock-merchant-a','RF-2002','mock-payment-2002',9900)
            key2=ledger.prepare(second);lab.dispatch(key2,'before_accept')
            check('not found is not proof of failure',lab.reconcile(key2)=='not_found_manual_review')
            check('not found does not permit another send',lab.dispatch(key2)=='query_required_no_resubmit')
            with transaction(provider.path) as c:
                rows=c.execute('SELECT key,state,version FROM refunds').fetchall()
            check('one provider fixture refund total',len(rows)==1 and rows[0]['key']==key)
            with transaction(ledger.path) as c:
                report['intents']=[dict(r) for r in c.execute('SELECT key,workspace,merchant,order_id,state,provider_state,version FROM intents ORDER BY key')]
                report['event_outcomes']=[dict(r) for r in c.execute('SELECT outcome,count(*) AS count FROM events GROUP BY outcome')]
                report['reconciliation']=[dict(r) for r in c.execute('SELECT key,outcome FROM reconciliation ORDER BY id')]
            report['fixture_sha256']={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in work.glob('*.sqlite3')}
            report['passed']=True
    except BaseException as error:
        report['error_type']=type(error).__name__
        raise
    finally:
        with args.report.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),
                      'kind':report['kind'],'provider_sandbox_verified':False}))


if __name__=='__main__':main()
