import copy
import json
import os
import uuid
import httpx
import pytest
from order_sync import OrderSnapshot,SyncError,fetch_snapshot,parse_snapshot,validate_configuration


def payload(**kw):
    return {'event_id':str(uuid.uuid4()),'workspace':'alpha','source':'synthetic-v1','synthetic':True,
            'version':1,'id':'RF-2001','owner':'demo','currency':'CNY','amount':100,'days':3,'used':False,'status':'delivered',**kw}


@pytest.mark.parametrize('key,value',[('amount',True),('amount','100'),('amount',0),('days',-1),('days',1.5),
    ('used','false'),('synthetic',False),('synthetic',1),('source','merchant'),('currency','USD'),
    ('owner','real-person'),('version',0),('version',True),('status','paid'),('event_id','not-uuid')])
def test_strict_source_contract(key,value):
    with pytest.raises(SyncError) as e:parse_snapshot(payload(**{key:value}),'alpha','RF-2001')
    assert e.value.status==502


def test_missing_extra_fields_and_scope_mismatch():
    for key in payload():
        d=payload();d.pop(key)
        with pytest.raises(SyncError):parse_snapshot(d,'alpha','RF-2001')
    with pytest.raises(SyncError):parse_snapshot(payload(token='secret'),'alpha','RF-2001')
    for data in (payload(workspace='beta'),payload(id='RF-2002')):
        with pytest.raises(SyncError) as e:parse_snapshot(data,'alpha','RF-2001')
        assert e.value.code=='source_scope_mismatch'


def test_fingerprint_excludes_event_identity_but_includes_snapshot_version():
    a=OrderSnapshot.model_validate(payload())
    b=OrderSnapshot.model_validate({**a.model_dump(mode='json'),'event_id':str(uuid.uuid4())})
    assert a.fingerprint()==b.fingerprint()
    assert a.fingerprint()!=b.model_copy(update={'version':2}).fingerprint()


@pytest.mark.parametrize('status',[301,302,401,403,500])
def test_source_redirect_and_error_never_imported(monkeypatch,status):
    monkeypatch.setenv('RF_ORDER_SOURCE_URL','http://source')
    monkeypatch.setenv('RF_ORDER_SOURCE_KEY','fixture-key')
    with pytest.raises(SyncError) as e:fetch_snapshot('RF-2001','alpha',transport=httpx.MockTransport(lambda r:httpx.Response(status,headers={'Location':'http://elsewhere'})))
    assert e.value.status==502


def test_source_timeout_and_size_and_fixed_url(monkeypatch):
    monkeypatch.setenv('RF_ORDER_SOURCE_URL','http://source')
    monkeypatch.setenv('RF_ORDER_SOURCE_KEY','fixture-key')
    def timeout(r):raise httpx.ReadTimeout('do not echo credentials')
    with pytest.raises(SyncError) as e:fetch_snapshot('RF-2001','alpha',transport=httpx.MockTransport(timeout))
    assert e.value.code=='source_timeout' and 'credentials' not in str(e.value)
    with pytest.raises(SyncError):fetch_snapshot('RF-2001','alpha',transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*65537)))
    seen=[]
    def handler(r):seen.append(str(r.url));return httpx.Response(200,json=payload())
    assert fetch_snapshot('RF-2001','alpha',transport=httpx.MockTransport(handler)).id=='RF-2001'
    assert seen==['http://source/orders/RF-2001']
    with pytest.raises(SyncError):fetch_snapshot('../internal','alpha',transport=httpx.MockTransport(handler))


def test_sync_requires_explicit_personal_demo_configuration(monkeypatch):
    monkeypatch.setenv('RF_ORDER_SYNC_ENABLED','1')
    for k,v in {'MODE':'demo','RF_AUTH_MODE':'personal','RF_WORKSPACE_ID':'alpha',
        'RF_ORDER_SOURCE_KEY':'x'*32,'RF_ORDER_SYNC_DATABASE_URL':'configured','RF_ORDER_SOURCE_URL':'http://source'}.items():monkeypatch.setenv(k,v)
    validate_configuration()
    monkeypatch.setenv('MODE','live')
    with pytest.raises(ValueError):validate_configuration()
