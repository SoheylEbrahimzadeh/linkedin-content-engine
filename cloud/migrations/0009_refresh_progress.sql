-- LCE-048: real progress of a Refresh (one replacement being written for a post).
-- Every row is an event that actually happened, reported by the component that did it
-- (writing session, refresh-package workflow, engine); the Control Center derives the
-- stages, elapsed time and stalls from these rows only. No estimates are stored.
CREATE TABLE refresh_progress (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id TEXT NOT NULL,
  decision_id TEXT,
  stage TEXT NOT NULL,
  note TEXT,
  at TEXT NOT NULL,
  reported_by TEXT NOT NULL
);
CREATE INDEX refresh_progress_post ON refresh_progress (post_id, at);
