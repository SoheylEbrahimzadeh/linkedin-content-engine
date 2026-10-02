import { describe, expect, it } from "vitest";
import { IDENTITY_HTTP, linkedinIdentity, USERINFO_URL } from "../src/identity";
import type { Settings } from "../src/db";
import { fakeFetch } from "./helpers";

const TOKEN = "fake.identity.test.token";
const S = (over: Partial<Settings> = {}): Settings =>
  ({ auto_publish: false, provider: "none", visibility: "PUBLIC", max_lateness_minutes: 180, ...over });
const ok = (body: unknown) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status: 200 });
// Synthetic userinfo payload (fictional member), shaped like LinkedIn's documented sample.
const USERINFO = { sub: "TestPerson1", name: "Test Person", given_name: "Test", family_name: "Person",
  picture: "https://media.licdn.example/p.jpg", locale: { country: "US", language: "en" } };

describe("linkedinIdentity (read-only userinfo check)", () => {
  it("resolves the person URN with one GET and returns no profile data or token", async () => {
    const f = fakeFetch(ok(USERINFO));
    const r = await linkedinIdentity(TOKEN, S({ api_version: "202609" }), f.fn);
    expect(r).toEqual({ ok: true, status: "verified", http_status: 200, person_urn: "urn:li:person:TestPerson1",
      configured_person_urn: null, person_urn_matches: null, api_version: "202609", api_version_valid: true });
    expect(f.calls).toHaveLength(1);
    expect(f.calls[0].url).toBe(USERINFO_URL);
    expect(f.calls[0].init.method).toBe("GET");
    expect(f.calls[0].init.body).toBeUndefined();
    const h = f.calls[0].init.headers as Record<string, string>;
    expect(h.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(h).not.toHaveProperty("Linkedin-Version");
    const text = JSON.stringify(r);
    for (const leak of [TOKEN, "Test Person", "licdn", "given_name"]) expect(text).not.toContain(leak);
  });
  it("compares with the configured person URN", async () => {
    let r = await linkedinIdentity(TOKEN, S({ person_urn: "urn:li:person:TestPerson1" }), fakeFetch(ok(USERINFO)).fn);
    expect(r.person_urn_matches).toBe(true);
    r = await linkedinIdentity(TOKEN, S({ person_urn: "urn:li:person:SomeoneElse" }), fakeFetch(ok(USERINFO)).fn);
    expect(r.ok).toBe(true);
    expect(r.person_urn_matches).toBe(false);
  });
  it("missing token: no request at all", async () => {
    const f = fakeFetch();
    const r = await linkedinIdentity(undefined, S(), f.fn);
    expect(r).toMatchObject({ ok: false, status: "token_missing" });
    expect(IDENTITY_HTTP[r.status]).toBe(503);
    expect(f.calls).toHaveLength(0);
    expect((await linkedinIdentity("", S(), f.fn)).status).toBe("token_missing");
  });
  it("maps LinkedIn 401, 403, 429 and 5xx without echoing LinkedIn's body", async () => {
    const body = JSON.stringify({ serviceErrorCode: 65600, message: `Invalid access token ${TOKEN}` });
    const cases: [number, string][] = [[401, "token_rejected"], [403, "forbidden"], [429, "rate_limited"],
      [500, "linkedin_error"], [302, "linkedin_error"]];
    for (const [code, status] of cases) {
      const r = await linkedinIdentity(TOKEN, S(), fakeFetch(new Response(body, { status: code })).fn);
      expect(r).toMatchObject({ ok: false, status, http_status: code });
      expect(r.person_urn).toBeUndefined();
      expect(JSON.stringify(r)).not.toContain(TOKEN);
    }
  });
  it("rejects malformed responses", async () => {
    for (const body of ["not json", "[]", "null", JSON.stringify({ name: "x" }), JSON.stringify({ sub: 12345 }),
      JSON.stringify({ sub: "" }), JSON.stringify({ sub: "a b" }), JSON.stringify({ sub: "x:y" }),
      JSON.stringify({ sub: "x".repeat(129) })]) {
      const r = await linkedinIdentity(TOKEN, S(), fakeFetch(ok(body)).fn);
      expect(r).toMatchObject({ ok: false, status: "malformed_response" });
      expect(r.person_urn).toBeUndefined();
    }
  });
  it("times out and reports network errors", async () => {
    const hang = (_u: string, init: RequestInit) => new Promise<Response>((_r, reject) => {
      init.signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
    });
    const r = await linkedinIdentity(TOKEN, S(), hang, 20);
    expect(r).toMatchObject({ ok: false, status: "timeout" });
    expect(IDENTITY_HTTP.timeout).toBe(504);
    const n = await linkedinIdentity(TOKEN, S(), fakeFetch(new TypeError("fetch failed")).fn);
    expect(n).toMatchObject({ ok: false, status: "network_error" });
  });
  it("flags an invalid api_version", async () => {
    const r = await linkedinIdentity(TOKEN, S({ api_version: "26-09" }), fakeFetch(ok(USERINFO)).fn);
    expect(r.api_version_valid).toBe(false);
  });
});
