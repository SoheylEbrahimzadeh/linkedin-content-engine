// LCE-062: a Preview deployment (LCE_PREVIEW = "true") never publishes and never calls LinkedIn, even with every
// other gate passing, a LinkedIn token present, auto-publish on and a due schedule. Local D1 (miniflare), fake fetch.
import { beforeEach, describe, expect, it } from "vitest";
import { isPreview } from "../src/db";
import { startWriter } from "../src/dispatch";
import { publishNow, runScheduled } from "../src/runner";
import { created, enableAll, fakeFetch, insertConsent, insertPost, qualityPass, reset, rows, setSettings, testEnv } from "./helpers";

const ID = "20261006-demo-post";
const SLOT = "2026-10-07-wed-0030";
const SLOT_UTC = "2026-10-07T06:30:00+00:00";
const AT = Date.parse("2026-10-07T06:30:00+00:00");
const userinfo = () => new Response(JSON.stringify({ sub: "TestPerson1" }), { status: 200 });
const prod = testEnv();
const preview = testEnv({ LCE_PREVIEW: "true" });

beforeEach(async () => reset(prod));

const state = async () => ({
  consents: await rows(prod, "SELECT * FROM consents ORDER BY consent_id"),
  jobs: await rows(prod, "SELECT * FROM jobs ORDER BY slot_id"),
  publications: await rows(prod, "SELECT * FROM publications"),
  posts: await rows(prod, "SELECT post_id, state, approved_hash FROM posts"),
  events: await rows(prod, "SELECT event FROM events"),
});

// everything that would allow a publication in production
async function publishable(schedule = false) {
  await enableAll(prod);                                                 // auto-publish ON, LinkedIn configured
  const h = await insertPost(prod, ID);
  await qualityPass(prod, ID);
  if (schedule) await insertConsent(prod, ID, SLOT, SLOT_UTC, h, "consent-sched");
  return h;
}

describe("Preview deployment never publishes", () => {
  it("the flag is read strictly: only the value true (any case, trimmed) enables it", () => {
    for (const v of ["true", "TRUE", " true "]) expect(isPreview(testEnv({ LCE_PREVIEW: v }))).toBe(true);
    for (const v of [undefined, "", "false", "1", "yes", "truthy"]) expect(isPreview(testEnv({ LCE_PREVIEW: v }))).toBe(false);
  });

  it("Publish now: refused first, nothing written, zero LinkedIn requests (same setup publishes in production)", async () => {
    const h = await publishable();
    const before = await state();
    const f = fakeFetch(userinfo(), created());
    const r = await publishNow(preview, AT, "owner@example.com", ID, h, f.fn);
    expect([r.http, r.detail]).toEqual([403, "this is a Preview deployment; it never publishes; nothing was changed or sent"]);
    expect(f.calls).toHaveLength(0);
    expect(await state()).toEqual(before);
    // control: the identical setup outside Preview does publish (so the refusal above is the Preview guard)
    const g = fakeFetch(userinfo(), created("urn:li:share:7301"));
    expect(await publishNow(prod, AT, "owner@example.com", ID, h, g.fn)).toMatchObject({ status: "done", outcome: "published" });
  });

  it("Publish now replacing a schedule: refused first, the schedule is untouched, zero LinkedIn requests", async () => {
    const h = await publishable(true);
    const before = await state();
    const f = fakeFetch(userinfo(), created());
    const r = await publishNow(preview, AT - 86_400_000, "owner@example.com", ID, h, f.fn, { replaceSchedule: true });
    expect(r.http).toBe(403);
    expect(f.calls).toHaveLength(0);
    expect(await state()).toEqual(before);
  });

  it("scheduled path: a due consent with auto-publish ON is not touched, zero LinkedIn requests", async () => {
    await publishable(true);
    const before = await state();
    const f = fakeFetch(created());
    expect(await runScheduled(preview, AT, f.fn)).toEqual({ status: "preview", detail: "Preview deployment: nothing is published" });
    expect(f.calls).toHaveLength(0);
    expect(await state()).toEqual(before);
    // control: the same due consent is published outside Preview
    const g = fakeFetch(created("urn:li:share:7302"));
    expect(await runScheduled(prod, AT, g.fn)).toMatchObject({ status: "done", outcome: "published" });
  });

  it("the writer Routine is never started from a Preview, even when its trigger is configured", async () => {
    const f = fakeFetch(new Response(JSON.stringify({ claude_code_session_url: "x" }), { status: 200 }));
    const env = testEnv({ LCE_PREVIEW: "true", LCE_ROUTINE_FIRE_URL: "https://api.example.test/v1/claude_code/routines/trig_X1/fire",
      LCE_ROUTINE_FIRE_TOKEN: "not-real" });
    const r = await startWriter(env, AT, f.fn, { kind: "refresh", post_id: ID, decision_id: "d-1" });
    expect(r).toEqual({ started: false, why: "Preview deployment: the writer is never started from a Preview" });
    expect(f.calls).toHaveLength(0);
  });
});
