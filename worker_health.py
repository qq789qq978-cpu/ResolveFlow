"""Docker health check scoped to this container, not another worker."""
import os
import socket
from storage import Store

with Store(os.environ['DATABASE_URL']).connect() as c:
    row=c.execute("SELECT count(*) AS n FROM rf_worker_heartbeats WHERE worker_id LIKE %s AND seen_at > now()-interval '20 seconds'",(socket.gethostname()+':%',)).fetchone()
    raise SystemExit(0 if row['n'] else 1)
