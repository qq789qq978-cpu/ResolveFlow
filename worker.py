"""Run separately from the API: python worker.py."""
import logging
import os
from pathlib import Path
import signal
import socket
import threading
import uuid
from dotenv import load_dotenv
from engine import Engine
from storage import Store
import jobs

def main():
    root=Path(__file__).resolve().parent
    load_dotenv(root/'.env',encoding='utf-8-sig')
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    store=Store(os.environ['DATABASE_URL'])
    store.setup()
    jobs.setup(store)
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stop.set())
    worker_id=socket.gethostname()+':'+str(uuid.uuid4())
    def pulse():
        while not stop.is_set():
            try:jobs.heartbeat(store,worker_id)
            except Exception as error:logging.error('Worker heartbeat failed: %s',type(error).__name__)
            stop.wait(5)
    thread=threading.Thread(target=pulse,daemon=True)
    thread.start()
    engine=Engine(os.getenv('DATA_DIR',str(root/'data')),os.getenv('MODE','live'),repository=store)
    try:
        while not stop.is_set():
            try:
                if not jobs.process_one(store,engine):stop.wait(1)
            except Exception as error:
                logging.error('Worker connection failed: %s',type(error).__name__)
                stop.wait(3)
    finally:
        stop.set()
        thread.join(timeout=6)
        engine.close()
        with store.connect() as c:c.execute('DELETE FROM rf_worker_heartbeats WHERE worker_id=%s',(worker_id,))

if __name__=='__main__':main()
