"""Identity/session security and gateway fail-closed behavior, no network/model."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import time

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
import httpx
import pytest

from identity import IdentityStore, IdentityError, password_hash, password_matches, session_digest
from identity_gateway import create_app, load_workspaces, business_route
import auth

PASSWORD = 'synthetic-test-password-52'
WORKSPACES = {'alpha': {'url': 'http://alpha-api:8000', 'service_key': 'a' * 43},
              'beta': {'url': 'http://beta-api:8000', 'service_key': 'b' * 43}}


@pytest.fixture
def identities(tmp_path):
    store = IdentityStore(tmp_path / 'identity.sqlite3')
    store.initialize('maintainer', PASSWORD)
    manager = store.login('maintainer', PASSWORD)
    return store, manager


def create(store, manager, name='operator', role='operator', workspace='alpha'):
    aid = store.create_account(manager, name, PASSWORD, role, workspace, WORKSPACES)
    return aid, store.login(name, PASSWORD)


def test_password_salted_and_no_plaintext(identities):
    store, manager = identities
    one, two = password_hash(PASSWORD), password_hash(PASSWORD)
    assert one != two and password_matches(PASSWORD, one) and not password_matches('wrong', one)
    assert not password_matches(PASSWORD, 'unknown$xx$yy')
    assert not password_matches('a' * 129, one)
    with store.connect() as c:
        row = c.execute('SELECT * FROM accounts').fetchone()
        assert PASSWORD not in json.dumps(dict(row))
        assert c.execute('SELECT digest FROM sessions').fetchone()[0] == session_digest(manager)
        assert manager not in json.dumps([dict(r) for r in c.execute('SELECT * FROM audit')])


def test_missing_unknown_and_reinitialize_refused(tmp_path, identities):
    with pytest.raises(sqlite3.Error):
        IdentityStore(tmp_path / 'missing').principal('x' * 43)
    store, _ = identities
    with pytest.raises(FileExistsError):
        store.initialize('again', PASSWORD)
    with sqlite3.connect(store.path) as c:
        c.execute('PRAGMA user_version=999')
    with pytest.raises(RuntimeError):
        store.principal('x' * 43)


@pytest.mark.parametrize('change', [{'enabled': False}, {'role': 'reviewer'}, {'password': 'changed-password-52'}])
def test_all_prior_sessions_revoked_and_reenable_does_not_restore(identities, change):
    store, manager = identities
    aid, first = create(store, manager)
    second = store.login('operator', PASSWORD)
    store.update_account(manager, aid, **change)
    for token in [first, second]:
        with pytest.raises(IdentityError) as error:
            store.principal(token)
        assert error.value.status == 401
    store.update_account(manager, aid, enabled=True)
    with pytest.raises(IdentityError):
        store.principal(first)
    fresh = store.login('operator', change.get('password', PASSWORD))
    assert store.principal(fresh)['role'] == change.get('role', 'operator')
    events = store.events(manager)
    if 'role' in change:
        assert any('role=operator->reviewer' in r['action'] for r in events)


def test_logout_only_current_session_and_expiration(identities):
    store, manager = identities
    aid, token = create(store, manager)
    other = store.login('operator', PASSWORD)
    store.logout(token)
    with pytest.raises(IdentityError):
        store.principal(token)
    assert store.principal(other)['id'] == aid
    with store.connect(True) as c:
        c.execute('UPDATE sessions SET expires_at=? WHERE account_id=?', (int(time.time()) - 1, aid))
    with pytest.raises(IdentityError):
        store.principal(other)


def test_account_cap_concurrent_create_and_reactivation(identities):
    store, manager = identities
    def attempt(n):
        try:
            return store.create_account(manager, 'user' + str(n), PASSWORD, 'operator', 'alpha', WORKSPACES)
        except IdentityError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(isinstance(r, str) for r in results) == 4
    assert results.count(409) == 4
    aid = next(r for r in results if isinstance(r, str))
    store.update_account(manager, aid, enabled=False)
    create(store, manager, 'replacement')
    with pytest.raises(IdentityError) as error:
        store.update_account(manager, aid, enabled=True)
    assert error.value.status == 409
    assert sum(r['enabled'] for r in store.accounts(manager)) == 5


def test_manager_cannot_be_business_account_or_disabled(identities):
    store, manager = identities
    aid = store.principal(manager)['id']
    for changes in [{'role': 'admin'}, {'enabled': False}]:
        with pytest.raises(IdentityError) as error:
            store.update_account(manager, aid, **changes)
        assert error.value.status == 409


def test_account_admin_and_audit_boundaries(identities):
    store, manager = identities
    _, operator = create(store, manager)
    _, admin = create(store, manager, 'admina', 'admin')
    _, other = create(store, manager, 'adminb', 'admin', 'beta')
    with pytest.raises(IdentityError):
        store.accounts(admin)
    with pytest.raises(IdentityError):
        store.events(operator)
    with pytest.raises(IdentityError):
        store.create_account(admin, 'bypass', PASSWORD, 'admin', 'beta', WORKSPACES)
    assert all(row['workspace'] == 'alpha' for row in store.events(admin))
    assert all(row['workspace'] == 'beta' for row in store.events(other))
    assert all(row['id'] < store.events(manager)[0]['id'] for row in store.events(manager, store.events(manager)[0]['id']))


def test_login_denials_locked_account_and_bounded_sessions(identities):
    store, manager = identities
    aid, _ = create(store, manager)
    for _ in range(8):
        with pytest.raises(IdentityError):
            store.login('operator', 'wrong')
    with pytest.raises(IdentityError):
        store.login('operator', PASSWORD)
    with pytest.raises(IdentityError):
        store.login('unknown', PASSWORD)
    store.update_account(manager, aid, password=PASSWORD)
    for _ in range(10):
        store.login('operator', PASSWORD)
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM sessions WHERE account_id=?', (aid,)).fetchone()[0] == 8
    assert any(r['action'] == 'login_denied' for r in store.events(manager))


def test_gateway_identity_routes_and_no_workspace_reassignment(identities):
    store, manager = identities
    _, token = create(store, manager)
    with TestClient(create_app(store, WORKSPACES)) as client:
        headers = {'Authorization': 'Bearer ' + manager}
        assert client.get('/').status_code == 200
        assert 'data-auth-mode="personal"' in client.get('/').text
        assert client.post('/api/session', json={'username': 'maintainer', 'password': PASSWORD}).status_code == 200
        assert client.get('/api/accounts').status_code == 401
        assert any(r['action']=='account_access_denied' and r['status']==401 for r in store.events(manager))
        assert client.get('/api/accounts', headers=headers).status_code == 200
        aid = store.principal(token)['id']
        assert client.post('/api/accounts/' + aid, headers=headers, json={'workspace': 'beta'}).status_code == 422
        assert client.post('/api/accounts/' + aid, headers=headers, json={'enabled': None}).status_code == 422
        assert client.post('/api/accounts/' + aid, headers=headers, json={}).status_code == 422
        invalid = client.post('/api/accounts/' + aid, headers=headers, json={'password':'short-pass'})
        assert invalid.status_code == 422 and 'short-pass' not in invalid.text
        assert client.get('/api/orders', headers=headers).status_code == 403
        assert client.post('/internal/session', headers={'Authorization': 'Bearer ' + token}).status_code == 401
        service = {'Authorization': 'Bearer ' + token, 'X-Workspace-Service': WORKSPACES['alpha']['service_key']}
        assert client.post('/internal/session', headers=service).status_code == 200
        service['X-Workspace-Service'] = WORKSPACES['beta']['service_key']
        assert client.post('/internal/session', headers=service).status_code == 403
        assert client.post('/api/session', content=b'x' * 32769).status_code == 413
        assert client.get('/api/accounts', headers=headers).headers['cache-control'] == 'no-store'


def test_gateway_routes_only_account_workspace_and_strips_untrusted_headers(identities):
    store, manager = identities
    _, token = create(store, manager)
    observed = []
    def upstream(request):
        observed.append(request)
        return httpx.Response(200, json={'ok': True})
    with TestClient(create_app(store, WORKSPACES, httpx.MockTransport(upstream))) as client:
        headers = {'Authorization': 'Bearer ' + token, 'X-API-Key': 'admin', 'X-Actor': 'forged'}
        assert client.get('/api/orders', headers=headers).status_code == 200
        assert client.get('/api/orders?workspace=beta', headers=headers).status_code == 403
        assert observed[0].url.host == 'alpha-api'
        assert 'x-api-key' not in observed[0].headers and 'x-actor' not in observed[0].headers
        for path in ['checkpoints', 'refunds', 'workspaces/beta/orders', 'runs/not-a-uuid', 'http://beta-api/orders']:
            assert client.get('/api/' + path, headers=headers).status_code == 404
        assert len(observed) == 1
        assert client.post('/api/logout', headers=headers).status_code == 200
        assert client.get('/api/orders', headers=headers).status_code == 401
        assert len(observed) == 1


@pytest.mark.parametrize('path', ['runs/../orders', '//beta-api/orders', 'runs/' + 'x' * 36, 'policy-releases/activate'])
def test_unknown_business_routes_not_forwarded(path):
    assert business_route('POST', path) is None


def test_workspace_configuration_rejects_duplicate_origin_keys_or_credentials():
    assert load_workspaces(json.dumps(WORKSPACES)) == WORKSPACES
    for value in ['http://user:password@api', 'http://api/path', 'file:///etc/passwd', 'http://api?redirect=1']:
        with pytest.raises(ValueError):
            load_workspaces(json.dumps({'alpha': {'url': value, 'service_key': 'a' * 43}}))
    with pytest.raises(ValueError):
        load_workspaces(json.dumps({'alpha': WORKSPACES['alpha'], 'beta': WORKSPACES['alpha']}))


@pytest.mark.parametrize('status', [401, 403, 500])
def test_backend_personal_fail_closed(monkeypatch, status):
    monkeypatch.setenv('RF_AUTH_MODE', 'personal'); monkeypatch.setenv('RF_WORKSPACE_ID', 'alpha')
    monkeypatch.setenv('RF_IDENTITY_URL', 'http://identity'); monkeypatch.setenv('RF_IDENTITY_SERVICE_KEY', 'a' * 43)
    monkeypatch.setenv('ADMIN_API_KEY', 'legacy-admin')
    auth.validate_keys()
    app = FastAPI()
    @app.get('/')
    def restricted(actor=Depends(auth.allow('admin'))):
        return {'actor': str(actor)}
    monkeypatch.setattr(auth.httpx, 'post', lambda *a, **kw: httpx.Response(status, request=httpx.Request('POST', 'http://identity')))
    with TestClient(app) as client:
        assert client.get('/', headers={'X-API-Key': 'legacy-admin'}).status_code == 401
        assert client.get('/', headers={'Authorization': 'Bearer ' + 'a' * 43}).status_code == (503 if status == 500 else status)


def test_backend_principal_role_scope_and_individual_actor(monkeypatch):
    monkeypatch.setenv('RF_AUTH_MODE', 'personal'); monkeypatch.setenv('RF_WORKSPACE_ID', 'alpha')
    monkeypatch.setenv('RF_IDENTITY_URL', 'http://identity'); monkeypatch.setenv('RF_IDENTITY_SERVICE_KEY', 'a' * 43)
    p = {'id': 'test-account', 'workspace': 'alpha', 'role': 'admin'}
    monkeypatch.setattr(auth.httpx, 'post', lambda *a, **kw: httpx.Response(200, json=p, request=httpx.Request('POST', 'http://identity')))
    app = FastAPI()
    @app.get('/')
    def restricted(actor=Depends(auth.allow('admin'))):
        return {'actor': str(actor), 'role': actor.role}
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + 'a' * 43}
        assert client.get('/', headers=headers).json() == {'actor': 'account:test-account', 'role': 'admin'}
        p['workspace'] = 'beta'
        assert client.get('/', headers=headers).status_code == 403
        p['workspace'] = 'alpha'; p['role'] = 'operator'
        assert client.get('/', headers=headers).status_code == 403
