"""PostgreSQL business persistence, separate from graph checkpoint tables."""
import uuid
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class Store:
    def __init__(self, url):
        self.url = url

    @contextmanager
    def connect(self):
        with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5) as connection:
            yield connection

    def setup(self):
        """Compatibility entry point: readiness only, never DDL or data seeding."""
        from database_state import require_ready
        require_ready(self.url)

    def order(self, order_id, owner):
        with self.connect() as c:
            return c.execute("SELECT * FROM rf_orders WHERE id=%s AND owner=%s", (order_id, owner)).fetchone() or {}

    def orders(self):
        with self.connect() as c:
            return c.execute("SELECT * FROM rf_orders ORDER BY id").fetchall()

    def create(self, run_id, ticket, order_id, mode, model):
        with self.connect() as c:
            c.execute("INSERT INTO rf_runs(id,ticket,order_id,mode,model) VALUES (%s,%s,%s,%s,%s)", (run_id,ticket,order_id,mode,model))

    def record(self, run_id, state, elapsed_ms=None):
        with self.connect() as c:
            c.execute("UPDATE rf_runs SET state=%s,status=%s,elapsed_ms=COALESCE(%s,elapsed_ms),error=NULL,updated_at=now() WHERE id=%s", (Jsonb(state),state['result']['status'],elapsed_ms,run_id))

    def failed(self, run_id, error):
        with self.connect() as c:
            c.execute("UPDATE rf_runs SET status='failed',error=%s,updated_at=now() WHERE id=%s", (error,run_id))

    def list(self, status=None, limit=50, offset=0):
        with self.connect() as c:
            return c.execute("SELECT id,ticket,order_id,status,mode,model,error,elapsed_ms,created_at,updated_at FROM rf_runs WHERE (%s::text IS NULL OR status=%s) ORDER BY created_at DESC LIMIT %s OFFSET %s", (status,status,limit,offset)).fetchall()

    def get(self, run_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM rf_runs WHERE id=%s", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            row['approval'] = c.execute("SELECT * FROM rf_approvals WHERE run_id=%s",(run_id,)).fetchone()
            row['review'] = c.execute("SELECT * FROM rf_reviews WHERE run_id=%s",(run_id,)).fetchone()
            return row

    def approve(self, run_id, approved, actor, reason):
        with self.connect() as c:
            row = c.execute("SELECT status FROM rf_runs WHERE id=%s FOR UPDATE",(run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            existing = c.execute("SELECT approved FROM rf_approvals WHERE run_id=%s",(run_id,)).fetchone()
            if existing:
                if existing['approved'] != approved:
                    raise ValueError('审批决定已保存，不能修改')
                return
            if row['status'] != 'awaiting_approval':
                raise ValueError('当前工单不允许退款审批')
            c.execute("INSERT INTO rf_approvals VALUES (%s,%s,%s,%s,DEFAULT)",(run_id,approved,actor,reason))

    def refund(self, order_id, run_id, amount, authorization):
        from grounding import action_supported, check_grounding
        from policy_governance import PolicyUnavailable
        with self.connect() as c:
            # Serialize policy changes/reindex against this authorization and ledger
            # transaction. A saved approval cannot override current policy state.
            c.execute('LOCK TABLE rf_knowledge_documents IN SHARE MODE')
            documents = c.execute('SELECT * FROM rf_knowledge_documents').fetchall()
            chunks = c.execute('SELECT * FROM rf_knowledge_chunks').fetchall()
            from policy_releases import active_context
            release = active_context(c, documents, chunks)
            proposal, evidence, mode = authorization
            grounded = check_grounding(proposal, evidence, mode=mode, documents=documents, release=release)
            if not action_supported('refund', grounded):
                raise PolicyUnavailable(grounded)
            result = c.execute("INSERT INTO rf_refunds VALUES (%s,%s,%s,DEFAULT) ON CONFLICT(order_id) DO NOTHING RETURNING order_id",(order_id,run_id,amount)).fetchone()
            return bool(result)

    def review(self, run_id, actor, resolution):
        with self.connect() as c:
            row = c.execute("SELECT status FROM rf_runs WHERE id=%s FOR UPDATE",(run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            if row['status'] != 'escalated':
                raise ValueError('仅可处理转人工工单')
            c.execute("INSERT INTO rf_reviews VALUES (%s,%s,%s,DEFAULT)",(run_id,actor,resolution))
            c.execute("UPDATE rf_runs SET status='closed',updated_at=now() WHERE id=%s",(run_id,))
