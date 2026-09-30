import { test } from "node:test";
import assert from "node:assert/strict";
import {
  UNKNOWN, calendarMonths, checkMode, claimLabel, describeRun, duplicateEvidenceNote, jobCounts, jobStateMeta, issueCounts, parseRoute,
  publicationLabel, researchGroups, runStatus, shortHash, show, splitApprovals, stateMeta,
} from "../../src/lce/dashboard/static/lib.js";

test("unknown values are shown as unknown, never invented", () => {
  assert.equal(show(null), UNKNOWN);
  assert.equal(show(undefined), UNKNOWN);
  assert.equal(show(""), UNKNOWN);
  assert.equal(show([]), "none");
  assert.equal(show(false), "no");
  assert.equal(shortHash(null), UNKNOWN);
  assert.equal(shortHash("0123456789abcdef"), "0123456789ab");
  assert.equal(stateMeta(undefined).label, UNKNOWN);
});

test("publication labels never say published", () => {
  assert.equal(publicationLabel("not_published"), "Not published");
  assert.equal(publicationLabel("ready_to_publish"), "Ready (not published)");
  assert.equal(publicationLabel(null), UNKNOWN);
});

test("approval split", () => {
  const r = splitApprovals([{ state: "AWAITING_APPROVAL" }, { state: "APPROVED" },
    { state: "READY_TO_PUBLISH" }, { state: "DRAFTED" }]);
  assert.equal(r.pending.length, 1);
  assert.equal(r.approved.length, 2);
  assert.equal(r.other.length, 1);
});

test("mode must match and never mix demo with real", () => {
  assert.deepEqual(checkMode("real", { meta: { mode: "real" } }), { ok: true, message: "REAL DATA" });
  assert.equal(checkMode("demo", { meta: { mode: "demo" } }).message, "DEMO MODE — NO PRIVATE DATA");
  assert.equal(checkMode("real", { meta: { mode: "demo" } }).ok, false);
  assert.equal(checkMode("demo", { meta: { mode: "real" } }).ok, false);
  assert.equal(checkMode(undefined, { meta: { mode: "real" } }).ok, false);
  assert.equal(checkMode("real", null).ok, false);
});

test("calendar months are Monday-first and only contain real entries", () => {
  const months = calendarMonths([{ date: "2026-09-29", topic: "t" }, { date: "bad" }, null]);
  assert.equal(months.length, 1);
  const cells = months[0].weeks.flat();
  assert.equal(cells[0], null); // 1 Sep 2026 is a Tuesday
  assert.equal(cells[1].date, "2026-09-01");
  const filled = cells.filter((c) => c && c.entries.length);
  assert.deepEqual(filled.map((c) => c.date), ["2026-09-29"]);
  assert.equal(months[0].weeks.every((w) => w.length === 7), true);
  assert.deepEqual(calendarMonths([]), []);
});

test("issue counts and run descriptions", () => {
  assert.deepEqual(issueCounts([{ kind: "FAILED" }, { kind: "NEEDS_RECONCILE" }, { kind: "NEEDS_RECONCILE" }]),
    { FAILED: 1, NEEDS_RECONCILE: 2, INCONSISTENT: 0 });
  assert.deepEqual(issueCounts([]), { FAILED: 0, NEEDS_RECONCILE: 0, INCONSISTENT: 0 });
  assert.equal(describeRun({ event: "approval", decision: "approved", content_hash: "abcdefabcdef0000" }).title, "Approval: approved");
  assert.equal(describeRun({ event: "state", state: "APPROVED" }).tone, "ok");
  assert.equal(describeRun({}).title, UNKNOWN);
});

test("routes", () => {
  assert.deepEqual(parseRoute("#/posts/abc%2Fd"), { name: "posts", id: "abc/d" });
  assert.deepEqual(parseRoute("#/nope"), { name: "dashboard", id: null });
  assert.deepEqual(parseRoute(""), { name: "dashboard", id: null });
});

test("run stage statuses are distinct and honest", () => {
  assert.equal(runStatus("done").symbol, "✓");
  assert.equal(runStatus("not_implemented").label, "not implemented");
  assert.notEqual(runStatus("not_implemented").label, runStatus("not_reached").label);
  assert.equal(runStatus("failed").tone, "error");
  assert.equal(runStatus("waiting").label, "waiting for human");
  assert.equal(runStatus("whatever").label, "whatever");
  assert.equal(runStatus(undefined).label, UNKNOWN);
});

test("research groups and claim labels come from data only", () => {
  const g = researchGroups([
    { candidate_id: "a", status: "selected", selected: true, claims_count: 4 },
    { candidate_id: "b", status: "new", selected: false, claims_count: 0 },
    { candidate_id: "c", status: "discarded", selected: false },
  ]);
  assert.deepEqual(g.selected.map((c) => c.candidate_id), ["a"]);
  assert.deepEqual(g.unselected.map((c) => c.candidate_id), ["b", "c"]);
  assert.equal(claimLabel(4), "4 claims extracted");
  assert.equal(claimLabel(1), "1 claim extracted");
  assert.equal(claimLabel(0), "no claims extracted");
  assert.equal(claimLabel(undefined), UNKNOWN);
  assert.deepEqual(researchGroups(null), { selected: [], unselected: [] });
});

test("zero-history duplicate check is qualified, not presented as evidence", () => {
  const note = duplicateEvidenceNote({ status: "passed", compared_against: 0, exact: [], near: [], similar: [] });
  assert.match(note, /^Duplicate check passed — no previous posts were available for comparison/);
  assert.match(note, /0 posts/);
  assert.equal(duplicateEvidenceNote({ status: "passed", compared_against: 3 }), null);
  assert.match(duplicateEvidenceNote({ status: "failed", compared_against: 0 }), /No previous posts/);
  assert.doesNotMatch(duplicateEvidenceNote({ status: "failed", compared_against: 0 }), /passed/);
  assert.equal(duplicateEvidenceNote(null), null);
  assert.equal(duplicateEvidenceNote({ status: "passed" }), null); // unknown count: no claim either way
});

test("job states and counts are explicit, never estimated", () => {
  assert.deepEqual(jobCounts({ BLOCKED: 2 }), { SCHEDULED: 0, READY: 0, RUNNING: 0, BLOCKED: 2,
    SUCCEEDED: 0, FAILED: 0, SKIPPED: 0, NEEDS_RECONCILE: 0 });
  assert.deepEqual(Object.values(jobCounts(null)).reduce((a, b) => a + b, 0), 0);
  assert.equal(jobStateMeta("NEEDS_RECONCILE").tone, "error");
  assert.notEqual(jobStateMeta("FAILED").label, jobStateMeta("NEEDS_RECONCILE").label);
  assert.match(jobStateMeta("BLOCKED", "awaiting_agent").detail, /Claude Code/);
  assert.equal(jobStateMeta("WHATEVER").label, "WHATEVER");
});

test("scheduler and job events are described", () => {
  assert.equal(describeRun({ event: "job.transition", job_id: "job-x", from: "RUNNING", to: "FAILED", reason: "r" }).tone, "error");
  assert.match(describeRun({ event: "scheduler.locked", holder: "a" }).title, /locked/);
  assert.match(describeRun({ event: "job.error", kind: "filesystem", retryable: true, message: "m" }).detail, /^retryable/);
  assert.equal(describeRun({ event: "scheduler.config_invalid", message: "x" }).tone, "error");
});
