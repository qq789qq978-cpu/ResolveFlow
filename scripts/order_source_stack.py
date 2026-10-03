"""Optional private synthetic sources for the local account stack."""
import json
import os
from pathlib import Path
import secrets
import uuid
from urllib.parse import urlsplit, urlunsplit


def fixture(workspace):
    rows={}
    for number,days,used,status in [(2001,3,False,'delivered'),(2002,12,True,'delivered'),
                                    (2003,0,False,'shipping'),(2004,3,True,'delivered')]:
        oid='RF-'+str(number)
        rows[oid]={'event_id':str(uuid.uuid4()),'workspace':workspace,'source':'synthetic-v1','synthetic':True,
                   'version':1,'id':oid,'owner':'demo','currency':'CNY','amount':19900,'days':days,'used':used,'status':status}
    return {'orders':rows}


def configure(config,work,image):
    config['x-resolveflow-local']['orders']=True
    for workspace in ('alpha','beta'):
        password=secrets.token_urlsafe(32);key=secrets.token_urlsafe(32)
        api=config['services'][workspace+'-api'];parts=urlsplit(api['environment']['DATABASE_URL'])
        dsn=urlunsplit((parts.scheme,'rf_sync:'+password+'@'+parts.hostname,parts.path,'',''))
        api['environment'].update(RF_ORDER_SYNC_ENABLED='1',RF_ORDER_SYNC_DATABASE_URL=dsn,
            RF_ORDER_SOURCE_URL='http://'+workspace+'-source:8000',RF_ORDER_SOURCE_KEY=key)
        config['services'][workspace+'-roles']['environment']['RF_ORDER_SYNC_PASSWORD']=password
        config['services'][workspace+'-migrate']['command']+=['--orders']
        path=work/(workspace+'-orders.json')
        with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o644),'w') as f:json.dump(fixture(workspace),f)
        config['services'][workspace+'-source']={
            'image':image,'command':['uvicorn','synthetic_orders:app','--host','0.0.0.0','--port','8000','--no-access-log'],
            'environment':{'RF_ORDER_SOURCE_KEY':key,'RF_SYNTHETIC_ORDERS_FILE':'/fixtures/orders.json'},
            'volumes':[{'type':'bind','source':str(path),'target':'/fixtures/orders.json','read_only':True}],
            'networks':[workspace+'-front'],'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
            'healthcheck':config['services']['gateway']['healthcheck'],
            'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}
        api['depends_on'][workspace+'-source']={'condition':'service_healthy'}
