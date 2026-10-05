from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3
import uuid
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
import capacity
from identity import IdentityStore, IdentityError
from identity_gateway import create_app
from test_identity import PASSWORD, WORKSPACES, create
from scripts.local_backup_worker import identity_snapshot

@pytest.fixture
def system(tmp_path,monkeypatch):
    monkeypatch.setenv('RF_CAPACITY_ENABLED','1')
    store=IdentityStore(tmp_path/'identity.sqlite3');store.initialize('maintainer',PASSWORD)
    manager=store.login('maintainer',PASSWORD)
    _,token=create(store,manager)
    return store,token

def test_atomic_global_day_cap_under_contention(system):
    store,token=system;p=store.principal(token)
    # Different windows isolate the daily cap from the independent burst limiter.
    def attempt(i):
        try:
            capacity.admit(store,{**p,'workspace':'alpha' if i%2 else 'beta'},str(uuid.uuid4()),36000+i*11)
            return 202
        except IdentityError as e:return e.status
    with ThreadPoolExecutor(max_workers=12) as pool:result=list(pool.map(attempt,range(140)))
    assert result.count(202)==100 and result.count(429)==40
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM capacity_admissions').fetchone()[0]==100
        assert c.execute("SELECT count(*) FROM audit WHERE action='admission_reserved'").fetchone()[0]==100

def test_midnight_restart_and_replay(system):
    store,token=system;p=store.principal(token)
    before=datetime(2026,10,5,15,59,59,tzinfo=timezone.utc).timestamp()
    assert capacity.day_at(before)=='2026-10-05' and capacity.day_at(before+1)=='2026-10-06'
    rid=str(uuid.uuid4());capacity.admit(store,p,rid,before)
    reopened=IdentityStore(store.path)
    with pytest.raises(IdentityError) as err:capacity.admit(reopened,p,rid,before+1)
    assert err.value.status==409
    capacity.admit(reopened,p,str(uuid.uuid4()),before+1)
    with store.connect() as c:assert c.execute('SELECT count(DISTINCT day) FROM capacity_admissions').fetchone()[0]==2

def test_submit_rate_then_window_recovers(system):
    store,token=system;p=store.principal(token)
    for _ in range(20):capacity.admit(store,p,str(uuid.uuid4()),1000)
    with pytest.raises(IdentityError) as err:capacity.admit(store,p,str(uuid.uuid4()),1001)
    assert err.value.status==429
    capacity.admit(store,p,str(uuid.uuid4()),1010)

def test_global_rate_across_accounts(system):
    store,token=system;p=store.principal(token)
    for n in range(30):capacity.admit(store,{**p,'id':str(n%3)},str(uuid.uuid4()),1000)
    with pytest.raises(IdentityError):capacity.admit(store,p,str(uuid.uuid4()),1000)

def test_login_throttles_unknown_users_and_ignores_forwarding(system,monkeypatch):
    store,_=system
    # Fixed-window behavior must be tested inside one window, not across a real minute boundary.
    fixed=int(capacity.time.time())//60*60+1
    monkeypatch.setattr(capacity.time,'time',lambda:fixed)
    with TestClient(create_app(store,WORKSPACES)) as client:
        for n in range(10):
            assert client.post('/api/session',json={'username':'unknown'+str(n),'password':'wrong'},headers={'X-Forwarded-For':str(n)}).status_code==401
        assert client.post('/api/session',json={'username':'maintainer','password':PASSWORD},headers={'X-Forwarded-For':'new'}).status_code==429

def test_login_global_and_cleanup(system):
    store,_=system
    for n in range(30):capacity.login_limit(store,'peer'+str(n),1000)
    with pytest.raises(IdentityError):capacity.login_limit(store,'new',1000)
    capacity.login_limit(store,'new',1200)
    with store.connect() as c:assert c.execute('SELECT count(*) FROM capacity_rates').fetchone()[0]==2

def test_internal_endpoint_auth_scope_replay_and_roles(system):
    store,token=system
    with TestClient(create_app(store,WORKSPACES)) as client:
        body={'run_id':str(uuid.uuid4())};h={'Authorization':'Bearer '+token,'X-Workspace-Service':'a'*43}
        assert client.post('/internal/admission',json=body).status_code==401
        assert client.post('/internal/admission',json=body,headers={**h,'X-Workspace-Service':'b'*43}).status_code==403
        assert client.post('/internal/admission',json=body,headers=h).status_code==200
        assert client.post('/internal/admission',json=body,headers=h).status_code==409
        manager=store.login('maintainer',PASSWORD)
        _,review=create(store,manager,'reviewer','reviewer')
        assert client.post('/internal/admission',json={'run_id':str(uuid.uuid4())},headers={**h,'Authorization':'Bearer '+review}).status_code==403
    with store.connect() as c:assert c.execute('SELECT count(*) FROM capacity_admissions').fetchone()[0]==1

@pytest.mark.parametrize('response',[503,429,401,403,409,'timeout','mismatch'])
def test_backend_admission_fails_closed(system,monkeypatch,response):
    monkeypatch.setenv('RF_AUTH_MODE','personal');monkeypatch.setenv('RF_IDENTITY_URL','http://identity');monkeypatch.setenv('RF_IDENTITY_SERVICE_KEY','a'*43)
    def post(*a,**kw):
        if response=='timeout':raise httpx.ReadTimeout('synthetic')
        return httpx.Response(200 if response=='mismatch' else response,json={'detail':'denied'},request=httpx.Request('POST','http://identity'))
    monkeypatch.setattr(capacity.httpx,'post',post)
    with pytest.raises(HTTPException) as err:capacity.authorize_submission(Request({'type':'http','headers':[]}),str(uuid.uuid4()))
    assert err.value.status_code==(response if isinstance(response,int) and response!=503 else 503)

def test_backup_fingerprints_capacity_and_rejects_partial(system):
    store,token=system;capacity.admit(store,store.principal(token),str(uuid.uuid4()))
    with sqlite3.connect(store.path) as c:
        snap=identity_snapshot(c);assert snap['capacity_admissions']['count']==1
        c.execute('DROP TABLE capacity_rates')
        with pytest.raises(ValueError):identity_snapshot(c)

@pytest.mark.parametrize('workspace,slots',[('alpha','3'),('beta','2'),('unknown','1')])
def test_slot_configuration_rejects_overallocation(monkeypatch,workspace,slots):
    monkeypatch.setenv('RF_CAPACITY_ENABLED','1');monkeypatch.setenv('RF_WORKSPACE_ID',workspace);monkeypatch.setenv('RF_EXECUTION_SLOTS',slots)
    with pytest.raises(ValueError):capacity.execution_slot(None)

def test_internal_http_submission_rate_persists_and_read_is_free(system,monkeypatch):
    store,token=system
    monkeypatch.setattr(capacity.time,'time',lambda:1800000000)
    with store.connect(True) as c:c.execute('UPDATE sessions SET expires_at=2000000000')
    h={'Authorization':'Bearer '+token,'X-Workspace-Service':'a'*43}
    with TestClient(create_app(store,WORKSPACES,transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[])))) as client:
        for _ in range(20):assert client.post('/internal/admission',headers=h,json={'run_id':str(uuid.uuid4())}).status_code==200
        assert client.post('/internal/admission',headers=h,json={'run_id':str(uuid.uuid4())}).status_code==429
        for _ in range(25):assert client.get('/api/runs',headers=h).status_code==200
    with TestClient(create_app(IdentityStore(store.path),WORKSPACES)) as client:
        assert client.post('/internal/admission',headers=h,json={'run_id':str(uuid.uuid4())}).status_code==429
    with store.connect() as c:assert c.execute('SELECT count(*) FROM capacity_admissions').fetchone()[0]==20

def test_reused_transport_does_not_cache_sessions_or_cross_request_tokens(monkeypatch):
    from fastapi import FastAPI,Depends
    from auth import authenticate
    monkeypatch.setenv('RF_AUTH_MODE','personal');monkeypatch.setenv('RF_WORKSPACE_ID','alpha')
    monkeypatch.setenv('RF_IDENTITY_URL','http://identity');monkeypatch.setenv('RF_IDENTITY_SERVICE_KEY','a'*43)
    seen=[]
    def respond(request):
        seen.append(request.headers['Authorization'])
        return httpx.Response(401 if len(seen)==3 else 200,json={'id':seen[-1], 'role':'operator','workspace':'alpha'})
    app=FastAPI()
    @app.get('/probe')
    def probe(p=Depends(authenticate)):return {'id':p.id}
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        app.state.identity_client=transport
        with TestClient(app) as client:
            assert client.get('/probe',headers={'Authorization':'Bearer first'}).json()['id']=='Bearer first'
            assert client.get('/probe',headers={'Authorization':'Bearer second'}).json()['id']=='Bearer second'
            assert client.get('/probe',headers={'Authorization':'Bearer first'}).status_code==401
    assert seen==['Bearer first','Bearer second','Bearer first']
