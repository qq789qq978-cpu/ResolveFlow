"""Run separately from the API: python worker.py."""
import logging
import os
from pathlib import Path
import signal
import socket
import threading
import uuid
from dotenv import load_dotenv
from task_runtime import TaskRunner
from storage import Store
import jobs
from observability import configure, correlation, event

def main():
    root=Path(__file__).resolve().parent
    load_dotenv(root/'.env',encoding='utf-8-sig')
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    configure()
    TaskRunner(os.getenv('DATA_DIR',str(root/'data')),os.getenv('MODE','live'))  # Validate limits before heartbeat.
    store=Store(os.environ['DATABASE_URL'],pooled=True,max_size=4,register=True)
    store.setup()  # Refuse missing/outdated schema before starting a heartbeat.
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stop.set())
    worker_id=socket.gethostname()+':'+str(uuid.uuid4())
    event('worker_started',worker_id=worker_id)
    heartbeat_store=Store(os.environ['DATABASE_URL'],pooled=True,max_size=1)
    def pulse():
        while not stop.is_set():
            try:jobs.heartbeat(heartbeat_store,worker_id)
            except Exception as error:event('worker_heartbeat_failed',worker_id=worker_id,error_type=type(error).__name__)
            stop.wait(5)
    thread=threading.Thread(target=pulse,daemon=True)
    thread.start()
    engine=None
    try:
        while not stop.is_set():
            try:
                if engine is None:
                    engine=TaskRunner(os.getenv('DATA_DIR',str(root/'data')),os.getenv('MODE','live'))
                if not jobs.process_one(store,engine,worker_id=worker_id):stop.wait(1)
            except Exception as error:
                event('worker_cycle_failed',worker_id=worker_id,error_type=type(error).__name__)
                if engine is not None:
                    try:engine.close()
                    except Exception as close_error:event('worker_close_failed',worker_id=worker_id,error_type=type(close_error).__name__)
                    engine=None
                stop.wait(3)
    finally:
        stop.set()
        thread.join(timeout=6)
        if engine is not None:engine.close()
        try:
            with store.connect() as c:c.execute('DELETE FROM rf_worker_heartbeats WHERE worker_id=%s',(worker_id,))
        except Exception as error:
            event('worker_cleanup_failed',worker_id=worker_id,error_type=type(error).__name__)
        heartbeat_store.close()
        store.close()
        event('worker_stopped',worker_id=worker_id)

if __name__=='__main__':main()
