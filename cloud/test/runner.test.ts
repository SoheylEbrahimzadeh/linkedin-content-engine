import { beforeEach, describe, expect, it } from "vitest";
import { snapshot } from "../src/api";
import { runScheduled } from "../src/runner";
import { created, enableAll, fakeFetch, insertConsent, insertPost, reset, rows, setSettings, status, testEnv } from "./helpers";

// Wed 2026-10-07 00:30 America/Denver (MDT, -06:00) = 06:30 UTC.
const SLOT = "2026-10-07-wed-0030";
const SLOT_UTC = "2026-10-07T06:30:00+00:00";
const AT = (hhmm: string) => Date.parse(`2026-10-07T${hhmm}:00+00:00`);
const e = testEnv();

async function armed(fetches: (Response | Error)[]) {
  await enableAll(e);
  const h = await insertPost(e);
  await insertConsent(e, "20261006-demo-post", SLOT, SLOT_UTC, h);
  return fakeFetch(...fetches);
}

beforeEach(async () => reset(e));

describe("cron runner", () => {
  it("does nothing and writes nothing when no consent is due", async () => {
    await enableAll(e);
    const h = await insertPost(e);
    await insertConsent(e, "20261006-demo-post", SLOT, SLOT_UTC, h);
    const f = fakeFetch();
    const before = JSON.stringify(await rows(e, "SELECT * FROM consents"));
    expect((await runScheduled(e, AT("06:29"), f.fn)).status).toBe("idle");
    expect(f.calls).toHaveLength(0);
    expect(JSON.stringify(await rows(e, "SELECT * FROM consents"))).toBe(before);
    expect(await rows(e, "SELECT * FROM events")).toHaveLength(0);
  });

  it("reports schema_missing instead of throwing when migrations are not applied", async () => {
    const f = fakeFetch();
    await e.DB.prepare("ALTER TABLE consents RENAME TO consents_unmigrated").run();
    try {
      expect((await runScheduled(e, AT("06:31"), f.fn)).status).toBe("schema_missing");
      expect(f.calls).toHaveLength(0);
    } finally {
      await e.DB.prepare("ALTER TABLE consents_unmigrated RENAME TO consents").run();
    }
  });

  it("publishes exactly once at the slot with the approved text", async () => {
    const f = await armed([created("urn:li:share:9001")]);
    const r = await runScheduled(e, AT("06:31"), f.fn);
    expect(r).toMatchObject({ status: "done", outcome: "published" });
    expect(f.calls).toHaveLength(1);
    const call = f.calls[0];
    expect(call.url).toBe("https://api.linkedin.com/rest/posts");
    expect((call.init.headers as Record<string, string>).Authorization).toBe("Bearer fake.cloud.test.token");
    const body = JSON.parse(String(call.init.body));
    expect(body.commentary).toBe("A fictional approved post \\(demo\\).\n\nIt uses \\[brackets\\] and #Hashtags.");
    const [post] = await rows<{ state: string }>(e, "SELECT state FROM posts");
    const [pub] = await rows<{ state: string; remote_id: string; attempts: string; verified_by: string }>(e, "SELECT * FROM publications");
    const [c] = await rows<{ status: string }>(e, "SELECT status FROM consents");
    const [job] = await rows<{ state: string }>(e, "SELECT state FROM jobs");
    expect([post.state, pub.state, pub.remote_id, pub.verified_by, c.status, job.state])
      .toEqual(["PUBLISHED", "published", "urn:li:share:9001", "api_response", "consumed", "SUCCEEDED"]);
    expect(JSON.parse(pub.attempts)).toMatchObject([{ attempt: 1, outcome: "published", http_status: 201, runtime: "cloud" }]);
    expect((await runScheduled(e, AT("06:36"), f.fn)).status).toBe("idle"); // consumed: never again
    expect(f.calls).toHaveLength(1);
  });

  it("kill switch OFF: no request and no writes", async () => {
    const f = await armed([created()]);
    await setSettings(e, { auto_publish: "false" });
    expect((await runScheduled(e, AT("06:31"), f.fn)).status).toBe("kill_switch_off");
    expect(f.calls).toHaveLength(0);
    expect((await rows<{ status: string }>(e, "SELECT status FROM consents"))[0].status).toBe("active");
    expect(await rows(e, "SELECT * FROM events")).toHaveLength(0);
  });

  it("missed window: consent expires, job skipped, nothing sent", async () => {
    const f = await armed([created()]);
    expect((await runScheduled(e, AT("09:31"), f.fn)).status).toBe("missed");
    expect(f.calls).toHaveLength(0);
    expect((await rows<{ status: string }>(e, "SELECT status FROM consents"))[0].status).toBe("expired");
    expect((await rows<{ state: string }>(e, "SELECT state FROM jobs"))[0].state).toBe("SKIPPED");
    expect((await rows<{ state: string }>(e, "SELECT state FROM posts"))[0].state).toBe("READY_TO_PUBLISH");
  });

  it.each([
    ["provider_not_enabled", { provider: "none" }],
    ["config: person_urn must look like urn:li:person:<id>", { person_urn: "nope" }],
    ["token_expired", { token_expires_at: "2026-10-01T00:00:00+00:00" }],
  ])("gate %s blocks sending", async (reason, settings) => {
    const f = await armed([created()]);
    await setSettings(e, settings as Record<string, string>);
    const r = await runScheduled(e, AT("06:31"), f.fn);
    expect(r).toMatchObject({ status: "blocked", detail: reason });
    expect(f.calls).toHaveLength(0);
    expect((await rows<{ status: string }>(e, "SELECT status FROM consents"))[0].status).toBe("invalid");
  });

  it("missing token secret blocks sending", async () => {
    await enableAll(e);
    const h = await insertPost(e);
    await insertConsent(e, "20261006-demo-post", SLOT, SLOT_UTC, h);
    const f = fakeFetch(created());
    const r = await runScheduled(testEnv({ LINKEDIN_TOKEN: undefined }), AT("06:31"), f.fn);
    expect(r.detail).toBe("token_missing");
    expect(f.calls).toHaveLength(0);
  });

  it("approved-hash gate: text changed in the database → nothing sent", async () => {
    const f = await armed([created()]);
    await e.DB.prepare("UPDATE posts SET text = text || ' tampered'").run();
    expect((await runScheduled(e, AT("06:31"), f.fn)).detail).toBe("hash_mismatch");
    expect(f.calls).toHaveLength(0);
  });

  it("post not READY_TO_PUBLISH → nothing sent", async () => {
    const f = await armed([created()]);
    await e.DB.prepare("UPDATE posts SET state = 'WITHDRAWN'").run();
    expect((await runScheduled(e, AT("06:31"), f.fn)).detail).toBe("post_is_WITHDRAWN");
    expect(f.calls).toHaveLength(0);
  });

  it.each([[400], [401], [403], [422], [429]])("HTTP %i → PUBLISH_FAILED, no retry", async (code) => {
    const f = await armed([status(code), created()]);
    await runScheduled(e, AT("06:31"), f.fn);
    await runScheduled(e, AT("06:36"), f.fn);
    expect(f.calls).toHaveLength(1);
    expect((await rows<{ state: string }>(e, "SELECT state FROM posts"))[0].state).toBe("PUBLISH_FAILED");
    expect((await rows<{ state: string }>(e, "SELECT state FROM jobs"))[0].state).toBe("FAILED");
  });

  it.each([["500", status(500)], ["503", status(503)], ["409", status(409)], ["timeout", new DOMException("t", "AbortError")],
    ["network", new TypeError("fetch failed")], ["201 without URN", new Response(null, { status: 201 })]])(
    "%s → NEEDS_RECONCILE, never retried", async (_n, resp) => {
      const f = await armed([resp as Response | Error, created()]);
      await runScheduled(e, AT("06:31"), f.fn);
      await runScheduled(e, AT("06:36"), f.fn);
      await runScheduled(e, AT("06:41"), f.fn);
      expect(f.calls).toHaveLength(1);
      expect((await rows<{ state: string }>(e, "SELECT state FROM posts"))[0].state).toBe("NEEDS_RECONCILE");
      expect((await rows<{ state: string }>(e, "SELECT state FROM publications"))[0].state).toBe("needs_reconcile");
    });

  it("two concurrent cron runs send one request", async () => {
    const f = await armed([created(), created()]);
    const [a, b] = await Promise.all([runScheduled(e, AT("06:31"), f.fn), runScheduled(e, AT("06:31"), f.fn)]);
    expect(f.calls).toHaveLength(1);
    expect([a.status, b.status].sort()).toEqual(["claim_lost", "done"].sort());
  });

  it("the token never reaches D1, events or the snapshot", async () => {
    const f = await armed([status(500)]);
    await runScheduled(e, AT("06:31"), f.fn);
    const dump = JSON.stringify([await rows(e, "SELECT * FROM events"), await rows(e, "SELECT * FROM publications"),
      await rows(e, "SELECT * FROM settings"), await snapshot(e, AT("06:40"))]);
    expect(dump).not.toContain("fake.cloud.test.token");
    expect((await snapshot(e, AT("06:40"))).settings.token_present).toBe(true);
  });
});
