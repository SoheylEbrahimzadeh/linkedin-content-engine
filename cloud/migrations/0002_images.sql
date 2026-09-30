-- Phase 6B (cloud): the approved image of a delegated post, kept apart from
-- `posts` so listings never load image bytes. D1 rows are limited to ~2 MB;
-- the API accepts images up to 1.5 MB (larger images: publish locally).
CREATE TABLE post_images (
  post_id TEXT PRIMARY KEY REFERENCES posts(post_id),
  data BLOB NOT NULL,
  sha256 TEXT NOT NULL,
  alt_text TEXT NOT NULL,
  bytes INTEGER NOT NULL
);

ALTER TABLE publications ADD COLUMN image_urn TEXT;
