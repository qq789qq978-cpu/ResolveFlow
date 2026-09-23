"""Operations API: validates and enqueues; execution belongs to worker.py."""
import os
import uuid
import logging
import psycopg
from psycopg_pool import PoolTimeout, TooManyRequests
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool
from storage import Store
from auth import authenticate, allow, validate_keys
import jobs

ROOT=Path(__file__).parent
class Ticket(BaseModel):
    ticket:str=Field(min_length=2,max_length=4000)
    order_id:str=Field(pattern=r'^RF-\d{4}$')
class Approval(BaseModel):
    approved:StrictBool
    reason:str=Field(min_length=2,max_length=1000)
class Review(BaseModel):
    resolution:str=Field(min_length=2,max_length=2000)

def create_app():
    @asynccontextmanager
    async def lifespan(app):
        load_dotenv(ROOT/'.env',override=False,encoding='utf-8-sig')
        validate_keys()
        store=Store(os.environ['DATABASE_URL'],pooled=True,register=True)
        try:
            store.setup()
            app.state.store=store
            yield
        finally:store.close()
    app=FastAPI(title='ResolveFlow Operations',version='3.0.0',lifespan=lifespan)
    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(psycopg.InterfaceError)
    @app.exception_handler(psycopg.errors.IdleInTransactionSessionTimeout)
    @app.exception_handler(PoolTimeout)
    @app.exception_handler(TooManyRequests)
    async def database_unavailable(request, error):
        logging.getLogger('resolveflow').warning('Database unavailable: %s',type(error).__name__)
        return JSONResponse(status_code=503,headers={'Retry-After':'2'},content={'detail':'数据库暂时不可用，请稍后查询状态再重试。'})
    def get(run_id):
        return {**app.state.store.get(run_id), **jobs.details(app.state.store,run_id)}
    @app.get('/health')
    def health():
        with app.state.store.connect() as c:c.execute('SELECT 1')
        return {'status':'ok','database':'postgresql','mode':os.getenv('MODE','live')}
    @app.get('/api/config')
    def config(actor=Depends(authenticate)):
        return {'mode':os.getenv('MODE','live'),'model':os.getenv('MODEL_NAME'),'database':'PostgreSQL','role':actor,'execution':'async'}
    @app.get('/api/orders',dependencies=[Depends(authenticate)])
    def orders():return app.state.store.orders()
    @app.get('/api/knowledge',dependencies=[Depends(authenticate)])
    def knowledge(query:str=Query(min_length=2,max_length=4000)):
        from rag import search
        return {'query':query,'retriever':'bm25',**search(query)}
    @app.get('/api/metrics',dependencies=[Depends(allow('admin'))])
    def metrics():return jobs.metrics(app.state.store)
    @app.get('/api/policy-releases',dependencies=[Depends(allow('admin'))])
    def policy_releases():
        from policy_releases import status
        with app.state.store.connect() as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return status(connection)
    @app.get('/api/runs',dependencies=[Depends(authenticate)])
    def runs(status:str|None=None,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
        return app.state.store.list(status or None,limit,offset)
    @app.post('/api/runs',status_code=202)
    def create(body:Ticket,actor=Depends(allow('operator','admin'))):
        run_id=str(uuid.uuid4())
        try:jobs.enqueue(app.state.store,run_id,body.ticket,body.order_id,os.getenv('MODE','live'),os.getenv('MODEL_NAME'),actor)
        except KeyError:raise HTTPException(404,'订单不存在') from None
        return get(run_id)
    @app.get('/api/runs/{run_id}',dependencies=[Depends(authenticate)])
    def detail(run_id:uuid.UUID):
        try:return get(str(run_id))
        except KeyError:raise HTTPException(404,'工单不存在') from None
    @app.post('/api/runs/{run_id}/approval',status_code=202)
    def approve(run_id:uuid.UUID,body:Approval,actor=Depends(allow('reviewer','admin'))):
        try:
            jobs.queue_approval(app.state.store,str(run_id),body.approved,actor,body.reason)
            return get(str(run_id))
        except KeyError:raise HTTPException(404,'工单不存在') from None
        except ValueError as error:raise HTTPException(409,str(error)) from None
    @app.post('/api/runs/{run_id}/retry',status_code=202)
    def retry(run_id:uuid.UUID,actor=Depends(allow('admin'))):
        try:
            jobs.retry(app.state.store,str(run_id),actor)
            return get(str(run_id))
        except KeyError:raise HTTPException(404,'任务不存在') from None
        except ValueError as error:raise HTTPException(409,str(error)) from None
    @app.post('/api/runs/{run_id}/review')
    def review(run_id:uuid.UUID,body:Review,actor=Depends(allow('reviewer','admin'))):
        try:
            app.state.store.review(str(run_id),actor,body.resolution)
            return get(str(run_id))
        except KeyError:raise HTTPException(404,'工单不存在') from None
        except ValueError as error:raise HTTPException(409,str(error)) from None
    app.mount('/',StaticFiles(directory=ROOT/'frontend'/'dist',html=True),name='frontend')
    return app
app=create_app()
