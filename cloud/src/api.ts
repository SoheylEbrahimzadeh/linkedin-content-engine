// Authenticated JSON API (behind Cloudflare Access). The API can never approve
// content: posts are accepted only with a hash that matches their text, and
// only a separate, explicit consent schedules one post for one slot.

import { AuthError, verifyAccess, type CertsFetcher } from "./auth";
import { event, isSchemaMissing, loadSettings, SETTING_KEYS, type ConsentRow, type Env, type PostRow } from "./db";
import { isoUtc, loadSchedule, parseIsoUtc, ScheduleError, slotById, slotsBetween } from "./schedule";
import { applyMigrations, MigrationConflict, migrationStatus } from "./migrations";
import { contentHash, sha256Bytes } from "./text";
import { IDENTITY_HTTP, linkedinIdentity } from "./identity";
import { cancelDecision, createDecision, DecisionError, listDecisions, resolveDecision } from "./decisions";
import { publishNow } from "./runner";
import type { FetchLike } from "./linkedin";

const POST_ID_RE = /^\d{8}-[a-z0-9-]{1,56}$/;
const HEX64 = /^[0-9a-f]{64}$/;
export const MAX_IMAGE_BYTES = 1_500_000;   // D1 rows are limited to ~2 MB
const MAGIC: [number[], string][] = [[[0x89, 0x50, 0x4e, 0x47], "png"], [[0xff, 0xd8, 0xff], "jpeg"],
  [[0x47, 0x49, 0x46, 0x38], "gif"]];

type ImageIn = { data: Uint8Array; sha256: string; alt: string };

async function parseImage(raw: unknown): Promise<ImageIn | null> {
  if (raw === undefined || raw === null) return null;
  const img = raw as { data_base64?: unknown; sha256?: unknown; alt_text?: unknown };
  if (typeof img.data_base64 !== "string" || !HEX64.test(String(img.sha256))) {
    throw new HttpError(400, "image needs data_base64 and sha256");
  }
  const alt = String(img.alt_text ?? "").trim();
  if (!alt || alt.length > 4086) throw new HttpError(400, "image alt_text is required (≤ 4086 chars)");
  let data: Uint8Array;
  try {
    data = Uint8Array.from(atob(img.data_base64), (ch) => ch.charCodeAt(0));
  } catch {
    throw new HttpError(400, "image data is not valid base64");
  }
  if (data.length === 0) throw new HttpError(400, "image is empty");
  if (data.length > MAX_IMAGE_BYTES) throw new HttpError(413, `image is larger than ${MAX_IMAGE_BYTES} bytes; publish it locally`);
  if (!MAGIC.some(([m]) => m.every((b, i) => data[i] === b))) throw new HttpError(400, "image must be PNG, JPEG or GIF");
  if ((await sha256Bytes(data)) !== img.sha256) throw new HttpError(409, "image does not match its approved hash");
  return { data, sha256: String(img.sha256), alt };
}
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
                                certs?: CertsFetcher,
                                fetchImpl: FetchLike = (input, init) => fetch(input, init)): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/api/health" && request.method === "GET") return json(200, { ok: true });
  try {
    const who = await verifyAccess(request, env, now, certs);
    const actor = who.subject;
    const human = () => {
      if (!who.human) throw new HttpError(403, "this action needs a person signed in through Cloudflare Access, not a service token");
    };
    if (request.method !== "GET") requireSameOriginClient(request, url);
    const m = (re: RegExp) => re.exec(url.pathname);
    let r: RegExpExecArray | null;
    if (request.method === "GET" && url.pathname === "/api/snapshot") return json(200, await snapshot(env, now));
    if (request.method === "GET" && url.pathname === "/api/pipeline") return await getPipeline(env);
    if (request.method === "GET" && url.pathname === "/api/linkedin/identity") {
      const report = await linkedinIdentity(env.LINKEDIN_TOKEN, await loadSettings(env.DB), fetchImpl);
      return json(IDENTITY_HTTP[report.status], report);
    }
    if (request.method === "GET" && url.pathname === "/api/decisions") return json(200, await listDecisions(env, url.searchParams.get("status")));
    if (request.method === "POST" && url.pathname === "/api/decisions") return json(201, await createDecision(env, now, who, await body(request)));
    if (request.method === "POST" && (r = m(/^\/api\/decisions\/(d-[0-9a-f-]{36})\/resolve$/))) {
      if (request.headers.get("x-lce-client") !== "cli") throw new HttpError(403, "decisions are resolved by the private workflow (CLI)");
      return json(200, await resolveDecision(env, now, actor, r[1], await body(request)));
    }
    if (request.method === "DELETE" && (r = m(/^\/api\/decisions\/(d-[0-9a-f-]{36})$/))) return json(200, await cancelDecision(env, now, who, r[1]));
    if (request.method === "GET" && (r = m(/^\/api\/posts\/([^/]+)\/image$/))) return await postImage(env, r[1]);
    if (request.method === "GET" && url.pathname === "/api/preview-media") return json(200, await previewMediaList(env));
    if (request.method === "PUT" && (r = m(/^\/api\/preview-media\/([^/]+)$/))) {
      if (request.headers.get("x-lce-client") !== "cli") throw new HttpError(403, "preview media is uploaded by lce cloud sync");
      return json(200, await putPreviewMedia(env, now, actor, r[1], await body(request)));
    }
    if (request.method === "POST" && (r = m(/^\/api\/posts\/([^/]+)\/publish-now$/))) {
      human();
      const id = requirePostId(r[1]);
      const b = await body(request);
      requirePhrase(b, `PUBLISH NOW ${id}`);
      if (!HEX64.test(String(b.approved_hash ?? ""))) throw new HttpError(400, "approved_hash is required");
      const rep = await publishNow(env, now, actor, id, String(b.approved_hash), fetchImpl);
      return json(rep.http, rep);
    }
    if (request.method === "GET" && url.pathname === "/api/migrations") return json(200, await migrationStatus(env.DB));
    if (request.method === "POST" && url.pathname === "/api/migrations") return json(200, await migrate(env, now, actor, await body(request)));
    if (request.method === "PUT" && url.pathname === "/api/pipeline") return json(200, await putPipeline(env, now, actor, request));
    if (request.method === "PUT" && (r = m(/^\/api\/posts\/([^/]+)$/))) return json(200, await pushPost(env, now, actor, r[1], await body(request)));
    if (request.method === "POST" && (r = m(/^\/api\/posts\/([^/]+)\/withdraw$/))) return json(200, await withdraw(env, now, actor, r[1], await body(request)));
    if (request.method === "POST" && (r = m(/^\/api\/posts\/([^/]+)\/rearm$/))) return json(200, await rearm(env, now, actor, r[1], await body(request)));
    if (request.method === "POST" && (r = m(/^\/api\/posts\/([^/]+)\/reconcile$/))) return json(200, await reconcile(env, now, actor, r[1], await body(request)));
    if (request.method === "POST" && url.pathname === "/api/consents") return json(201, await consent(env, now, actor, await body(request)));
    if (request.method === "DELETE" && (r = m(/^\/api\/consents\/([^/]+)$/))) return json(200, await revoke(env, now, actor, r[1]));
    if (request.method === "PUT" && url.pathname === "/api/settings") return json(200, await putSettings(env, now, actor, await body(request), who.human));
    return json(404, { error: "not found" });
  } catch (err) {
    if (err instanceof AuthError) return json(err.status, { error: err.message });
    if (err instanceof HttpError) return json(err.status, { error: err.message });
    if (err instanceof DecisionError) return json(err.status, { error: err.message });
    if (err instanceof ScheduleError) return json(422, { error: err.message });
    if (err instanceof MigrationConflict) return json(409, { error: err.message });
    if (isSchemaMissing(err)) return json(503, { error: "database schema missing: apply cloud/migrations to D1" });
    return json(500, { error: "internal error" });
  }
}

// Mutations must come from the CLI or the dashboard: a custom header forces a CORS
// preflight (never granted), and a foreign Origin is refused. Together with Access
// this blocks cross-site requests that would ride on the Access cookie.
function requireSameOriginClient(request: Request, url: URL): void {
  if (!["cli", "dashboard"].includes(request.headers.get("x-lce-client") ?? "")) {
    throw new HttpError(403, "missing x-lce-client header");
  }
  const origin = request.headers.get("origin");
  if (origin && origin !== url.origin) throw new HttpError(403, "cross-origin request refused");
}

// Actions that can lead to a publication need the owner's typed phrase.
function requirePhrase(b: Record<string, unknown>, phrase: string): void {
  if (String(b.confirm ?? "").trim() !== phrase) throw new HttpError(428, `type '${phrase}' to confirm`);
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
  const image = await parseImage(b.image);
  const existing = await env.DB.prepare("SELECT * FROM posts WHERE post_id = ?").bind(id).first<PostRow>();
  const at = isoUtc(now);
  if (existing) {
    const had = await env.DB.prepare("SELECT sha256 FROM post_images WHERE post_id = ?").bind(id).first<{ sha256: string }>();
    const same = existing.approved_hash === h && (had?.sha256 ?? null) === (image?.sha256 ?? null);
    if (existing.state === "WITHDRAWN") {
      // Re-delegation after a withdrawal (the owner typed DELEGATE again locally).
      // A new approved version replaces the withdrawn one; nothing was published.
      await env.DB.batch([
        env.DB.prepare(`UPDATE posts SET text = ?, language = ?, plan_date = ?, state = 'READY_TO_PUBLISH', content_hash = ?,
                        approved_hash = ?, approved_at = ?, pushed_at = ?, pushed_by = ?, updated_at = ?
                        WHERE post_id = ? AND state = 'WITHDRAWN'`)
          .bind(text, String(language ?? "en"), plan_date ?? null, h, h, approved_at, at, actor, at, id),
        env.DB.prepare("DELETE FROM post_images WHERE post_id = ?").bind(id),
        ...(image ? [env.DB.prepare("INSERT INTO post_images (post_id, data, sha256, alt_text, bytes) VALUES (?, ?, ?, ?, ?)")
          .bind(id, image.data, image.sha256, image.alt, image.data.length)] : []),
        event(env.DB, now, "post.redelegated", actor, id, { approved_hash: h, same_version: same }),
      ]);
      return { post_id: id, state: "READY_TO_PUBLISH", redelegated: true };
    }
    if (same) return { post_id: id, state: existing.state, unchanged: true };
    throw new HttpError(409, `post already exists with a different approved text or image (${existing.state})`);
  }
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO posts (post_id, text, language, plan_date, state, content_hash, approved_hash,
                    approved_at, pushed_at, pushed_by, updated_at) VALUES (?, ?, ?, ?, 'READY_TO_PUBLISH', ?, ?, ?, ?, ?, ?)`)
      .bind(id, text, String(language ?? "en"), plan_date ?? null, h, h, approved_at, at, actor, at),
    ...(image ? [env.DB.prepare("INSERT INTO post_images (post_id, data, sha256, alt_text, bytes) VALUES (?, ?, ?, ?, ?)")
      .bind(id, image.data, image.sha256, image.alt, image.data.length)] : []),
    event(env.DB, now, "post.pushed", actor, id, { approved_hash: h, image_sha256: image?.sha256 ?? null }),
  ]);
  return { post_id: id, state: "READY_TO_PUBLISH", image: image ? { sha256: image.sha256, bytes: image.data.length } : null };
}

// ── consent: explicit, per post and per slot ────────────────────────────
async function consent(env: Env, now: number, actor: string, b: Record<string, unknown>) {
  const id = requirePostId(String(b.post_id ?? ""));
  requirePhrase(b, `SCHEDULE ${id}`);
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

async function withdraw(env: Env, now: number, actor: string, rawId: string, b: Record<string, unknown>) {
  const id = requirePostId(rawId);
  requirePhrase(b, `WITHDRAW ${id}`);
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

async function rearm(env: Env, now: number, actor: string, rawId: string, b: Record<string, unknown>) {
  const id = requirePostId(rawId);
  requirePhrase(b, `REARM ${id}`);
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
  requirePhrase(b, `RECONCILE ${id}`);
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
const PROFILE_URL_RE = /^https:\/\/www\.linkedin\.com\/in\/[A-Za-z0-9_%-]{3,100}\/?$/;

async function putSettings(env: Env, now: number, actor: string, raw: Record<string, unknown>, human = false) {
  const { confirm, ...b } = raw;
  // Turning publication ON (auto-publish, or releasing the emergency stop) needs a person and the phrase.
  if (b.auto_publish === true) {
    if (!human) throw new HttpError(403, "only a signed-in person can enable auto-publish");
    requirePhrase({ confirm }, "ENABLE AUTO-PUBLISH");
  }
  if (b.emergency_stop === false) {
    if (!human) throw new HttpError(403, "only a signed-in person can release the emergency stop");
    requirePhrase({ confirm }, "RELEASE EMERGENCY STOP");
  }
  const updates: [string, string][] = [];
  for (const [k, v] of Object.entries(b)) {
    if (!(SETTING_KEYS as readonly string[]).includes(k)) throw new HttpError(400, `unknown setting ${k}`);
    if (k === "auto_publish" || k === "emergency_stop") {
      if (typeof v !== "boolean") throw new HttpError(400, `${k} must be true or false`);
      updates.push([k, String(v)]);
    } else if (k === "display_name") {
      const name = String(v).trim();
      if (!name || name.length > 100) throw new HttpError(400, "display_name must be 1..100 characters");
      updates.push([k, name]);
    } else if (k === "profile_url") {
      if (!PROFILE_URL_RE.test(String(v))) throw new HttpError(400, "profile_url must be https://www.linkedin.com/in/<handle>/");
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
  const imgs = new Map((await q<{ post_id: string; sha256: string; bytes: number }>(
    "SELECT post_id, sha256, bytes FROM post_images")).map((r) => [r.post_id, { sha256: r.sha256, bytes: r.bytes }]));
  let upcoming: unknown[] = [], scheduleError: string | null = null;
  try {
    upcoming = slotsBetween(loadSchedule(settings as unknown as Record<string, unknown>), now, now + 31 * 86400e3);
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
    posts: posts.map((p) => ({ ...p, image: imgs.get(p.post_id) ?? null })), consents, jobs,
    publications: publications.map((p) => ({ ...p, attempts: JSON.parse(String(p.attempts ?? "[]")) })),
    events,
    decisions: (await listDecisions(env, null)).decisions.slice(0, 100),
    preview_media: (await previewMediaList(env)).media,
  };
}

async function previewMediaList(env: Env) {
  const rows = (await env.DB.prepare("SELECT post_id, sha256, bytes, mime, alt_text, updated_at FROM preview_media ORDER BY post_id")
    .all<Record<string, unknown>>()).results;
  return { media: rows };
}

async function putPreviewMedia(env: Env, now: number, actor: string, rawId: string, b: Record<string, unknown>) {
  const id = requirePostId(rawId);
  const img = await parseImage({ data_base64: b.data_base64, sha256: b.sha256, alt_text: String(b.alt_text ?? "").trim() || "image" });
  if (!img) throw new HttpError(400, "image is required");
  const type = MAGIC.find(([mg]) => mg.every((x, i) => img.data[i] === x))![1];
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO preview_media (post_id, data, sha256, bytes, mime, alt_text, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(post_id) DO UPDATE SET data = excluded.data, sha256 = excluded.sha256, bytes = excluded.bytes,
        mime = excluded.mime, alt_text = excluded.alt_text, updated_at = excluded.updated_at`)
      .bind(id, img.data, img.sha256, img.data.length, `image/${type}`, String(b.alt_text ?? ""), isoUtc(now)),
    event(env.DB, now, "preview_media.stored", actor, id, { sha256: img.sha256 }),
  ]);
  return { post_id: id, sha256: img.sha256, bytes: img.data.length };
}

// Image of a pushed post (D1), for the Control Center preview. Access-protected like every API route.
async function postImage(env: Env, rawId: string): Promise<Response> {
  const id = requirePostId(rawId);
  // The approved image in the publish queue wins; otherwise the pipeline's preview copy (LCE-037).
  const row = await env.DB.prepare("SELECT data FROM post_images WHERE post_id = ?").bind(id).first<{ data: ArrayBuffer }>()
    ?? await env.DB.prepare("SELECT data FROM preview_media WHERE post_id = ?").bind(id).first<{ data: ArrayBuffer }>();
  if (!row) return json(404, { error: "no image in the cloud for this post" });
  const data = new Uint8Array(row.data);
  const type = MAGIC.find(([m]) => m.every((x, i) => data[i] === x))?.[1];
  if (!type) return json(415, { error: "unrecognised image" });
  return new Response(data, { status: 200, headers: { "Content-Type": `image/${type}`, "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'", "Referrer-Policy": "no-referrer" } });
}


// ── LCE-013: private-pipeline mirror for the cloud Web Control Center ──
export const MAX_PIPELINE_BYTES = 1_500_000;   // D1 rows are limited to ~2 MB

async function putPipeline(env: Env, now: number, actor: string, request: Request) {
  const raw = await request.text();
  const bytes = new TextEncoder().encode(raw).length;
  if (bytes > MAX_PIPELINE_BYTES) throw new HttpError(413, `pipeline snapshot is larger than ${MAX_PIPELINE_BYTES} bytes`);
  let snap: { schema?: unknown; meta?: { mode?: unknown; generated_at?: unknown } };
  try {
    snap = JSON.parse(raw);
  } catch {
    throw new HttpError(400, "expected a JSON object");
  }
  if (!snap || typeof snap !== "object" || Array.isArray(snap)) throw new HttpError(400, "expected a JSON object");
  if (typeof snap.schema !== "number" || snap.meta?.mode !== "real") {
    throw new HttpError(400, "not a real-mode dashboard snapshot (lce cloud sync builds one)");
  }
  const generated = typeof snap.meta.generated_at === "string" ? snap.meta.generated_at : null;
  const sha = await sha256Bytes(new TextEncoder().encode(raw));
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO pipeline_snapshot (id, body, sha256, bytes, generated_at, received_at, received_by)
      VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body, sha256 = excluded.sha256,
      bytes = excluded.bytes, generated_at = excluded.generated_at, received_at = excluded.received_at,
      received_by = excluded.received_by`).bind(raw, sha, bytes, generated, isoUtc(now), actor),
    event(env.DB, now, "pipeline.synced", actor, null, { bytes, sha256: sha, generated_at: generated }),
  ]);
  return { stored: true, bytes, sha256: sha, received_at: isoUtc(now) };
}

async function getPipeline(env: Env): Promise<Response> {
  const row = await env.DB.prepare("SELECT body, received_at, received_by, sha256 FROM pipeline_snapshot WHERE id = 1")
    .first<{ body: string; received_at: string; received_by: string; sha256: string }>();
  if (!row) return json(404, { error: "no pipeline snapshot yet: run `lce cloud sync` from the private data" });
  const snap = JSON.parse(row.body) as { meta?: Record<string, unknown> };
  snap.meta = { ...(snap.meta ?? {}), mirror: { received_at: row.received_at, received_by: row.received_by,
    sha256: row.sha256 } };
  return json(200, snap);
}


// ── LCE-032: apply pending D1 migrations (Access + CLI header + typed phrase) ──
async function migrate(env: Env, now: number, actor: string, b: Record<string, unknown>) {
  requirePhrase(b, "APPLY MIGRATIONS");
  const applied = await applyMigrations(env.DB);
  if (applied.length) {
    await event(env.DB, now, "migrations.applied", actor, null, { applied }).run();
  }
  return { applied_now: applied, ...(await migrationStatus(env.DB)) };
}
