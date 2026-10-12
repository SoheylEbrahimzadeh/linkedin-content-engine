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
