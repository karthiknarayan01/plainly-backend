-- Real job/chunk schema for the rewrite pipeline. See the plan this was
-- built from for the full architecture — no design doc file existed
-- before this; comments elsewhere in the repo referencing "the design
-- doc §06/§09" were describing a spoken/planned concept, not a file.
--
-- Applied directly via `gcloud sql connect plainly-db --user=plainly_app
-- --database=grasp < infra/sql/schema.sql` (or psql through the Cloud
-- SQL Auth Proxy) — no migration framework yet, this is the only
-- migration so far.

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- for gen_random_uuid()

CREATE TABLE IF NOT EXISTS rewrite_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- pending | processing | completed | failed
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rewrite_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID NOT NULL REFERENCES rewrite_jobs(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,               -- order within the job (1 page = 1 chunk for v1)
  original_text TEXT NOT NULL,            -- client-extracted page text
  status TEXT NOT NULL DEFAULT 'pending', -- pending | processing | completed | failed
  rewrite_text TEXT,
  scores JSONB,                            -- {fidelity, readability, explanation, style, overall}
  approved BOOLEAN,
  attempt_count INT NOT NULL DEFAULT 0,
  last_feedback TEXT,                      -- judge's verdict_reason, fed back into the next attempt
  claimed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (job_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS rewrite_chunks_pending_idx ON rewrite_chunks (created_at) WHERE status = 'pending';
