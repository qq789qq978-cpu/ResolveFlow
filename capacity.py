"""Opt-in single-host capacity: durable shared admission and PG execution locks.

Admission is fail-closed: an uncertain downstream result retains its reservation.
No distributed transaction or expiry can grant a second ticket for the same slot.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import os
import time
import httpx
from fastapi import HTTPException
from identity import IdentityError

SCHEMA = '''
CREATE TABLE capacity_admissions (
 run_id TEXT PRIMARY KEY, day TEXT NOT NULL, actor TEXT NOT NULL,
 workspace TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX capacity_day ON capacity_admissions(day);
CREATE TABLE capacity_rates (
 key TEXT PRIMARY KEY, window INTEGER NOT NULL, count INTEGER NOT NULL
);
'''

def enabled():
    value=os.getenv('RF_CAPACITY_ENABLED','0')
    if value not in ('0','1'): raise ValueError('Invalid capacity flag')
    return value=='1'

def verify(c):
    c.execute('SELECT run_id,day,actor,workspace,created_at FROM capacity_admissions LIMIT 0')
    c.execute('SELECT key,window,count FROM capacity_rates LIMIT 0')

def day_at(now):
    # Hong Kong has no daylight-saving transition in the supported period.
    return datetime.fromtimestamp(now,timezone(timedelta(hours=8))).date().isoformat()

def rate(c,key,limit,seconds,now):
    window=int(now)//seconds*seconds
    row=c.execute('SELECT window,count FROM capacity_rates WHERE key=?',(key,)).fetchone()
    count=row['count'] if row and row['window']==window else 0
    if count>=limit: raise IdentityError(429,'请求过于频繁，请稍后重试')
    c.execute('INSERT INTO capacity_rates VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET window=excluded.window,count=excluded.count',
              (key,window,count+1))

def login_limit(store,peer,now=None):
    now=time.time() if now is None else now
    with store.connect(True) as c:
        c.execute('DELETE FROM capacity_rates WHERE window<?',(int(now)-120,))
        rate(c,'login:global',30,60,now)
        rate(c,'login:'+hashlib.sha256(peer.encode()).hexdigest(),10,60,now)

def admit(store,p,run_id,now=None):
    now=time.time() if now is None else now
    with store.connect(True) as c:
        if c.execute('SELECT 1 FROM capacity_admissions WHERE run_id=?',(run_id,)).fetchone():
            raise IdentityError(409,'工单额度已登记，请查询原工单')
        day=day_at(now)
        if c.execute('SELECT count(*) FROM capacity_admissions WHERE day=?',(day,)).fetchone()[0]>=100:
            raise IdentityError(429,'本部署今日100条新工单额度已用完')
        c.execute('DELETE FROM capacity_rates WHERE window<?',(int(now)-120,))
        rate(c,'submit:global',30,10,now)
        rate(c,'submit:'+p['id'],20,10,now)
        c.execute('INSERT INTO capacity_admissions VALUES (?,?,?,?,?)',(run_id,day,p['id'],p['workspace'],int(now)))
        store._audit(c,p['id'],p['workspace'],'admission_reserved',run_id,102)

def authorize_submission(request,run_id):
    if os.getenv('RF_AUTH_MODE')!='personal': raise HTTPException(503,'capacity_requires_personal_accounts')
    try:
        from auth import identity_post
        result=identity_post(request,os.environ['RF_IDENTITY_URL'].rstrip('/')+'/internal/admission',
            headers={'Authorization':request.headers.get('Authorization',''),
                     'X-Workspace-Service':os.environ['RF_IDENTITY_SERVICE_KEY']},
            json={'run_id':run_id})
        if result.status_code in (401,403,409,429):
            raise HTTPException(result.status_code,result.json()['detail'])
        result.raise_for_status()
        if result.json()!= {'run_id':run_id}: raise ValueError('Admission mismatch')
    except (httpx.HTTPError,KeyError,ValueError):
        raise HTTPException(503,{'message':'额度服务暂不可用；结果未知请先查询','run_id':run_id}) from None

def execution_slot(c):
    if not enabled(): return True
    workspace=os.environ.get('RF_WORKSPACE_ID')
    required={'alpha':2,'beta':1}.get(workspace)
    if required is None or os.getenv('RF_EXECUTION_SLOTS')!=str(required):
        raise ValueError('Capacity workers require fixed alpha=2, beta=1 slot allocation')
    for slot in range(required):
        if c.execute('SELECT pg_try_advisory_xact_lock(56300,%s) AS acquired',(slot,)).fetchone()['acquired']:
            return True
    return False
