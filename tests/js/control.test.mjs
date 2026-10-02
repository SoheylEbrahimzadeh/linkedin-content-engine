// LCE-036 Control Center helpers (cloud/src/ui/lib.txt, served as /lib.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../cloud/src/ui/lib.txt", import.meta.url), "utf8");
const lib = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));
const { CONTENT_TYPES, attention, contentType, hashtags, hook, initials, localDate, parseHash, segments, timeline, upcoming } = lib;

test("only text and single-image posts are marked implemented", () => {
  assert.deepEqual(CONTENT_TYPES.filter((t) => t.implemented).map((t) => t.key), ["text", "image"]);
  assert.equal(contentType({}).key, "text");
  assert.equal(contentType({ image: { kind: "none" } }).key, "text");
  assert.equal(contentType({ image: { kind: "diagram", sha256: "a" } }).key, "image");
  assert.equal(contentType({ image: { sha256: "a", bytes: 10 } }).key, "image");   // cloud post row
  for (const [fmt, key] of [["video", "video"], ["carousel", "document"], ["pdf", "document"], ["link", "article"], ["poll", "poll"]]) {
    const t = contentType({ format: fmt });
    assert.equal(t.key, key);
    assert.equal(t.implemented, false);
  }
});

test("hook, hashtags, segments and initials never invent", () => {
  assert.equal(hook("\n\n  First line here.\nSecond"), "First line here.");
  assert.equal(hook(""), null);
  assert.equal(hook("x".repeat(200), 20).length, 20);
  assert.deepEqual(hashtags("Text #AI and #ITSM.\n#AI again"), ["#AI", "#ITSM"]);
  assert.deepEqual(segments("a #b c").map((s) => [s.text, s.tag]), [["a ", false], ["#b", true], [" c", false]]);
  assert.equal(initials("Test Person"), "TP");
  assert.equal(initials(""), "?");
});

test("local dates follow the configured time zone", () => {
  assert.equal(localDate("2026-10-05T23:30:00Z", "Europe/Berlin"), "2026-10-06");
  assert.equal(localDate("2026-10-05T23:30:00Z", "UTC"), "2026-10-05");
});

const NOW = "2026-10-05T08:00:00Z";
const pipeline = {
  posts: [{ post_id: "20261006-demo-a", state: "AWAITING_APPROVAL", text: "Hook A\nbody", plan_date: "2026-10-06", image: null },
    { post_id: "20261008-demo-b", state: "READY_TO_PUBLISH", text: "Hook B", plan_date: "2026-10-08",
      image: { kind: "diagram", sha256: "a" }, history: [{ at: "2026-10-04T10:00:00Z", state: "APPROVED" }] }],
  calendar: [{ date: "2026-10-06", topic: "A", status: "awaiting_approval", draft_ref: "20261006-demo-a" },
    { date: "2026-10-08", topic: "B", status: "ready_to_publish", draft_ref: "20261008-demo-b" },
    { date: "2026-10-09", topic: "Planned only", status: "planned" },
    { date: "2026-10-30", topic: "Too far", status: "planned" }],
};
const cloud = {
  posts: [{ post_id: "20261008-demo-b", state: "READY_TO_PUBLISH", text: "Hook B", image: { sha256: "a", bytes: 9 } }],
  consents: [{ consent_id: "c1", post_id: "20261008-demo-b", slot_id: "2026-10-08-thu-0830", slot_utc: "2026-10-08T06:30:00Z", status: "active" }],
  upcoming_slots: [{ slot_id: "2026-10-08-thu-0830", utc: "2026-10-08T06:30:00Z" }, { slot_id: "2026-10-10-sat-1000", utc: "2026-10-10T08:00:00Z" }],
  publications: [], events: [{ at: "2026-10-04T11:00:00Z", event: "consent.created", actor: "owner@example.com",
    post_id: "20261008-demo-b", detail: '{"consent_id":"c1"}' }],
};

test("upcoming merges plan, scheduled consents and free slots for the next days only", () => {
  const up = upcoming({ pipeline, cloud, now: NOW, tz: "Europe/Berlin", days: 7 });
  assert.equal(up.from, "2026-10-05");
  assert.equal(up.days.length, 7);
  const items = up.days.flatMap((d) => d.items);
  const b = items.filter((i) => i.post_id === "20261008-demo-b");
  assert.equal(b.length, 1, "a scheduled post is not listed twice");
  assert.equal(b[0].kind, "scheduled");
  assert.equal(b[0].time, "08:30");
  assert.equal(b[0].type.key, "image");
  const a = items.find((i) => i.post_id === "20261006-demo-a");
  assert.equal(a.approval, "pending");
  const planned = items.find((i) => i.title === "Planned only");
  assert.equal(planned.approval, "no post yet");
  assert.ok(!items.some((i) => i.title === "Too far"));
  assert.deepEqual(items.filter((i) => i.kind === "slot").map((i) => i.slot_id), ["2026-10-10-sat-1000"]);
});

test("timeline and attention", () => {
  const t = timeline({ cloud, pipeline });
  assert.deepEqual(t.map((x) => x.stage), ["scheduled", "approved"]);
  assert.equal(t[0].id, "c1");
  assert.equal(timeline({ cloud, pipeline, postId: "20261006-demo-a" }).length, 0);
  const a = attention({ cloud: { posts: [{ post_id: "x", state: "NEEDS_RECONCILE" }] }, pipeline,
    decisions: [{ status: "refused", action: "approve", post_id: "y", result: "hash changed" }] });
  assert.deepEqual(a.map((x) => x.level), ["err", "warn", "info"]);
});

test("routes", () => {
  assert.deepEqual(parseHash(""), { view: "overview", arg: null });
  assert.deepEqual(parseHash("#post/20261006-demo-a"), { view: "post", arg: "20261006-demo-a" });
  assert.deepEqual(parseHash("#upcoming/14"), { view: "upcoming", arg: "14" });
});

// ── LCE-037 content plan model ──
const { contentPlan, displayStatus, media, humanizationRows, applyFilter } = lib;

test("future planned posts appear before scheduling, distinct from scheduled ones", () => {
  const cp = contentPlan({ pipeline, cloud, now: NOW, tz: "Europe/Berlin", days: 14 });
  const a = cp.items.find((i) => i.post_id === "20261006-demo-a");
  assert.equal(a.status, "AWAITING_APPROVAL");
  assert.equal(a.date, "2026-10-06");
  const b = cp.items.find((i) => i.post_id === "20261008-demo-b");
  assert.equal(b.status, "SCHEDULED");
  assert.equal(b.time, "08:30");
  const planned = cp.items.find((i) => i.title === "Planned only");
  assert.equal(planned.status, "PLANNED");
  assert.equal(planned.note, "No post written yet");
  assert.ok(cp.days.find((d) => d.date === "2026-10-06").items.some((i) => i.post_id === "20261006-demo-a"));
  // a free slot is listed only where nothing occupies it
  const slots = cp.days.flatMap((d) => d.items.filter((i) => i.kind === "slot").map((i) => i.slot_id));
  assert.deepEqual(slots, ["2026-10-10-sat-1000"]);
  assert.equal(cp.counts.SCHEDULED, 1);
  assert.ok(cp.items.some((i) => i.title === "Too far"), "30-day items stay in the plan");
});

test("a free slot on the same day gives a planned post its planned time without hiding it", () => {
  const c2 = { ...cloud, consents: [], upcoming_slots: [{ slot_id: "s6", utc: "2026-10-06T06:30:00Z" }] };
  const cp = contentPlan({ pipeline, cloud: c2, now: NOW, tz: "Europe/Berlin", days: 7 });
  const day = cp.days.find((d) => d.date === "2026-10-06");
  assert.deepEqual(day.items.map((i) => i.kind), ["post"]);
  assert.equal(day.items[0].time, "08:30");
  assert.equal(day.items[0].time_source, "planned slot");
});

test("past planned posts that were never published are surfaced", () => {
  const p2 = { posts: [{ post_id: "20260929-x", state: "APPROVED", text: "Old", plan_date: "2026-09-29" }],
    calendar: [{ date: "2026-09-29", topic: "Old", status: "approved", draft_ref: "20260929-x" }] };
  const cp = contentPlan({ pipeline: p2, cloud: {}, now: NOW, tz: "UTC", days: 7 });
  assert.deepEqual(cp.pastDue.map((i) => [i.post_id, i.status]), [["20260929-x", "APPROVED"]]);
});

test("display statuses", () => {
  assert.equal(displayStatus({ post: { state: "DRAFTED" } }), "PLANNED");
  assert.equal(displayStatus({ post: { state: "NEEDS_REVISION" } }), "NEEDS_REGENERATION");
  assert.equal(displayStatus({ post: { state: "AWAITING_APPROVAL" } }), "AWAITING_APPROVAL");
  assert.equal(displayStatus({ post: { state: "READY_TO_PUBLISH" }, cloudPost: { state: "READY_TO_PUBLISH" } }), "APPROVED");
  assert.equal(displayStatus({ post: { state: "READY_TO_PUBLISH" }, consent: { slot_id: "x" } }), "SCHEDULED");
  assert.equal(displayStatus({ publication: { state: "published" } }), "PUBLISHED");
  assert.equal(displayStatus({ cloudPost: { state: "PUBLISH_FAILED" } }), "FAILED");
  assert.equal(displayStatus({ cloudPost: { state: "NEEDS_RECONCILE" } }), "NEEDS_RECONCILE");
  assert.equal(displayStatus({ planStatus: "skipped" }), "SKIPPED");
  assert.equal(displayStatus({ post: { state: "REJECTED" } }), "REJECTED");
  assert.equal(applyFilter({ kind: "post", status: "SCHEDULED" }, "scheduled"), true);
  assert.equal(applyFilter({ kind: "slot" }, "scheduled"), false);
  assert.equal(applyFilter({ kind: "slot" }, "all"), true);
});

test("media: real preview only when an image is actually stored", () => {
  const post = { post_id: "p1", image: { kind: "diagram", sha256: "s1", file: "image.png" } };
  assert.equal(media(post, {}).preview, "missing");
  assert.equal(media(post, { preview_media: [{ post_id: "p1", sha256: "other" }] }).preview, "missing");
  assert.equal(media(post, { preview_media: [{ post_id: "p1", sha256: "s1" }] }).preview, "available");
  assert.equal(media({ post_id: "p2", image: { kind: "none" } }, {}).type.key, "text");
  assert.equal(media({ post_id: "p3", format: "video" }, {}).type.implemented, false);
});

test("humanization rows claim only what is recorded", () => {
  const voice = { objectives: [{ id: "share-lesson", label: "Share a lesson learned" }] };
  const legacy = humanizationRows({ qa: { status: "passed" } }, voice);
  assert.equal(legacy[0].value, "not recorded");
  assert.equal(legacy.find((r) => r.label === "Objective").value, "not recorded");
  assert.equal(legacy.find((r) => r.label === "Tone & positioning").value, "your review");
  const rec = humanizationRows({ objective: "share-lesson", humanization: { source: "session", voice_version: 2, profile_current: true,
    checklist: { passed: 9, failed: 0, review: 2 } } }, voice);
  assert.equal(rec[0].value, "applied (v2)");
  assert.equal(rec[1].value, "9 passed");
  assert.equal(rec.find((r) => r.label === "Objective").value, "Share a lesson learned");
  const stale = humanizationRows({ humanization: { source: "session", voice_version: 2, profile_current: false, checklist: {} } }, voice);
  assert.equal(stale[0].tone, "warn");
  const edit = humanizationRows({ humanization: { source: "owner_edit", checklist: { failed: 1 } } }, voice);
  assert.equal(edit[0].value, "owner edit");
  assert.equal(edit[1].tone, "err");
});

// ── LCE-038 media record ──
const { mediaRecord, mediaSourceShort } = lib;

test("media record shows real source, rights, size and alt text; nothing invented", () => {
  const post = { post_id: "p", image: { kind: "diagram", media_status: "attached", sha256: "s", width: 1200, height: 1200, bytes: 61440,
    mime: "image/png", alt_text: "Checklist", relation: "restates the post",
    provenance: { origin: "own_creation", usage: "owned", generation: { method: "lce image diagram" } }, decided_by: "agent" } };
  const r = mediaRecord(post);
  assert.equal(r.status, "attached");
  const rows = Object.fromEntries(r.rows.map(([k, v]) => [k, v]));
  assert.equal(rows.Source, "Own creation");
  assert.equal(rows.Size, "1200×1200 px · 60 KB · PNG");
  assert.equal(rows["Alt text"], "Checklist");
  assert.equal(rows.Rights, "owned");
  assert.equal(mediaSourceShort(post), "own diagram");
  const commons = { image: { kind: "source_image", provenance: { origin: "licensed_stock", usage: "licensed", license: "CC BY-SA 4.0",
    credit: "Jane via Wikimedia Commons", source_url: "https://commons.wikimedia.org/wiki/File:X.png" } } };
  const c = Object.fromEntries(mediaRecord(commons).rows.map(([k, v]) => [k, v]));
  assert.equal(c.Rights, "CC BY-SA 4.0 · licensed");
  assert.deepEqual(c["Source URL"], { href: "https://commons.wikimedia.org/wiki/File:X.png" });
  assert.equal(mediaSourceShort(commons), "CC BY-SA 4.0");
});

test("text-only shows its reason; a missing decision says so", () => {
  const t = mediaRecord({ image: { kind: "none", text_only_reason: "text_carries_point", rationale: "one figure" } });
  assert.equal(t.status, "text_only");
  assert.equal(t.rows[0][1], "The text carries the point");
  const legacy = mediaRecord({ post_id: "old" });
  assert.equal(legacy.status, "undecided");
  assert.equal(legacy.statusLabel, "No media decision recorded");
  assert.match(legacy.note, /predates the media stage/);
});
