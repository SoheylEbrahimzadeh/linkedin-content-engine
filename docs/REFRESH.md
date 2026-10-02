# Same-day content refresh (LCE-040)

Every post planned or scheduled for a day gets a final freshness check on that
day, before its publishing time. It runs whether or not auto-publish is on. It
never approves, never publishes, and never rewrites a published post.

## Who does what

| Step | Done by | How |
|---|---|---|
| Re-read every recorded source | engine (workflow) | `lce refresh run`: HTTPS fetch, page text normalised |
| Re-check every recorded claim | engine | each claim's text (numbers included) must still be stated on its source: `found` / `partial` / `missing` / `unverified` |
| Re-evaluate the image | engine | file hash, rights metadata; for a diagram: are its strings still in the post text (`still_relevant` / `stale` / `invalid` / `needs_review` / `text_only`) |
| Look for new developments | Claude Code session | `lce-refresh` skill: web research, then `lce refresh research` with the sources checked |
| Decide on a material change | session (judgment), engine (rules below) | |
| Update the text | session | `lce refresh apply`: humanization record → QA → duplicate check against the archive → media re-evaluation → new approval artifact |
| Replace a stale image | session | `lce image diagram` (verbatim new text) or text-only, then `lce refresh finish` |
| Approve the new version | owner | Control Center (hash-bound, as always) |

No LLM API is used anywhere; the Worker generates nothing.

## Decision rules

- Every claim still found and the image still relevant → `unchanged`, status
  `current`. Text, image and approval stay as they are.
- A claim no longer found on its source, or a stale/invalid image →
  `update_required`.
- A source could not be read (403, paywall, network) → `unverifiable`, status
  `needs_review`: a session must look at it. It is never counted as verified.
- Session research: `--material no` → `confirmed` (`current` if the image is
  fine); `--material yes` → `update_required`.
- `lce refresh apply` (material change) → `updated`. The text hash changes, so
  the old approval is discarded (it was bound to the old hashes) and the post
  goes back through QA, the duplicate check and approval preparation to
  AWAITING_APPROVAL. The record has the old and new text and image hashes and
  `approval_effect: invalidated`.

## Records

`posts/<id>/freshness.yaml` (schema `freshness`, last 30 checks): timestamp,
check date, mode (check / research / update), who, decision, status, material
change, reason, every source with its HTTP status, every claim with its
status, the media check, text and image hashes (before → after for updates),
approval before/after and its effect, and the re-run steps (humanization
checklist, QA, duplicate result with archive count).

A run with `--as-of` another day is recorded with `test_mode: true`. It is
shown as a test run, never counted as that day's check, never sent to the
Worker, and the Worker would refuse it anyway.

## Timing and the publishing gate

The private repository's `refresh` workflow runs at 03:41 and 05:11 UTC (05:41
and 07:11 in Berlin summer time; one hour earlier in winter), at least 79
minutes before the first 08:30 slot, and on every push of freshness records
(after a session's research or update). It runs `lce refresh run --push`,
commits the records and refreshes the cloud mirror.

The Worker refuses a **scheduled** publication (`freshness_pending`, consent
kept, nothing sent) unless its freshness row for the post:

- was received on the publishing day (Worker clock, owner time zone),
- has `check_date` = the publishing day,
- has status `current`,
- and its text hash equals the approved hash.

`PUT /api/freshness/:id` accepts only CLI requests and only `check_date` =
today. If no current check exists by the slot, the slot is missed (nothing is
published late). The manual publish-now path stays behind its own typed
confirmation; the refresh does not change it.

## Dashboard

Upcoming rows and the post page show the freshness state: **Not checked**,
**Checked today** (needs review), **No changes needed**, **Update needed**,
**Updated today**, **Update requires approval**, plus whether approval is
still valid and, for scheduled posts, what the publishing gate sees. The post
page's "Same-day freshness" card opens the evidence: sources, claims, image
re-evaluation, hashes, re-run steps and the recent checks.
