"""Legacy local role codes, or revocable personal sessions on isolated backends."""
from dataclasses import dataclass
import hmac
import os
from urllib.parse import urlsplit
import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

header = APIKeyHeader(name='X-API-Key', auto_error=False)
KEYS = {'operator': 'APP_API_KEY', 'reviewer': 'REVIEWER_API_KEY', 'admin': 'ADMIN_API_KEY'}


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    workspace: str

    def __str__(self):
        return 'account:' + self.id


def validate_keys():
    mode = os.getenv('RF_AUTH_MODE', 'legacy')
    if mode == 'personal':
        parts = urlsplit(os.environ.get('RF_IDENTITY_URL', ''))
        if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password
                or parts.path not in {'', '/'} or parts.query or parts.fragment
                or len(os.getenv('RF_IDENTITY_SERVICE_KEY', '')) < 32 or not os.getenv('RF_WORKSPACE_ID')):
            raise RuntimeError('Personal mode requires a workspace and private identity service')
        return
    if mode != 'legacy':
        raise RuntimeError('Unknown authentication mode')
    values = [os.getenv(name, '') for name in KEYS.values()]
    if not all(values) or len(set(values)) != len(values):
        raise RuntimeError('Configure three distinct access codes: ' + ', '.join(KEYS.values()))


def identity_post(request, url, **kwargs):
    # Reuse transport connections, never cache a principal or a Bearer header.
    state = getattr(request.scope.get('app'), 'state', None)
    client = getattr(state, 'identity_client', None)
    if client is not None:
        return client.post(url, timeout=5, follow_redirects=False, **kwargs)
    return httpx.post(url, timeout=5, trust_env=False, follow_redirects=False, **kwargs)


def authenticate(request: Request, key=Depends(header)):
    if os.getenv('RF_AUTH_MODE', 'legacy') == 'personal':
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('Bearer ') or len(authorization) > 200:
            raise HTTPException(401, '请使用个人账号登录')
        try:
            result = identity_post(request, os.environ['RF_IDENTITY_URL'].rstrip('/') + '/internal/session',
                headers={'Authorization': authorization, 'X-Workspace-Service': os.environ['RF_IDENTITY_SERVICE_KEY']})
            if result.status_code in {401, 403}:
                raise HTTPException(result.status_code, '会话无效或不属于当前工作区')
            result.raise_for_status()
            p = result.json()
            if p['workspace'] != os.environ['RF_WORKSPACE_ID'] or p['role'] not in KEYS:
                raise HTTPException(403, '工作区不匹配')
            return Principal(p['id'], p['role'], p['workspace'])
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(503, '账号服务暂不可用') from None
    if key:
        for role, name in KEYS.items():
            expected = os.getenv(name, '')
            if expected and hmac.compare_digest(key.encode(), expected.encode()):
                return role
    raise HTTPException(401, '授权码不正确')


def allow(*roles):
    def permission(actor=Depends(authenticate)):
        if getattr(actor, 'role', actor) not in roles:
            raise HTTPException(403, '当前角色没有此操作权限')
        return actor
    return permission
