"""Strict synthetic-only order snapshots and transactional monotonic synchronization."""
import hashlib
import json
import os
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, model_validator
from storage import Store


class SyncError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class OrderChanged(Exception):
    pass


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: UUID
    workspace: str = Field(pattern=r'^[a-z][a-z0-9-]{0,31}$')
    source: Literal['synthetic-v1']
    synthetic: StrictBool
    version: StrictInt = Field(ge=1, le=9223372036854775807)
    id: str = Field(pattern=r'^RF-\d{4}$')
    owner: Literal['demo']
    currency: Literal['CNY']
    amount: StrictInt = Field(ge=1, le=100000000)
    days: StrictInt = Field(ge=0, le=36500)
    used: StrictBool
    status: Literal['shipping','delivered','cancelled']

    @model_validator(mode='after')
    def synthetic_only(self):
        if not self.synthetic: raise ValueError('Synthetic orders only')
        return self

    def fingerprint(self):
        # Event identity is separate from snapshot content/version identity.
        return hashlib.sha256(json.dumps(self.model_dump(mode='json',exclude={'event_id'}),
            sort_keys=True,separators=(',',':')).encode()).hexdigest()


def enabled():
    return os.getenv('RF_ORDER_SYNC_ENABLED') == '1'


def validate_configuration():
    if not enabled(): return
    p=urlsplit(os.environ.get('RF_ORDER_SOURCE_URL',''))
    if (os.getenv('MODE')!='demo' or os.getenv('RF_AUTH_MODE')!='personal'
        or not os.getenv('RF_WORKSPACE_ID') or not os.getenv('RF_ORDER_SYNC_DATABASE_URL')
        or len(os.getenv('RF_ORDER_SOURCE_KEY',''))<32
        or p.scheme not in ('http','https') or not p.hostname or p.username or p.password
        or p.path not in ('','/') or p.query or p.fragment):
        raise ValueError('Synthetic sync requires personal demo mode and a fixed private source')


def parse_snapshot(data, workspace, order_id):
    try: item=OrderSnapshot.model_validate(data)
    except (ValidationError,ValueError,TypeError):raise SyncError('invalid_source_payload',502) from None
    if item.workspace!=workspace or item.id!=order_id:
        raise SyncError('source_scope_mismatch',403)
    return item


def fetch_snapshot(order_id, workspace, *, transport=None):
    if not __import__('re').fullmatch(r'RF-\d{4}',order_id):raise SyncError('invalid_order_id',422)
    started=time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(2,connect=1,pool=1),trust_env=False,
                          follow_redirects=False,transport=transport) as client:
            with client.stream('GET',os.environ['RF_ORDER_SOURCE_URL'].rstrip('/')+'/orders/'+order_id,
                               headers={'X-Order-Source-Key':os.environ['RF_ORDER_SOURCE_KEY']}) as response:
                if response.status_code==404:raise SyncError('source_order_not_found',404)
                if response.status_code!=200:raise SyncError('source_unavailable',502)
                chunks=[];length=0
                for chunk in response.iter_bytes():
                    length+=len(chunk)
                    if length>65536:raise SyncError('source_payload_too_large',502)
                    if time.monotonic()-started>5:raise SyncError('source_timeout',504)
                    chunks.append(chunk)
                data=json.loads(b''.join(chunks))
    except httpx.TimeoutException:raise SyncError('source_timeout',504) from None
    except (httpx.HTTPError,ValueError):raise SyncError('source_unavailable',502) from None
    return parse_snapshot(data,workspace,order_id)


def apply_snapshot(store, item, workspace, actor):
    if item.workspace!=workspace:raise SyncError('source_scope_mismatch',403)
    digest=item.fingerprint()
    with store.connect() as c:
        c.execute('SET LOCAL statement_timeout=3000');c.execute('SET LOCAL lock_timeout=1000')
        # Event lock first, then order lock: duplicate event IDs across orders
        # cannot race past the identity check. Hash collisions only serialize.
        for key in ('order-event:'+str(item.event_id),'order:'+item.id):
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
        previous=c.execute('SELECT * FROM rf_order_events WHERE event_id=%s',(item.event_id,)).fetchone()
        if previous:
            if previous['payload_sha']!=digest or previous['workspace']!=workspace:
                raise SyncError('event_id_conflict')
            return {'outcome':'duplicate','event_id':str(item.event_id),'order_id':item.id,
                    'version':item.version,'original_outcome':previous['outcome']}
        version=c.execute('SELECT * FROM rf_order_versions WHERE order_id=%s FOR UPDATE',(item.id,)).fetchone()
        current=c.execute('SELECT * FROM rf_orders WHERE id=%s FOR UPDATE',(item.id,)).fetchone()
        if current and not version:raise SyncError('unmanaged_order_conflict')
        if version and (version['workspace']!=workspace or version['source']!=item.source):
            raise SyncError('source_scope_mismatch',403)
        if version and item.version<version['version']:outcome='ignored_stale'
        elif version and item.version==version['version']:
            if digest!=version['payload_sha']:raise SyncError('version_conflict')
            outcome='unchanged'
        else:
            if current:
                if item.amount!=current['amount'] or item.owner!=current['owner']:
                    raise SyncError('immutable_order_field_changed')
                if (current['status']=='cancelled' and item.status!='cancelled'
                    or current['status']=='delivered' and item.status=='shipping'
                    or current['used'] and not item.used or item.days<current['days']):
                    raise SyncError('order_state_regression')
            values=(item.id,item.owner,item.amount,item.days,item.used,item.status)
            c.execute('INSERT INTO rf_orders VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE '
                      'SET days=EXCLUDED.days,used=EXCLUDED.used,status=EXCLUDED.status',values)
            c.execute('INSERT INTO rf_order_versions(order_id,workspace,source,version,payload_sha) VALUES (%s,%s,%s,%s,%s) '
                      'ON CONFLICT(order_id) DO UPDATE SET version=EXCLUDED.version,payload_sha=EXCLUDED.payload_sha,updated_at=now()',
                      (item.id,workspace,item.source,item.version,digest))
            outcome='applied'
        c.execute('INSERT INTO rf_order_events(event_id,order_id,workspace,version,payload_sha,outcome,actor) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                  (item.event_id,item.id,workspace,item.version,digest,outcome,actor))
        return {'outcome':outcome,'event_id':str(item.event_id),'order_id':item.id,'version':item.version}


def sync(order_id,actor):
    if not enabled():raise SyncError('order_sync_not_enabled',404)
    workspace=os.environ['RF_WORKSPACE_ID']
    item=fetch_snapshot(order_id,workspace)
    # Only this explicit admin operation has the narrow importer credential.
    with_store=Store(os.environ['RF_ORDER_SYNC_DATABASE_URL'])
    return apply_snapshot(with_store,item,workspace,str(actor))
