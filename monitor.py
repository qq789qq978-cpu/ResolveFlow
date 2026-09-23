"""Independent read-only alert observer; JSON transitions go to local container logs."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading
import time
from alerts import Limits, Transitions, snapshot
from observability import configure, event
from runtime_db import integer
from storage import Store

HEALTH=Path('/tmp/resolveflow-monitor-health.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    limits=Limits.environment();interval=integer('RF_ALERT_POLL_SECONDS',5,1,60)
    grace=integer('RF_ALERT_STARTUP_GRACE_SECONDS',30,0,300)
    store=Store(os.environ['READONLY_DATABASE_URL'])
    if args.once:
        try:result=snapshot(store,limits)
        except Exception:print(json.dumps({'available':False,'alerts':[{'code':'monitor_unavailable'}]}));return 3
        print(json.dumps(result));return 2 if result['alerts'] else 0
    configure();stop=threading.Event();transitions=Transitions();started=time.monotonic()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    event('monitor_started')
    while not stop.is_set():
        result=None
        try:
            result=snapshot(store,limits)
            if time.monotonic()-started<grace:
                result['alerts']=[a for a in result['alerts'] if a['code']!='worker_offline']
        except Exception:
            pass  # Emit an unknown-state incident, never raw DSNs/provider errors.
        for item in transitions.update(result):
            event('alert_transition',alert_id=item['id'],code=item['code'],severity=item['severity'],
                  run_id=item.get('run_id'),transition=item['transition'])
        HEALTH.write_text(json.dumps({'time':time.time(),'available':result is not None}))
        stop.wait(interval)
    store.close();event('monitor_stopped');return 0


if __name__=='__main__':raise SystemExit(main())
