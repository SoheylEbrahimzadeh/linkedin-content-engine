// Authenticated JSON API (behind Cloudflare Access). The API can never approve
// content: posts are accepted only with a hash that matches their text, and
// only a separate, explicit consent schedules one post for one slot.

import { AuthError, verifyAccess, type CertsFetcher } from "./auth";
import { event, loadSettings, SETTING_KEYS, type ConsentRow, type Env, type PostRow } from "./db";
import { isoUtc, loadSchedule, parseIsoUtc, ScheduleError, slotById, slotsBetween } from "./schedule";
import { contentHash } from "./text";

const POST_ID_RE = /^\d{8}-[a-z0-9-]{1,56}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const SECURITY_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: SECURITY_HEADERS });

async function body(request: Request): Promise<Record<string, unknown>> {
  try {
    const data = await request.json();
    if (data && typeof data === "object" && !Array.isArray(data)) return data as Record<string, unknown>;
  } catch { /* fall through */ }
  throw new HttpError(400, "expected a JSON object");
}

export async function handleApi(request: Request, env: Env, now: number,
                                certs?: CertsFetcher): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/api/health" && request.method === "GET") return json(200, { ok: true });
  try {
    const who = await verifyAccess(request, env, now, certs);
    const actor = who.subject;
    const m = (re: RegExp) => re.exec(url.pathname);
    let r: RegExpExecArray | null;
    if (request.method === "GET" && url.pathname === "/api/snapshot") return json(200, await snapshot(env, now));
    if (request.method === "PUT" && (r = m(/^\/api\/posts\/([^/]+)$/))) return json(200, await pushPost(env, now, actor, r[1], await body(request)));
    if (request.method === "POST" && (r = m(/^\/api\/posts\/([^/]+)\/withdraw$/))) return json(200, await withdraw(env, now, actor, r[1]));
    if (request.method === "POST" && (r = m(/^\/api\/posts\/([^/]+)\/rearm$/))) return json(200, await rearm(env, now, actor, r[1]));
    if (request.method === "POST" && (r = m(/^\/api\/posts\/([^/]+)\/reconcile$/))) return json(200, await reconcile(env, now, actor, r[1], await body(request)));
    if (request.method === "POST" && url.pathname === "/api/consents") return json(201, await consent(env, now, actor, await body(request)));
    if (request.method === "DELETE" && (r = m(/^\/api\/consents\/([^/]+)$/))) return json(200, await revoke(env, now, actor, r[1]));
    if (request.method === "PUT" && url.pathname === "/api/settings") return json(200, await putSettings(env, now, actor, await body(request)));
    return json(404, { error: "not found" });
  } catch (err) {
    if (err instanceof AuthError) return json(err.status, { error: err.message });
    if (err instanceof HttpError) return json(err.status, { error: err.message });
    if (err instanceof ScheduleError) return json(422, { error: err.message });
    return json(500, { error: "internal error" });
  }
}

function requirePostId(id: string): string {
  if (!POST_ID_RE.test(id)) throw new HttpError(400, "invalid post id");
  return id;
}

async function getPost(env: Env, id: string): Promise<PostRow> {
  const post = await env.DB.prepare("SELECT * FROM posts WHERE post_id = ?").bind(id).first<PostRow>();
  if (!post) throw new HttpError(404, "post not found");
  return post;
}

// ── push: a locally approved post is delegated to the cloud ─────────────
async function pushPost(env: Env, now: number, actor: string, rawId: string, b: Record<string, unknown>) {
  const id = requirePostId(rawId);
  const { text, approved_hash, approved_at, language, plan_date } = b as Record<string, string>;
  if (typeof text !== "string" || !text.trim()) throw new HttpError(400, "text is required");
  if (!HEX64.test(String(approved_hash))) throw new HttpError(400, "approved_hash must be 64 hex chars");
  if (typeof approved_at !== "string") throw new HttpError(400, "approved_at is required");
  parseIsoUtc(approved_at);
  const h = await contentHash(text);
  if (h !== approved_hash) throw new HttpError(409, "text does not match the approved hash");
  const existing = await env.DB.prepare("SELECT * FROM posts WHERE post_id = ?").bind(id).first<PostRow>();
  if (existing) {
    if (existing.approved_hash === h) return { post_id: id, state: existing.state, unchanged: true };
    throw new HttpError(409, `post already exists with a different approved text (${existing.state})`);
  }
  const at = isoUtc(now);
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO posts (post_id, text, language, plan_date, state, content_hash, approved_hash,
                    approved_at, pushed_at, pushed_by, updated_at) VALUES (?, ?, ?, ?, 'READY_TO_PUBLISH', ?, ?, ?, ?, ?, ?)`)
      .bind(id, text, String(language ?? "en"), plan_date ?? null, h, h, approved_at, at, actor, at),
    event(env.DB, now, "post.pushed", actor, id, { approved_hash: h }),
  ]);
  return { post_id: id, state: "READY_TO_PUBLISH" };
}

// ── consent: explicit, per post and per slot ────────────────────────────
async function consent(env: Env, now: number, actor: string, b: Record<string, unknown>) {
  const id = requirePostId(String(b.post_id ?? ""));
  const post = await getPost(env, id);
  if (post.state !== "READY_TO_PUBLISH") throw new HttpError(409, `post is ${post.state}`);
  if ((await contentHash(post.text)) !== post.approved_hash) throw new HttpError(409, "post text does not match its approved hash");
  const settings = await loadSettings(env.DB);
  const slot = slotById(loadSchedule(settings as unknown as Record<string, unknown>), String(b.slot_id ?? ""));
  const slotMs = parseIsoUtc(slot.utc);
  if (slotMs <= now + 60e3) throw new HttpError(409, "slot is in the past or less than a minute away");
  if (slotMs > now + 60 * 86400e3) throw new HttpError(409, "slot is more than 60 days ahead");
  const consentId = crypto.randomUUID();
  const at = isoUtc(now);
  try {
    await env.DB.batch([
      env.DB.prepare(`INSERT INTO consents (consent_id, post_id, slot_id, slot_utc, approved_hash, status, created_at, created_by)
                      VALUES (?, ?, ?, ?, ?, 'active', ?, ?)`)
        .bind(consentId, id, slot.slot_id, slot.utc, post.approved_hash, at, actor),
      env.DB.prepare(`INSERT INTO jobs (job_id, slot_id, slot_utc, state, post_id, consent_id, reason, updated_at)
                      VALUES (?, ?, ?, 'SCHEDULED', ?, ?, 'consented', ?)
                      ON CONFLICT(slot_id) DO UPDATE SET state = 'SCHEDULED', post_id = excluded.post_id,
                        consent_id = excluded.consent_id, reason = 'consented', updated_at = excluded.updated_at`)
        .bind(`job-${slot.slot_id}`, slot.slot_id, slot.utc, id, consentId, at),
      event(env.DB, now, "consent.created", actor, id, { consent_id: consentId, slot_id: slot.slot_id }),
    ]);
  } catch (err) {
    if (String((err as Error).message).includes("UNIQUE")) {
      throw new HttpError(409, "this post or this slot already has an active consent");
    }
    throw err;
  }
  return { consent_id: consentId, post_id: id, slot };
}

async function revoke(env: Env, now: number, actor: string, consentId: string) {
  const c = await env.DB.prepare("SELECT * FROM consents WHERE consent_id = ?").bind(consentId).first<ConsentRow>();
  if (!c) throw new HttpError(404, "consent not found");
  if (c.status !== "active") throw new HttpError(409, `consent is ${c.status}`);
  const res = await env.DB.batch([
    env.DB.prepare("UPDATE consents SET status = 'revoked', resolved_at = ?, reason = 'owner' WHERE consent_id = ? AND status = 'active'")
      .bind(isoUtc(now), consentId),
    env.DB.prepare("UPDATE jobs SET state = 'SKIPPED', reason = 'revoked', updated_at = ? WHERE slot_id = ? AND state = 'SCHEDULED'")
      .bind(isoUtc(now), c.slot_id),
    event(env.DB, now, "consent.revoked", actor, c.post_id, { consent_id: consentId }),
  ]);
  if (res[0].meta.changes !== 1) throw new HttpError(409, "consent was already used or changed");
  return { consent_id: consentId, status: "revoked" };
}

async function withdraw(env: Env, now: number, actor: string, rawId: string) {
  const id = requirePostId(rawId);
  const res = await env.DB.batch([
    env.DB.prepare("UPDATE posts SET state = 'WITHDRAWN', updated_at = ? WHERE post_id = ? AND state IN ('READY_TO_PUBLISH', 'PUBLISH_FAILED')")
      .bind(isoUtc(now), id),
    env.DB.prepare("UPDATE consents SET status = 'revoked', resolved_at = ?, reason = 'withdrawn' WHERE post_id = ? AND status = 'active'")
      .bind(isoUtc(now), id),
    event(env.DB, now, "post.withdrawn", actor, id),
  ]);
  if (res[0].meta.changes !== 1) throw new HttpError(409, "only READY_TO_PUBLISH or PUBLISH_FAILED posts can be withdrawn");
  return { post_id: id, state: "WITHDRAWN" };
}

async function rearm(env: Env, now: number, actor: string, rawId: string) {
  const id = requirePostId(rawId);
  const post = await getPost(env, id);
  if (post.state !== "PUBLISH_FAILED") throw new HttpError(409, "only PUBLISH_FAILED posts can be re-armed");
  await env.DB.batch([
    env.DB.prepare("UPDATE posts SET state = 'READY_TO_PUBLISH', updated_at = ? WHERE post_id = ? AND state = 'PUBLISH_FAILED'")
      .bind(isoUtc(now), id),
    event(env.DB, now, "post.rearmed", actor, id),
  ]);
  return { post_id: id, state: "READY_TO_PUBLISH" };
}

const RECONCILE_URL = /^https:\/\/www\.linkedin\.com\/(?:feed\/update\/(urn:li:(?:share|ugcPost|activity):\d+)\/?|posts\/[A-Za-z0-9_%.-]+)(?:\?.*)?$/;

async function reconcile(env: Env, now: number, actor: string, rawId: string, b: Record<string, unknown>) {
  const id = requirePostId(rawId);
  const post = await getPost(env, id);
  if (!["NEEDS_RECONCILE", "PUBLISHING"].includes(post.state)) {
    throw new HttpError(409, `only NEEDS_RECONCILE or interrupted PUBLISHING posts are reconciled (${post.state})`);
  }
  const decision = b.decision;
  const at = isoUtc(now);
  const resolution = JSON.stringify({ at, decision, by: actor });
  if (decision === "published") {
    const m = RECONCILE_URL.exec(String(b.url ?? ""));
    if (!m) throw new HttpError(400, "expected a LinkedIn post URL");
    await env.DB.batch([
      env.DB.prepare(`UPDATE publications SET state = 'published', url = ?, remote_id = COALESCE(?, remote_id),
                      published_at = COALESCE(published_at, ?), verified_by = 'owner', resolution = ?, updated_at = ? WHERE post_id = ?`)
        .bind(String(b.url), m[1] ?? null, at, resolution, at, id),
      env.DB.prepare("UPDATE posts SET state = 'PUBLISHED', updated_at = ? WHERE post_id = ?").bind(at, id),
      event(env.DB, now, "publish.reconciled", actor, id, { decision }),
    ]);
    return { post_id: id, state: "PUBLISHED" };
  }
  if (decision === "not_published") {
    await env.DB.batch([
      env.DB.prepare(`UPDATE publications SET state = 'not_published_confirmed', resolution = ?, updated_at = ? WHERE post_id = ?`)
        .bind(resolution, at, id),
      env.DB.prepare("UPDATE posts SET state = 'READY_TO_PUBLISH', updated_at = ? WHERE post_id = ?").bind(at, id),
      event(env.DB, now, "publish.reconciled", actor, id, { decision }),
    ]);
    return { post_id: id, state: "READY_TO_PUBLISH" };
  }
  throw new HttpError(400, "decision must be 'published' (with url) or 'not_published'");
}

// ── settings (never secrets) ────────────────────────────────────────────
async function putSettings(env: Env, now: number, actor: string, b: Record<string, unknown>) {
  const updates: [string, string][] = [];
  for (const [k, v] of Object.entries(b)) {
    if (!(SETTING_KEYS as readonly string[]).includes(k)) throw new HttpError(400, `unknown setting ${k}`);
    if (k === "auto_publish") {
      if (typeof v !== "boolean") throw new HttpError(400, "auto_publish must be true or false");
      updates.push([k, String(v)]);
    } else if (k === "cadence") {
      updates.push([k, JSON.stringify(v)]);
    } else if (k === "max_lateness_minutes") {
      if (!Number.isInteger(v) || (v as number) < 5 || (v as number) > 1440) throw new HttpError(400, "max_lateness_minutes must be 5..1440");
      updates.push([k, String(v)]);
    } else if (k === "provider") {
      if (!["none", "linkedin_api"].includes(String(v))) throw new HttpError(400, "provider must be none or linkedin_api");
      updates.push([k, String(v)]);
    } else if (k === "token_expires_at") {
      parseIsoUtc(String(v));
      updates.push([k, String(v)]);
    } else {
      updates.push([k, String(v)]);
    }
  }
  const current = await loadSettings(env.DB);
  const merged = { ...current } as Record<string, unknown>;
  for (const [k, v] of updates) merged[k] = k === "cadence" ? JSON.parse(v) : v;
  if (merged.timezone || merged.cadence) loadSchedule(merged);            // validate before writing
  if (b.api_version !== undefined && !/^\d{6}$/.test(String(b.api_version))) throw new HttpError(400, "api_version must be YYYYMM");
  if (b.person_urn !== undefined && !/^urn:li:person:[A-Za-z0-9_-]+$/.test(String(b.person_urn))) {
    throw new HttpError(400, "person_urn must look like urn:li:person:<id>");
  }
  if (b.visibility !== undefined && !["PUBLIC", "CONNECTIONS", "LOGGED_IN"].includes(String(b.visibility))) {
    throw new HttpError(400, "visibility must be PUBLIC, CONNECTIONS or LOGGED_IN");
  }
  const at = isoUtc(now);
  await env.DB.batch([
    ...updates.map(([k, v]) => env.DB.prepare(
      "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
    ).bind(k, v, at)),
    event(env.DB, now, "settings.updated", actor, null, { keys: updates.map(([k]) => k) }),
  ]);
  return { updated: updates.map(([k]) => k) };
}

// ── snapshot for the dashboard (never contains the token) ───────────────
export async function snapshot(env: Env, now: number) {
  const settings = await loadSettings(env.DB);
  const q = async <T>(sql: string) => (await env.DB.prepare(sql).all<T>()).results;
  const [posts, consents, jobs, publications, events] = await Promise.all([
    q<PostRow>("SELECT * FROM posts ORDER BY plan_date, post_id"),
    q<ConsentRow>("SELECT * FROM consents ORDER BY slot_utc DESC LIMIT 200"),
    q("SELECT * FROM jobs ORDER BY slot_utc DESC LIMIT 200"),
    q<Record<string, unknown>>("SELECT * FROM publications"),
    q("SELECT * FROM events ORDER BY id DESC LIMIT 200"),
  ]);
  let upcoming: unknown[] = [], scheduleError: string | null = null;
  try {
    upcoming = slotsBetween(loadSchedule(settings as unknown as Record<string, unknown>), now, now + 14 * 86400e3);
  } catch (err) {
    scheduleError = (err as Error).message;
  }
  const active = consents.filter((c) => c.status === "active").sort((a, b) => a.slot_utc.localeCompare(b.slot_utc));
  return {
    mode: "cloud",
    now: isoUtc(now),
    settings: { ...settings, token_present: Boolean(env.LINKEDIN_TOKEN) },
    next_scheduled_publication: active[0] ?? null,
    schedule_error: scheduleError,
    upcoming_slots: upcoming,
    posts, consents, jobs,
    publications: publications.map((p) => ({ ...p, attempts: JSON.parse(String(p.attempts ?? "[]")) })),
    events,
  };
}
