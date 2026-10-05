"""Offline payment-contract laboratory. NOT a payment provider or business refund path.

No network client, credential loader, production DSN, or ResolveFlow Store import.
The local_mock protocol (including HMAC and sequence) is not a vendor protocol.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid


class ContractError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def identifier(value, pattern):
    if type(value) is not str or not re.fullmatch(pattern, value):
        raise ContractError('invalid_identifier')


@dataclass(frozen=True)
class Payment:
    workspace: str
    merchant: str
    order_id: str
    payment_id: str
    amount: int
    currency: str = 'CNY'
    environment: str = 'local_mock'
    synthetic: bool = True

    def __post_init__(self):
        identifier(self.workspace, r'[a-z][a-z0-9-]{0,31}')
        identifier(self.merchant, r'mock-merchant-[a-z0-9-]{1,32}')
        identifier(self.order_id, r'RF-[0-9]{4}')
        identifier(self.payment_id, r'mock-payment-[a-z0-9-]{1,40}')
        if (self.environment != 'local_mock' or self.synthetic is not True
                or self.currency != 'CNY' or type(self.amount) is not int
                or not 1 <= self.amount <= 100000000):
            raise ContractError('local_synthetic_full_refund_only')

    @property
    def key(self):
        # One full refund per order and merchant/workspace, across process restarts.
        return 'mock-refund-' + hashlib.sha256(canonical(
            [self.environment, self.workspace, self.merchant, self.order_id])).hexdigest()


@contextmanager
def transaction(path):
    # mode=rw prevents accidental creation of unrelated database files.
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=3)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('BEGIN IMMEDIATE')
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def initialize(path, schema, kind):
    path = Path(path)
    # Refuse all pre-existing targets, including a business/identity SQLite file.
    with path.open('xb'):
        pass
    with transaction(path) as c:
        c.execute('CREATE TABLE lab_marker(kind TEXT NOT NULL)')
        c.execute('INSERT INTO lab_marker VALUES (?)', (kind,))
        for statement in schema.split(';'):
            if statement.strip():
                c.execute(statement)


def check_kind(path, kind):
    with transaction(path) as c:
        if [r[0] for r in c.execute('SELECT kind FROM lab_marker')] != [kind]:
            raise ContractError('not_a_matching_local_mock_database')


LEDGER_SCHEMA = '''
CREATE TABLE intents (
 key TEXT PRIMARY KEY, workspace TEXT NOT NULL, merchant TEXT NOT NULL,
 order_id TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','unknown','pending','succeeded','failed','review_required')),
 provider_state TEXT, version INTEGER NOT NULL DEFAULT 0, refund_id TEXT,
 UNIQUE(workspace,merchant,order_id), UNIQUE(workspace,merchant,refund_id));
CREATE TABLE events (
 workspace TEXT NOT NULL, merchant TEXT NOT NULL, event_id TEXT NOT NULL,
 key TEXT NOT NULL REFERENCES intents(key), digest TEXT NOT NULL, outcome TEXT NOT NULL,
 PRIMARY KEY(workspace,merchant,event_id));
CREATE TABLE reconciliation (
 id INTEGER PRIMARY KEY, key TEXT NOT NULL REFERENCES intents(key), outcome TEXT NOT NULL)
'''
PROVIDER_SCHEMA = '''
CREATE TABLE refunds (key TEXT PRIMARY KEY, payload TEXT NOT NULL,
 refund_id TEXT UNIQUE NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL)
'''


class Ledger:
    def __init__(self, path):
        check_kind(path, 'ledger-v1')
        self.path = Path(path)

    @classmethod
    def create(cls, path):
        initialize(path, LEDGER_SCHEMA, 'ledger-v1')
        return cls(path)

    def prepare(self, payment):
        if type(payment) is not Payment:
            raise ContractError('payment_fixture_required')
        payload = canonical(asdict(payment)).decode()
        with transaction(self.path) as c:
            row = c.execute('SELECT payload FROM intents WHERE key=?', (payment.key,)).fetchone()
            if row and row['payload'] != payload:
                raise ContractError('immutable_payment_binding_conflict')
            if not row:
                c.execute('INSERT INTO intents(key,workspace,merchant,order_id,payload,state) VALUES (?,?,?,?,?,?)',
                          (payment.key, payment.workspace, payment.merchant, payment.order_id, payload, 'prepared'))
        return payment.key

    def get(self, key, workspace, merchant):
        with transaction(self.path) as c:
            row = c.execute('SELECT * FROM intents WHERE key=? AND workspace=? AND merchant=?',
                            (key, workspace, merchant)).fetchone()
            if not row:
                raise ContractError('intent_not_found_in_scope')
            return dict(row)

    def claim(self, key, workspace, merchant):
        # Durable unknown BEFORE transmission: after crash, only query, never resend.
        self.get(key, workspace, merchant)
        with transaction(self.path) as c:
            return c.execute("UPDATE intents SET state='unknown' WHERE key=? AND state='prepared'",
                             (key,)).rowcount == 1

    def receive(self, envelope, *, workspace, merchant, secret, now=None):
        event = verify(envelope, secret, now=now)
        payment = Payment(**event['payment'])
        if (payment.workspace, payment.merchant) != (workspace, merchant):
            raise ContractError('event_scope_mismatch')
        digest = hashlib.sha256(envelope.body).hexdigest()
        key = payment.key
        with transaction(self.path) as c:
            row = c.execute('SELECT * FROM intents WHERE key=?', (key,)).fetchone()
            if not row or row['payload'] != canonical(asdict(payment)).decode():
                raise ContractError('event_payment_binding_mismatch')
            if row['state'] == 'prepared':
                raise ContractError('unsent_intent')
            old = c.execute('SELECT digest FROM events WHERE workspace=? AND merchant=? AND event_id=?',
                            (workspace, merchant, event['event_id'])).fetchone()
            if old:
                if old['digest'] != digest:
                    raise ContractError('event_identity_conflict')
                return 'duplicate'
            if row['refund_id'] and row['refund_id'] != event['refund_id']:
                raise ContractError('refund_identity_conflict')
            version, status = event['version'], event['status']
            if version < row['version']:
                outcome = 'ignored_stale'
            elif version == row['version']:
                if status != row['provider_state']:
                    raise ContractError('version_conflict')
                outcome = 'unchanged'
            else:
                # Some providers can report a late failure after apparent success.
                # Keep that discrepancy visible; never silently ignore or re-refund.
                state = status
                if (row['state'] == 'review_required' or
                    row['state'] in ('succeeded', 'failed') and status != row['state']):
                    state = 'review_required'
                c.execute('UPDATE intents SET state=?,provider_state=?,version=?,refund_id=? WHERE key=?',
                          (state, status, version, event['refund_id'], key))
                outcome = 'needs_review' if state == 'review_required' else 'applied'
            c.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                      (workspace, merchant, event['event_id'], key, digest, outcome))
            return outcome

    def note_query(self, key, outcome):
        with transaction(self.path) as c:
            c.execute('INSERT INTO reconciliation(key,outcome) VALUES (?,?)', (key, outcome))


@dataclass(frozen=True)
class Envelope:
    body: bytes
    timestamp: int
    signature: str


def sign(event, secret, now=None):
    timestamp = int(time.time()) if now is None else now
    body = canonical(event)
    return Envelope(body, timestamp, hmac.new(secret, str(timestamp).encode()+b'.'+body, hashlib.sha256).hexdigest())


def verify(envelope, secret, now=None):
    now = int(time.time()) if now is None else now
    if (type(secret) is not bytes or len(secret) < 32 or type(envelope) is not Envelope
            or type(envelope.body) is not bytes or len(envelope.body) > 16384
            or type(envelope.timestamp) is not int or abs(now-envelope.timestamp) > 300
            or type(envelope.signature) is not str
            or not re.fullmatch('[0-9a-f]{64}', envelope.signature)):
        raise ContractError('invalid_mock_envelope')
    expected = hmac.new(secret, str(envelope.timestamp).encode()+b'.'+envelope.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, envelope.signature):
        raise ContractError('invalid_mock_signature')
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ContractError('duplicate_json_field')
            result[k] = v
        return result
    try:
        event = json.loads(envelope.body, object_pairs_hook=pairs)
        if type(event) is not dict or set(event) != {'event_id','payment','refund_id','status','version'}:
            raise ContractError('invalid_mock_event')
        identifier(event['event_id'], r'mock-event-[a-z0-9-]{1,64}')
        identifier(event['refund_id'], r'mock-provider-[a-z0-9-]{1,64}')
        if (type(event['version']) is not int or not 1 <= event['version'] <= 2147483647
                or event['status'] not in ('pending','succeeded','failed')):
            raise ContractError('invalid_mock_status_or_version')
        if type(event['payment']) is not dict or set(event['payment']) != set(Payment.__dataclass_fields__):
            raise ContractError('incomplete_payment_binding')
        Payment(**event['payment'])
        return event
    except (TypeError, ValueError, UnicodeError) as error:
        raise ContractError('invalid_mock_event') from None


class MockProvider:
    """SQLite fixture provider: no URL, HTTP client, real key, or vendor API."""
    def __init__(self, path, secret):
        check_kind(path, 'provider-v1')
        if type(secret) is not bytes or len(secret) < 32:
            raise ContractError('mock_secret_too_short')
        self.path, self.secret = Path(path), secret

    @classmethod
    def create(cls, path, secret):
        initialize(path, PROVIDER_SCHEMA, 'provider-v1')
        return cls(path, secret)

    def submit(self, payment, fault=None):
        if fault not in (None, 'before_accept', 'after_accept'):
            raise ContractError('invalid_fault')
        if fault == 'before_accept':
            raise TimeoutError('mock_timeout')
        payload = canonical(asdict(payment)).decode()
        with transaction(self.path) as c:
            old = c.execute('SELECT payload FROM refunds WHERE key=?', (payment.key,)).fetchone()
            if old and old['payload'] != payload:
                raise ContractError('mock_idempotency_conflict')
            if not old:
                c.execute('INSERT INTO refunds VALUES (?,?,?,?,?)',
                          (payment.key, payload, 'mock-provider-'+uuid.uuid4().hex, 'pending', 1))
        if fault == 'after_accept':
            raise TimeoutError('mock_response_lost_after_commit')
        return self.query(payment.key)

    def query(self, key):
        with transaction(self.path) as c:
            row = c.execute('SELECT * FROM refunds WHERE key=?', (key,)).fetchone()
        if not row:
            return None
        return sign({'event_id':'mock-event-'+uuid.uuid4().hex, 'payment':json.loads(row['payload']),
                     'refund_id':row['refund_id'], 'version':row['version'], 'status':row['state']}, self.secret)

    def advance(self, key, status):
        if status not in ('pending','succeeded','failed'):
            raise ContractError('invalid_mock_status')
        with transaction(self.path) as c:
            if c.execute('UPDATE refunds SET state=?,version=version+1 WHERE key=?', (status,key)).rowcount != 1:
                raise ContractError('mock_refund_not_found')
        return self.query(key)


class Lab:
    def __init__(self, ledger, provider, workspace, merchant):
        if type(ledger) is not Ledger or type(provider) is not MockProvider:
            raise ContractError('only_local_mock_supported')
        self.ledger, self.provider = ledger, provider
        self.workspace, self.merchant = workspace, merchant

    def receive(self, envelope):
        return self.ledger.receive(envelope, workspace=self.workspace, merchant=self.merchant,
                                   secret=self.provider.secret)

    def dispatch(self, key, fault=None):
        row = self.ledger.get(key, self.workspace, self.merchant)
        if not self.ledger.claim(key, self.workspace, self.merchant):
            return 'query_required_no_resubmit'
        try:
            response = self.provider.submit(Payment(**json.loads(row['payload'])), fault=fault)
        except TimeoutError:
            return 'unknown_query_required'
        return self.receive(response)

    def reconcile(self, key):
        self.ledger.get(key, self.workspace, self.merchant)
        response = self.provider.query(key)
        # Not found may be eventual consistency; cannot prove no refund occurred.
        result = 'not_found_manual_review' if response is None else self.receive(response)
        self.ledger.note_query(key, result)
        return result
