import { test } from "node:test";
import assert from "node:assert/strict";
import {
  UNKNOWN, calendarMonths, checkMode, describeRun, issueCounts, parseRoute, publicationLabel,
  shortHash, show, splitApprovals, stateMeta,
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
