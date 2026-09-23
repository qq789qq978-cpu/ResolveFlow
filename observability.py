"""Allowlisted correlation events; never log payloads, credentials or exceptions."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import re

_context=ContextVar('resolveflow_correlation',default={})
FIELDS={'request_id','run_id','worker_id','attempt_id','attempt','kind','actor',
        'status','success','error_type','elapsed_ms','task_token','http_status',
        'method','route','alert_id','code','severity','transition','active_count'}


@contextmanager
def correlation(**fields):
    token=_context.set({**_context.get(),**fields})
    try:yield
    finally:_context.reset(token)


def identify(**fields):
    _context.set({**_context.get(),**fields})


def event(name,**fields):
    row={'timestamp':datetime.now(timezone.utc).isoformat(),'event':name}
    for key,value in {**_context.get(),**fields}.items():
        if key not in FIELDS or value is None:continue
        if isinstance(value,(bool,int)):row[key]=value
        elif isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_:/{}.\-]{1,160}',value):row[key]=value
    logging.getLogger('resolveflow').info(json.dumps(row,separators=(',',':')))


def configure():
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    logger=logging.getLogger('resolveflow')
    logger.setLevel(logging.INFO)
    # Access URLs may contain a policy query. Application events use route templates.
    logging.getLogger('uvicorn.access').disabled=True
