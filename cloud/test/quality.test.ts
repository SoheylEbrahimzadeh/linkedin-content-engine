// LCE-059: publication-time quality gate (Humanity PASS + Voice Gate PASS for exactly the approved text).
// Deterministic: local D1 (miniflare) and a fake fetch; nothing is ever sent to LinkedIn.
import { beforeEach, describe, expect, it } from "vitest";
import { qualityVerdict } from "../src/quality";
import { publishNow, runScheduled } from "../src/runner";
import { contentHash } from "../src/text";
import { created, enableAll, fakeFetch, insertConsent, insertPost, qualityPass, reset, rows, setSettings, TEXT, testEnv } from "./helpers";

const e = testEnv();
const ID = "20261006-demo-post";
const SLOT = "2026-10-07-wed-0030";
const SLOT_UTC = "2026-10-07T06:30:00+00:00";
const AT = Date.parse("2026-10-07T06:30:00+00:00");
const userinfo = () => new Response(JSON.stringify({ sub: "TestPerson1" }), { status: 200 });
const posts = (f: { calls: { url: string }[] }) => f.calls.filter((c) => c.url.endsWith("/rest/posts")).length;

beforeEach(async () => reset(e));

// Every way the quality result can be unusable. Each must block both paths, before anything is sent.
const OTHER = "A different text than the one approved.\n";
const BLOCKED: [string, (h: string) => Promise<void>, RegExp][] = [
  ["Voice Gate SOURCE_HEAVY", () => qualityPass(e, ID, TEXT, { voice: "SOURCE_HEAVY" }), /Voice Gate is SOURCE_HEAVY/],
  ["Voice Gate FAIL", () => qualityPass(e, ID, TEXT, { voice: "FAIL" }), /Voice Gate is FAIL/],
  ["Voice Gate REVIEW_NEEDED", () => qualityPass(e, ID, TEXT, { voice: "REVIEW_NEEDED" }), /Voice Gate is REVIEW_NEEDED/],
  ["Humanity FAIL", () => qualityPass(e, ID, TEXT, { humanity: "FAIL" }), /Humanity is FAIL/],
  ["Humanity PARTIAL", () => qualityPass(e, ID, TEXT, { humanity: "PARTIAL" }), /Humanity is PARTIAL/],
  ["stale result (scored another text)", () => qualityPass(e, ID, OTHER, { hash: undefined }).then(() => undefined), /different text/],
  ["result without a text hash", () => qualityPass(e, ID, TEXT, { hash: null }), /not tied to a text hash/],
  ["scoring error", () => qualityPass(e, ID, TEXT, { raw: { error: "boom" } }), /could not run/],
  ["no result for the post", () => qualityPass(e, ID, TEXT, { raw: null }), /no quality result/],
  ["post absent from the mirror", () => qualityPass(e, "20261099-other-post"), /not in the pipeline mirror/],
  ["no mirror at all", async () => undefined, /no pipeline mirror/],
];

describe("quality gate: manual Publish now", () => {
  async function ready() {
    await enableAll(e);
    await setSettings(e, { auto_publish: "false" });                 // the owner's model: auto-publish OFF
    return insertPost(e, ID);
  }

  it("PASS + PASS for the approved text publishes (auto-publish OFF)", async () => {
    const h = await ready();
    await qualityPass(e, ID);
    const f = fakeFetch(userinfo(), created("urn:li:share:5901"));
    const r = await publishNow(e, AT, "owner@example.com", ID, h, f.fn);
    expect(r).toMatchObject({ status: "done", outcome: "published", http: 200 });
    expect(posts(f)).toBe(1);
  });

  it.each(BLOCKED)("%s: refused before anything is written or sent", async (_n, seed, why) => {
    const h = await ready();
    await seed(h);
    const f = fakeFetch(userinfo(), created());
    const r = await publishNow(e, AT, "owner@example.com", ID, h, f.fn);
    expect(r.http).toBe(409);
    expect(r.detail).toMatch(why);
    expect(f.calls).toHaveLength(0);                                     // not even the identity check
    expect(await rows(e, "SELECT * FROM consents")).toHaveLength(0);
    expect(await rows(e, "SELECT * FROM publications")).toHaveLength(0);
    expect((await rows<{ state: string }>(e, `SELECT state FROM posts WHERE post_id = '${ID}'`))[0].state).toBe("READY_TO_PUBLISH");
  });

  it("the emergency stop is checked first and still blocks a fully passing post", async () => {
    const h = await ready();
    await qualityPass(e, ID);
    await setSettings(e, { emergency_stop: "true" });
    const f = fakeFetch(userinfo(), created());
    const r = await publishNow(e, AT, "owner@example.com", ID, h, f.fn);
    expect([r.http, r.detail]).toEqual([409, "the emergency stop is on; release it first"]);
    expect(f.calls).toHaveLength(0);
  });

  it("a passing result never overrides the approved-hash gate", async () => {
    const h = await ready();
    await qualityPass(e, ID);
    const r = await publishNow(e, AT, "owner@example.com", ID, await contentHash(OTHER), fakeFetch().fn);
    expect(r.http).toBe(409);
    expect(r.detail).toContain("not the approved text");
    void h;
  });
});

describe("quality gate: scheduled publishing", () => {
  async function armed() {
    await enableAll(e);                                                  // auto-publish ON only to reach the gate
    const h = await insertPost(e, ID);
    await insertConsent(e, ID, SLOT, SLOT_UTC, h);
    return h;
  }

  it("PASS + PASS publishes at the slot", async () => {
    await armed();
    await qualityPass(e, ID);
    const f = fakeFetch(created("urn:li:share:5902"));
    expect(await runScheduled(e, AT, f.fn)).toMatchObject({ status: "done", outcome: "published" });
    expect(posts(f)).toBe(1);
  });

  it.each(BLOCKED)("%s: blocked, the consent is invalidated, nothing is sent", async (_n, seed, why) => {
    const h = await armed();
    await seed(h);
    const f = fakeFetch(created());
    const r = await runScheduled(e, AT, f.fn);
    expect(r.status).toBe("blocked");
    expect(r.detail).toMatch(why);
    expect(f.calls).toHaveLength(0);
    const [c] = await rows<{ status: string; reason: string }>(e, "SELECT status, reason FROM consents");
    expect(c.status).toBe("invalid");
    expect(c.reason).toMatch(/^quality: /);
    expect(await rows(e, "SELECT * FROM publications")).toHaveLength(0);
  });

  it("auto-publish OFF: nothing is published or written, whatever the quality", async () => {
    await armed();
    await qualityPass(e, ID);
    await setSettings(e, { auto_publish: "false" });
    const f = fakeFetch(created());
    expect((await runScheduled(e, AT, f.fn)).status).toBe("kill_switch_off");
    expect(f.calls).toHaveLength(0);
    expect((await rows<{ status: string }>(e, "SELECT status FROM consents"))[0].status).toBe("active");
  });

  it("the emergency stop blocks scheduled publishing of a fully passing post", async () => {
    await armed();
    await qualityPass(e, ID);
    await setSettings(e, { emergency_stop: "true" });
    const f = fakeFetch(created());
    expect((await runScheduled(e, AT, f.fn)).status).toBe("emergency_stop");
    expect(f.calls).toHaveLength(0);
  });
});

describe("quality rule (pure)", () => {
  const H = "a".repeat(64);
  it.each([
    [{ content_hash: H, verdict: "PASS", voice_gate: { verdict: "PASS" } }, true],
    [{ content_hash: H, verdict: "PASS", voice_gate: { verdict: "SOURCE_HEAVY" } }, false],
    [{ content_hash: H, verdict: "PASS", voice_gate: null }, false],
    [{ content_hash: H, verdict: "PASS" }, false],
    [{ content_hash: H.toUpperCase(), verdict: "PASS", voice_gate: { verdict: "PASS" } }, false],
    [{ content_hash: "b".repeat(64), verdict: "PASS", voice_gate: { verdict: "PASS" } }, false],
    [{ verdict: "PASS", voice_gate: { verdict: "PASS" } }, false],
    [{ content_hash: H, verdict: "pass", voice_gate: { verdict: "PASS" } }, false],
    [{ content_hash: H, verdict: "PASS", voice_gate: { verdict: "PASS" }, error: "x" }, false],
    [null, false],
    [undefined, false],
  ])("%j → ok=%s", (q, ok) => {
    expect(qualityVerdict(q as never, H).ok).toBe(ok);
  });
  it("an invalid approved hash never passes", () => {
    expect(qualityVerdict({ content_hash: "", verdict: "PASS", voice_gate: { verdict: "PASS" } }, "").ok).toBe(false);
  });
});

// LCE-060: Publish now on a post that already has a scheduled time (owner's explicit replace_schedule).
describe("Publish now on a scheduled post", () => {
  async function scheduledPost() {
    await enableAll(e);
    await setSettings(e, { auto_publish: "false" });
    const h = await insertPost(e, ID);
    await insertConsent(e, ID, SLOT, SLOT_UTC, h, "consent-sched");
    return h;
  }
  const consents = () => rows<{ consent_id: string; status: string; reason: string | null }>(e, "SELECT consent_id, status, reason FROM consents ORDER BY created_at, consent_id");

  it("without replace_schedule it is refused and nothing changes", async () => {
    const h = await scheduledPost();
    await qualityPass(e, ID);
    const f = fakeFetch(userinfo(), created());
    const r = await publishNow(e, AT - 86_400_000, "owner@example.com", ID, h, f.fn);
    expect([r.http, r.detail]).toEqual([409, "this post is already scheduled; revoke that consent first"]);
    expect(f.calls).toHaveLength(0);
    expect((await consents()).map((c) => c.status)).toEqual(["active"]);
  });

  it("with replace_schedule it publishes once now, the schedule is revoked, and the slot later sends nothing", async () => {
    const h = await scheduledPost();
    await qualityPass(e, ID);
    const f = fakeFetch(userinfo(), created("urn:li:share:6001"));
    const r = await publishNow(e, AT - 86_400_000, "owner@example.com", ID, h, f.fn, { replaceSchedule: true });   // a day early
    expect(r).toMatchObject({ status: "done", outcome: "published", http: 200 });
    expect(posts(f)).toBe(1);
    const cs = await consents();
    expect(cs.find((c) => c.consent_id === "consent-sched")).toMatchObject({ status: "revoked", reason: "replaced_by_publish_now" });
    expect(cs.filter((c) => c.status === "active")).toHaveLength(0);
    await setSettings(e, { auto_publish: "true" });                       // even if auto-publish were on at the slot
    const later = fakeFetch(created());
    expect((await runScheduled(e, AT, later.fn)).status).toBe("idle");
    expect(later.calls).toHaveLength(0);
    expect((await rows<{ state: string }>(e, "SELECT state FROM publications")).map((x) => x.state)).toEqual(["published"]);
  });

  it.each([
    ["SOURCE_HEAVY", () => qualityPass(e, ID, TEXT, { voice: "SOURCE_HEAVY" })],
    ["no quality result", async () => undefined],
  ])("quality gate (%s) still refuses, and the schedule is left untouched", async (_n, seed) => {
    const h = await scheduledPost();
    await seed();
    const f = fakeFetch(userinfo(), created());
    const r = await publishNow(e, AT - 86_400_000, "owner@example.com", ID, h, f.fn, { replaceSchedule: true });
    expect(r.http).toBe(409);
    expect(r.detail).toMatch(/quality gate/);
    expect(f.calls).toHaveLength(0);
    expect((await consents()).map((c) => [c.consent_id, c.status])).toEqual([["consent-sched", "active"]]);
  });

  it("the emergency stop still refuses, and the schedule is left untouched", async () => {
    const h = await scheduledPost();
    await qualityPass(e, ID);
    await setSettings(e, { emergency_stop: "true" });
    const f = fakeFetch(userinfo(), created());
    const r = await publishNow(e, AT - 86_400_000, "owner@example.com", ID, h, f.fn, { replaceSchedule: true });
    expect(r.http).toBe(409);
    expect(f.calls).toHaveLength(0);
    expect((await consents()).map((c) => c.status)).toEqual(["active"]);
  });

  it("a wrong approved hash is refused even with replace_schedule", async () => {
    await scheduledPost();
    await qualityPass(e, ID);
    const r = await publishNow(e, AT - 86_400_000, "owner@example.com", ID, await contentHash(OTHER), fakeFetch().fn, { replaceSchedule: true });
    expect(r.http).toBe(409);
    expect((await consents()).map((c) => c.status)).toEqual(["active"]);
  });

  it("SIMULATED race: the schedule changed between the check and the batch; the manual consent is withdrawn, nothing is sent", async () => {
    const h = await scheduledPost();
    await qualityPass(e, ID);
    const db = e.DB;
    const lying = new Proxy(db, { get(t, k) {
      if (k === "batch") return async (stmts: D1PreparedStatement[]) => {
        const res = await db.batch(stmts);
        return stmts.length > 3 ? res.map((x, i) => (i === 0 ? { ...x, meta: { ...x.meta, changes: 0 } } : x)) : res;
      };
      const v = (t as never)[k]; return typeof v === "function" ? (v as Function).bind(t) : v;
    } });
    const f = fakeFetch(userinfo(), created());
    const r = await publishNow({ ...e, DB: lying as typeof db }, AT - 86_400_000, "owner@example.com", ID, h, f.fn, { replaceSchedule: true });
    expect(r.http).toBe(409);
    expect(r.detail).toContain("changed while this request ran");
    expect(posts(f)).toBe(0);
    expect((await consents()).filter((c) => c.status === "active")).toHaveLength(0);
    expect(await rows(e, "SELECT * FROM publications")).toHaveLength(0);
  });
});

// LCE-060: EVERY rejection path of Publish now with replace_schedule leaves the original schedule untouched:
// the scheduled consent stays active, its job stays scheduled, no manual consent, no publication, nothing sent.
describe("Publish now on a scheduled post: every rejection keeps the schedule", () => {
  const LONG = "A fictional approved post.\n\n" + "word ".repeat(700) + "\n";
  const LABEL = "A fictional approved post.\n\nImage: Example News\n\n#Demo\n";
  async function scheduledPost(text = TEXT) {
    await enableAll(e);
    await setSettings(e, { auto_publish: "false" });
    const h = await insertPost(e, ID, text);
    await qualityPass(e, ID, text);
    await insertConsent(e, ID, SLOT, SLOT_UTC, h, "consent-sched");
    return h;
  }
  type Case = [string, { text?: string; setup?: (h: string) => Promise<void>; env?: Partial<typeof e>; hash?: () => Promise<string>;
    fetches?: () => Response[] }, RegExp];
  const CASES: Case[] = [
    ["emergency stop", { setup: () => setSettings(e, { emergency_stop: "true" }) }, /emergency stop/],
    ["provider not enabled", { setup: () => setSettings(e, { provider: "none" }) }, /provider/],
    ["LinkedIn config incomplete", { setup: () => setSettings(e, { person_urn: "" }) }, /config/],
    ["token missing", { env: { LINKEDIN_TOKEN: "" } }, /token_missing/],
    ["token expired", { setup: () => setSettings(e, { token_expires_at: "2026-01-01T00:00:00+00:00" }) }, /token_expired/],
    ["post not ready", { setup: async () => { await e.DB.prepare("UPDATE posts SET state = 'PUBLISH_FAILED'").run(); } }, /PUBLISH_FAILED/],
    ["confirmed hash is not the approved one", { hash: () => contentHash(OTHER) }, /not the approved text/],
    ["stored text no longer matches its hash", { setup: async () => { await e.DB.prepare("UPDATE posts SET text = text || 'x'").run(); } }, /hash_mismatch/],
    ["text too long", { text: LONG }, /text_too_long/],
    ["visible image/source label", { text: LABEL }, /visible_image_credit/],
    ["stored image does not match its hash", { setup: async () => {
      await e.DB.prepare("INSERT INTO post_images (post_id, data, sha256, alt_text, bytes) VALUES (?, ?, ?, 'x', 4)")
        .bind(ID, new Uint8Array([1, 2, 3, 4]), "0".repeat(64)).run(); } }, /image_hash_mismatch/],
    ["quality SOURCE_HEAVY", { setup: () => qualityPass(e, ID, TEXT, { voice: "SOURCE_HEAVY" }) }, /SOURCE_HEAVY/],
    ["quality result missing", { setup: async () => { await e.DB.prepare("DELETE FROM pipeline_snapshot").run(); } }, /no pipeline mirror/],
    ["LinkedIn identity not verified", { fetches: () => [new Response("{}", { status: 401 })] }, /identity not verified/],
    ["token belongs to another member", { fetches: () => [new Response(JSON.stringify({ sub: "Someone" }), { status: 200 })] }, /not the configured person_urn/],
  ];

  it.each(CASES)("%s", async (_n, c, why) => {
    const h = await scheduledPost(c.text);
    await c.setup?.(h);
    const before = await rows(e, "SELECT * FROM consents ORDER BY consent_id");
    const jobsBefore = await rows(e, "SELECT * FROM jobs ORDER BY slot_id");
    const f = fakeFetch(...(c.fetches ? c.fetches() : [userinfo()]), created());
    const r = await publishNow({ ...e, ...c.env }, AT - 86_400_000, "owner@example.com", ID, c.hash ? await c.hash() : h, f.fn, { replaceSchedule: true });
    expect(r.http).toBeGreaterThanOrEqual(400);
    expect(r.detail).toMatch(why);
    expect(posts(f)).toBe(0);
    expect(f.calls.filter((x) => !x.url.endsWith("/v2/userinfo"))).toHaveLength(0);     // at most the identity check
    expect(await rows(e, "SELECT * FROM consents ORDER BY consent_id")).toEqual(before);   // schedule untouched
    expect(await rows(e, "SELECT * FROM jobs ORDER BY slot_id")).toEqual(jobsBefore);
    expect(await rows(e, "SELECT * FROM publications")).toHaveLength(0);
  });
});
