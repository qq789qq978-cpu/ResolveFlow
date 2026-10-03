"""Optional synthetic-order import journal; preserves the frozen core schema."""
from alembic import op
import sqlalchemy as sa

revision = 'rf_orders_0001'
down_revision = None
branch_labels = ('orders',)
depends_on = 'rf_core_0001'

SQL = '''
CREATE TABLE rf_order_versions (
 order_id TEXT PRIMARY KEY REFERENCES rf_orders(id),
 workspace TEXT NOT NULL, source TEXT NOT NULL CHECK(source='synthetic-v1'),
 version BIGINT NOT NULL CHECK(version>0), payload_sha TEXT NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE rf_order_events (
 event_id UUID PRIMARY KEY, order_id TEXT NOT NULL REFERENCES rf_orders(id),
 workspace TEXT NOT NULL, version BIGINT NOT NULL CHECK(version>0),
 payload_sha TEXT NOT NULL, outcome TEXT NOT NULL CHECK(outcome IN ('applied','ignored_stale','unchanged')),
 actor TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX rf_order_events_created ON rf_order_events(created_at,event_id);
'''


def upgrade():
    for statement in SQL.split(';'):
        if statement.strip(): op.execute(sa.text(statement))


def downgrade():
    raise RuntimeError('Import journal is durable audit state; restore a validated backup instead of dropping it')
