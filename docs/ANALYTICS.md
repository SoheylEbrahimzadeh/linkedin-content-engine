# Analytics & learning loop (Phase 7A)

Analytics exist to refine the personal brand, not to report numbers. They use
only data the owner can legitimately access. Nothing is estimated, simulated or
scraped.

## Recording what was published

- Published through the engine: `lce publish <post>` records the publication.
- Posted by hand (the approved fallback): after `lce ready <post>`, run
  `lce publish manual <post> --published-url <LinkedIn post URL> [--published-at ISO]`
  in your own terminal and type `PUBLISHED <post>`. It only accepts the exact
  approved text and image, refuses delegated posts and existing records, and
  stores `provider: manual`, `verified_by: owner`.

## Metrics

Metrics belong to `PUBLISHED` posts (`posts/<id>/metrics.yaml`, private):
impressions, members reached, reactions, comments, reposts, clicks, followers
gained, each snapshot with its time and source.

- `lce analytics record <post> --impressions N --reactions N --comments N --reposts N [--at ISO]`
  for numbers you read on LinkedIn.
- `lce analytics import <file.csv>`: columns `post_id` or `url` (the post URL or
  URN you recorded), optional `at`, and any of `impressions, members_reached,
  reactions, comments, reposts, clicks, followers_gained`. Rows that match no
  published post are reported, never guessed.

### Official API (Phase 7B, gated)

LinkedIn's `memberCreatorPostAnalytics` endpoint returns impressions, members
reached, reactions, comments and reshares for the authenticated member. It
requires the `r_member_postAnalytics` permission from the Community Management
API, which LinkedIn grants through its own access review. An adapter will be
added only if the owner obtains that access (credential gate); until then the
sources above are the only ones.

## Learning

`lce analytics insights` joins each post's latest snapshot with its features:
pillar, theme, evidence mode, format, image kind, length, hook length,
hashtags, closing question, weekday and hour of publication (in the configured
timezone). It reports medians of impressions and engagement rate
((reactions + comments + reposts) / impressions) per feature value, only for
groups with at least 3 posts (`--min-sample`), and lists **saturated topics**
(3+ similar topics within 60 days).

It feeds the brand engine in two explicit ways:

- `lce brand next` breaks ties between equally used themes by their median
  engagement rate (only themes with enough data) and says so in its reasons.
- `lce analytics suggest-mix` proposes a pillar mix that moves each target by at
  most 0.1 toward pillars performing above the overall median. It is a
  suggestion: the owner applies it with `lce interview set brand_mix`.

Nothing changes the strategy silently, and small samples never drive decisions.
