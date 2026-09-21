"""QA-only PostgreSQL gates for the refund-commit/checkpoint-write window.

Imported only by the step 1.7 container observer. All trigger names are private
to this harness; no application table contents are removed or rewritten.
"""
import time

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

TABLES = ("rf_refunds", "checkpoint_writes", "checkpoint_blobs", "checkpoints")
FUNCTION = "rf_qa_step17_gate"


class ReplayGate:
    def __init__(self, url, observer):
        self.url, self.observer, self.lock = url, observer, None

    def seed(self, source):
        if source not in {"RF-1001", "RF-1004"}:
            raise ValueError("Only synthetic refund fixture templates allowed")
        row = self.observer.execute("""
            INSERT INTO rf_orders(id,owner,amount,days,used,status)
            SELECT 'RF-' || n, o.owner,o.amount,o.days,o.used,o.status
            FROM generate_series(1700,9999) n CROSS JOIN rf_orders o
            WHERE o.id=%s AND NOT EXISTS(SELECT 1 FROM rf_orders x WHERE x.id='RF-' || n)
            ORDER BY n LIMIT 1 RETURNING *
        """, (source,)).fetchone()
        if not row:
            raise RuntimeError("No free synthetic order ID")
        return {"template": source, "order": row}

    def install(self, run_id):
        if self.lock is not None:
            raise RuntimeError("Replay gate already installed")
        self.lock = psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
        # Bounds lock lifetime if the host process disappears mid-test.
        self.lock.execute("SET idle_session_timeout='60s'")
        self.lock.execute("SELECT pg_advisory_lock(1707,1),pg_advisory_lock(1707,2)")
        with self.observer.transaction():
            self.observer.execute("""
                CREATE OR REPLACE FUNCTION rf_qa_step17_gate() RETURNS trigger AS $$
                BEGIN
                    IF TG_TABLE_NAME='rf_refunds' THEN
                        IF NEW.run_id::text=TG_ARGV[0] THEN
                            PERFORM pg_advisory_xact_lock(1707,2);
                        END IF;
                    ELSE
                        IF NEW.thread_id=TG_ARGV[0] AND EXISTS(
                            SELECT 1 FROM rf_refunds WHERE run_id::text=NEW.thread_id
                        ) THEN
                            PERFORM pg_advisory_xact_lock(1707,1);
                        END IF;
                    END IF;
                    RETURN NEW;
                END; $$ LANGUAGE plpgsql
            """)
            for table in TABLES:
                self.observer.execute(sql.SQL("CREATE OR REPLACE TRIGGER {} BEFORE INSERT OR UPDATE ON {} "
                    "FOR EACH ROW EXECUTE FUNCTION {}({})").format(
                    sql.Identifier(FUNCTION), sql.Identifier(table), sql.Identifier(FUNCTION), sql.Literal(run_id)))
        return {"gate_pid": self.lock.info.backend_pid, "run_id": run_id,
                "tables": TABLES, "idle_session_timeout_seconds": 60}

    def blocked(self, checkpoint=False):
        pattern = "%INSERT INTO checkpoint%" if checkpoint else "%INSERT INTO rf_refunds%"
        deadline = time.monotonic() + 12
        while True:
            rows = self.observer.execute(
                "SELECT pid,host(client_addr) AS client_addr,state,wait_event_type,wait_event,query "
                "FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) AND query ILIKE %s",
                (self.lock.info.backend_pid, pattern)).fetchall()
            if rows:
                return rows
            if time.monotonic() >= deadline:
                raise TimeoutError("Replay boundary not observed")
            time.sleep(0.05)

    def allow_refund(self):
        return self.lock.execute("SELECT pg_advisory_unlock(1707,2) AS released").fetchone()

    def abort_checkpoint_writers(self):
        # Killing the client does not necessarily cancel an in-flight PG statement.
        # Terminate only checkpoint INSERTs blocked by this observer's QA lock,
        # so releasing that lock cannot later commit the dead client's output.
        return self.observer.execute(
            "SELECT pid,pg_terminate_backend(pid,5000) AS terminated FROM pg_stat_activity "
            "WHERE %s=ANY(pg_blocking_pids(pid)) AND query ILIKE '%%INSERT INTO checkpoint%%'",
            (self.lock.info.backend_pid,)).fetchall()

    def describe(self, run_id):
        return self.observer.execute(
            "SELECT checkpoint_id,task_id,channel,md5(blob) AS blob_md5 FROM checkpoint_writes "
            "WHERE thread_id=%s ORDER BY checkpoint_id,task_id,idx", (run_id,)).fetchall()

    def close(self):
        if self.lock is not None:
            self.lock.close()
            self.lock = None
        # Drop only this harness's QA triggers/function; preserve all business data.
        with self.observer.transaction():
            self.observer.execute("SET LOCAL lock_timeout='5s'")
            for table in TABLES:
                self.observer.execute(sql.SQL("DROP TRIGGER IF EXISTS {} ON {}").format(
                    sql.Identifier(FUNCTION), sql.Identifier(table)))
            self.observer.execute(sql.SQL("DROP FUNCTION IF EXISTS {}()").format(sql.Identifier(FUNCTION)))
        count = self.observer.execute(
            "SELECT count(*) AS n FROM pg_trigger WHERE tgname=%s", (FUNCTION,)).fetchone()["n"]
        function = self.observer.execute("SELECT to_regprocedure('rf_qa_step17_gate()') AS f").fetchone()["f"]
        return {"remaining_test_triggers": count, "test_function": function}
