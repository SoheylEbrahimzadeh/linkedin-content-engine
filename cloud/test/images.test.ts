import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { handleApi, MAX_IMAGE_BYTES, snapshot } from "../src/api";
import { resetCertsCache } from "../src/auth";
import { runScheduled } from "../src/runner";
import { contentHash, sha256Bytes } from "../src/text";
import { created, enableAll, fakeFetch, insertConsent, rows, status, TEXT, testEnv, reset } from "./helpers";

// Fictional data only. Same Access test key setup as api.test.ts, done inline.
const e = testEnv();
const NOW = Date.parse("2026-09-30T12:00:00+00:00");
const SLOT = "2026-10-07-wed-0030";
const SLOT_UTC = "2026-10-07T06:30:00+00:00";
const AT = Date.parse("2026-10-07T06:31:00+00:00");
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3, 4, 5]);
const b64 = (d: Uint8Array) => btoa(String.fromCharCode(...d));

let key: CryptoKeyPair;
let jwk: JsonWebKey;
const enc = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
async function token(): Promise<string> {
  const head = enc({ alg: "RS256", kid: "k1" });
  const claims = enc({ aud: ["test-aud"], iss: "https://test-team.cloudflareaccess.com", exp: Math.floor(NOW / 1000) + 600,
    email: "owner@example.com" });
  const sig = new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key.privateKey, new TextEncoder().encode(`${head}.${claims}`)));
  return `${head}.${claims}.${btoa(String.fromCharCode(...sig)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_")}`;
}
const certs = async () => ({ keys: [{ ...jwk, kid: "k1", alg: "RS256" }] });
async function push(body: Record<string, unknown>) {
  const res = await handleApi(new Request("https://lce.example/api/posts/20261006-demo-post", {
    method: "PUT", headers: { "cf-access-jwt-assertion": await token() }, body: JSON.stringify(body) }), e, NOW, certs);
  return { status: res.status, body: (await res.json()) as Record<string, unknown> };
}

beforeAll(async () => {
  key = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
  jwk = await crypto.subtle.exportKey("jwk", key.publicKey) as JsonWebKey;
});
beforeEach(async () => { resetCertsCache(); await reset(e); });

async function base() {
  const h = await contentHash(TEXT);
  return { text: TEXT, approved_hash: h, approved_at: "2026-09-30T10:00:00+00:00", language: "en" };
}

describe("cloud image upload", () => {
  it("push stores a verified image apart from the post; snapshot has no bytes", async () => {
    const sha = await sha256Bytes(PNG);
    const r = await push({ ...(await base()), image: { data_base64: b64(PNG), sha256: sha, alt_text: "A diagram" } });
    expect(r.status).toBe(200);
    expect(r.body.image).toEqual({ sha256: sha, bytes: PNG.length });
    const again = await push({ ...(await base()), image: { data_base64: b64(PNG), sha256: sha, alt_text: "A diagram" } });
    expect(again.body.unchanged).toBe(true);
    expect((await push(await base())).status).toBe(409);                    // image dropped → conflict
    const snap = JSON.stringify(await snapshot(e, NOW));
    expect(snap).toContain(sha);
    expect(snap).not.toContain(b64(PNG));
  });

  it("push rejects wrong hash, non-images, missing alt text and oversize", async () => {
    const good = await sha256Bytes(PNG);
    expect((await push({ ...(await base()), image: { data_base64: b64(PNG), sha256: "0".repeat(64), alt_text: "x" } })).status).toBe(409);
    const txt = new TextEncoder().encode("not an image");
    expect((await push({ ...(await base()), image: { data_base64: b64(txt), sha256: await sha256Bytes(txt), alt_text: "x" } })).status).toBe(400);
    expect((await push({ ...(await base()), image: { data_base64: b64(PNG), sha256: good, alt_text: " " } })).status).toBe(400);
    const big = new Uint8Array(MAX_IMAGE_BYTES + 1);
    big.set(PNG.subarray(0, 8));
    let s = "";
    for (let i = 0; i < big.length; i += 8192) s += String.fromCharCode(...big.subarray(i, i + 8192));
    expect((await push({ ...(await base()), image: { data_base64: btoa(s), sha256: await sha256Bytes(big), alt_text: "x" } })).status).toBe(413);
    expect(await rows(e, "SELECT * FROM post_images")).toHaveLength(0);
  });

  async function armedWithImage(...responses: (Response | Error)[]) {
    await enableAll(e);
    const sha = await sha256Bytes(PNG);
    await push({ ...(await base()), image: { data_base64: b64(PNG), sha256: sha, alt_text: "A diagram" } });
    await insertConsent(e, "20261006-demo-post", SLOT, SLOT_UTC, await contentHash(TEXT));
    return fakeFetch(...responses);
  }
  const init = (url = "https://www.linkedin.com/dms-uploads/fake/0?ut=x") =>
    new Response(JSON.stringify({ value: { uploadUrl: url, image: "urn:li:image:CloudFake1" } }), { status: 200 });

  it("cron uploads the image, then posts with content.media", async () => {
    const f = await armedWithImage(init(), new Response(null, { status: 201 }), created("urn:li:share:9100"));
    expect(await runScheduled(e, AT, f.fn)).toMatchObject({ status: "done", outcome: "published" });
    expect(f.calls.map((c) => [c.init.method, c.url.split("?")[0]])).toEqual([
      ["POST", "https://api.linkedin.com/rest/images"], ["PUT", "https://www.linkedin.com/dms-uploads/fake/0"],
      ["POST", "https://api.linkedin.com/rest/posts"]]);
    expect(new Uint8Array(f.calls[1].init.body as ArrayBuffer)).toEqual(PNG);
    expect(JSON.parse(String(f.calls[2].init.body)).content).toEqual({ media: { id: "urn:li:image:CloudFake1", altText: "A diagram" } });
    const [pub] = await rows<{ image_urn: string; state: string }>(e, "SELECT * FROM publications");
    expect([pub.state, pub.image_urn]).toEqual(["published", "urn:li:image:CloudFake1"]);
  });

  it("image failures create no post: PUBLISH_FAILED, token never sent elsewhere", async () => {
    for (const responses of [[status(500)], [init("https://evil.example/u")], [init(), status(400)], [new Error("net")]]) {
      await reset(e);
      const f = await armedWithImage(...responses);
      await runScheduled(e, AT, f.fn);
      expect(f.calls.some((c) => c.url.includes("/rest/posts"))).toBe(false);
      expect(f.calls.some((c) => c.url.includes("evil.example"))).toBe(false);
      const [post] = await rows<{ state: string }>(e, "SELECT state FROM posts");
      expect(post.state).toBe("PUBLISH_FAILED");
    }
  });

  it("a tampered stored image is never sent", async () => {
    const f = await armedWithImage();
    await e.DB.prepare("UPDATE post_images SET data = ?").bind(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 9])).run();
    const r = await runScheduled(e, AT, f.fn);
    expect(r).toMatchObject({ status: "blocked", detail: "image_hash_mismatch" });
    expect(f.calls).toHaveLength(0);
  });
});
