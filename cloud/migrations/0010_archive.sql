-- Owner request 2026-10-06: 'archive' and 'restore' decisions (a finished post leaves the working
-- views; nothing is deleted). Also adds 'radar_use' (LCE-050), which the Worker accepted but this
-- CHECK did not list. SQLite cannot change a CHECK constraint in place: rebuild, keeping every row.
CREATE TABLE decisions_v10 (
  decision_id TEXT PRIMARY KEY,
  post_id TEXT,
  plan_date TEXT,
  action TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'edit', 'regenerate', 'reschedule', 'skip', 'duplicate',
    'refresh', 'radar_use', 'archive', 'restore')),
  content_hash TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'refused', 'superseded', 'cancelled')),
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  resolved_at TEXT,
  resolved_by TEXT,
  result TEXT,
  request_id TEXT
);
INSERT INTO decisions_v10 (decision_id, post_id, plan_date, action, content_hash, payload, status, created_at, created_by,
  resolved_at, resolved_by, result, request_id)
  SELECT decision_id, post_id, plan_date, action, content_hash, payload, status, created_at, created_by,
    resolved_at, resolved_by, result, request_id FROM decisions;
DROP TABLE decisions;
ALTER TABLE decisions_v10 RENAME TO decisions;
CREATE INDEX decisions_by_status ON decisions (status, created_at);
CREATE UNIQUE INDEX decisions_request_id ON decisions (request_id) WHERE request_id IS NOT NULL;
