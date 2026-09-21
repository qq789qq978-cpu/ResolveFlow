"""Container-side observer for steps 1.5/1.6; invoked by worker_crash_qa.py.

Uses a bounded QA orders (1.5) or approvals (1.6) table lock. Never runs a job.
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
        fixture = os.environ.get("APP_API_KEY")
        if fixture not in {"qa-step15-operator", "qa-step16-operator"}:
            raise RuntimeError("Fault injection requires a dedicated crash QA fixture")
        approval_test = fixture == "qa-step16-operator"
        gate_table = "rf_approvals" if approval_test else "rf_orders"
        from engine import Engine
        from storage import Store
        with tempfile.TemporaryDirectory(prefix="rf-crash-") as directory:
            engine = Engine(directory, "demo", repository=Store(url))
            gate = None

            def snapshot(run_id):
                run = observer.execute("SELECT * FROM rf_runs WHERE id=%s", (run_id,)).fetchone()
                job = observer.execute("SELECT * FROM rf_jobs WHERE run_id=%s", (run_id,)).fetchone()
                available = observer.execute(
                    "SELECT run_id FROM rf_jobs WHERE run_id=%s FOR UPDATE SKIP LOCKED", (run_id,)
                ).fetchone() is not None
                graph = engine.graph.get_state(engine.config(run_id))
                # During 1.6, only the lock-owning connection can read approvals.
                approval_reader = gate if approval_test and gate is not None else observer
                return {
                    "run": run, "job": job, "job_lock_available": available,
                    "graph_next": graph.next, "graph_state": graph.values,
                    "graph_interrupts": [item.value for task in graph.tasks for item in task.interrupts],
                    "approval": approval_reader.execute("SELECT * FROM rf_approvals WHERE run_id=%s", (run_id,)).fetchone(),
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
                            gate = psycopg.connect(url, row_factory=dict_row)
                            gate.execute("SET idle_in_transaction_session_timeout='30s'")
                            gate.execute("SET lock_timeout='3s'")
                            gate.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(sql.Identifier(gate_table)))
                            result = {"gate_pid": gate.info.backend_pid, "table": gate_table, "auto_release_seconds": 30}
                        elif command == "blocked":
                            deadline = time.monotonic() + 10
                            while True:
                                blockers = observer.execute(
                                    "SELECT pid,host(client_addr) AS client_addr,state,wait_event_type,wait_event,query "
                                    "FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) "
                                    "AND query LIKE %s", (gate.info.backend_pid, "SELECT * FROM " + gate_table + " WHERE%")
                                ).fetchall()
                                if blockers:
                                    key = "blocked_approval_reads" if approval_test else "blocked_order_reads"
                                    result = {key: blockers, **snapshot(request["run_id"])}
                                    break
                                if time.monotonic() >= deadline:
                                    raise TimeoutError("No matching Worker read observed")
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
