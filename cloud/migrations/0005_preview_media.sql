-- LCE-037: preview copies of the images recorded for posts in the private
-- pipeline, uploaded by `lce cloud sync` (sha-checked) so the Control Center can
-- show the real thumbnail before a post is approved or scheduled. Never used
-- for publishing (that uses post_images, bound to the approval). Owner-only.
CREATE TABLE preview_media (
  post_id TEXT PRIMARY KEY,
  data BLOB NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  mime TEXT NOT NULL,
  alt_text TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
