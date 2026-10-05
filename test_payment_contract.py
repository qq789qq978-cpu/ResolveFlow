"""Local-mock contract tests only: no provider account, network, or business DB."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from experiments.payment_contract import (ContractError, Envelope, Lab, Ledger, MockProvider,
                                         Payment, canonical, sign, transaction)


class PaymentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='resolveflow-payment-mock-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.secret = secrets.token_bytes(32)
        self.ledger = Ledger.create(self.root/'ledger.sqlite3')
        self.provider = MockProvider.create(self.root/'provider.sqlite3', self.secret)
        self.payment = Payment('alpha', 'mock-merchant-a', 'RF-2001', 'mock-payment-2001', 19900)
        self.lab = Lab(self.ledger, self.provider, 'alpha', 'mock-merchant-a')
        self.key = self.ledger.prepare(self.payment)

    def row(self):
        return self.ledger.get(self.key, 'alpha', 'mock-merchant-a')

    def count(self, path, table):
        with transaction(path) as c:
            return c.execute('SELECT count(*) FROM '+table).fetchone()[0]

    def pending(self):
        self.assertEqual(self.lab.dispatch(self.key), 'applied')
        return self.provider.query(self.key)

    def changed(self, envelope, **changes):
        event = json.loads(envelope.body);event.update(changes)
        return sign(event, self.secret)

    def test_reject_live_and_bad_payment_facts(self):
        for changes in ({'environment':'live'}, {'environment':'sandbox'}, {'synthetic':False},
                        {'synthetic':1}, {'amount':True}, {'amount':'19900'}, {'amount':0},
                        {'amount':-1}, {'amount':1.5}, {'amount':100000001}, {'currency':'USD'},
                        {'order_id':'RF-１２３４'}, {'merchant':'real-shop'}, {'payment_id':'real-id'}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                replace(self.payment, **changes)

    def test_stable_key_and_immutable_binding(self):
        for changes in ({'amount':20000}, {'payment_id':'mock-payment-other'}):
            changed = replace(self.payment, **changes)
            self.assertEqual(changed.key, self.key)
            with self.assertRaises(ContractError):self.ledger.prepare(changed)
        self.assertEqual(self.ledger.prepare(self.payment), self.key)
        self.assertEqual(self.count(self.ledger.path,'intents'),1)

    def test_scope_keys_are_distinct(self):
        for changes in ({'workspace':'beta'}, {'merchant':'mock-merchant-b'}):
            self.assertNotEqual(replace(self.payment, **changes).key, self.key)

    def test_refuse_existing_file(self):
        original = self.ledger.path.read_bytes()
        with self.assertRaises(FileExistsError):Ledger.create(self.ledger.path)
        self.assertEqual(self.ledger.path.read_bytes(), original)

    def test_refuse_wrong_database_kind(self):
        with self.assertRaises(ContractError):Ledger(self.provider.path)
        with self.assertRaises(ContractError):MockProvider(self.ledger.path,self.secret)

    def test_refuse_other_provider(self):
        with self.assertRaises(ContractError):Lab(self.ledger,object(),'alpha','mock-merchant-a')

    def test_success_and_duplicate_dispatch_do_not_create_second_refund(self):
        self.pending()
        self.assertEqual(self.row()['state'],'pending')
        self.assertEqual(self.lab.receive(self.provider.advance(self.key,'succeeded')),'applied')
        self.assertEqual(self.row()['state'],'succeeded')
        with patch.object(self.provider,'submit',side_effect=AssertionError('must not resubmit')):
            self.assertEqual(self.lab.dispatch(self.key),'query_required_no_resubmit')
        self.assertEqual(self.count(self.provider.path,'refunds'),1)

    def test_response_lost_after_provider_commit_reconciles(self):
        self.assertEqual(self.lab.dispatch(self.key,'after_accept'),'unknown_query_required')
        self.assertEqual(self.row()['state'],'unknown')
        self.assertEqual(self.count(self.provider.path,'refunds'),1)
        with patch.object(self.provider,'submit',side_effect=AssertionError('must query')):
            self.lab.dispatch(self.key)
            self.assertEqual(self.lab.reconcile(self.key),'applied')
        self.provider.advance(self.key,'succeeded')
        self.lab.reconcile(self.key)
        self.assertEqual(self.row()['state'],'succeeded')
        self.assertEqual(self.count(self.provider.path,'refunds'),1)

    def test_timeout_before_acceptance_stays_unknown_no_blind_retry(self):
        self.lab.dispatch(self.key,'before_accept')
        self.assertEqual(self.lab.reconcile(self.key),'not_found_manual_review')
        self.assertEqual(self.row()['state'],'unknown')
        self.assertEqual(self.lab.dispatch(self.key),'query_required_no_resubmit')
        self.assertEqual(self.count(self.provider.path,'refunds'),0)

    def test_crash_after_claim_remains_query_only_across_processes(self):
        code = ('import os,sys;from experiments.payment_contract import Ledger;'
                "Ledger(sys.argv[1]).claim(sys.argv[2],'alpha','mock-merchant-a');os._exit(17)")
        result = subprocess.run([sys.executable,'-c',code,str(self.ledger.path),self.key],
                                cwd=Path(__file__).resolve().parent,capture_output=True)
        self.assertEqual(result.returncode,17)
        reopened = Lab(Ledger(self.ledger.path),MockProvider(self.provider.path,self.secret),'alpha','mock-merchant-a')
        self.assertEqual(reopened.dispatch(self.key),'query_required_no_resubmit')
        self.assertEqual(reopened.reconcile(self.key),'not_found_manual_review')

    def test_concurrent_submission_is_once(self):
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _:self.lab.dispatch(self.key),range(16)))
        self.assertEqual(results.count('applied'),1)
        self.assertEqual(self.count(self.provider.path,'refunds'),1)

    def test_provider_idempotency_survives_reopen(self):
        first = self.provider.submit(self.payment)
        reopened = MockProvider(self.provider.path,self.secret)
        second = reopened.submit(self.payment)
        self.assertEqual(json.loads(first.body)['refund_id'],json.loads(second.body)['refund_id'])
        self.assertEqual(self.count(self.provider.path,'refunds'),1)
        with self.assertRaises(ContractError):reopened.submit(replace(self.payment,amount=20000))

    def test_duplicate_callback_and_new_event_same_version(self):
        event = self.pending()
        self.assertEqual(self.lab.receive(event),'unchanged')
        self.assertEqual(self.lab.receive(event),'duplicate')
        self.assertEqual(self.lab.receive(self.provider.query(self.key)),'unchanged')

    def test_concurrent_duplicate_callback_is_once(self):
        self.pending();event=self.provider.advance(self.key,'succeeded')
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _:self.lab.receive(event),range(16)))
        self.assertEqual(results.count('applied'),1)
        self.assertEqual(results.count('duplicate'),15)

    def test_same_event_changed_content_rejected(self):
        event = self.pending();self.lab.receive(event)
        with self.assertRaises(ContractError):self.lab.receive(self.changed(event,status='succeeded',version=2))
        self.assertEqual(self.row()['state'],'pending')

    def test_same_version_conflict_rejected(self):
        event = self.pending()
        with self.assertRaises(ContractError):self.lab.receive(self.changed(event,status='succeeded'))
        self.assertEqual(self.row()['state'],'pending')

    def test_out_of_order_event_cannot_rewind(self):
        old = self.pending();self.lab.receive(self.provider.advance(self.key,'succeeded'))
        self.assertEqual(self.lab.receive(old),'ignored_stale')
        self.assertEqual(self.row()['state'],'succeeded')

    def test_late_failure_is_visible_and_never_auto_refunded_again(self):
        self.pending();self.lab.receive(self.provider.advance(self.key,'succeeded'))
        self.assertEqual(self.lab.receive(self.provider.advance(self.key,'failed')),'needs_review')
        self.assertEqual(self.row()['state'],'review_required')
        self.assertEqual(self.row()['provider_state'],'failed')
        self.lab.receive(self.provider.advance(self.key,'succeeded'))
        self.assertEqual(self.row()['state'],'review_required')
        self.lab.dispatch(self.key)
        self.assertEqual(self.count(self.provider.path,'refunds'),1)

    def test_failure_before_success_is_preserved(self):
        self.pending();self.lab.receive(self.provider.advance(self.key,'failed'))
        self.assertEqual(self.row()['state'],'failed')
        self.lab.dispatch(self.key)
        self.assertEqual(self.count(self.provider.path,'refunds'),1)

    def test_tampered_body_signature_and_timestamp_refused(self):
        event = self.pending()
        for wrong in (replace(event,body=event.body+b' '), replace(event,signature='0'*64),
                      replace(event,timestamp=event.timestamp+1), replace(event,signature='x'),
                      sign(json.loads(event.body),secrets.token_bytes(32)),
                      sign(json.loads(event.body),self.secret,now=int(time.time())-301),
                      sign(json.loads(event.body),self.secret,now=int(time.time())+301)):
            with self.subTest(case=type(wrong)), self.assertRaises(ContractError):self.lab.receive(wrong)
        self.assertEqual(self.row()['state'],'pending')

    def test_strict_payload_shape_and_size(self):
        event = self.pending();original=json.loads(event.body)
        cases=[[], {'extra':True,**original}, {k:v for k,v in original.items() if k!='payment'},
               {**original,'version':True},{**original,'status':'refunded'},
               {**original,'payment':{k:v for k,v in original['payment'].items() if k!='synthetic'}},
               {**original,'payment':{**original['payment'],'amount':'19900'}}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ContractError):self.lab.receive(sign(value,self.secret))
        with self.assertRaises(ContractError):self.lab.receive(replace(event,body=b'x'*16385))

    def test_duplicate_json_fields_refused_even_with_valid_signature(self):
        event = self.pending();body=event.body[:-1]+b',"version":99}'
        signature=hmac.new(self.secret,str(event.timestamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
        with self.assertRaises(ContractError):self.lab.receive(Envelope(body,event.timestamp,signature))

    def test_payment_scope_amount_currency_and_reference_binding(self):
        event=self.pending();original=json.loads(event.body)
        for changes in ({'workspace':'beta'},{'merchant':'mock-merchant-b'},{'order_id':'RF-2002'},
                        {'amount':19800},{'currency':'USD'},{'payment_id':'mock-payment-other'},
                        {'environment':'live'}):
            value={**original,'payment':{**original['payment'],**changes}}
            with self.subTest(changes=changes), self.assertRaises(ContractError):self.lab.receive(sign(value,self.secret))
        self.assertEqual(self.row()['state'],'pending')

    def test_refund_id_cannot_change(self):
        event=self.pending()
        with self.assertRaises(ContractError):
            self.lab.receive(self.changed(event,refund_id='mock-provider-other',version=2))

    def test_foreign_workspace_cannot_read_send_query_or_receive(self):
        other=Lab(self.ledger,self.provider,'beta','mock-merchant-a')
        for action in (other.dispatch,other.reconcile):
            with self.assertRaises(ContractError):action(self.key)
        event=self.pending()
        with self.assertRaises(ContractError):other.receive(event)

    def test_callback_before_submission_rejected(self):
        event=self.provider.submit(self.payment)
        with self.assertRaises(ContractError):self.lab.receive(event)
        self.assertEqual(self.row()['state'],'prepared')

    def test_journal_failure_rolls_back_state(self):
        self.pending();event=self.provider.advance(self.key,'succeeded')
        before=self.count(self.ledger.path,'events')
        with transaction(self.ledger.path) as c:
            c.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.lab.receive(event)
        self.assertEqual(self.row()['state'],'pending')
        self.assertEqual(self.count(self.ledger.path,'events'),before)

    def test_reconciliation_handles_missing_callback_after_restart(self):
        self.pending();self.provider.advance(self.key,'succeeded')
        restarted=Lab(Ledger(self.ledger.path),MockProvider(self.provider.path,self.secret),'alpha','mock-merchant-a')
        restarted.reconcile(self.key)
        self.assertEqual(self.row()['state'],'succeeded')
        self.assertEqual(self.count(self.ledger.path,'reconciliation'),1)

    def test_no_network_or_business_dependency(self):
        # This guard proves the local exercise can run with network creation denied.
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):
            self.pending();self.provider.advance(self.key,'succeeded');self.lab.reconcile(self.key)
        self.assertEqual(self.row()['state'],'succeeded')

    def test_one_provider_refund_cannot_bind_two_orders(self):
        event=self.pending();refund_id=json.loads(event.body)['refund_id']
        second=replace(self.payment,order_id='RF-2002',payment_id='mock-payment-2002')
        key=self.ledger.prepare(second);self.ledger.claim(key,'alpha','mock-merchant-a')
        second_event=self.provider.submit(second)
        with self.assertRaises(sqlite3.IntegrityError):
            self.lab.receive(self.changed(second_event,refund_id=refund_id))
        self.assertEqual(self.ledger.get(key,'alpha','mock-merchant-a')['state'],'unknown')

    def test_query_timeout_preserves_unknown_and_never_sends(self):
        self.lab.dispatch(self.key,'after_accept')
        with patch.object(self.provider,'query',side_effect=TimeoutError('mock query failure')):
            with self.assertRaises(TimeoutError):self.lab.reconcile(self.key)
        self.assertEqual(self.row()['state'],'unknown')
        self.assertEqual(self.lab.dispatch(self.key),'query_required_no_resubmit')
        self.assertEqual(self.count(self.provider.path,'refunds'),1)

    def test_bad_signature_creates_no_event_then_valid_retry_works(self):
        self.pending();event=self.provider.advance(self.key,'succeeded')
        before=self.count(self.ledger.path,'events')
        with self.assertRaises(ContractError):self.lab.receive(replace(event,signature='0'*64))
        self.assertEqual(self.count(self.ledger.path,'events'),before)
        self.assertEqual(self.lab.receive(event),'applied')


if __name__ == '__main__':unittest.main()
