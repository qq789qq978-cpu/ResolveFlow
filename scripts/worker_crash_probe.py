"""Container-side observer for step 1.5; invoked by worker_crash_qa.py.

The only fault is a bounded QA rf_orders table lock. Never executes a job.
JSON line protocol keeps the blocker connection alive across Docker commands.
"""
import hashlib
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, "/app")

import psycopg
from psycopg.rows import dict_row
from psycopg import sql


TABLES = (
    "rf_orders", "rf_runs", "rf_jobs", "rf_approvals", "rf_reviews",
    "rf_refunds", "rf_audit", "rf_job_attempts", "checkpoints",
    "checkpoint_blobs", "checkpoint_writes", "rf_knowledge_documents",
    "rf_knowledge_chunks",
)


def fingerprints(connection):
    result = {}
    for table in TABLES:
        rows = connection.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))).fetchall()
        encoded = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
        result[table] = {"count": len(rows), "sha256": hashlib.sha256(
            json.dumps(encoded).encode("utf-8")).hexdigest()}
    return result


def main():
    if os.environ.get("MODE") != "demo":
        raise RuntimeError("Observer requires demo mode")
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as observer:
        if sys.argv[1:] == ["--fingerprints"]:
            print(json.dumps(fingerprints(observer)))
            return
        if os.environ.get("APP_API_KEY") != "qa-step15-operator":
            raise RuntimeError("Fault injection requires the dedicated step 1.5 QA fixture")
        from engine import Engine
        from storage import Store
        with tempfile.TemporaryDirectory(prefix="rf-step15-") as directory:
            engine = Engine(directory, "demo", repository=Store(url))
            gate = None

            def snapshot(run_id):
                run = observer.execute("SELECT * FROM rf_runs WHERE id=%s", (run_id,)).fetchone()
                job = observer.execute("SELECT * FROM rf_jobs WHERE run_id=%s", (run_id,)).fetchone()
                available = observer.execute(
                    "SELECT run_id FROM rf_jobs WHERE run_id=%s FOR UPDATE SKIP LOCKED", (run_id,)
                ).fetchone() is not None
                graph = engine.graph.get_state(engine.config(run_id))
                return {
                    "run": run, "job": job, "job_lock_available": available,
                    "graph_next": graph.next, "graph_state": graph.values,
                    "checkpoint_ids": [row["checkpoint_id"] for row in observer.execute(
                        "SELECT checkpoint_id FROM checkpoints WHERE thread_id=%s ORDER BY checkpoint_id", (run_id,))],
                    "attempts": observer.execute("SELECT * FROM rf_job_attempts WHERE run_id=%s ORDER BY id", (run_id,)).fetchall(),
                    "audit": observer.execute("SELECT action FROM rf_audit WHERE run_id=%s ORDER BY id", (run_id,)).fetchall(),
                    "refunds": observer.execute("SELECT * FROM rf_refunds WHERE order_id=%s", (run["order_id"],)).fetchall(),
                }

            print(json.dumps({"ready": True}), flush=True)
            try:
                for line in sys.stdin:
                    request = json.loads(line)
                    command = request["command"]
                    try:
                        if command == "gate":
                            if gate is not None:
                                raise RuntimeError("Gate already held")
                            gate = psycopg.connect(url)
                            gate.execute("SET idle_in_transaction_session_timeout='30s'")
                            gate.execute("SET lock_timeout='3s'")
                            gate.execute("LOCK TABLE rf_orders IN ACCESS EXCLUSIVE MODE")
                            result = {"gate_pid": gate.info.backend_pid, "auto_release_seconds": 30}
                        elif command == "blocked":
                            deadline = time.monotonic() + 10
                            while True:
                                blockers = observer.execute(
                                    "SELECT pid,host(client_addr) AS client_addr,state,wait_event_type,wait_event,query "
                                    "FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) "
                                    "AND query LIKE 'SELECT * FROM rf_orders WHERE%%'", (gate.info.backend_pid,)
                                ).fetchall()
                                if blockers:
                                    result = {"blocked_order_reads": blockers, **snapshot(request["run_id"])}
                                    break
                                if time.monotonic() >= deadline:
                                    raise TimeoutError("No real MCP order read observed")
                                time.sleep(0.05)
                        elif command == "unlock":
                            if gate is not None:
                                gate.close()  # Rollback; never changes orders.
                                gate = None
                            result = {"gate_released": True}
                        elif command == "snapshot":
                            result = snapshot(request["run_id"])
                        elif command == "fingerprints":
                            result = fingerprints(observer)
                        elif command == "quit":
                            break
                        else:
                            raise ValueError("Unknown observer command")
                        print(json.dumps({"ok": True, "result": result}, default=str), flush=True)
                    except Exception as error:
                        # Do not serialize connection strings or provider responses.
                        print(json.dumps({"ok": False, "error_type": type(error).__name__}), flush=True)
            finally:
                if gate is not None:
                    gate.close()
                engine.close()


if __name__ == "__main__":
    main()
