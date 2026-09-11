"""Role-scoped service access codes. Individual SSO accounts are out of scope."""
import hmac
import os
from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

header = APIKeyHeader(name='X-API-Key', auto_error=False)
KEYS = {'operator': 'APP_API_KEY', 'reviewer': 'REVIEWER_API_KEY', 'admin': 'ADMIN_API_KEY'}

def validate_keys():
    values = [os.getenv(name, '') for name in KEYS.values()]
    if not all(values) or len(set(values)) != len(values):
        raise RuntimeError('Configure three distinct access codes: ' + ', '.join(KEYS.values()))

def authenticate(key=Depends(header)):
    if key:
        for role, name in KEYS.items():
            expected = os.getenv(name, '')
            if expected and hmac.compare_digest(key.encode(), expected.encode()):
                return role
    raise HTTPException(401, '授权码不正确')

def allow(*roles):
    def permission(actor=Depends(authenticate)):
        if actor not in roles:
            raise HTTPException(403, '当前角色没有此操作权限')
        return actor
    return permission
