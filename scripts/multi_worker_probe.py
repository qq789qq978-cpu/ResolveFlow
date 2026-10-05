"""Dedicated step 1.9 gates; imported only after the demo fixture guard."""
import psycopg
from psycopg import sql


class MultiGate:
    def __init__(self, url, observer):
        self.url, self.observer, self.lock = url, observer, None

    def command(self, command, request):
        if command == 'multi_seed':
            return self.observer.execute("""
                INSERT INTO rf_orders(id,owner,amount,days,used,status)
                SELECT 'RF-' || n,o.owner,o.amount,o.days,o.used,o.status
                FROM generate_series(1900,9999) n CROSS JOIN rf_orders o
                WHERE o.id='RF-1001' AND NOT EXISTS(SELECT 1 FROM rf_orders x WHERE x.id='RF-' || n)
                ORDER BY n LIMIT 1 RETURNING *
            """).fetchone()
        if command == 'multi_gate':
            if self.lock is not None:
                raise RuntimeError('Gate already held')
            self.lock = psycopg.connect(self.url, autocommit=True)
            self.lock.execute("SET idle_session_timeout='180s'")
            self.lock.execute('SELECT pg_advisory_lock(1909,1)')
            with self.observer.transaction():
                self.observer.execute("SET LOCAL lock_timeout='5s'")
                self.observer.execute("""
                    CREATE OR REPLACE FUNCTION rf_qa_step19_gate() RETURNS trigger AS $$
                    BEGIN
                        IF NEW.order_id=TG_ARGV[0] THEN
                            PERFORM pg_advisory_xact_lock(1909,1);
                        END IF;
                        RETURN NEW;
                    END; $$ LANGUAGE plpgsql
                """)
                self.observer.execute(sql.SQL("CREATE OR REPLACE TRIGGER rf_qa_step19_gate "
                    "BEFORE INSERT ON rf_refunds FOR EACH ROW EXECUTE FUNCTION rf_qa_step19_gate({})")
                    .format(sql.Literal(request['order_id'])))
            return {'gate_pid': self.lock.info.backend_pid, 'order_id': request['order_id'], 'max_idle_seconds': 180}
        if command == 'multi_activity':
            # No connection strings or application request text are exported.
            return self.observer.execute("""
                SELECT pid,host(client_addr) AS client_addr,state,wait_event_type,wait_event,
                    extract(epoch FROM clock_timestamp()-xact_start) AS transaction_seconds,
                    extract(epoch FROM clock_timestamp()-query_start) AS query_seconds,
                    pg_blocking_pids(pid) AS blockers,
                    CASE WHEN EXISTS(SELECT 1 FROM pg_locks l WHERE l.pid=pg_stat_activity.pid
                                      AND l.relation='rf_jobs'::regclass AND l.mode='RowShareLock' AND l.granted)
                              AND state='idle in transaction' THEN 'queue_claim'
                         WHEN query LIKE 'SELECT pg_advisory_xact_lock(hashtextextended(%' THEN 'order_guard'
                         WHEN query LIKE 'INSERT INTO rf_refunds%' THEN 'refund_insert'
                         WHEN query LIKE 'SELECT * FROM rf_orders WHERE%' THEN 'order_read'
                         ELSE 'other' END AS operation
                FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
            """).fetchall()
        if command == 'multi_release':
            if self.lock is not None:
                self.lock.close()
                self.lock = None
            return {'released': True}
        if command == 'multi_cleanup':
            return self.close()
        raise ValueError('Unknown multi-worker command')

    def close(self):
        self.command('multi_release', {})
        with self.observer.transaction():
            self.observer.execute("SET LOCAL lock_timeout='5s'")
            self.observer.execute('DROP TRIGGER IF EXISTS rf_qa_step19_gate ON rf_refunds')
            self.observer.execute('DROP FUNCTION IF EXISTS rf_qa_step19_gate()')
        return {'trigger_count': self.observer.execute(
            "SELECT count(*) AS n FROM pg_trigger WHERE tgname='rf_qa_step19_gate'").fetchone()['n'],
            'function': self.observer.execute("SELECT to_regprocedure('rf_qa_step19_gate()') AS f").fetchone()['f']}
