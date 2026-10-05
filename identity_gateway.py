"""Personal-account entry point; explicit workspace routing, no business DSNs."""
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from identity import IdentityError, IdentityStore

ROOT = Path(__file__).parent


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Admission(Input):
    run_id: uuid.UUID


class Login(Input):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class NewAccount(Login):
    role: str
    workspace: str


class EditAccount(Input):
    role: str | None = None
    enabled: StrictBool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)


def load_workspaces(raw):
    rows = json.loads(raw)
    if not isinstance(rows, dict) or not rows or len(rows) > 5:
        raise ValueError('Configure one to five isolated workspaces')
    urls, keys = set(), set()
    for name, item in rows.items():
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', name) or set(item) != {'url', 'service_key'}:
            raise ValueError('Invalid workspace configuration')
        url, key = item['url'], item['service_key']
        parts = urlsplit(url)
        if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password
                or parts.path not in {'', '/'} or parts.query or parts.fragment or url in urls
                or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', key) or key in keys):
            raise ValueError('Distinct private origins and service keys required')
        urls.add(url); keys.add(key)
    return rows


def bearer(request):
    value = request.headers.get('Authorization', '')
    if not value.startswith('Bearer '):
        raise IdentityError(401, '请使用个人账号登录')
    return value[7:]


def business_route(method, path):
    if path == 'order-sync' and method in ('GET','POST'):
        return 'order-sync'
    if method == 'GET' and path in {'config', 'orders', 'knowledge', 'runs', 'metrics', 'alerts', 'policy-releases'}:
        return path
    if method == 'POST' and path == 'runs':
        return 'runs'
    match = re.fullmatch(r'runs/([0-9a-fA-F-]{36})(?:/(approval|review|retry))?', path)
    if match:
        try:
            uuid.UUID(match[1])
        except ValueError:
            return None
        if (method == 'GET' and not match[2]) or (method == 'POST' and match[2]):
            return 'runs/{id}' + ('/' + match[2] if match[2] else '')
    return None


def create_app(store=None, workspaces=None, transport=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.identities = store or IdentityStore(os.environ['RF_IDENTITY_DB'])
        app.state.workspaces = workspaces or load_workspaces(os.environ['RF_WORKSPACES'])
        with app.state.identities.connect() as c:
            c.execute('SELECT count(*) FROM accounts')
            from capacity import enabled, verify
            if enabled(): verify(c)
        async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False,
                                     transport=transport) as client:
            app.state.client = client
            yield

    app = FastAPI(title='ResolveFlow personal access', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def headers_and_size(request, call_next):
        # No browser cookies or credentialed CORS: all authorization is explicit
        # Bearer data kept in page memory. The proxy accepts only a bounded body.
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 32768:
                return JSONResponse({'detail': '请求过大'}, status_code=413)
            chunks.append(chunk)
        request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(IdentityError)
    async def identity_error(request, error):
        if error.status in {401, 403} and request.url.path.startswith('/api/accounts'):
            try:
                p = app.state.identities.principal(bearer(request))
            except IdentityError:
                p = {'id': None, 'workspace': None}
            try:
                app.state.identities.audit(p, 'account_access_denied', None, error.status)
            except sqlite3.Error:
                return JSONResponse({'detail': '账号服务暂不可用'}, status_code=503)
        return JSONResponse({'detail': error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        # Pydantic's default response includes rejected values, including passwords.
        return JSONResponse({'detail': '请求字段或格式不正确'}, status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def unavailable(request, error):
        return JSONResponse({'detail': '账号服务暂不可用'}, status_code=503)

    @app.exception_handler(httpx.HTTPError)
    async def upstream_unavailable(request, error):
        return JSONResponse({'detail': '工作区暂不可用；提交结果未知时请先查询记录'}, status_code=503)

    @app.get('/health')
    def health():
        with app.state.identities.connect() as c:
            c.execute('SELECT 1')
        return {'status': 'ok'}

    @app.post('/api/session')
    def login(body: Login, request: Request):
        from capacity import enabled, login_limit
        if enabled(): login_limit(app.state.identities, request.client.host if request.client else "unknown")
        token = app.state.identities.login(body.username, body.password)
        return {'token': token, 'account': app.state.identities.principal(token)}

    @app.post('/api/logout')
    def logout(request: Request):
        app.state.identities.logout(bearer(request))
        return {'logged_out': True}

    @app.get('/api/me')
    def me(request: Request):
        return app.state.identities.principal(bearer(request))

    @app.post('/internal/session')
    def introspect(request: Request):
        key = request.headers.get('X-Workspace-Service', '')
        workspace = next((name for name, item in app.state.workspaces.items()
                          if hmac.compare_digest(key.encode(), item['service_key'].encode())), None)
        if workspace is None:
            raise IdentityError(401, 'Invalid service identity')
        p = app.state.identities.principal(bearer(request))
        if p['workspace'] != workspace or p['role'] == 'manager':
            app.state.identities.audit(p, 'workspace_denied', workspace, 403)
            raise IdentityError(403, '工作区不匹配')
        return p

    @app.post('/internal/admission')
    def admission(body: Admission, request: Request):
        from capacity import enabled, admit
        if not enabled(): raise HTTPException(503, 'capacity_not_enabled')
        p = introspect(request)
        if p['role'] not in {'operator', 'admin'}: raise HTTPException(403, 'submission_denied')
        admit(app.state.identities, p, str(body.run_id))
        return {'run_id': str(body.run_id)}

    @app.get('/api/accounts')
    def accounts(request: Request):
        return {'accounts': app.state.identities.accounts(bearer(request)),
                'workspaces': list(app.state.workspaces)}

    @app.post('/api/accounts', status_code=201)
    def create(body: NewAccount, request: Request):
        aid = app.state.identities.create_account(bearer(request), body.username, body.password,
                body.role, body.workspace, app.state.workspaces)
        return {'id': aid}

    @app.post('/api/accounts/{account_id}')
    def edit(account_id: uuid.UUID, body: EditAccount, request: Request):
        if not body.model_fields_set or any(getattr(body, field) is None for field in body.model_fields_set):
            raise IdentityError(422, '请选择需要变更的账号属性')
        app.state.identities.update_account(bearer(request), str(account_id), **body.model_dump(exclude_unset=True))
        return {'updated': True, 'sessions_revoked': True}

    @app.get('/api/access-audit')
    def audit(request: Request, before: int | None = Query(None, ge=1), limit: int = Query(100, ge=1, le=100)):
        return app.state.identities.events(bearer(request), before, limit)

    @app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
    async def proxy(path: str, request: Request):
        token = bearer(request)
        p = app.state.identities.principal(token)
        if p['workspace'] not in app.state.workspaces or p['role'] == 'manager':
            app.state.identities.audit(p, 'business_denied', None, 403)
            raise IdentityError(403, '账号维护员没有业务工作区权限')
        selectors = [request.query_params.get(k) for k in ('workspace', 'workspace_id')]
        selectors.append(request.headers.get('X-Workspace-ID'))
        if any(value is not None and value != p['workspace'] for value in selectors):
            app.state.identities.audit(p, 'workspace_selector_denied', None, 403)
            raise IdentityError(403, '不能切换至其他工作区')
        route = business_route(request.method, path)
        if route is None:
            app.state.identities.audit(p, 'route_denied', None, 404)
            raise HTTPException(404, '接口不存在')
        target = path.split('/')[1] if route.startswith('runs/{id}') else None
        # A durable intent survives a gateway crash between forwarding and receipt.
        # Only identifiers and status are stored, never ticket/password/token data.
        app.state.identities.audit(p, request.method + ':' + route + ':started', target, 102)
        try:
            upstream = await app.state.client.request(request.method,
                app.state.workspaces[p['workspace']]['url'].rstrip('/') + '/api/' + path,
                params=request.query_params, content=await request.body(),
                headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        except httpx.HTTPError:
            app.state.identities.audit(p, request.method + ':' + route, target, 503)
            raise
        app.state.identities.audit(p, request.method + ':' + route, target, upstream.status_code)
        return Response(upstream.content, status_code=upstream.status_code, media_type='application/json')

    @app.get('/', response_class=HTMLResponse)
    @app.get('/index.html', response_class=HTMLResponse)
    def index():
        return (ROOT / 'frontend/dist/index.html').read_text(encoding='utf-8').replace(
            '<html lang="zh-CN">', '<html lang="zh-CN" data-auth-mode="personal">')

    app.mount('/', StaticFiles(directory=ROOT / 'frontend/dist'), name='static')
    return app


app = create_app()
