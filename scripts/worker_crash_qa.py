"""SIGKILL a real demo Worker: investigation (1.5) or saved approval (1.6).

Run from any directory: python scripts/worker_crash_qa.py --report <new.json>
Requires Docker Compose, an existing resolveflow:local image, and the main
resolveflow stack running in demo mode for read-only data comparisons. Host
Python uses only the standard library. Fixed isolated project/port, no production .env,
no image rebuild, no volume deletion. Stops its QA services even on failure.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "resolveflow-qa-step15"
API = PROJECT + "-resolveflow-1"
WORKER = PROJECT + "-worker-1"
MAIN = "resolveflow-resolveflow-1"
URL = "http://127.0.0.1:8006"
PROBE = Path(__file__).with_name("worker_crash_probe.py")
PUBLIC_ENV = {
    "MODE": "demo", "APP_PORT": "8006", "APP_API_KEY": "qa-step15-operator",
    "REVIEWER_API_KEY": "qa-step15-reviewer", "ADMIN_API_KEY": "qa-step15-admin",
    "POSTGRES_PASSWORD": "qa-step15-database", "OPENAI_API_KEY": "",
    "OPENAI_BASE_URL": "https://api.deepseek.com", "MODEL_NAME": "deepseek-flash",
}


def docker(*args, timeout=30, stdin=None, env=None):
    process = subprocess.run(["docker", *args], input=stdin, capture_output=True,
                             text=True, encoding="utf-8", timeout=timeout, env=env, cwd=ROOT)
    if process.returncode:
        # Docker/config errors can contain environment values; keep reports clean.
        raise RuntimeError(f"Docker {args[0]} failed (exit {process.returncode})")
    return process.stdout.strip()


def container(name):
    # Full inspect contains secrets: request only operational fields.
    template = ('{"id":{{json .Id}},"image":{{json .Image}},"state":{{json .State.Status}},'
                '"health":{{json .State.Health.Status}},"exit_code":{{.State.ExitCode}},'
                '"pid":{{.State.Pid}},"started":{{json .State.StartedAt}},'
                '"finished":{{json .State.FinishedAt}},"command":{{json .Config.Cmd}},'
                '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
                '"networks":{{json .NetworkSettings.Networks}}}')
    return json.loads(docker("inspect", "--format", template, name))


def api(path, body=None, role="operator", expected=None):
    key_name = "REVIEWER_API_KEY" if role == "reviewer" else "APP_API_KEY"
    request = urllib.request.Request(URL + path, headers={
        "X-API-Key": PUBLIC_ENV[key_name], "Content-Type": "application/json"},
        data=None if body is None else json.dumps(body).encode("utf-8"))
    if expected is None:
        expected = 200 if body is None else 202
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        if response.status != expected:
            raise RuntimeError("Unexpected API status")
        return json.load(response)


def wait_for(check, seconds=60):
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError("QA condition not reached")
        time.sleep(0.2)


class Observer:
    def __init__(self):
        docker("cp", str(PROBE), API + ":/tmp/worker_crash_probe.py")
        self.process = subprocess.Popen(
            ["docker", "exec", "-i", API, "python", "-u", "/tmp/worker_crash_probe.py"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1)
        self.lines = queue.Queue()

        def read():
            for line in self.process.stdout:
                self.lines.put(line)
            self.lines.put(None)

        threading.Thread(target=read, daemon=True).start()
        if self.receive() != {"ready": True}:
            raise RuntimeError("Observer failed startup")

    def receive(self):
        line = self.lines.get(timeout=20)
        if line is None:
            raise RuntimeError("Observer exited")
        return json.loads(line)

    def call(self, command, **args):
        self.process.stdin.write(json.dumps({"command": command, **args}) + "\n")
        self.process.stdin.flush()
        response = self.receive()
        if not response["ok"]:
            raise RuntimeError("Observer " + response["error_type"])
        return response["result"]

    def close(self):
        self.process.stdin.close()  # EOF releases the DB lock in probe finally.
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)


def main_fingerprints():
    return json.loads(docker("exec", "-i", MAIN, "python", "-", "--fingerprints",
                            stdin=PROBE.read_text(encoding="utf-8")))


def investigation(observer, report, require):
    # Avoid the demo's short investigation racing ahead before the gate exists.
    # Start-up setup is already complete; pausing does not fake graph execution.
    docker("pause", WORKER)
    created = api("/api/runs", {"order_id": "RF-1001", "ticket": "申请退款"})
    run_id = report["run_id"] = created["id"]
    require(created["status"] == "queued", "API accepted one queued investigation")
    report["gate"] = observer.call("gate")
    docker("unpause", WORKER)
    blocked = report["during_investigation"] = observer.call("blocked", run_id=run_id)
    worker_addresses = {n["IPAddress"] for n in report["worker_before"]["networks"].values()}
    require(all(row["client_addr"] in worker_addresses for row in blocked["blocked_order_reads"]),
            "MCP order SELECT is blocked from the real Worker container")
    require(blocked["run"]["status"] == "running" and not blocked["job_lock_available"],
            "Worker claimed and locked this running job")
    require(blocked["graph_next"] == ["investigate"] and bool(blocked["checkpoint_ids"]),
            "PostgreSQL checkpoint persists pending investigate node")
    require("proposal" not in blocked["graph_state"] and "result" not in blocked["graph_state"],
            "investigation has not completed")
    print("Confirmed real MCP investigation; sending SIGKILL...", flush=True)
    docker("kill", "--signal", "KILL", WORKER)
    killed = report["worker_killed"] = container(WORKER)
    require(killed["state"] == "exited" and killed["exit_code"] == 137, "Worker exited from SIGKILL (137)")
    after = wait_for(lambda: (s if (s := observer.call("snapshot", run_id=run_id))["job_lock_available"] else None), 8)
    report["after_kill"] = after
    require(after["job"]["status"] == "queued" and after["run"]["status"] == "running",
            "interrupted job transaction rolled back; queued job remains eligible")
    require(after["checkpoint_ids"] == blocked["checkpoint_ids"] and after["graph_next"] == ["investigate"],
            "same checkpoint and pending node survive process death")
    require(after["job"]["attempts"] == 0 and not after["attempts"], "killed uncommitted attempt is not counted")
    require(after["refunds"] == blocked["refunds"], "no refund was committed during interrupted investigation")
    report["gate_release"] = observer.call("unlock")
    recovery_started = time.monotonic()
    # Docker kill is an operator stop; explicitly restart the container.
    # Queue recovery itself needs no admin retry or resubmission.
    docker("start", WORKER)
    final = wait_for(lambda: (s if (s := api("/api/runs/" + run_id))["job"]["status"] == "done" else None))
    report["recovery_seconds"] = round(time.monotonic() - recovery_started, 3)
    report["recovered"] = observer.call("snapshot", run_id=run_id)
    expected = "already_refunded" if blocked["refunds"] else "refunded"
    require(final["status"] == expected, "same run automatically finishes with " + expected)
    recovered = report["recovered"]
    require(set(after["checkpoint_ids"]).issubset(recovered["checkpoint_ids"]) and not recovered["graph_next"],
            "original thread history retained and graph completed")
    require(recovered["job"]["attempts"] == 1 and len(recovered["attempts"]) == 1 and recovered["attempts"][0]["success"],
            "one committed successful job attempt")
    require(recovered["audit"] == [{"action": "create"}], "no manual retry or extra submission of interrupted run")
    require(len(recovered["refunds"]) == 1, "exactly one refund ledger row for the order")

    # A new API run both proves continued processing and duplicate protection.
    follow_up = api("/api/runs", {"order_id": "RF-1001", "ticket": "申请退款"})
    report["follow_up_run_id"] = follow_up["id"]
    wait_for(lambda: api("/api/runs/" + follow_up["id"])["job"]["status"] == "done")
    follow = report["follow_up"] = observer.call("snapshot", run_id=follow_up["id"])
    require(follow["run"]["status"] == "already_refunded" and follow["refunds"] == recovered["refunds"],
            "Worker processes next job and blocks duplicate refund")
    for label, item in (("recovered", recovered), ("follow_up", follow)):
        require(item["graph_state"]["usage"] == {"model_calls": 0, "input_tokens": 0, "output_tokens": 0},
                label + " uses zero model calls or tokens")


def approvals(observer, report, require):
    """Commit a real reviewer decision, then kill before the Worker reads it."""
    report["scenarios"] = []
    for approved in (False, True):
        case = {"approved": approved, "worker_before": container(WORKER)}
        report["scenarios"].append(case)
        created = api("/api/runs", {"order_id": "RF-1004", "ticket": "申请退款"})
        rid = case["run_id"] = created["id"]
        wait_for(lambda: api("/api/runs/" + rid)["status"] == "awaiting_approval")
        pending = case["awaiting_approval"] = observer.call("snapshot", run_id=rid)
        require(pending["graph_next"] == ["approval"] and bool(pending["graph_interrupts"])
                and pending["approval"] is None, "graph is interrupted awaiting an unsaved human decision")
        docker("pause", WORKER)
        body = {"approved": approved, "reason": "1.6恢复验收：" + ("同意退款" if approved else "拒绝退款")}
        saved = api("/api/runs/" + rid + "/approval", body, role="reviewer")
        case["approval_api_response"] = {key: saved[key] for key in ("id", "status", "approval", "job")}
        committed = case["after_approval_commit"] = observer.call("snapshot", run_id=rid)
        require(saved["status"] == "approval_queued" and committed["job"]["kind"] == "approval"
                and committed["job"]["status"] == "queued", "HTTP 202 decision and approval job are committed")
        require(committed["approval"]["approved"] is approved and committed["approval"]["actor"] == "reviewer"
                and committed["approval"]["reason"] == body["reason"], "independent DB read confirms decision, reviewer and reason")
        require(committed["graph_state"] == pending["graph_state"] and committed["checkpoint_ids"] == pending["checkpoint_ids"],
                "saving approval does not execute or replace the suspended graph")
        case["gate"] = observer.call("gate")
        docker("unpause", WORKER)
        blocked = case["before_kill"] = observer.call("blocked", run_id=rid)
        addresses = {n["IPAddress"] for n in case["worker_before"]["networks"].values()}
        require(all(row["client_addr"] in addresses for row in blocked["blocked_approval_reads"]),
                "real Worker SELECT of saved approval is blocked")
        require(not blocked["job_lock_available"] and blocked["run"]["status"] == "approval_queued",
                "Worker has claimed approval job but has not resumed the graph")
        print("Saved " + str(approved) + " approval; killing claimed Worker...", flush=True)
        docker("kill", "--signal", "KILL", WORKER)
        killed = case["worker_killed"] = container(WORKER)
        require(killed["state"] == "exited" and killed["exit_code"] == 137, "approval Worker exits from real SIGKILL (137)")
        after = wait_for(lambda: (s if (s := observer.call("snapshot", run_id=rid))["job_lock_available"] else None), 8)
        case["after_kill"] = after
        require(after["approval"] == committed["approval"], "saved approval survives process death without modification")
        require(after["graph_state"] == pending["graph_state"] and after["checkpoint_ids"] == pending["checkpoint_ids"]
                and after["graph_interrupts"] == pending["graph_interrupts"], "original checkpoint and approval interrupt survive")
        require(after["job"]["kind"] == "approval" and after["job"]["status"] == "queued"
                and after["job"]["attempts"] == 0 and after["attempts"] == pending["attempts"],
                "uncommitted approval attempt rolls back and releases its queue lock")
        require(after["refunds"] == pending["refunds"], "no refund before saved decision is resumed")
        case["gate_release"] = observer.call("unlock")
        # Worker remains dead here: rejection is checked before any recovery.
        case["duplicate_responses_before_restart"] = [api("/api/runs/" + rid + "/approval",
            {**body, "approved": decision}, role="reviewer", expected=409) for decision in (approved, not approved)]
        require(observer.call("snapshot", run_id=rid)["approval"] == committed["approval"],
                "duplicate and opposite decisions return 409 while Worker is down; original decision retained")
        started = time.monotonic()
        docker("start", WORKER)
        wait_for(lambda: api("/api/runs/" + rid)["job"]["status"] == "done")
        case["recovery_seconds"] = round(time.monotonic() - started, 3)
        final = case["recovered"] = observer.call("snapshot", run_id=rid)
        expected = ("already_refunded" if pending["refunds"] else "refunded") if approved else "rejected"
        require(final["run"]["status"] == expected and final["graph_state"]["decision"] is approved
                and final["graph_state"]["result"]["decision_source"] == "human", "recovered outcome honors the saved human decision: " + expected)
        require(final["approval"] == committed["approval"], "decision, reviewer, reason and timestamp are unchanged after recovery")
        require(not final["graph_next"] and not final["graph_interrupts"]
                and set(pending["checkpoint_ids"]).issubset(final["checkpoint_ids"]), "same thread history retained and approval interrupt resolved")
        approval_attempts = [item for item in final["attempts"] if item["kind"] == "approval"]
        require(final["job"]["attempts"] == 1 and len(approval_attempts) == 1 and approval_attempts[0]["success"],
                "exactly one committed successful approval attempt")
        require(final["audit"] == [{"action": "create"}, {"action": "approve:" + ("yes" if approved else "no")}],
                "one approval audit, no admin retry or duplicate approval audit")
        if approved:
            require(len(final["refunds"]) == 1, "approved order has exactly one refund")
            require(final["refunds"] == pending["refunds"] if pending["refunds"] else
                    final["refunds"][0]["run_id"] == rid and final["refunds"][0]["amount"] == final["graph_state"]["order"]["amount"],
                    "approved recovery retains existing refund or attributes first refund to original run")
        else:
            require(final["refunds"] == pending["refunds"], "rejected decision produces no refund")
        require(final["graph_state"]["usage"] == {"model_calls": 0, "input_tokens": 0, "output_tokens": 0}, "recovery makes zero model calls")
        case["duplicate_responses_after_recovery"] = [api("/api/runs/" + rid + "/approval",
            {**body, "approved": decision}, role="reviewer", expected=409) for decision in (approved, not approved)]
        after_duplicates = observer.call("snapshot", run_id=rid)
        require(after_duplicates == final, "duplicate and opposite decisions return 409 after recovery; no state or ledger changes")
        case["worker_after"] = wait_for(lambda: (c if (c := container(WORKER))["health"] == "healthy" else None))
        require(case["worker_after"]["started"] != case["worker_before"]["started"], "Worker restarted and is healthy")

    # A distinct approved run exercises business idempotency after both crashes.
    follow = api("/api/runs", {"order_id": "RF-1004", "ticket": "申请退款"})
    rid = follow["id"]
    report["follow_up_run_id"] = rid
    wait_for(lambda: api("/api/runs/" + rid)["status"] == "awaiting_approval")
    api("/api/runs/" + rid + "/approval", {"approved": True, "reason": "1.6恢复后重复退款验收"}, role="reviewer")
    wait_for(lambda: api("/api/runs/" + rid)["job"]["status"] == "done")
    final = report["follow_up"] = observer.call("snapshot", run_id=rid)
    require(final["run"]["status"] == "already_refunded" and final["refunds"] == report["scenarios"][-1]["recovered"]["refunds"],
            "subsequent approved run finishes and cannot duplicate the existing refund")
    require(final["graph_state"]["usage"] == {"model_calls": 0, "input_tokens": 0, "output_tokens": 0}, "follow-up makes zero model calls")


def main():
    global PROJECT, API, WORKER, URL, PUBLIC_ENV
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", choices=("1.5", "1.6"), default="1.5")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.step == "1.6":
        PROJECT = "resolveflow-qa-step16"
        API, WORKER = PROJECT + "-resolveflow-1", PROJECT + "-worker-1"
        URL = "http://127.0.0.1:8007"
        PUBLIC_ENV = {**PUBLIC_ENV, "APP_PORT": "8007", "APP_API_KEY": "qa-step16-operator",
                      "REVIEWER_API_KEY": "qa-step16-reviewer", "ADMIN_API_KEY": "qa-step16-admin",
                      "POSTGRES_PASSWORD": "qa-step16-database"}
    expected_runs = 2 if args.step == "1.5" else 3
    if args.report.exists():
        parser.error("Choose a new report path; historical evidence is never overwritten")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {"step": args.step, "started_utc": datetime.now(timezone.utc).isoformat(),
              "project": PROJECT, "url": URL, "mode": "demo", "passed": False,
              "checks": [], "cleanup_errors": []}

    def require(condition, label):
        report["checks"].append({"check": label, "passed": bool(condition)})
        if not condition:
            raise AssertionError(label)

    observer = None
    compose = None
    environment = {**os.environ, **PUBLIC_ENV}
    try:
        report["main_before"] = main_fingerprints()
        report["main_container"] = container(MAIN)
        print("Starting isolated demo QA services on " + URL + "...", flush=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as empty_env:
            env_path = Path(empty_env.name)
        compose = ("compose", "--env-file", str(env_path), "-f", str(ROOT / "compose.yaml"), "-p", PROJECT)
        docker(*compose, "up", "--no-build", "--pull", "never", "-d", "--wait", "--wait-timeout", "180",
               env=environment, timeout=200)
        report["worker_before"] = container(WORKER)
        require(report["worker_before"]["project"] == PROJECT, "dedicated QA Worker")
        require(report["worker_before"]["command"] == ["python", "worker.py"], "real unmodified Worker command")
        require(report["worker_before"]["image"] == report["main_container"]["image"] == container(API)["image"],
                "QA and main application use the same image")
        require(api("/health")["mode"] == "demo", "QA is demo")
        observer = Observer()
        report["qa_before"] = observer.call("fingerprints")

        if args.step == "1.5":
            investigation(observer, report, require)
        else:
            approvals(observer, report, require)
        report["worker_after"] = wait_for(lambda: (c if (c := container(WORKER))["health"] == "healthy" else None))
        require(report["worker_after"]["started"] != report["worker_before"]["started"], "new Worker process started")
        report["qa_after"] = observer.call("fingerprints")
        require(report["qa_after"]["rf_runs"]["count"] == report["qa_before"]["rf_runs"]["count"] + expected_runs
                and report["qa_after"]["rf_jobs"]["count"] == report["qa_before"]["rf_jobs"]["count"] + expected_runs,
                f"exactly {expected_runs} new runs and jobs, no duplicate interrupted job")
        report["main_after"] = main_fingerprints()
        require(report["main_before"] == report["main_after"], "main business, checkpoint and RAG tables unchanged")
        report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        print("QA failed: " + type(error).__name__, flush=True)
    finally:
        for action in (
            lambda: observer.close() if observer else None,
            lambda: docker("unpause", WORKER) if compose and container(WORKER)["state"] == "paused" else None,
            lambda: docker(*compose, "stop", env=environment, timeout=60) if compose else None,
        ):
            try:
                action()
            except Exception as error:
                report["cleanup_errors"].append(type(error).__name__)
        if compose:
            env_path.unlink(missing_ok=True)
            try:
                report["qa_final"] = {service: container(PROJECT + "-" + service + "-1")["state"]
                                      for service in ("resolveflow", "worker", "db")}
                if set(report["qa_final"].values()) != {"exited"}:
                    raise RuntimeError("QA services did not all stop")
            except Exception as error:
                report["cleanup_errors"].append(type(error).__name__)
        report["passed"] = report["passed"] and not report["cleanup_errors"]
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "report": str(args.report)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
