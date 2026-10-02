// LCE-036: decision inbox, controlled manual publish, emergency stop, image route.
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { handleApi } from "../src/api";
import { resetCertsCache } from "../src/auth";
import { runScheduled } from "../src/runner";
import { contentHash } from "../src/text";
import { created, enableAll, fakeFetch, insertConsent, insertPost, reset, rows, setSettings, TEXT, testEnv } from "./helpers";

const TEAM = "test-team.cloudflareaccess.com";
const NOW = Date.parse("2026-10-05T12:00:00+00:00");
const e = testEnv();
let keys: CryptoKeyPair;
let jwk: JsonWebKey;
const certs = async () => ({ keys: [jwk] });
const b64 = (b: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(b)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
async function jwt(claims: Record<string, unknown>) {
  const h = b64(new TextEncoder().encode(JSON.stringify({ alg: "RS256", kid: "k1" })));
  const p = b64(new TextEncoder().encode(JSON.stringify({ aud: ["ab".repeat(32)], iss: `https://${TEAM}`,
    exp: Math.floor(NOW / 1000) + 600, ...claims })));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", keys.privateKey, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64(sig)}`;
}
const OWNER = { email: "owner@example.com" };
const SERVICE = { common_name: "0123456789abcdef0123456789abcdef.access" };

async function call(method: string, path: string, body?: unknown, who: Record<string, unknown> = OWNER,
                    f = fakeFetch(), client = "dashboard") {
  const headers: Record<string, string> = { "cf-access-jwt-assertion": await jwt(who), "content-type": "application/json" };
  if (method !== "GET") headers["x-lce-client"] = client;
  const res = await handleApi(new Request(`https://lce.example/api${path}`, { method, headers,
    body: body === undefined ? undefined : JSON.stringify(body) }), e, NOW, certs, f.fn);
  const type = res.headers.get("content-type") ?? "";
  return { status: res.status, body: type.startsWith("application/json") ? await res.json() as Record<string, any> : null,
    res, calls: f.calls };
}

// Synthetic pipeline mirror (fictional posts only).
const AWAITING = "20261006-demo-awaiting";
async function mirror(posts: Record<string, unknown>[], calendar: Record<string, unknown>[] = []) {
  const body = JSON.stringify({ schema: 1, meta: { mode: "real" }, posts, calendar });
  await e.DB.prepare(`INSERT INTO pipeline_snapshot (id, body, sha256, bytes, generated_at, received_at, received_by)
    VALUES (1, ?, 'x', ?, NULL, '2026-10-05T11:00:00+00:00', 'test')
    ON CONFLICT(id) DO UPDATE SET body = excluded.body`).bind(body, body.length).run();
}
async function awaitingMirror(extra: Record<string, unknown> = {}) {
  const h = await contentHash(TEXT);
  await mirror([{ post_id: AWAITING, state: "AWAITING_APPROVAL", text: TEXT, actual_hash: h, plan_date: "2026-10-06",
    image: null, ...extra }], [{ date: "2026-10-09", topic: "Planned topic", status: "planned" }]);
  return h;
}

beforeAll(async () => {
  keys = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
  jwk = { ...(await crypto.subtle.exportKey("jwk", keys.publicKey)) as JsonWebKey, kid: "k1" } as JsonWebKey;
});
beforeEach(async () => {
  await reset(e);
  await e.DB.batch(["decisions", "pipeline_snapshot"].map((t) => e.DB.prepare(`DELETE FROM ${t}`)));
  resetCertsCache();
});

describe("decision inbox", () => {
  it("records an approval bound to the reviewed text, only from a person with the phrase", async () => {
    const h = await awaitingMirror();
    expect((await call("POST", "/decisions", { action: "approve", post_id: AWAITING, content_hash: h,
      confirm: `APPROVE ${AWAITING}` }, SERVICE)).status).toBe(403);
    expect((await call("POST", "/decisions", { action: "approve", post_id: AWAITING, content_hash: h })).status).toBe(428);
    const stale = await call("POST", "/decisions", { action: "approve", post_id: AWAITING, content_hash: "0".repeat(64),
      confirm: `APPROVE ${AWAITING}` });
    expect(stale.status).toBe(409);
    const ok = await call("POST", "/decisions", { action: "approve", post_id: AWAITING, content_hash: h,
      confirm: `APPROVE ${AWAITING}` });
    expect(ok.status).toBe(201);
    const [d] = await rows<Record<string, string>>(e, "SELECT * FROM decisions");
    expect(d).toMatchObject({ action: "approve", status: "pending", content_hash: h, created_by: "owner@example.com" });
    // Nothing else changes: no cloud post, no consent, auto-publish untouched.
    expect(await rows(e, "SELECT * FROM posts")).toEqual([]);
    expect(await rows(e, "SELECT * FROM consents")).toEqual([]);
  });
  it("refuses approval of a post that is not awaiting approval", async () => {
    const h = await awaitingMirror({ state: "HUMANIZED" });
    const r = await call("POST", "/decisions", { action: "approve", post_id: AWAITING, content_hash: h, confirm: `APPROVE ${AWAITING}` });
    expect(r.status).toBe(409);
  });
  it("a newer decision supersedes the pending one for the same post", async () => {
    const h = await awaitingMirror();
    await call("POST", "/decisions", { action: "regenerate", post_id: AWAITING, reason: "sharper hook" });
    const r = await call("POST", "/decisions", { action: "edit", post_id: AWAITING, base_hash: h, text: "A new fictional text." });
    expect(r.status).toBe(201);
    const ds = await rows<Record<string, string>>(e, "SELECT action, status FROM decisions ORDER BY created_at, action");
    expect(ds.map((d) => `${d.action}:${d.status}`).sort()).toEqual(["edit:pending", "regenerate:superseded"]);
  });
  it("validates reject, edit, reschedule, skip and duplicate", async () => {
    const h = await awaitingMirror();
    expect((await call("POST", "/decisions", { action: "reject", post_id: AWAITING, confirm: `REJECT ${AWAITING}` })).status).toBe(400);
    expect((await call("POST", "/decisions", { action: "reject", post_id: AWAITING, reason: "off-topic" })).status).toBe(428);
    expect((await call("POST", "/decisions", { action: "edit", post_id: AWAITING, base_hash: "f".repeat(64), text: "x" })).status).toBe(409);
    expect((await call("POST", "/decisions", { action: "edit", post_id: AWAITING, base_hash: h, text: TEXT })).status).toBe(400);
    expect((await call("POST", "/decisions", { action: "edit", post_id: AWAITING, base_hash: h, text: "x".repeat(3001) })).status).toBe(400);
    expect((await call("POST", "/decisions", { action: "reschedule", post_id: AWAITING, date: "2026-01-01" })).status).toBe(400);
    expect((await call("POST", "/decisions", { action: "reschedule", post_id: AWAITING, date: "2026-10-12" })).status).toBe(201);
    expect((await call("POST", "/decisions", { action: "skip", plan_date: "2026-10-09" })).status).toBe(201);
    expect((await call("POST", "/decisions", { action: "skip", plan_date: "2026-10-10" })).status).toBe(404);
    expect((await call("POST", "/decisions", { action: "duplicate", post_id: AWAITING, date: "2026-10-20" })).status).toBe(201);
    expect((await call("POST", "/decisions", { action: "publish", post_id: AWAITING })).status).toBe(400);
  });
  it("refuses edits of a post already delegated to the cloud publisher", async () => {
    const h = await awaitingMirror({ state: "READY_TO_PUBLISH" });
    await insertPost(e, AWAITING);
    const r = await call("POST", "/decisions", { action: "edit", post_id: AWAITING, base_hash: h, text: "Changed fictional text." });
    expect(r.status).toBe(409);
  });
  it("refresh (LCE-041) is only recorded: note optional, bound to the reviewed text; never for queued posts", async () => {
    const h = await awaitingMirror();
    const r = await call("POST", "/decisions", { action: "refresh", post_id: AWAITING, note: "the image repeats the text" });
    expect(r.status).toBe(201);
    const [d] = await rows<Record<string, string>>(e, "SELECT * FROM decisions");
    expect(d).toMatchObject({ action: "refresh", status: "pending", created_by: "owner@example.com" });
    expect(JSON.parse(d.payload)).toEqual({ note: "the image repeats the text", base_hash: h });
    expect(await rows(e, "SELECT * FROM posts")).toEqual([]);           // nothing else changes
    expect(await rows(e, "SELECT * FROM consents")).toEqual([]);
    expect((await call("POST", "/decisions", { action: "refresh", post_id: AWAITING }, SERVICE)).status).toBe(403);
    await awaitingMirror({ state: "READY_TO_PUBLISH" });
    await insertPost(e, AWAITING);
    expect((await call("POST", "/decisions", { action: "refresh", post_id: AWAITING })).status).toBe(409);
  });
  it("whoami reports the Access session (subject, person or service, expiry) for the Control Center", async () => {
    const r = await call("GET", "/whoami");
    expect(r.status).toBe(200);
    expect(r.body).toMatchObject({ subject: "owner@example.com", human: true });
    expect(typeof r.body!.session_expires_at).toBe("number");
    expect((await call("GET", "/whoami", undefined, SERVICE)).body).toMatchObject({ human: false });
  });
  it("a retried action with the same request_id returns the first decision, never a second one (LCE-042)", async () => {
    await awaitingMirror();
    const body = { action: "refresh", post_id: AWAITING, note: "x", request_id: "req-0123456789" };
    const first = await call("POST", "/decisions", body);
    expect(first.status).toBe(201);
    const again = await call("POST", "/decisions", body);
    expect(again.status).toBe(200);
    expect(again.body).toMatchObject({ decision_id: first.body!.decision_id, replayed: true, status: "pending" });
    expect(await rows(e, "SELECT * FROM decisions")).toHaveLength(1);
    expect((await call("POST", "/decisions", { ...body, request_id: "bad id!" })).status).toBe(400);
    expect((await call("POST", "/decisions", body, { email: "other@example.com" })).status).toBe(409);
  });
  it("records which Access sessions reach the Worker and what the browser reports (LCE-042)", async () => {
    await awaitingMirror();
    const iat = Math.floor(NOW / 1000) - 300;
    const who = { email: "owner@example.com", iat };
    await call("GET", "/whoami", undefined, who);
    await call("POST", "/decisions", { action: "refresh", post_id: AWAITING }, who);
    const [sess] = await rows<Record<string, unknown>>(e, "SELECT * FROM access_sessions");
    expect(sess).toMatchObject({ subject: "owner@example.com", issued_at: iat, expires_at: Math.floor(NOW / 1000) + 600, requests: 2 });
    expect(sess.last_get).toBeTruthy();
    expect(sess.last_mutation).toBeTruthy();
    const ok = await call("POST", "/client-report", { kind: "access_redirect", detail: { method: "POST", path: "/api/decisions", status: 0 } });
    expect(ok.status).toBe(201);
    expect((await call("POST", "/client-report", { kind: "whatever", detail: {} })).status).toBe(400);
    expect((await call("POST", "/client-report", { kind: "access_redirect", detail: { c: "CF_Authorization=abc" } })).status).toBe(400);
    const snap = await call("GET", "/snapshot");
    expect(snap.body!.client_reports).toMatchObject([{ kind: "access_redirect", detail: { method: "POST", status: 0 } }]);
    expect(snap.body!.access_sessions[0]).toMatchObject({ subject: "owner@example.com", issued_at: iat });
    expect((await rows(e, "SELECT * FROM access_sessions WHERE subject LIKE '%.access'"))).toEqual([]);   // service tokens not recorded
  });
  it("refuses to refresh a published post", async () => {
    await awaitingMirror({ state: "PUBLISHED" });
    expect((await call("POST", "/decisions", { action: "refresh", post_id: AWAITING })).status).toBe(409);
  });
  it("only the CLI resolves; a person may cancel", async () => {
    await awaitingMirror();
    const { body } = await call("POST", "/decisions", { action: "regenerate", post_id: AWAITING, reason: "more concrete" });
    const id = body!.decision_id;
    expect((await call("POST", `/decisions/${id}/resolve`, { status: "applied" }, SERVICE, fakeFetch(), "dashboard")).status).toBe(403);
    const r = await call("POST", `/decisions/${id}/resolve`, { status: "applied", result: "marked" }, SERVICE, fakeFetch(), "cli");
    expect(r.status).toBe(200);
    expect((await call("POST", `/decisions/${id}/resolve`, { status: "applied" }, SERVICE, fakeFetch(), "cli")).status).toBe(409);
    const { body: b2 } = await call("POST", "/decisions", { action: "regenerate", post_id: AWAITING, reason: "again" });
    expect((await call("DELETE", `/decisions/${b2!.decision_id}`, undefined, SERVICE)).status).toBe(403);
    expect((await call("DELETE", `/decisions/${b2!.decision_id}`)).status).toBe(200);
    const list = await call("GET", "/decisions?status=pending", undefined, SERVICE);
    expect(list.body!.decisions).toEqual([]);
  });
});

const userinfo = (sub = "TestPerson1") => new Response(JSON.stringify({ sub }), { status: 200 });
const READY = "20261006-demo-post";

describe("controlled manual publish", () => {
  async function ready() {
    await enableAll(e);
    await setSettings(e, { auto_publish: "false" });
    return insertPost(e, READY);
  }
  it("publishes one approved post now with auto-publish OFF, after verifying the author", async () => {
    const h = await ready();
    const f = fakeFetch(userinfo(), created("urn:li:share:7001"));
    const r = await call("POST", `/posts/${READY}/publish-now`, { approved_hash: h, confirm: `PUBLISH NOW ${READY}` }, OWNER, f);
    expect(r.status).toBe(200);
    expect(r.body).toMatchObject({ status: "done", outcome: "published",
      publication: { state: "published", remote_id: "urn:li:share:7001" } });
    expect(f.calls.map((c) => c.url)).toEqual(["https://api.linkedin.com/v2/userinfo", "https://api.linkedin.com/rest/posts"]);
    expect(JSON.parse(String(f.calls[1].init.body)).author).toBe("urn:li:person:TestPerson1");
    const [s] = await rows<{ value: string }>(e, "SELECT value FROM settings WHERE key = 'auto_publish'");
    expect(s.value).toBe("false");
    const evs = await rows<{ event: string; actor: string }>(e, "SELECT event, actor FROM events ORDER BY id");
    expect(evs.map((x) => x.event)).toEqual(["publish.manual_requested", "publish.intent", "publish.published"]);
    expect(new Set(evs.map((x) => x.actor))).toEqual(new Set(["owner@example.com"]));
    expect((await rows<{ status: string }>(e, "SELECT status FROM consents"))[0].status).toBe("consumed");
  });
  it("needs a person, the phrase and the approved hash", async () => {
    const h = await ready();
    expect((await call("POST", `/posts/${READY}/publish-now`, { approved_hash: h, confirm: `PUBLISH NOW ${READY}` }, SERVICE)).status).toBe(403);
    expect((await call("POST", `/posts/${READY}/publish-now`, { approved_hash: h })).status).toBe(428);
    const r = await call("POST", `/posts/${READY}/publish-now`, { approved_hash: "0".repeat(64), confirm: `PUBLISH NOW ${READY}` });
    expect(r.status).toBe(409);
    expect(r.calls).toHaveLength(0);
  });
  it("sends nothing when the token belongs to another member", async () => {
    const h = await ready();
    const f = fakeFetch(userinfo("SomeoneElse"));
    const r = await call("POST", `/posts/${READY}/publish-now`, { approved_hash: h, confirm: `PUBLISH NOW ${READY}` }, OWNER, f);
    expect(r.status).toBe(409);
    expect(f.calls).toHaveLength(1);
    expect(await rows(e, "SELECT * FROM publications")).toEqual([]);
  });
  it("is blocked by the emergency stop and by an existing scheduled consent", async () => {
    const h = await ready();
    await setSettings(e, { emergency_stop: "true" });
    let r = await call("POST", `/posts/${READY}/publish-now`, { approved_hash: h, confirm: `PUBLISH NOW ${READY}` });
    expect(r.status).toBe(409);
    expect(r.body!.detail).toContain("emergency stop");
    await setSettings(e, { emergency_stop: "false" });
    await insertConsent(e, READY, "2026-10-07-wed-0030", "2026-10-07T00:30:00+00:00", h);
    r = await call("POST", `/posts/${READY}/publish-now`, { approved_hash: h, confirm: `PUBLISH NOW ${READY}` });
    expect(r.status).toBe(409);
    expect(r.calls).toHaveLength(0);
  });
});

describe("emergency stop", () => {
  it("blocks scheduled publishing even with auto-publish on", async () => {
    await enableAll(e);
    await setSettings(e, { emergency_stop: "true" });
    const h = await insertPost(e, READY);
    await insertConsent(e, READY, "2026-10-05-mon-1100", "2026-10-05T11:00:00+00:00", h);
    const f = fakeFetch();
    expect((await runScheduled(e, NOW, f.fn)).status).toBe("emergency_stop");
    expect(f.calls).toHaveLength(0);
  });
  it("turning it on is free; releasing needs a person and the phrase", async () => {
    expect((await call("PUT", "/settings", { emergency_stop: true }, SERVICE, fakeFetch(), "cli")).status).toBe(200);
    expect((await call("PUT", "/settings", { emergency_stop: false, confirm: "RELEASE EMERGENCY STOP" }, SERVICE, fakeFetch(), "cli")).status).toBe(403);
    expect((await call("PUT", "/settings", { emergency_stop: false })).status).toBe(428);
    expect((await call("PUT", "/settings", { emergency_stop: false, confirm: "RELEASE EMERGENCY STOP" })).status).toBe(200);
  });
  it("auto-publish can only be enabled by a person", async () => {
    expect((await call("PUT", "/settings", { auto_publish: true, confirm: "ENABLE AUTO-PUBLISH" }, SERVICE, fakeFetch(), "cli")).status).toBe(403);
  });
  it("validates the profile display settings", async () => {
    expect((await call("PUT", "/settings", { profile_url: "https://www.linkedin.com/company/x/" }, SERVICE, fakeFetch(), "cli")).status).toBe(400);
    expect((await call("PUT", "/settings", { display_name: "Test Person", profile_url: "https://www.linkedin.com/in/test-person/" },
      SERVICE, fakeFetch(), "cli")).status).toBe(200);
  });
});

describe("post image", () => {
  it("serves a pushed post's image behind Access", async () => {
    await insertPost(e, READY);
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);
    await e.DB.prepare("INSERT INTO post_images (post_id, data, sha256, bytes, alt_text) VALUES (?, ?, 'x', ?, 'alt')")
      .bind(READY, png, png.length).run();
    const r = await call("GET", `/posts/${READY}/image`);
    expect(r.status).toBe(200);
    expect(r.res.headers.get("content-type")).toBe("image/png");
    expect(new Uint8Array(await r.res.arrayBuffer())).toEqual(png);
    expect((await call("GET", "/posts/20261006-none/image")).status).toBe(404);
  });
});

describe("preview media (LCE-037)", () => {
  const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 9, 8, 7, 6]);
  const b64png = btoa(String.fromCharCode(...png));
  async function sha(d: Uint8Array) {
    return [...new Uint8Array(await crypto.subtle.digest("SHA-256", d))].map((x) => x.toString(16).padStart(2, "0")).join("");
  }
  it("is uploaded by the CLI only, sha-checked, and served for the preview", async () => {
    const h = await sha(png);
    const body = { data_base64: b64png, sha256: h, alt_text: "A fictional diagram" };
    expect((await call("PUT", "/preview-media/20261006-demo-awaiting", body, SERVICE, fakeFetch(), "dashboard")).status).toBe(403);
    expect((await call("PUT", "/preview-media/20261006-demo-awaiting", { ...body, sha256: "0".repeat(64) }, SERVICE, fakeFetch(), "cli")).status).toBe(409);
    const ok = await call("PUT", "/preview-media/20261006-demo-awaiting", body, SERVICE, fakeFetch(), "cli");
    expect(ok.status).toBe(200);
    expect((await call("GET", "/preview-media", undefined, SERVICE)).body!.media).toMatchObject([{ post_id: "20261006-demo-awaiting", sha256: h, mime: "image/png" }]);
    const img = await call("GET", "/posts/20261006-demo-awaiting/image");
    expect(img.status).toBe(200);
    expect(img.res.headers.get("content-type")).toBe("image/png");
    expect(img.res.headers.get("content-disposition")).toBe("inline");
    expect(img.res.headers.get("cache-control")).toBe("private, no-cache");
    expect(img.res.headers.get("etag")).toBe(`"${h}"`);
    expect(new Uint8Array(await img.res.arrayBuffer())).toEqual(png);
    // revalidation by content hash
    const again = await handleApi(new Request("https://lce.example/api/posts/20261006-demo-awaiting/image",
      { headers: { "cf-access-jwt-assertion": await jwt(OWNER), "if-none-match": `"${h}"` } }), e, NOW, certs, fakeFetch().fn);
    expect(again.status).toBe(304);
    const snap = await call("GET", "/snapshot");
    expect(snap.body!.preview_media).toHaveLength(1);
  });
  it("the approved publish-queue image wins over the preview copy", async () => {
    await insertPost(e, READY);
    const queued = new Uint8Array([0xff, 0xd8, 0xff, 1]);
    await e.DB.prepare("INSERT INTO post_images (post_id, data, sha256, bytes, alt_text) VALUES (?, ?, 'x', 4, 'alt')").bind(READY, queued).run();
    await call("PUT", `/preview-media/${READY}`, { data_base64: b64png, sha256: await sha(png), alt_text: "x" }, SERVICE, fakeFetch(), "cli");
    const img = await call("GET", `/posts/${READY}/image`);
    expect(img.res.headers.get("content-type")).toBe("image/jpeg");
  });
  it("earlier version images (LCE-041): CLI upload only, served inline per version", async () => {
    const h = await sha(png);
    const body = { data_base64: b64png, sha256: h, alt_text: "Previous version diagram" };
    expect((await call("PUT", `/version-media/${AWAITING}/1`, body, SERVICE, fakeFetch(), "dashboard")).status).toBe(403);
    expect((await call("PUT", `/version-media/${AWAITING}/0`, body, SERVICE, fakeFetch(), "cli")).status).toBe(400);
    expect((await call("PUT", `/version-media/${AWAITING}/1`, body, SERVICE, fakeFetch(), "cli")).status).toBe(200);
    expect((await call("GET", "/version-media", undefined, SERVICE)).body!.media).toMatchObject([{ post_id: AWAITING, version: 1, sha256: h }]);
    const img = await call("GET", `/posts/${AWAITING}/versions/1/image`);
    expect(img.status).toBe(200);
    expect(img.res.headers.get("content-disposition")).toBe("inline");
    expect(img.res.headers.get("etag")).toBe(`"${h}"`);
    expect((await call("GET", `/posts/${AWAITING}/versions/2/image`)).status).toBe(404);
    expect((await call("GET", "/snapshot")).body!.version_media).toHaveLength(1);
  });
  it("lists free publishing slots for the next 31 days", async () => {
    await enableAll(e);
    const snap = await call("GET", "/snapshot");
    const last = Math.max(...snap.body!.upcoming_slots.map((s: { utc: string }) => Date.parse(s.utc)));
    expect(last - NOW).toBeGreaterThan(25 * 86400e3);
  });
});

describe("same-day freshness gate (LCE-040)", () => {
  const SLOT = "2026-10-05T11:00:00+00:00";
  async function scheduled(fresh: boolean) {
    await enableAll(e);
    const h = await insertPost(e, READY);
    await insertConsent(e, READY, "2026-10-05-mon-0500", SLOT, h, "consent-1", fresh);
    return h;
  }
  async function setFresh(h: string, status: string, day = "2026-10-05", received = "2026-10-05T10:00:00+00:00") {
    await e.DB.prepare(`INSERT INTO freshness (post_id, check_date, checked_at, status, decision, content_hash, reason, received_at, received_by)
      VALUES (?, ?, ?, ?, 'x', ?, '', ?, 'x')
      ON CONFLICT(post_id) DO UPDATE SET check_date = excluded.check_date, status = excluded.status, content_hash = excluded.content_hash,
        received_at = excluded.received_at`)
      .bind(READY, day, received, status, h, received).run();
  }
  it("blocks a scheduled post without a same-day check, writes nothing, keeps the consent", async () => {
    await scheduled(false);
    const f = fakeFetch();
    const r = await runScheduled(e, NOW, f.fn);
    expect(r).toMatchObject({ status: "freshness_pending", detail: "no same-day freshness check" });
    expect(f.calls).toHaveLength(0);
    expect((await rows<{ status: string }>(e, "SELECT status FROM consents"))[0].status).toBe("active");
  });
  it("refuses a check from another day, a non-current status, or another text version", async () => {
    const h = await scheduled(false);
    for (const [status, day, hash, why] of [["current", "2026-10-04", h, "not 2026-10-05"], ["needs_review", "2026-10-05", h, "needs_review"],
      ["update_required", "2026-10-05", h, "update_required"], ["current", "2026-10-05", "f".repeat(64), "another text version"]]) {
      await setFresh(hash, status, day);
      const f = fakeFetch();
      const r = await runScheduled(e, NOW, f.fn);
      expect(r.status).toBe("freshness_pending");
      expect(r.detail).toContain(why);
      expect(f.calls).toHaveLength(0);
    }
  });
  it("refuses a check dated for the publishing day but received on another day (test run as-of)", async () => {
    const h = await scheduled(false);
    await setFresh(h, "current", "2026-10-05", "2026-10-02T09:00:00+00:00");
    const f = fakeFetch();
    const r = await runScheduled(e, NOW, f.fn);
    expect(r).toMatchObject({ status: "freshness_pending", detail: "freshness check was not received on the publishing day" });
    expect(f.calls).toHaveLength(0);
  });
  it("publishes once a same-day current check for the approved text exists", async () => {
    const h = await scheduled(false);
    await setFresh(h, "current");
    const f = fakeFetch(created("urn:li:share:7100"));
    expect((await runScheduled(e, NOW, f.fn)).outcome).toBe("published");
  });
  it("freshness rows are sent by the CLI only and validated", async () => {
    const body = { status: "current", decision: "unchanged", check_date: "2026-10-05", checked_at: "2026-10-05T04:00:00+00:00",
      content_hash: "a".repeat(64), image_sha256: null, reason: "every claim still stated" };
    expect((await call("PUT", `/freshness/${READY}`, body, SERVICE, fakeFetch(), "dashboard")).status).toBe(403);
    expect((await call("PUT", `/freshness/${READY}`, { ...body, status: "fine" }, SERVICE, fakeFetch(), "cli")).status).toBe(400);
    // only same-day checks (Worker clock, owner time zone): a future-dated test run is refused
    const future = await call("PUT", `/freshness/${READY}`, { ...body, check_date: "2026-10-08" }, SERVICE, fakeFetch(), "cli");
    expect(future.status).toBe(409);
    expect((await call("PUT", `/freshness/${READY}`, body, SERVICE, fakeFetch(), "cli")).status).toBe(200);
    const snap = await call("GET", "/snapshot");
    expect(snap.body!.freshness).toMatchObject([{ post_id: READY, status: "current", check_date: "2026-10-05" }]);
  });
});
