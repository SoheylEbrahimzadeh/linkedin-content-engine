-- LCE cloud state (Cloudflare D1). Source of truth for cloud publishing.
-- All timestamps are UTC ISO 8601 with +00:00.

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- Kill switch OFF by default; no provider until the owner enables it.
INSERT INTO settings (key, value, updated_at) VALUES
  ('auto_publish', 'false', '1970-01-01T00:00:00+00:00'),
  ('provider', 'none', '1970-01-01T00:00:00+00:00'),
  ('visibility', 'PUBLIC', '1970-01-01T00:00:00+00:00'),
  ('max_lateness_minutes', '180', '1970-01-01T00:00:00+00:00');

-- Posts pushed after LOCAL human approval (approved hash verified on push).
CREATE TABLE posts (
  post_id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  language TEXT NOT NULL,
  plan_date TEXT,
  state TEXT NOT NULL CHECK (state IN
    ('READY_TO_PUBLISH', 'PUBLISHING', 'PUBLISHED', 'PUBLISH_FAILED', 'NEEDS_RECONCILE', 'WITHDRAWN')),
  content_hash TEXT NOT NULL,
  approved_hash TEXT NOT NULL,
  approved_at TEXT NOT NULL,
  pushed_at TEXT NOT NULL,
  pushed_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Explicit owner consent to publish one post at one slot.
CREATE TABLE consents (
  consent_id TEXT PRIMARY KEY,
  post_id TEXT NOT NULL REFERENCES posts(post_id),
  slot_id TEXT NOT NULL,
  slot_utc TEXT NOT NULL,
  approved_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'consumed', 'expired', 'revoked', 'invalid')),
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  resolved_at TEXT,
  reason TEXT
);
CREATE UNIQUE INDEX one_active_consent_per_post ON consents (post_id) WHERE status = 'active';
CREATE UNIQUE INDEX one_active_consent_per_slot ON consents (slot_id) WHERE status = 'active';
CREATE INDEX consents_due ON consents (status, slot_utc);

-- One job per slot that has (or had) a consent.
CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY,
  slot_id TEXT NOT NULL UNIQUE,
  slot_utc TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN
    ('SCHEDULED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'NEEDS_RECONCILE')),
  post_id TEXT,
  consent_id TEXT,
  reason TEXT,
  updated_at TEXT NOT NULL
);

-- Publication intent and result. The idempotency key is local only.
CREATE TABLE publications (
  post_id TEXT PRIMARY KEY REFERENCES posts(post_id),
  idempotency_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN
    ('publishing', 'published', 'publish_failed', 'needs_reconcile', 'not_published_confirmed')),
  approved_hash TEXT NOT NULL,
  commentary_hash TEXT NOT NULL,
  api_version TEXT NOT NULL,
  author TEXT NOT NULL,
  remote_id TEXT,
  url TEXT,
  published_at TEXT,
  verified_by TEXT,
  attempts TEXT NOT NULL DEFAULT '[]',
  resolution TEXT,
  updated_at TEXT NOT NULL
);

-- Audit trail. Never contains credentials.
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  event TEXT NOT NULL,
  actor TEXT NOT NULL,
  post_id TEXT,
  detail TEXT
);
CREATE INDEX events_at ON events (at);
