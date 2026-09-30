-- LCE-013: read-only mirror of the private pipeline for the cloud dashboard
-- (research, plan, drafts, QA, approvals, analytics), pushed by `lce cloud sync`
-- from the private data. One row: the latest snapshot. Never a credential; the
-- snapshot is allowlisted and redacted by the engine before upload.
CREATE TABLE pipeline_snapshot (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  body TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  generated_at TEXT,
  received_at TEXT NOT NULL,
  received_by TEXT NOT NULL
);
