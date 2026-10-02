-- LCE-036: owner decisions made in the cloud Control Center (approve, reject,
-- edit, regenerate, reschedule, skip, duplicate). The cloud only RECORDS them;
-- the private repository's workflow applies each one to the git-tracked
-- pipeline (re-checking the content hash) and marks it applied or refused.
-- GitHub stays the source of truth. Never a credential.
CREATE TABLE decisions (
  decision_id TEXT PRIMARY KEY,
  post_id TEXT,
  plan_date TEXT,
  action TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'edit', 'regenerate', 'reschedule', 'skip', 'duplicate')),
  content_hash TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'refused', 'superseded', 'cancelled')),
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  resolved_at TEXT,
  resolved_by TEXT,
  result TEXT
);
CREATE INDEX decisions_by_status ON decisions (status, created_at);
