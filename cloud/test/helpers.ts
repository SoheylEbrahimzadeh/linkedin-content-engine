import { env } from "cloudflare:test";
import type { Env } from "../src/db";
import { contentHash } from "../src/text";
import { localDate } from "../src/runner";

export const TEXT = "A fictional approved post (demo).\n\nIt uses [brackets] and #Hashtags.\n";
// Synthetic schedule only; never the owner's configuration.
export const SYNTH = {
  timezone: "America/Denver",
  cadence: { posts_per_week: 3, slots: [{ day: "mon", time: "11:10" }, { day: "wed", time: "00:30" },
    { day: "fri", time: "15:45" }] },
};

export function testEnv(overrides: Partial<Env> = {}): Env {
  return { ...(env as unknown as Env), LINKEDIN_TOKEN: "fake.cloud.test.token", ...overrides };
}

export async function reset(e: Env): Promise<void> {
  await e.DB.batch(["events", "publications", "jobs", "consents", "post_images", "posts", "settings", "pipeline_snapshot", "decisions", "preview_media", "freshness", "version_media", "access_sessions", "client_reports"]
    .map((t) => e.DB.prepare(`DELETE FROM ${t}`)));
  await e.DB.prepare(`INSERT INTO settings (key, value, updated_at) VALUES
    ('auto_publish','false','1970-01-01T00:00:00+00:00'), ('provider','none','1970-01-01T00:00:00+00:00'),
    ('visibility','PUBLIC','1970-01-01T00:00:00+00:00'), ('max_lateness_minutes','180','1970-01-01T00:00:00+00:00')`).run();
}

export async function setSettings(e: Env, kv: Record<string, string>): Promise<void> {
  await e.DB.batch(Object.entries(kv).map(([k, v]) => e.DB.prepare(
    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, '2026-01-01T00:00:00+00:00') ON CONFLICT(key) DO UPDATE SET value = excluded.value",
  ).bind(k, v)));
}

export async function enableAll(e: Env): Promise<void> {
  await setSettings(e, {
    auto_publish: "true", provider: "linkedin_api", api_version: "202609",
    person_urn: "urn:li:person:TestPerson1", timezone: SYNTH.timezone, cadence: JSON.stringify(SYNTH.cadence),
  });
}

export async function insertPost(e: Env, id = "20261006-demo-post", text = TEXT, state = "READY_TO_PUBLISH"): Promise<string> {
  const h = await contentHash(text);
  await e.DB.prepare(`INSERT INTO posts (post_id, text, language, plan_date, state, content_hash, approved_hash,
    approved_at, pushed_at, pushed_by, updated_at) VALUES (?, ?, 'en', NULL, ?, ?, ?, '2026-09-30T10:00:00+00:00',
    '2026-09-30T10:00:00+00:00', 'test', '2026-09-30T10:00:00+00:00')`).bind(id, text, state, h, h).run();
  return h;
}

export async function insertConsent(e: Env, postId: string, slotId: string, slotUtc: string, hash: string,
                                    id = "consent-1", fresh = true): Promise<void> {
  // LCE-040: by default the post had its same-day freshness check (status current, same text).
  if (fresh) {
    const tz = (await e.DB.prepare("SELECT value FROM settings WHERE key = 'timezone'").first<{ value: string }>())?.value
      ?? SYNTH.timezone;
    await e.DB.prepare(`INSERT INTO freshness (post_id, check_date, checked_at, status, decision, content_hash, image_sha256,
      reason, received_at, received_by) VALUES (?, ?, ?, 'current', 'unchanged', ?, NULL, 'test', ?, 'test')
      ON CONFLICT(post_id) DO UPDATE SET check_date = excluded.check_date, status = 'current', content_hash = excluded.content_hash`)
      .bind(postId, localDate(slotUtc, tz), slotUtc, hash, slotUtc).run();
  }
  await e.DB.batch([
    e.DB.prepare(`INSERT INTO consents (consent_id, post_id, slot_id, slot_utc, approved_hash, status, created_at, created_by)
      VALUES (?, ?, ?, ?, ?, 'active', '2026-09-30T10:00:00+00:00', 'test')`).bind(id, postId, slotId, slotUtc, hash),
    e.DB.prepare(`INSERT INTO jobs (job_id, slot_id, slot_utc, state, post_id, consent_id, updated_at)
      VALUES (?, ?, ?, 'SCHEDULED', ?, ?, '2026-09-30T10:00:00+00:00') ON CONFLICT(slot_id) DO NOTHING`)
      .bind(`job-${slotId}`, slotId, slotUtc, postId, id),
  ]);
}

export type Call = { url: string; init: RequestInit };
export function fakeFetch(...responses: (Response | Error)[]) {
  const calls: Call[] = [];
  const fn = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const r = responses.shift();
    if (!r) throw new Error("unexpected extra request");
    if (r instanceof Error) throw r;
    return r;
  };
  return { fn, calls };
}

export const created = (urn = "urn:li:share:7001") => new Response(null, { status: 201, headers: { "x-restli-id": urn } });
export const status = (code: number) => new Response(JSON.stringify({ message: "x" }), { status: code });

export async function rows<T = Record<string, unknown>>(e: Env, sql: string): Promise<T[]> {
  return (await e.DB.prepare(sql).all<T>()).results;
}

// LCE-059: a quality result in the pipeline mirror for one post (merged into any existing mirror). The
// defaults describe a publishable post: Humanity PASS and Voice Gate PASS for exactly `text`.
export async function qualityPass(e: Env, postId = "20261006-demo-post", text = TEXT,
                                  q: { humanity?: unknown; voice?: unknown; hash?: string | null; raw?: unknown } = {}): Promise<void> {
  const row = await e.DB.prepare("SELECT body FROM pipeline_snapshot WHERE id = 1").first<{ body: string }>();
  const snap = row ? JSON.parse(row.body) : { schema: 1, meta: { mode: "real", generated_at: "2026-10-05T00:00:00+00:00" }, posts: [] };
  const hash = q.hash === undefined ? await contentHash(text) : q.hash;
  const humanity = q.raw !== undefined ? q.raw
    : { verdict: q.humanity ?? "PASS", score: 10, ...(hash === null ? {} : { content_hash: hash }), voice_gate: { verdict: q.voice ?? "PASS" } };
  const others = (snap.posts ?? []).filter((p: { post_id: string }) => p.post_id !== postId);
  const mine = (snap.posts ?? []).find((p: { post_id: string }) => p.post_id === postId) ?? { post_id: postId, state: "READY_TO_PUBLISH", text };
  snap.posts = [...others, { ...mine, humanity }];
  const body = JSON.stringify(snap);
  await e.DB.prepare(`INSERT INTO pipeline_snapshot (id, body, sha256, bytes, generated_at, received_at, received_by)
    VALUES (1, ?, 'test', ?, '2026-10-05T00:00:00+00:00', '2026-10-05T00:00:00+00:00', 'test')
    ON CONFLICT(id) DO UPDATE SET body = excluded.body`).bind(body, body.length).run();
}
