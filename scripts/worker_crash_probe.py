"""Container-side observer for steps 1.5-1.7; invoked by worker_crash_qa.py.

Uses a bounded QA orders (1.5) or approvals (1.6) table lock. Never runs a job.
Step 1.7 delegates temporary refund/checkpoint gates to refund_replay_probe.py.
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
        if fixture not in {"qa-step15-operator", "qa-step16-operator", "qa-step17-operator", "qa-step18-operator", "qa-step19-operator"}:
            raise RuntimeError("Fault injection requires a dedicated crash QA fixture")
        approval_test = fixture == "qa-step16-operator"
        gate_table = "rf_approvals" if approval_test else "rf_orders"
        from engine import Engine
        from storage import Store
        with tempfile.TemporaryDirectory(prefix="rf-crash-") as directory:
            engine = Engine(directory, "demo", repository=Store(url))
            gate = None
            replay = None
            multi = None
            if fixture == "qa-step19-operator":
                from multi_worker_probe import MultiGate
                multi = MultiGate(url, observer)
            if fixture == "qa-step17-operator":
                from refund_replay_probe import ReplayGate
                replay = ReplayGate(url, observer)

            def snapshot(run_id):
                run = observer.execute("SELECT * FROM rf_runs WHERE id=%s", (run_id,)).fetchone()
                job = observer.execute("SELECT * FROM rf_jobs WHERE run_id=%s", (run_id,)).fetchone()
                available = observer.execute(
                    "SELECT run_id FROM rf_jobs WHERE run_id=%s FOR UPDATE SKIP LOCKED", (run_id,)
                ).fetchone() is not None
                graph = engine.graph.get_state(engine.config(run_id))
                # During 1.6, only the lock-owning connection can read approvals.
                approval_reader = gate if gate_table == "rf_approvals" and gate is not None else observer
                result = {
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
                if replay is not None:
                    result["checkpoint_writes"] = replay.describe(run_id)
                return result

            print(json.dumps({"ready": True}), flush=True)
            try:
                for line in sys.stdin:
                    request = json.loads(line)
                    command = request["command"]
                    diagnostics = {}
                    try:
                        if command.startswith("multi_") and multi is not None:
                            result = multi.command(command, request)
                        elif command.startswith("replay_") and replay is not None:
                            if command == "replay_seed":
                                result = replay.seed(request["source"])
                            elif command == "replay_install":
                                result = replay.install(request["run_id"])
                            elif command == "replay_blocked":
                                result = {"blocked_writes": replay.blocked(request.get("checkpoint", False)),
                                          **snapshot(request["run_id"])}
                            elif command == "replay_allow_refund":
                                result = replay.allow_refund()
                            elif command == "replay_abort_writers":
                                result = replay.abort_checkpoint_writers()
                            elif command == "replay_cleanup":
                                result = replay.close()
                            else:
                                raise ValueError("Unknown replay command")
                        elif command == "gate":
                            if gate is not None:
                                raise RuntimeError("Gate already held")
                            if fixture in {"qa-step18-operator", "qa-step19-operator"}:
                                gate_table = request.get("table", "rf_orders")
                                if gate_table not in {"rf_orders", "rf_approvals"}:
                                    raise ValueError("Only QA order/approval read gates allowed")
                            gate = psycopg.connect(url, row_factory=dict_row)
                            gate_seconds = 180 if multi is not None else 30
                            gate.execute(f"SET idle_in_transaction_session_timeout='{gate_seconds}s'")
                            gate.execute("SET lock_timeout='3s'")
                            gate.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(sql.Identifier(gate_table)))
                            result = {"gate_pid": gate.info.backend_pid, "table": gate_table, "auto_release_seconds": gate_seconds}
                        elif command == "blocked":
                            deadline = time.monotonic() + 10
                            while True:
                                blockers = observer.execute(
                                    "SELECT pid,host(client_addr) AS client_addr,state,wait_event_type,wait_event,query "
                                    "FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) "
                                    "AND query LIKE %s", (gate.info.backend_pid, "SELECT * FROM " + gate_table + " WHERE%")
                                ).fetchall()
                                if blockers:
                                    key = "blocked_approval_reads" if gate_table == "rf_approvals" else "blocked_order_reads"
                                    result = {key: blockers, **snapshot(request["run_id"])}
                                    break
                                if time.monotonic() >= deadline:
                                    diagnostics['blocked_activity'] = observer.execute(
                                        'SELECT pid,application_name,wait_event_type,wait_event,left(query,200) AS query '
                                        'FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))',
                                        (gate.info.backend_pid,)).fetchall()
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
                        print(json.dumps({"ok": False, "error_type": type(error).__name__,
                                          'diagnostics': diagnostics}, default=str), flush=True)
            finally:
                if gate is not None:
                    gate.close()
                if replay is not None:
                    replay.close()
                if multi is not None:
                    multi.close()
                engine.close()


if __name__ == "__main__":
    main()
