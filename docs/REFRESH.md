# Refresh (LCE-040 same-day check, LCE-041 manual Refresh)

## Manual Refresh (LCE-041, semantics LCE-042)

**LCE-042 — Refresh rejects; Skip releases.** Refresh means "I reject this
version — write another publishable candidate for the same slot". When the
decision is applied, the current package is archived at once (`versions/vN`,
status `rejected`), its approval is discarded and the post waits in
NEEDS_REVISION ("Rejected · replacement being written"): it cannot be approved or
published. The replacement must be new: a hook no earlier version had, a text
that is not a rewording of an earlier one (≤ 50% of its words in shared 4-word
runs), a new image (never an earlier version's), sources re-checked at refresh
time; keeping the old image or the old post is not possible (`lce refresh keep`
was removed). Every click adds a version (v1 → v2 → v3 …). Skip releases the slot
(REJECTED, plan entry skipped, "Skipped · slot released") and generates nothing.

**Reliable actions.** Every Control Center change goes through one path: a
client `request_id` (the Worker returns the first decision for a repeated id, so
a retry never duplicates), a session check (GET /api/whoami) right before the
action, and, if Cloudflare Access redirects the request to its sign-in page, a
sign-in window that keeps the page, then the same action sent exactly once more.
The post shows "… requested — recorded" or "… failed — the request was not
recorded" with Try again; never an ambiguous state. What the browser saw (stage,
timing, the session's issue/expiry time, whether the GET just before succeeded)
is reported to D1 `client_reports`; D1 `access_sessions` records which Access
sessions reached the Worker with GETs and with changes. The dashboard check prints
both, which is the production evidence for why a POST was refused.

On every unpublished post that is not in the cloud publish queue, the Control
Center offers **Refresh** (next to Approve | Edit | Reschedule | Skip | Reject).
It means: regenerate the whole post package (text, hook, sources, claims, image,
alt text, metadata), not just the image.

1. Click → confirmation ("Refresh this post? … the current version will be
   preserved in History and the refreshed version will require your approval
   again") with an optional note → a `refresh` decision in D1 (nothing else
   changes; only a signed-in person can record it).
2. The private `decisions` workflow (hourly, :17) records `refresh_request` on
   the post. The current version and its approval state stay untouched.
3. A Claude Code session (the scheduled Routine, or one started by hand) runs
   the `lce-refresh` skill: `lce refresh pending` → research → new text →
   media decision for that text → `lce refresh package <id> --file pkg.yaml`.
   No LLM API is used; the Worker generates nothing.
4. `lce refresh package` is atomic: it copies the current package to
   `posts/<id>/versions/vN/` (text, post.yaml, image file and decision,
   APPROVAL.md, QA and duplicate reports, `version.yaml` with hashes, hook,
   media concept, approval state), replaces text/sources/claims, records the
   humanization/voice check, decides the media (a new conceptual visual or an
   explicit text-only; never the old image), runs QA, the duplicate
   check against the archive, the media checks, and writes a fresh approval
   artifact bound to the new text and image hashes. Any failure restores the
   previous version exactly.
5. The post is AWAITING_APPROVAL; the Control Center shows "Refreshed ·
   Awaiting approval", the new preview, and Versions: previous (with its
   image) → current. Approve as usual; nothing is published by a refresh.
6. `lce versions list|restore` — earlier versions stay recoverable; a restore
   keeps the current package as a new version and needs approval again.

Interplay with the same-day check: a refreshed text has a new hash, so the
Worker's publishing gate needs a new same-day `current` check for it, and the
owner's approval (and a schedule) of that exact version.

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
