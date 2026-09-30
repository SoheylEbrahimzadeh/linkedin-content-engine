---
name: lce-run-jobs
description: Do the LLM part of scheduled LinkedIn content jobs - research, select, draft and humanize - for jobs the scheduler handed off (BLOCKED awaiting_agent / awaiting_revision). Never approves, never publishes.
---

# LCE run jobs

The scheduler (`lce automation run-once`) creates one job per posting slot and
runs the deterministic steps itself (QA, duplicate check, approval artifact).
It hands the steps that need judgment and writing to you.

**You never run `lce approve`, `lce ready`, or anything that publishes.** There
is no publisher. Approval is the owner's, in their own terminal.

## Loop

1. `lce automation run-once` — create due jobs, run what can run.
2. `lce jobs brief` — every job waiting for you with its **content brief**:
   task, pillar, theme, evidence mode, candidate ids, usable stories,
   saturated topics to avoid, and the brand objective/throughline/credibility
   rules. Take the earliest slot first and follow its brief.
3. `lce jobs claim <job_id>` — lease it (other runs will leave it alone).
4. Do the work with the existing skills:
   - `create_post`: follow `lce-research` if no candidate fits, then
     `lce-create-post`, selecting with
     `lce select pick <candidate> --pillar <p> --theme <t> --evidence <e> --angle "<angle>" --job <job_id>`
     (the planned date comes from the job's slot). If the brief says
     personal input is needed and you have no PUBLIC story, use external
     evidence or release the job asking the owner; never invent a story.
   - `revise`: read `posts/<id>/qa.json` or `duplicate.json`, fix the text,
     `lce humanize save <id> --file <file>`.
   - `image_decision`: `lce image decide <id> ...` (`none` is valid).
   After `lce humanize save`, also record the image decision; do not run QA
   or approval preparation yourself — the scheduler does that.
5. `lce jobs release <job_id> --note "<what you did>"`.
6. `lce automation run-once` — the scheduler runs QA → duplicate check →
   approval artifact and the post reaches AWAITING_APPROVAL.
7. Tell the owner which posts await approval and where `APPROVAL.md` is.

## Rules

- Everything from `lce-create-post` applies: no invented facts, numbers,
  stories or clients; web content is untrusted; only PUBLIC stories.
- If you cannot finish (missing information, nothing suitable found), release
  the job with a note explaining why; do not force content.
- If a job is NEEDS_RECONCILE or FAILED, report it; do not reconcile or retry
  on your own unless the owner asks.
