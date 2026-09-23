"""One supervised graph attempt; stdin/stdout are private parent IPC."""
from task_runtime import parent_guard
parent_guard()  # Install before importing graph/model libraries.
import json
import os
import sys
import psycopg
from engine import Engine
from storage import Store


def main():
    payload=json.load(sys.stdin)
    store=Store(os.environ['DATABASE_URL'],pooled=True,max_size=4,register=True)
    engine=None
    try:
        engine=Engine(payload['directory'],payload['mode'],repository=store)
        result=engine.recover(payload['run_id'],payload['ticket'],payload['order_id'],payload['approval'])
        message={'result':result}
    except Exception as error:
        timed=isinstance(error,(psycopg.errors.QueryCanceled,psycopg.errors.LockNotAvailable))
        message={'error':type(error).__name__,'infrastructure':
            isinstance(error,(psycopg.OperationalError,psycopg.InterfaceError,psycopg.errors.IdleInTransactionSessionTimeout)) and not timed}
    finally:
        try:
            if engine:engine.close()
        finally:store.close()
    print(json.dumps(message,default=str))


if __name__=='__main__':main()
