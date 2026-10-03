"""Private local synthetic source, not a merchant or payment-provider integration."""
import hmac
import json
import os
from pathlib import Path
import time
from fastapi import FastAPI, Header, HTTPException

app=FastAPI(title='Synthetic order source',docs_url=None,redoc_url=None,openapi_url=None)


@app.get('/health')
def health():
    if len(os.environ.get('RF_ORDER_SOURCE_KEY',''))<32:raise HTTPException(503,'not configured')
    return {'status':'ok','synthetic':True}


@app.get('/orders/{order_id}')
def order(order_id:str,x_order_source_key:str=Header(default='')):
    expected=os.environ.get('RF_ORDER_SOURCE_KEY','')
    if not expected or not hmac.compare_digest(expected.encode(),x_order_source_key.encode()):
        raise HTTPException(401,'source authentication required')
    try:data=json.loads(Path(os.environ['RF_SYNTHETIC_ORDERS_FILE']).read_text(encoding='utf-8'))
    except (ValueError,OSError):raise HTTPException(503,'source fixture unavailable') from None
    item=data.get('orders',{}).get(order_id)
    if item is None:raise HTTPException(404,'synthetic order not found')
    if os.getenv('RF_SYNTHETIC_QA')=='1':
        # Fault injection is disabled in normal deployments, fixture not a user API.
        time.sleep(min(4,max(0,float(data.get('qa_delay_seconds',0)))))
    return item
