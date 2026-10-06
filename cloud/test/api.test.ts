import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { handleApi } from "../src/api";
import { resetCertsCache } from "../src/auth";
import { SYNTH, fakeFetch, insertPost, reset, rows, setSettings, TEXT, testEnv } from "./helpers";
import { contentHash } from "../src/text";

const TEAM = "test-team.cloudflareaccess.com";
const NOW = Date.parse("2026-09-30T12:00:00+00:00");
const e = testEnv();
let keys: CryptoKeyPair;
let jwk: JsonWebKey;
const certs = async () => ({ keys: [jwk] });

const b64 = (b: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(b)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
async function token(claims: Record<string, unknown> = {}, key = keys.privateKey, kid = "k1") {
  const h = b64(new TextEncoder().encode(JSON.stringify({ alg: "RS256", kid })));
  const p = b64(new TextEncoder().encode(JSON.stringify({ aud: ["ab".repeat(32)], iss: `https://${TEAM}`,
    exp: Math.floor(NOW / 1000) + 600, email: "owner@example.com", ...claims })));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64(sig)}`;
}
// The owner's typed phrase for each action, added unless a test sets `confirm` itself.
export function phraseFor(method: string, path: string, b: Record<string, unknown>): string | undefined {
  let r: RegExpExecArray | null;
  if (method === "POST" && path === "/consents") return `SCHEDULE ${b.post_id}`;
  if ((r = /^\/posts\/([^/]+)\/(withdraw|rearm|reconcile)$/.exec(path))) return `${r[2].toUpperCase()} ${r[1]}`;
  if (path === "/settings" && b.auto_publish === true) return "ENABLE AUTO-PUBLISH";
  return undefined;
}

async function call(method: string, path: string, bodyObj?: unknown, jwt?: string | null, env = e,
                    extra: Record<string, string> = { "x-lce-client": "cli" }) {
  const headers: Record<string, string> = { "content-type": "application/json", ...(method === "GET" ? {} : extra) };
  if (jwt !== null) headers["cf-access-jwt-assertion"] = jwt ?? await token();
  if (method !== "GET" && method !== "DELETE" && bodyObj === undefined) bodyObj = {};
  if (bodyObj && typeof bodyObj === "object" && !("confirm" in bodyObj)) {
    const phrase = phraseFor(method, path, bodyObj as Record<string, unknown>);
    if (phrase) bodyObj = { ...(bodyObj as Record<string, unknown>), confirm: phrase };
  }
  const res = await handleApi(new Request(`https://lce.example/api${path}`, {
    method, headers, body: bodyObj === undefined ? undefined : JSON.stringify(bodyObj) }), env, NOW, certs);
  return { status: res.status, body: await res.json() as Record<string, unknown> };
}

beforeAll(async () => {
  keys = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
  jwk = { ...(await crypto.subtle.exportKey("jwk", keys.publicKey)) as JsonWebKey, kid: "k1" } as JsonWebKey;
});
beforeEach(async () => { await reset(e); resetCertsCache(); });

describe("authentication (Cloudflare Access)", () => {
  it("health is public and reveals nothing", async () => {
    expect(await call("GET", "/health", undefined, null)).toEqual({ status: 200, body: { ok: true } });
  });
  it("reports a missing schema as 503, not an opaque 500", async () => {
    await e.DB.prepare("ALTER TABLE settings RENAME TO settings_unmigrated").run();
    try {
      const r = await call("GET", "/snapshot");
      expect(r.status).toBe(503);
      expect(String(r.body.error)).toContain("apply cloud/migrations");
    } finally {
      await e.DB.prepare("ALTER TABLE settings_unmigrated RENAME TO settings").run();
    }
  });
  it("fails closed on a malformed Access configuration and never fetches keys from it", async () => {
    let fetched = false;
    const spy = async () => { fetched = true; return { keys: [jwk] }; };
    for (const bad of [{ ACCESS_TEAM_DOMAIN: "evil.example" }, { ACCESS_TEAM_DOMAIN: "x.cloudflareaccess.com/evil" },
      { ACCESS_AUD: "not-a-64-hex-tag" },
      { ACCESS_TEAM_DOMAIN: "", ACCESS_AUD: "", LCE_ACCESS_TEAM_DOMAIN: "evil.example", LCE_ACCESS_AUD: "ab".repeat(32) }]) {
      const res = await handleApi(new Request("https://lce.example/api/snapshot",
        { headers: { "cf-access-jwt-assertion": await token() } }), testEnv(bad), NOW, spy);
      expect(res.status).toBe(503);
    }
    expect(fetched).toBe(false);
  });
  it("accepts Access values with surrounding whitespace (secrets pasted with a newline)", async () => {
    const env = testEnv({ ACCESS_TEAM_DOMAIN: ` ${TEAM}\n`, ACCESS_AUD: `${e.ACCESS_AUD!.toUpperCase()}\n` });
    expect((await call("GET", "/snapshot", undefined, undefined, env)).status).toBe(200);
  });
  it("falls back to the public [vars] pair when the secrets are absent, never mixing them", async () => {
    const vars = { ACCESS_TEAM_DOMAIN: "", ACCESS_AUD: "", LCE_ACCESS_TEAM_DOMAIN: TEAM, LCE_ACCESS_AUD: e.ACCESS_AUD! };
    expect((await call("GET", "/snapshot", undefined, undefined, testEnv(vars))).status).toBe(200);
    // only one secret set: the secret pair is incomplete, so the vars pair is used as a whole
    const half = { ...vars, ACCESS_TEAM_DOMAIN: "other-team.cloudflareaccess.com" };
    expect((await call("GET", "/snapshot", undefined, undefined, testEnv(half))).status).toBe(200);
    // the secret pair wins when complete: a token for the vars pair is then refused
    const secret = { ...vars, ACCESS_TEAM_DOMAIN: "other-team.cloudflareaccess.com", ACCESS_AUD: "cd".repeat(32) };
    expect((await call("GET", "/snapshot", undefined, undefined, testEnv(secret))).status).toBe(401);
    const none = { ...vars, LCE_ACCESS_TEAM_DOMAIN: "", LCE_ACCESS_AUD: "" };
    expect((await call("GET", "/snapshot", undefined, undefined, testEnv(none))).status).toBe(503);
  });
  it("fails closed without Access configuration", async () => {
    const r = await call("GET", "/snapshot", undefined, undefined, testEnv({ ACCESS_AUD: "", LCE_ACCESS_AUD: "" }));
    expect(r.status).toBe(503);
  });
  it.each([
    ["missing", null], ["garbage", "a.b.c"],
  ])("rejects %s token", async (_n, t) => {
    expect((await call("GET", "/snapshot", undefined, t as string | null)).status).toBe(401);
  });
  it("rejects wrong audience, issuer, expiry and signature", async () => {
    const other = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
    for (const t of [await token({ aud: ["other"] }), await token({ iss: "https://evil.example" }),
                     await token({ exp: Math.floor(NOW / 1000) - 1 }), await token({}, other.privateKey)]) {
      expect((await call("GET", "/snapshot", undefined, t)).status).toBe(401);
    }
  });
  it("accepts a valid token", async () => {
    expect((await call("GET", "/snapshot")).status).toBe(200);
  });
});

describe("push, consent, settings, reconcile", () => {
  it("push requires the text to match the approved hash (no approval in the cloud)", async () => {
    const h = await contentHash(TEXT);
    const bad = await call("PUT", "/posts/20261006-demo-post", { text: TEXT + "x", approved_hash: h,
      approved_at: "2026-09-30T10:00:00+00:00" });
    expect(bad.status).toBe(409);
    const ok = await call("PUT", "/posts/20261006-demo-post", { text: TEXT, approved_hash: h,
      approved_at: "2026-09-30T10:00:00+00:00", language: "en" });
    expect(ok).toMatchObject({ status: 200, body: { state: "READY_TO_PUBLISH" } });
    const again = await call("PUT", "/posts/20261006-demo-post", { text: TEXT, approved_hash: h,
      approved_at: "2026-09-30T10:00:00+00:00" });
    expect(again.body).toMatchObject({ unchanged: true });
    expect((await call("PUT", "/posts/bad id", { text: TEXT })).status).toBe(400);
  });

  it("consent: an approved text with a visible image/source label cannot be scheduled (LCE-053)", async () => {
    await setSettings(e, { timezone: SYNTH.timezone, cadence: JSON.stringify(SYNTH.cadence) });
    await insertPost(e, "20261006-demo-post", "A fictional approved post.\n\nImage: Example News\n\n#Demo\n");
    const r = await call("POST", "/consents", { post_id: "20261006-demo-post", slot_id: "2026-10-07-wed-0030" });
    expect(r.status).toBe(409);
    expect(String(r.body?.error ?? "")).toContain("image/source label");
    expect(await rows(e, "SELECT * FROM consents")).toHaveLength(0);
  });
  it("consent: configured future slot, one active per post and per slot, revocable", async () => {
    await setSettings(e, { timezone: SYNTH.timezone, cadence: JSON.stringify(SYNTH.cadence) });
    await insertPost(e);
    await insertPost(e, "20261008-other-post", TEXT + "Other.\n");
    expect((await call("POST", "/consents", { post_id: "20261006-demo-post", slot_id: "2026-10-08-thu-0030" })).status).toBe(422);
    expect((await call("POST", "/consents", { post_id: "20261006-demo-post", slot_id: "2026-09-30-wed-0030" })).status).toBe(409);
    const ok = await call("POST", "/consents", { post_id: "20261006-demo-post", slot_id: "2026-10-07-wed-0030" });
    expect(ok.status).toBe(201);
    expect((ok.body.slot as { utc: string }).utc).toBe("2026-10-07T06:30:00+00:00");
    expect((await call("POST", "/consents", { post_id: "20261006-demo-post", slot_id: "2026-10-09-fri-1545" })).status).toBe(409);
    expect((await call("POST", "/consents", { post_id: "20261008-other-post", slot_id: "2026-10-07-wed-0030" })).status).toBe(409);
    const rev = await call("DELETE", `/consents/${ok.body.consent_id}`);
    expect(rev.body).toMatchObject({ status: "revoked" });
    const [job] = await rows<{ state: string }>(e, "SELECT state FROM jobs");
    expect(job.state).toBe("SKIPPED");
  });

  it("consent refused for a post whose text no longer matches its approval", async () => {
    await setSettings(e, { timezone: SYNTH.timezone, cadence: JSON.stringify(SYNTH.cadence) });
    await insertPost(e);
    await e.DB.prepare("UPDATE posts SET text = 'changed'").run();
    expect((await call("POST", "/consents", { post_id: "20261006-demo-post", slot_id: "2026-10-07-wed-0030" })).status).toBe(409);
  });

  it("settings: kill switch and validation; the token is never a setting", async () => {
    expect((await call("PUT", "/settings", { auto_publish: "yes" })).status).toBe(400);
    expect((await call("PUT", "/settings", { linkedin_token: "x" })).status).toBe(400);
    expect((await call("PUT", "/settings", { api_version: "26-09" })).status).toBe(400);
    expect((await call("PUT", "/settings", { timezone: "Mars/Olympus", cadence: SYNTH.cadence })).status).toBe(422);
    expect((await call("PUT", "/settings", { auto_publish: true, provider: "linkedin_api", ...SYNTH })).status).toBe(200);
    const snap = (await call("GET", "/snapshot")).body as { settings: Record<string, unknown> };
    expect(snap.settings.auto_publish).toBe(true);
    expect(snap.settings.token_present).toBe(true);
    expect(JSON.stringify(snap)).not.toContain("fake.cloud.test.token");
  });

  it("reconcile: owner decides published (URL) or not published", async () => {
    await insertPost(e, "20261006-demo-post", TEXT, "NEEDS_RECONCILE");
    await e.DB.prepare(`INSERT INTO publications (post_id, idempotency_key, state, approved_hash, commentary_hash, api_version, author, updated_at)
      VALUES ('20261006-demo-post', 'k', 'needs_reconcile', 'h', 'c', '202609', 'urn:li:person:x', 'now')`).run();
    expect((await call("POST", "/posts/20261006-demo-post/reconcile", { decision: "published", url: "https://evil.example" })).status).toBe(400);
    const r = await call("POST", "/posts/20261006-demo-post/reconcile", { decision: "published",
      url: "https://www.linkedin.com/feed/update/urn:li:share:77/" });
    expect(r.body).toMatchObject({ state: "PUBLISHED" });
    const [pub] = await rows<{ remote_id: string; verified_by: string }>(e, "SELECT * FROM publications");
    expect([pub.remote_id, pub.verified_by]).toEqual(["urn:li:share:77", "owner"]);
    expect((await call("POST", "/posts/20261006-demo-post/reconcile", { decision: "not_published" })).status).toBe(409);
  });

  it("withdraw and rearm", async () => {
    await insertPost(e, "20261006-demo-post", TEXT, "PUBLISH_FAILED");
    expect((await call("POST", "/posts/20261006-demo-post/rearm")).body).toMatchObject({ state: "READY_TO_PUBLISH" });
    expect((await call("POST", "/posts/20261006-demo-post/withdraw")).body).toMatchObject({ state: "WITHDRAWN" });
    expect((await call("POST", "/posts/20261006-demo-post/rearm")).status).toBe(409);
  });

  it("every mutation is audited with the Access identity", async () => {
    await call("PUT", "/settings", { auto_publish: false });
    const [ev] = await rows<{ event: string; actor: string }>(e, "SELECT * FROM events");
    expect([ev.event, ev.actor]).toEqual(["settings.updated", "owner@example.com"]);
  });
});


describe("mutation safety (CSRF, typed confirmation)", () => {
  it("mutations need the client header and a same-origin request", async () => {
    await insertPost(e, "20261006-demo-post", TEXT, "PUBLISH_FAILED");
    expect((await call("POST", "/posts/20261006-demo-post/rearm", undefined, undefined, e, {})).status).toBe(403);
    expect((await call("POST", "/posts/20261006-demo-post/rearm", undefined, undefined, e,
      { "x-lce-client": "dashboard", origin: "https://evil.example" })).status).toBe(403);
    expect((await call("POST", "/posts/20261006-demo-post/rearm", undefined, undefined, e,
      { "x-lce-client": "dashboard", origin: "https://lce.example" })).status).toBe(200);
    expect((await call("GET", "/snapshot")).status).toBe(200);          // reads need no header
  });

  it("actions that can lead to a publication need the typed phrase", async () => {
    await setSettings(e, { timezone: SYNTH.timezone, cadence: JSON.stringify(SYNTH.cadence) });
    await insertPost(e);
    const wrong = await call("POST", "/consents", { post_id: "20261006-demo-post", slot_id: "2026-10-07-wed-0030", confirm: "yes" });
    expect(wrong.status).toBe(428);
    expect((await call("PUT", "/settings", { auto_publish: true, confirm: "" })).status).toBe(428);
    expect((await call("PUT", "/settings", { auto_publish: false })).status).toBe(200);   // switching off needs no phrase
    for (const action of ["withdraw", "rearm", "reconcile"]) {
      expect((await call("POST", `/posts/20261006-demo-post/${action}`, { confirm: "no" })).status).toBe(428);
    }
    expect(await rows(e, "SELECT * FROM consents")).toHaveLength(0);
  });
});


describe("withdraw and re-delegate", () => {
  it("a withdrawn post can be delegated again (same or new approved version)", async () => {
    const h = await contentHash(TEXT);
    const push = (text: string, hash: string) => call("PUT", "/posts/20261006-demo-post",
      { text, approved_hash: hash, approved_at: "2026-09-30T10:00:00+00:00", language: "en" });
    expect((await push(TEXT, h)).status).toBe(200);
    expect((await call("POST", "/posts/20261006-demo-post/withdraw")).body).toMatchObject({ state: "WITHDRAWN" });
    expect((await push(TEXT, h)).body).toMatchObject({ state: "READY_TO_PUBLISH", redelegated: true });
    await call("POST", "/posts/20261006-demo-post/withdraw");
    const v2 = TEXT + "Revised and approved again.\n";
    expect((await push(v2, await contentHash(v2))).body).toMatchObject({ redelegated: true });
    const [row] = await rows<{ approved_hash: string; state: string }>(e, "SELECT approved_hash, state FROM posts");
    expect([row.state, row.approved_hash]).toEqual(["READY_TO_PUBLISH", await contentHash(v2)]);
    expect((await push(TEXT, h)).status).toBe(409);            // a live post is never replaced
  });
});


describe("pipeline mirror (LCE-013)", () => {
  const snap = (extra: Record<string, unknown> = {}) => ({ schema: 1, meta: { mode: "real",
    generated_at: "2026-09-30T11:00:00+00:00" }, posts: [{ post_id: "20261006-demo-post", state: "AWAITING_APPROVAL" }], ...extra });

  it("is empty until the first sync, then returns the latest snapshot with mirror metadata", async () => {
    const empty = await call("GET", "/pipeline");
    expect(empty.status).toBe(404);
    expect(String(empty.body.error)).toContain("lce cloud sync");
    const put = await call("PUT", "/pipeline", snap());
    expect(put.status).toBe(200);
    expect(put.body.stored).toBe(true);
    await call("PUT", "/pipeline", snap({ posts: [] }));          // replaces, never appends
    expect(await rows(e, "SELECT id FROM pipeline_snapshot")).toHaveLength(1);
    const got = await call("GET", "/pipeline");
    expect(got.status).toBe(200);
    expect(got.body.posts).toEqual([]);
    const meta = got.body.meta as { mode: string; mirror: { received_by: string; received_at: string } };
    expect(meta.mode).toBe("real");
    expect(meta.mirror.received_by).toBe("owner@example.com");
    expect((await rows(e, "SELECT event FROM events WHERE event = 'pipeline.synced'"))).toHaveLength(2);
  });

  it("requires Access and the CLI header, and rejects anything but a real-mode snapshot", async () => {
    expect((await call("GET", "/pipeline", undefined, null)).status).toBe(401);
    expect((await call("PUT", "/pipeline", snap(), null)).status).toBe(401);
    expect((await call("PUT", "/pipeline", snap(), undefined, e, {})).status).toBe(403);
    expect((await call("PUT", "/pipeline", { schema: 1, meta: { mode: "demo" } })).status).toBe(400);
    expect((await call("PUT", "/pipeline", { meta: { mode: "real" } })).status).toBe(400);
    expect((await call("PUT", "/pipeline", [1, 2])).status).toBe(400);
    expect(await rows(e, "SELECT id FROM pipeline_snapshot")).toHaveLength(0);
  });

  it("refuses oversized snapshots", async () => {
    const r = await call("PUT", "/pipeline", snap({ blob: "x".repeat(1_500_001) }));
    expect(r.status).toBe(413);
  });
});

describe("GET /api/linkedin/identity", () => {
  const get = async (env = e, jwt?: string | null, f = fakeFetch(new Response(JSON.stringify({ sub: "TestPerson1" }), { status: 200 }))) => {
    const headers: Record<string, string> = {};
    if (jwt !== null) headers["cf-access-jwt-assertion"] = jwt ?? await token();
    const res = await handleApi(new Request("https://lce.example/api/linkedin/identity", { headers }), env, NOW, certs, f.fn);
    return { status: res.status, body: await res.json() as Record<string, unknown>, calls: f.calls };
  };
  it("requires Cloudflare Access and calls nothing without it", async () => {
    const r = await get(e, null);
    expect(r.status).toBe(401);
    expect(r.calls).toHaveLength(0);
  });
  it("verifies, compares with settings and writes nothing", async () => {
    await setSettings(e, { person_urn: "urn:li:person:TestPerson1", api_version: "202609" });
    const before = await rows(e, "SELECT * FROM events");
    const settingsBefore = await rows(e, "SELECT key, value FROM settings ORDER BY key");
    const r = await get();
    expect(r.status).toBe(200);
    expect(r.body).toMatchObject({ ok: true, person_urn: "urn:li:person:TestPerson1", person_urn_matches: true });
    expect(JSON.stringify(r.body)).not.toContain("fake.cloud.test.token");
    expect(await rows(e, "SELECT * FROM events")).toEqual(before);
    expect(await rows(e, "SELECT key, value FROM settings ORDER BY key")).toEqual(settingsBefore);
  });
  it("answers 503 without a token and 502 when LinkedIn refuses it", async () => {
    expect((await get(testEnv({ LINKEDIN_TOKEN: undefined }))).body).toMatchObject({ status: "token_missing" });
    expect((await get(testEnv({ LINKEDIN_TOKEN: undefined }))).status).toBe(503);
    const r = await get(e, undefined, fakeFetch(new Response("{}", { status: 401 })));
    expect(r.status).toBe(502);
    expect(r.body).toMatchObject({ status: "token_rejected", http_status: 401 });
  });
  it("only GET: other methods are not routed", async () => {
    const res = await handleApi(new Request("https://lce.example/api/linkedin/identity", { method: "POST",
      headers: { "cf-access-jwt-assertion": await token(), "x-lce-client": "cli" } }), e, NOW, certs, fakeFetch().fn);
    expect(res.status).toBe(404);
  });
});
