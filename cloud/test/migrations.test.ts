import { applyD1Migrations, env } from "cloudflare:test";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { handleApi } from "../src/api";
import { resetCertsCache } from "../src/auth";
import { applyMigrations, MIGRATIONS, migrationStatus, statements } from "../src/migrations";
import { testEnv } from "./helpers";

const NOW = Date.parse("2026-10-01T12:00:00+00:00");
const TEAM = "test-team.cloudflareaccess.com";
const e = testEnv();
let keys: CryptoKeyPair;
let jwk: JsonWebKey;
const b64 = (b: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(b)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
async function token() {
  const h = b64(new TextEncoder().encode(JSON.stringify({ alg: "RS256", kid: "k1" })));
  const p = b64(new TextEncoder().encode(JSON.stringify({ aud: ["ab".repeat(32)], iss: `https://${TEAM}`,
    exp: Math.floor(NOW / 1000) + 600, common_name: "lce-ci.access" })));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", keys.privateKey, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64(sig)}`;
}
async function api(method: string, path: string, body?: unknown, headers: Record<string, string> | null = {}) {
  const h: Record<string, string> = { "content-type": "application/json", "x-lce-client": "cli", ...(headers ?? {}) };
  if (headers !== null) h["cf-access-jwt-assertion"] = await token();
  const res = await handleApi(new Request(`https://lce.example/api${path}`, { method, headers: h,
    body: body === undefined ? undefined : JSON.stringify(body) }), e, NOW, async () => ({ keys: [jwk] }));
  return { status: res.status, body: await res.json() as Record<string, unknown> };
}
const tableNames = async () => (await e.DB.prepare(
  "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%' ORDER BY name",
).all<{ name: string }>()).results.map((r) => r.name);
async function dropAll() {
  for (const t of await tableNames()) await e.DB.prepare(`DROP TABLE ${t}`).run();
}

beforeAll(async () => {
  keys = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]) as CryptoKeyPair;
  jwk = { ...(await crypto.subtle.exportKey("jwk", keys.publicKey)) as JsonWebKey, kid: "k1" } as JsonWebKey;
  resetCertsCache();
});
afterAll(async () => { await applyD1Migrations(env.DB, env.TEST_MIGRATIONS); });

describe("D1 migrations through the Worker (LCE-032)", () => {
  it("reports status: every migration applied in the test database", async () => {
    const r = await api("GET", "/migrations");
    expect(r.status).toBe(200);
    expect(r.body.pending).toEqual([]);
    expect(r.body.applied).toEqual(MIGRATIONS.map(([n]) => n));
  });

  it("applies all pending migrations to an empty database, atomically per file, once", async () => {
    const reference = await tableNames();
    await dropAll();
    expect((await api("GET", "/migrations")).body.pending).toEqual(MIGRATIONS.map(([n]) => n));
    expect((await api("GET", "/snapshot")).status).toBe(503);              // schema missing
    const r = await api("POST", "/migrations", { confirm: "APPLY MIGRATIONS" });
    expect(r.status).toBe(200);
    expect(r.body.applied_now).toEqual(MIGRATIONS.map(([n]) => n));
    expect(r.body.applied).toEqual(MIGRATIONS.map(([n]) => n));
    expect(r.body.pending).toEqual([]);
    expect(await tableNames()).toEqual(reference);
    expect((await api("GET", "/snapshot")).status).toBe(200);
    const again = await api("POST", "/migrations", { confirm: "APPLY MIGRATIONS" });
    expect(again.body.applied_now).toEqual([]);
    const ev = await e.DB.prepare("SELECT actor FROM events WHERE event = 'migrations.applied'").all<{ actor: string }>();
    expect(ev.results.map((x) => x.actor)).toEqual(["lce-ci.access"]);
    // the kill switch stays off after a fresh migration
    const s = await e.DB.prepare("SELECT value FROM settings WHERE key = 'auto_publish'").first<{ value: string }>();
    expect(s?.value).toBe("false");
  });

  it("refuses when a table exists without a recorded migration", async () => {
    await e.DB.prepare("DELETE FROM d1_migrations").run();
    const r = await api("POST", "/migrations", { confirm: "APPLY MIGRATIONS" });
    expect(r.status).toBe(409);
    expect(String(r.body.error)).toContain("exists but the migration is not recorded");
    for (const [n] of MIGRATIONS) await e.DB.prepare("INSERT INTO d1_migrations (name) VALUES (?)").bind(n).run();
    expect((await migrationStatus(e.DB)).pending).toEqual([]);
  });

  it("requires Access, the CLI header and the typed phrase", async () => {
    expect((await api("GET", "/migrations", undefined, null)).status).toBe(401);
    expect((await api("POST", "/migrations", { confirm: "APPLY MIGRATIONS" }, null)).status).toBe(401);
    const noHeader = await handleApi(new Request("https://lce.example/api/migrations", { method: "POST",
      headers: { "content-type": "application/json", "cf-access-jwt-assertion": await token() },
      body: JSON.stringify({ confirm: "APPLY MIGRATIONS" }) }), e, NOW, async () => ({ keys: [jwk] }));
    expect(noHeader.status).toBe(403);
    expect((await api("POST", "/migrations", {})).status).toBe(428);
    expect(await applyMigrations(e.DB)).toEqual([]);
  });

  it("0007 rebuilds decisions keeping every row, then accepts 'refresh' (LCE-041)", async () => {
    await e.DB.prepare("DROP TABLE decisions").run();
    await e.DB.prepare("DROP TABLE version_media").run();
    for (const s of statements(MIGRATIONS.find(([n]) => n === "0004_decisions.sql")![1])) await e.DB.prepare(s).run();
    await e.DB.prepare(`INSERT INTO decisions (decision_id, post_id, action, payload, status, created_at, created_by)
      VALUES ('d-old', '20261006-demo', 'approve', '{}', 'applied', '2026-10-01T10:00:00+00:00', 'owner@example.com')`).run();
    await expect(e.DB.prepare(`INSERT INTO decisions (decision_id, action, status, created_at, created_by)
      VALUES ('d-x', 'refresh', 'pending', 'now', 'o')`).run()).rejects.toThrow();
    await e.DB.prepare("DELETE FROM d1_migrations WHERE name = '0007_refresh.sql'").run();
    expect((await migrationStatus(e.DB)).pending).toEqual(["0007_refresh.sql"]);
    expect(await applyMigrations(e.DB)).toEqual(["0007_refresh.sql"]);
    const rows = (await e.DB.prepare("SELECT decision_id, action, status FROM decisions").all()).results;
    expect(rows).toEqual([{ decision_id: "d-old", action: "approve", status: "applied" }]);
    await e.DB.prepare(`INSERT INTO decisions (decision_id, action, status, created_at, created_by)
      VALUES ('d-new', 'refresh', 'pending', 'now', 'o')`).run();
    expect((await tableNames())).toContain("version_media");
  });

  it("splits the migration files into the expected statements", () => {
    expect(statements(MIGRATIONS[0][1]).filter((s) => s.startsWith("CREATE TABLE"))).toHaveLength(6);
    expect(statements(MIGRATIONS[1][1])).toHaveLength(2);
    expect(statements(MIGRATIONS[2][1])).toHaveLength(1);
    expect(statements("-- only a comment\n")).toEqual([]);
  });
});
