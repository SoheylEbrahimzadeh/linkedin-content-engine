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
