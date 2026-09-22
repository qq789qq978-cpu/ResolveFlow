-- Frozen legacy startup DDL from ba17859 for upgrade regression only.

CREATE TABLE IF NOT EXISTS rf_orders (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount > 0),
 days INTEGER NOT NULL CHECK(days >= 0), used BOOLEAN NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rf_runs (
 id UUID PRIMARY KEY, ticket TEXT NOT NULL, order_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'running', mode TEXT NOT NULL, model TEXT,
 state JSONB, error TEXT, elapsed_ms INTEGER,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS rf_runs_status_created ON rf_runs(status, created_at DESC);
CREATE TABLE IF NOT EXISTS rf_approvals (
 run_id UUID PRIMARY KEY REFERENCES rf_runs(id), approved BOOLEAN NOT NULL,
 actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS rf_refunds (
 order_id TEXT PRIMARY KEY REFERENCES rf_orders(id), run_id UUID NOT NULL REFERENCES rf_runs(id),
 amount INTEGER NOT NULL CHECK(amount > 0), created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS rf_reviews (
 run_id UUID PRIMARY KEY REFERENCES rf_runs(id), actor TEXT NOT NULL,
 resolution TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());


CREATE TABLE IF NOT EXISTS rf_jobs (
 run_id UUID PRIMARY KEY REFERENCES rf_runs(id),
 kind TEXT NOT NULL CHECK(kind IN ('investigate','approval')),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','done','failed')),
 attempts INTEGER NOT NULL DEFAULT 0,
 available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_error TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS rf_jobs_ready ON rf_jobs(status,available_at);
CREATE TABLE IF NOT EXISTS rf_job_attempts (
 id BIGSERIAL PRIMARY KEY, run_id UUID NOT NULL REFERENCES rf_runs(id),
 kind TEXT NOT NULL, success BOOLEAN NOT NULL, error_type TEXT,
 elapsed_ms INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS rf_worker_heartbeats (
 worker_id TEXT PRIMARY KEY, seen_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS rf_audit (
 id BIGSERIAL PRIMARY KEY, run_id UUID REFERENCES rf_runs(id),
 actor TEXT NOT NULL, action TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE rf_runs ADD COLUMN IF NOT EXISTS created_by TEXT NOT NULL DEFAULT 'operator';


CREATE TABLE IF NOT EXISTS rf_knowledge_documents (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
 version TEXT NOT NULL, sha256 TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rf_knowledge_chunks (
 chunk_id TEXT PRIMARY KEY,
 document_id TEXT NOT NULL REFERENCES rf_knowledge_documents(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, text TEXT NOT NULL,
 line_start INTEGER NOT NULL, line_end INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS rf_chunks_document ON rf_knowledge_chunks(document_id);
ALTER TABLE rf_knowledge_documents ADD COLUMN IF NOT EXISTS governance JSONB NOT NULL DEFAULT '{}';


CREATE TABLE IF NOT EXISTS rf_policy_releases (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, payload JSONB NOT NULL,
 actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS rf_policy_head (
 singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
 release_id TEXT REFERENCES rf_policy_releases(id), generation BIGINT NOT NULL DEFAULT 0);
INSERT INTO rf_policy_head(singleton) VALUES(TRUE) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS rf_policy_reviews (
 document_sha256 TEXT PRIMARY KEY, governance JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS rf_policy_events (
 id BIGSERIAL PRIMARY KEY, action TEXT NOT NULL, previous_release TEXT,
 target_release TEXT, generation BIGINT NOT NULL, actor TEXT NOT NULL,
 reason TEXT NOT NULL, details JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
