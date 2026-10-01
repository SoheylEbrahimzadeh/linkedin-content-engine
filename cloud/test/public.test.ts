import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { testEnv } from "./helpers";

const e = testEnv();
const fetchPath = (path: string, init: RequestInit = {}) =>
  worker.fetch(new Request(`https://lce.example${path}`, init), e);

describe("public privacy policy", () => {
  it("is served without any Access credential, as static HTML without scripts", async () => {
    const r = await fetchPath("/privacy");
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toContain("text/html");
    const csp = r.headers.get("content-security-policy") ?? "";
    expect(csp).toContain("default-src 'none'");
    expect(csp).not.toContain("script-src");
    const html = await r.text();
    expect(html).toContain("<h1>Privacy Policy");
    for (const topic of ["w_member_social", "/v2/userinfo", "Images API", "Cloudflare D1", "GitHub Actions",
      "Retention and deletion", "Security", "Contact", "SoheylEbrahimzadeh"]) {
      expect(html, topic).toContain(topic);
    }
    expect(html).not.toMatch(/<script/i);
  });

  it("answers HEAD, refuses other methods, and opens nothing else publicly", async () => {
    expect((await fetchPath("/privacy", { method: "HEAD" })).status).toBe(200);
    expect((await fetchPath("/privacy", { method: "POST" })).status).toBe(405);
    expect((await fetchPath("/privacy/")).status).toBe(404);
    expect((await fetchPath("/")).status).toBe(401);
    expect((await fetchPath("/api/snapshot")).status).toBe(401);
  });
});
