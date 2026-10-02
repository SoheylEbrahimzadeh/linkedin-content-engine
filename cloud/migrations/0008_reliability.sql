-- LCE-042: reliable Control Center actions and evidence for Access failures.
-- 1. Every action carries a client request id; a retry of the same action returns the
--    decision already recorded instead of creating a second one.
ALTER TABLE decisions ADD COLUMN request_id TEXT;
CREATE UNIQUE INDEX decisions_request_id ON decisions (request_id) WHERE request_id IS NOT NULL;
-- 2. Which Access sessions actually reached the Worker, and with which methods
--    (from the verified Access JWT: issued/expiry time; never the token itself).
CREATE TABLE access_sessions (
  subject TEXT NOT NULL,
  issued_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  last_get TEXT,
  last_mutation TEXT,
  requests INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (subject, issued_at)
);
-- 3. What the browser saw when a request did NOT reach the Worker (Access redirect,
--    network error): sent by the page after signing in again. No secrets.
CREATE TABLE client_reports (
  report_id TEXT PRIMARY KEY,
  at TEXT NOT NULL,
  subject TEXT NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}'
);
