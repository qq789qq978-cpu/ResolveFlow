"""Step 1.8: stop/start ONLY the dedicated QA PostgreSQL, not its clients.

Requires a running demo main stack (read-only fingerprints), Docker, and an
existing resolveflow:local image. New image candidates can differ from main.
No real .env, builds, volume deletions, paid calls, or real refunds.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request

import worker_crash_qa as qa


def offline_request(path, body=None):
    request = urllib.request.Request(qa.URL + path, headers={
        "X-API-Key": qa.PUBLIC_ENV["APP_API_KEY"], "Content-Type": "application/json"},
        data=None if body is None else json.dumps(body).encode())
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return {"status": response.status, "body": response.read().decode("utf-8")[:300]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    qa.fault_options(parser)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a new report path")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    qa.PROJECT = "resolveflow-qa-step18"
    qa.API, qa.WORKER = qa.PROJECT + "-resolveflow-1", qa.PROJECT + "-worker-1"
    qa.URL = "http://127.0.0.1:8009"
    qa.PUBLIC_ENV = {**qa.PUBLIC_ENV, "APP_PORT": "8009", "APP_API_KEY": "qa-step18-operator",
                     "REVIEWER_API_KEY": "qa-step18-reviewer", "ADMIN_API_KEY": "qa-step18-admin",
                     "POSTGRES_PASSWORD": "qa-step18-database"}
    qa.configure_fault(args)
    db = qa.PROJECT + "-db-1"
    environment = {**os.environ, **qa.PUBLIC_ENV}
    report = {"step": "1.8", "started_utc": datetime.now(timezone.utc).isoformat(),
              "project": qa.PROJECT, "url": qa.URL, "mode": "demo", "passed": False,
              "checks": [], "scenarios": [], "cleanup_errors": []}
    observer = None
    compose = None

    def require(condition, label):
        report["checks"].append({"check": label, "passed": bool(condition)})
        if not condition:
            raise AssertionError(label)

    try:
        report["main_before"] = qa.main_fingerprints()
        report["main_image"] = qa.container(qa.MAIN)["image"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as empty_env:
            env_path = Path(empty_env.name)
        compose = qa.fault_compose(env_path)
        print("Starting dedicated database-outage QA on 8009...", flush=True)
        qa.docker(*compose, "up", "--no-build", "--pull", "never", "-d", "--wait", "--wait-timeout", "180",
                  env=environment, timeout=200)
        report["worker_before"], report["api_before"] = qa.container(qa.WORKER), qa.container(qa.API)
        require(report["worker_before"]["project"] == qa.PROJECT and report["worker_before"]["command"] == ["python", "worker.py"],
                "dedicated QA executes real Worker")
        require(report["worker_before"]["image"] == report["api_before"]["image"], "QA API and Worker use the same image")
        require(qa.api("/health")["mode"] == "demo", "demo mode")
        observer = qa.Observer()
        report["qa_before"] = observer.call("fingerprints")

        for manual in (False, True):
            case = {"manual_approval": manual, "db_before": qa.container(db)}
            report["scenarios"].append(case)
            qa.docker("pause", qa.WORKER)
            created = qa.api("/api/runs", {"order_id": "RF-1004" if manual else "RF-1002", "ticket": "申请退款"})
            rid = case["run_id"] = created["id"]
            if manual:
                qa.docker("unpause", qa.WORKER)
                qa.wait_for(lambda: qa.api("/api/runs/" + rid)["status"] == "awaiting_approval")
                qa.docker("pause", qa.WORKER)
                qa.api("/api/runs/" + rid + "/approval", {"approved": True, "reason": "1.8数据库恢复验收"}, role="reviewer")
            case["gate"] = observer.call("gate", table="rf_approvals" if manual else "rf_orders")
            qa.docker("unpause", qa.WORKER)
            before = case["before_outage"] = observer.call("blocked", run_id=rid)
            rows = before["blocked_approval_reads" if manual else "blocked_order_reads"]
            addresses = {n["IPAddress"] for n in report["worker_before"]["networks"].values()}
            require(not before["job_lock_available"] and all(row["client_addr"] in addresses for row in rows),
                    "real Worker owns the active job at the database outage boundary")
            require(before["graph_next"] == (["approval"] if manual else ["investigate"]), "original workflow checkpoint is durable")
            print("Stopping QA PostgreSQL during " + ("saved approval" if manual else "investigation") + "...", flush=True)
            started = time.monotonic()
            qa.docker("stop", "--time", "5", db)
            case["db_offline"] = qa.container(db)
            require(case["db_offline"]["state"] == "exited", "QA database really stopped")
            # The observer's connections die too; recreate it after PG returns.
            observer.close()
            observer = None
            case["offline_http"] = {
                "health": offline_request("/health"),
                "read": offline_request("/api/runs/" + rid),
                "create": offline_request("/api/runs", {"order_id": "RF-1002", "ticket": "1.8断库期间提交"}),
            }
            time.sleep(max(0, 8 - (time.monotonic() - started)))
            case["offline_seconds"] = round(time.monotonic() - started, 3)
            case["worker_while_db_down"] = qa.container(qa.WORKER)
            require(case["worker_while_db_down"]["pid"] == report["worker_before"]["pid"], "Worker remains alive while DB is unavailable")
            recovery_started = time.monotonic()
            qa.docker("start", db)
            case["db_after"] = qa.wait_for(lambda: (c if (c := qa.container(db))["health"] == "healthy" else None))
            qa.wait_for(lambda: offline_request("/health")["status"] == 200)
            observer = qa.Observer()
            result = qa.wait_for(lambda: (s if (s := qa.api("/api/runs/" + rid))["job"]["status"] in {"done", "failed"} else None), 50)
            case["recovery_seconds"] = round(time.monotonic() - recovery_started, 3)
            final = case["recovered"] = observer.call("snapshot", run_id=rid)
            expected = ("already_refunded" if before["refunds"] else "refunded") if manual else "auto_rejected"
            require(result["status"] == expected and result["job"]["status"] == "done", "original job recovers without restarting Worker or admin retry")
            require(final["job"]["attempts"] == 1 and final["job"]["last_error"] is None,
                    "DB outage does not consume the business retry budget")
            require(set(before["checkpoint_ids"]).issubset(final["checkpoint_ids"]) and not final["graph_next"],
                    "same graph thread finishes with history preserved")
            require(final["approval"] == before["approval"], "saved approval remains unchanged across DB restart")
            require(final["audit"] == [{"action": "create"}] + ([{"action": "approve:yes"}] if manual else []),
                    "no admin retry, duplicate submission or duplicate approval")
            if manual:
                require(len(final["refunds"]) == 1, "approved order has exactly one refund")
                if before["refunds"]:
                    require(final["refunds"] == before["refunds"], "existing refund unchanged")
            else:
                require(not final["refunds"], "rejected investigation produces no refund")
            require(all(item["status"] == 503 for item in case["offline_http"].values()),
                    "health, reads and writes return temporary-unavailable HTTP 503 during outage")
            require(final["graph_state"]["usage"] == {"model_calls": 0, "input_tokens": 0, "output_tokens": 0}, "zero model calls and tokens")
            for name, baseline in ((qa.WORKER, report["worker_before"]), (qa.API, report["api_before"])):
                current = qa.container(name)
                require(current["pid"] == baseline["pid"] and current["started"] == baseline["started"],
                        name + " was never restarted")

        follow = qa.api("/api/runs", {"order_id": "RF-1004", "ticket": "申请退款"})
        rid = report["follow_up_run_id"] = follow["id"]
        qa.wait_for(lambda: qa.api("/api/runs/" + rid)["status"] == "awaiting_approval")
        qa.api("/api/runs/" + rid + "/approval", {"approved": True, "reason": "1.8恢复后重复退款验收"}, role="reviewer")
        qa.wait_for(lambda: qa.api("/api/runs/" + rid)["job"]["status"] == "done")
        follow = report["follow_up"] = observer.call("snapshot", run_id=rid)
        require(follow["run"]["status"] == "already_refunded" and follow["refunds"] == report["scenarios"][-1]["recovered"]["refunds"],
                "subsequent approved job works and cannot duplicate the refund")
        report["qa_after"] = observer.call("fingerprints")
        require(report["qa_after"]["rf_runs"]["count"] == report["qa_before"]["rf_runs"]["count"] + 3
                and report["qa_after"]["rf_jobs"]["count"] == report["qa_before"]["rf_jobs"]["count"] + 3,
                "exactly three accepted jobs; outage POSTs create no orphan or duplicate jobs")
        report["worker_after"] = qa.wait_for(lambda: (c if (c := qa.container(qa.WORKER))["health"] == "healthy" else None))
        report["main_after"] = qa.main_fingerprints()
        require(report["main_before"] == report["main_after"], "main business/checkpoint/RAG tables unchanged")
        report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, qa.ObserverError):
            report['observer_error'] = error.details
        print("QA failed: " + type(error).__name__, flush=True)
    finally:
        for action in (
            lambda: observer.close() if observer else None,
            lambda: qa.docker("unpause", qa.WORKER) if compose and qa.container(qa.WORKER)["state"] == "paused" else None,
            lambda: qa.docker(*compose, "stop", env=environment, timeout=60) if compose else None,
        ):
            try:
                action()
            except Exception as error:
                report["cleanup_errors"].append(type(error).__name__)
        if compose:
            env_path.unlink(missing_ok=True)
            report["qa_final"] = {service: qa.container(qa.PROJECT + "-" + service + "-1")["state"]
                                  for service in ("resolveflow", "worker", "db")}
            if set(report["qa_final"].values()) != {"exited"}:
                report["cleanup_errors"].append("QA services did not all stop")
        report["passed"] = report["passed"] and not report["cleanup_errors"]
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "report": str(args.report)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
