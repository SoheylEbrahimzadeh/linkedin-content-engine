-- LCE-040: latest same-day freshness check per post, sent by the private
-- workflow (`lce refresh run --push`). The cron publisher refuses a scheduled
-- publication unless the post had a `current` check on the publication day for
-- exactly the approved text.
CREATE TABLE freshness (
  post_id TEXT PRIMARY KEY,
  check_date TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL,
  decision TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  image_sha256 TEXT,
  reason TEXT NOT NULL DEFAULT '',
  received_at TEXT NOT NULL,
  received_by TEXT NOT NULL
);
