import { beforeAll, describe, expect, it } from "vitest";
import worker from "../src/index";
import { handleUi } from "../src/ui";
import { testEnv } from "./helpers";

const e = testEnv();
const NOW = Date.parse("2026-09-30T12:00:00+00:00");
let key: CryptoKeyPair;
let jwk: JsonWebKey;
const enc = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
async function token(): Promise<string> {
  const head = enc({ alg: "RS256", kid: "k1" });
  const claims = enc({ aud: ["ab".repeat(32)], iss: "https://test-team.cloudflareaccess.com", exp: Math.floor(NOW / 1000) + 600, email: "owner@example.com" });
  const sig = new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key.privateKey, new TextEncoder().encode(`${head}.${claims}`)));
  return `${head}.${claims}.${btoa(String.fromCharCode(...sig)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_")}`;
}
const certs = async () => ({ keys: [{ ...jwk, kid: "k1", alg: "RS256" }] });
const get = async (path: string, auth = true, env = e) => handleUi(new Request(`https://lce.example${path}`,
  { headers: auth ? { "cf-access-jwt-assertion": await token() } : {} }), env, NOW, certs);

beforeAll(async () => {
  key = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
  jwk = await crypto.subtle.exportKey("jwk", key.publicKey) as JsonWebKey;
});

describe("remote dashboard", () => {
  it("serves the page, script and styles only with a valid Access login", async () => {
    for (const [path, type] of [["/", "text/html"], ["/app.js", "text/javascript"], ["/app.css", "text/css"]]) {
      const ok = await get(path);
      expect(ok.status).toBe(200);
      expect(ok.headers.get("content-type")).toContain(type);
      expect(ok.headers.get("content-security-policy")).toContain("script-src 'self'");
      expect(ok.headers.get("content-security-policy")).not.toContain("unsafe");
      expect(ok.headers.get("x-frame-options")).toBe("DENY");
      expect((await get(path, false)).status).toBe(401);
    }
  });

  it("fails closed when Access is not configured", async () => {
    const off = testEnv({ ACCESS_TEAM_DOMAIN: "", ACCESS_AUD: "" });
    const r = await get("/", true, off);
    expect(r.status).toBe(503);
    expect(await r.text()).toContain("not configured");
  });

  it("the page contains no data and the script never uses innerHTML or eval", async () => {
    const page = await (await get("/")).text();
    expect(page).not.toContain("urn:li");
    const js = await (await get("/app.js")).text();
    for (const bad of ["innerHTML", "outerHTML", "eval(", "new Function", "document.write", "localStorage"]) {
      expect(js).not.toContain(bad);
    }
    expect(js).toContain('"x-lce-client": "dashboard"');
    expect(js).not.toContain("/approve");                 // no approval path exists
  });

  it("is routed by the Worker; unknown paths stay 404", async () => {
    const res = await worker.fetch(new Request("https://lce.example/nope"), e);
    expect(res.status).toBe(404);
    expect((await worker.fetch(new Request("https://lce.example/"), e)).status).toBe(401);
  });
});
