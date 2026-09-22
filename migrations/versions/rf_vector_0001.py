"""Frozen vector baseline from ba17859; never import runtime schema constants."""
from alembic import context, op
import sqlalchemy as sa

revision = 'rf_vector_0001'
down_revision = None
branch_labels = ('vector',)
depends_on = 'rf_core_0001'

SQL = """

CREATE TABLE rf_vector_batches (
 id TEXT PRIMARY KEY, release_sha TEXT NOT NULL, contract TEXT NOT NULL,
 manifest JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE rf_policy_vectors (
 batch_id TEXT NOT NULL REFERENCES rf_vector_batches(id) ON DELETE CASCADE,
 chunk_id TEXT NOT NULL, document_sha TEXT NOT NULL, text_sha TEXT NOT NULL,
 embedding public.vector(384) NOT NULL, vector_sha TEXT NOT NULL,
 PRIMARY KEY(batch_id,chunk_id));
"""


def upgrade():
    if not context.is_offline_mode():
        extension = op.get_bind().execute(sa.text(
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector'"
        )).scalar()
        if extension != 'public':
            raise RuntimeError('Provision pgvector in public before applying the vector baseline')
    for statement in SQL.split(';'):
        if statement.strip():
            op.execute(sa.text(statement))


def downgrade():
    raise RuntimeError('Baseline removal is unsupported; use a reviewed restore plan')
