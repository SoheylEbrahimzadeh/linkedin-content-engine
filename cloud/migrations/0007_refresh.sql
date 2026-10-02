-- LCE-041: images of earlier post versions (posts/<id>/versions/vN), uploaded by
-- `lce cloud sync` (sha-checked) so the Control Center can show the previous
-- version next to the refreshed one. Never used for publishing. Owner-only.
CREATE TABLE version_media (
  post_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  data BLOB NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  mime TEXT NOT NULL,
  alt_text TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (post_id, version)
);

-- LCE-041: the owner may request a full refresh of a post ('refresh' decision).
-- SQLite cannot change a CHECK constraint in place: rebuild the table, keeping every row.
CREATE TABLE decisions_v7 (
  decision_id TEXT PRIMARY KEY,
  post_id TEXT,
  plan_date TEXT,
  action TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'edit', 'regenerate', 'reschedule', 'skip', 'duplicate', 'refresh')),
  content_hash TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'refused', 'superseded', 'cancelled')),
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  resolved_at TEXT,
  resolved_by TEXT,
  result TEXT
);
INSERT INTO decisions_v7 (decision_id, post_id, plan_date, action, content_hash, payload, status, created_at, created_by,
  resolved_at, resolved_by, result)
  SELECT decision_id, post_id, plan_date, action, content_hash, payload, status, created_at, created_by,
    resolved_at, resolved_by, result FROM decisions;
DROP TABLE decisions;
ALTER TABLE decisions_v7 RENAME TO decisions;
CREATE INDEX decisions_by_status ON decisions (status, created_at);
