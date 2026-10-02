// Cron runner: publishes at most ONE consented, human-approved post per run.
//
// Gates (all required): kill switch on, provider linkedin_api, valid config,
// token secret present and not expired, active consent due within the window,
// post READY_TO_PUBLISH, hash(text) == approved hash == consented hash.
//
// Duplicate protection: one D1 transaction consumes the consent, moves the
// post to PUBLISHING and records the intent BEFORE the single request. A
// concurrent run cannot win the same claim. Ambiguous results become
// NEEDS_RECONCILE and are never retried.

import { event, isSchemaMissing, loadSettings, type ConsentRow, type Env, type PostRow, type Settings } from "./db";
import { configProblems, publish, type FetchLike, type ImageInput, type LinkedInConfig, type PublishResult } from "./linkedin";
import { isoUtc, parseIsoUtc } from "./schedule";
import { contentHash, sha256Bytes, sha256Hex, stripText, toLittle } from "./text";
import { linkedinIdentity } from "./identity";

const ACTOR = "cron";

export type RunReport = { status: string; consent_id?: string; post_id?: string; outcome?: string; detail?: string };

function linkedinConfig(s: Settings): LinkedInConfig {
  return { apiVersion: s.api_version ?? "", personUrn: s.person_urn ?? "", visibility: s.visibility, maxChars: 3000 };
}

const invalidateAs = invalidate;
async function invalidate(env: Env, now: number, c: ConsentRow, reason: string, jobState = "FAILED",
                          actor = ACTOR): Promise<RunReport> {
  await env.DB.batch([
    env.DB.prepare("UPDATE consents SET status = 'invalid', resolved_at = ?, reason = ? WHERE consent_id = ? AND status = 'active'")
      .bind(isoUtc(now), reason, c.consent_id),
    env.DB.prepare("UPDATE jobs SET state = ?, reason = ?, updated_at = ? WHERE slot_id = ?")
      .bind(jobState, reason, isoUtc(now), c.slot_id),
    event(env.DB, now, "publish.blocked", actor, c.post_id, { consent_id: c.consent_id, reason }),
  ]);
  return { status: "blocked", consent_id: c.consent_id, post_id: c.post_id, detail: reason };
}

export async function runScheduled(env: Env, now: number, fetchImpl: FetchLike): Promise<RunReport> {
  let due: ConsentRow[];
  try {
    due = (await env.DB.prepare(
      "SELECT * FROM consents WHERE status = 'active' AND slot_utc <= ? ORDER BY slot_utc LIMIT 5",
    ).bind(isoUtc(now)).all<ConsentRow>()).results;
  } catch (err) {
    // Deployed without migrations: nothing can be due, so report it instead of throwing every run.
    if (isSchemaMissing(err)) return { status: "schema_missing", detail: "apply cloud/migrations to D1" };
    throw err;
  }
  if (due.length === 0) return { status: "idle" };            // no writes when nothing is due

  const settings = await loadSettings(env.DB);
  const lateMs = settings.max_lateness_minutes * 60e3;
  for (const c of due) {
    if (now - parseIsoUtc(c.slot_utc) > lateMs) {
      await env.DB.batch([
        env.DB.prepare("UPDATE consents SET status = 'expired', resolved_at = ?, reason = 'missed_slot' WHERE consent_id = ? AND status = 'active'")
          .bind(isoUtc(now), c.consent_id),
        env.DB.prepare("UPDATE jobs SET state = 'SKIPPED', reason = 'missed_slot', updated_at = ? WHERE slot_id = ?")
          .bind(isoUtc(now), c.slot_id),
        event(env.DB, now, "publish.missed", ACTOR, c.post_id, { consent_id: c.consent_id, slot_id: c.slot_id }),
      ]);
      continue;
    }
    // Emergency stop and kill switch: nothing is written; the consent expires if they stay on/off.
    if (settings.emergency_stop) return { status: "emergency_stop", consent_id: c.consent_id };
    if (!settings.auto_publish) return { status: "kill_switch_off", consent_id: c.consent_id };
    // LCE-040: a scheduled post needs a same-day `current` freshness check of exactly the approved text.
    // Without it nothing is written; the consent waits (and expires as missed if no check arrives).
    const fresh = await freshFor(env, c, settings);
    if (!fresh.ok) return { status: "freshness_pending", consent_id: c.consent_id, post_id: c.post_id, detail: fresh.why };
    return await publishOne(env, now, c, settings, fetchImpl);
  }
  return { status: "missed" };
}

export function localDate(iso: string, tz: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export async function freshFor(env: Env, c: ConsentRow, settings: Settings): Promise<{ ok: boolean; why: string }> {
  const row = await env.DB.prepare("SELECT * FROM freshness WHERE post_id = ?").bind(c.post_id)
    .first<{ check_date: string; status: string; content_hash: string; received_at: string }>();
  if (!row) return { ok: false, why: "no same-day freshness check" };
  const day = localDate(c.slot_utc, settings.timezone || "UTC");
  if (row.check_date !== day) return { ok: false, why: `last freshness check is for ${row.check_date}, not ${day}` };
  if (!row.received_at || localDate(row.received_at, settings.timezone || "UTC") !== day) {
    return { ok: false, why: "freshness check was not received on the publishing day" };
  }
  if (row.status !== "current") return { ok: false, why: `freshness status ${row.status}` };
  if (row.content_hash !== c.approved_hash) return { ok: false, why: "freshness check was for another text version" };
  return { ok: true, why: "" };
}

async function publishOne(env: Env, now: number, c: ConsentRow, settings: Settings,
                          fetchImpl: FetchLike, actor = ACTOR): Promise<RunReport> {
  const invalidate = (e: Env, n: number, cr: ConsentRow, reason: string, jobState = "FAILED") =>
    invalidateAs(e, n, cr, reason, jobState, actor);
  if (settings.provider !== "linkedin_api") return invalidate(env, now, c, "provider_not_enabled");
  const cfg = linkedinConfig(settings);
  const problems = configProblems(cfg);
  if (problems.length) return invalidate(env, now, c, `config: ${problems.join("; ")}`);
  const token = env.LINKEDIN_TOKEN;
  if (!token) return invalidate(env, now, c, "token_missing");
  if (settings.token_expires_at && parseIsoUtc(settings.token_expires_at) <= now) {
    return invalidate(env, now, c, "token_expired");
  }
  const post = await env.DB.prepare("SELECT * FROM posts WHERE post_id = ?").bind(c.post_id).first<PostRow>();
  if (!post) return invalidate(env, now, c, "post_missing");
  if (post.state !== "READY_TO_PUBLISH") return invalidate(env, now, c, `post_is_${post.state}`);
  const h = await contentHash(post.text);
  if (h !== post.approved_hash || h !== post.content_hash || h !== c.approved_hash) {
    return invalidate(env, now, c, "hash_mismatch");
  }
  if (stripText(post.text).length > cfg.maxChars) return invalidate(env, now, c, "text_too_long");
  const img = await env.DB.prepare("SELECT data, sha256, alt_text FROM post_images WHERE post_id = ?")
    .bind(post.post_id).first<{ data: ArrayBuffer; sha256: string; alt_text: string }>();
  let image: ImageInput | undefined;
  if (img) {
    const data = new Uint8Array(img.data);
    if ((await sha256Bytes(data)) !== img.sha256) return invalidate(env, now, c, "image_hash_mismatch");
    image = { data, alt: img.alt_text };
  }

  const at = isoUtc(now);
  const commentary = toLittle(stripText(post.text));
  const attempt = { attempt: 1, intent_at: at, outcome: "pending", runtime: "cloud" };
  const claim = await env.DB.batch([
    env.DB.prepare(
      `UPDATE posts SET state = 'PUBLISHING', updated_at = ?
       WHERE post_id = ? AND state = 'READY_TO_PUBLISH' AND approved_hash = ?
         AND EXISTS (SELECT 1 FROM consents WHERE consent_id = ? AND status = 'active' AND approved_hash = ?)`,
    ).bind(at, post.post_id, h, c.consent_id, h),
    env.DB.prepare(
      `UPDATE consents SET status = 'consumed', resolved_at = ?
       WHERE consent_id = ? AND status = 'active'
         AND EXISTS (SELECT 1 FROM posts WHERE post_id = ? AND state = 'PUBLISHING' AND updated_at = ?)`,
    ).bind(at, c.consent_id, post.post_id, at),
    env.DB.prepare(
      `INSERT INTO publications (post_id, idempotency_key, state, approved_hash, commentary_hash, api_version,
                                 author, attempts, updated_at)
       SELECT ?, ?, 'publishing', ?, ?, ?, ?, json_array(json(?)), ?
       WHERE EXISTS (SELECT 1 FROM consents WHERE consent_id = ? AND status = 'consumed' AND resolved_at = ?)
       ON CONFLICT(post_id) DO UPDATE SET state = 'publishing', approved_hash = excluded.approved_hash,
         commentary_hash = excluded.commentary_hash, api_version = excluded.api_version,
         author = excluded.author, updated_at = excluded.updated_at,
         attempts = json_insert(publications.attempts, '$[#]',
           json_set(json(?), '$.attempt', json_array_length(publications.attempts) + 1))`,
    ).bind(post.post_id, await sha256Hex(`${post.post_id}:${h}`), h, await sha256Hex(commentary),
      cfg.apiVersion, cfg.personUrn, JSON.stringify(attempt), at, c.consent_id, at, JSON.stringify(attempt)),
    env.DB.prepare(
      `UPDATE jobs SET state = 'RUNNING', reason = 'publishing', updated_at = ?
       WHERE slot_id = ? AND EXISTS (SELECT 1 FROM consents WHERE consent_id = ? AND status = 'consumed' AND resolved_at = ?)`,
    ).bind(at, c.slot_id, c.consent_id, at),
    event(env.DB, now, "publish.intent", actor, post.post_id, { consent_id: c.consent_id, slot_id: c.slot_id }),
  ]);
  if (claim[0].meta.changes !== 1 || claim[1].meta.changes !== 1) {
    return { status: "claim_lost", consent_id: c.consent_id, post_id: post.post_id };
  }

  let result: PublishResult;
  try {
    result = await publish(cfg, token, post.text, fetchImpl, 25000, image);  // the single post request
  } catch (err) {
    result = { outcome: "ambiguous", detail: { reason: `internal:${(err as Error)?.name}`, sent: true } };
  }
  await record(env, now, post.post_id, c.slot_id, result, actor);
  return { status: "done", consent_id: c.consent_id, post_id: post.post_id, outcome: result.outcome };
}

async function record(env: Env, now: number, postId: string, slotId: string, r: PublishResult,
                      actor = ACTOR): Promise<void> {
  const at = isoUtc(now);
  const d = r.detail;
  const attemptPatch = JSON.stringify({
    finished_at: at, outcome: r.outcome === "published" ? "published" : r.outcome === "rejected" ? "rejected" : "ambiguous",
    ...(d.http_status !== undefined ? { http_status: d.http_status } : {}),
    ...(d.reason ? { reason: d.reason } : {}), ...(d.message ? { message: d.message } : {}),
    ...(d.retryable !== undefined ? { retryable: d.retryable } : {}), sent: d.sent,
  });
  const map = {
    published: { post: "PUBLISHED", pub: "published", job: "SUCCEEDED", event: "publish.published" },
    rejected: { post: "PUBLISH_FAILED", pub: "publish_failed", job: "FAILED", event: "publish.failed" },
    ambiguous: { post: "NEEDS_RECONCILE", pub: "needs_reconcile", job: "NEEDS_RECONCILE", event: "publish.ambiguous" },
  }[r.outcome];
  await env.DB.batch([
    env.DB.prepare(
      `UPDATE publications SET state = ?, remote_id = COALESCE(?, remote_id), url = COALESCE(?, url),
         image_urn = COALESCE(?, image_urn),
         published_at = CASE WHEN ? = 'published' THEN ? ELSE published_at END,
         verified_by = CASE WHEN ? = 'published' THEN 'api_response' ELSE verified_by END,
         attempts = json_set(attempts, '$[#-1]', json_patch(json_extract(attempts, '$[#-1]'), json(?))),
         updated_at = ?
       WHERE post_id = ?`,
    ).bind(map.pub, r.remoteId ?? null, r.url ?? null, d.image_urn ?? null, map.pub, at, map.pub, attemptPatch,
      at, postId),
    env.DB.prepare("UPDATE posts SET state = ?, updated_at = ? WHERE post_id = ? AND state = 'PUBLISHING'")
      .bind(map.post, at, postId),
    env.DB.prepare("UPDATE jobs SET state = ?, reason = ?, updated_at = ? WHERE slot_id = ?")
      .bind(map.job, d.reason ?? r.outcome, at, slotId),
    event(env.DB, now, map.event, actor, postId, {
      http_status: d.http_status ?? null, reason: d.reason ?? null, remote_id: r.remoteId ?? null,
    }),
  ]);
}

// ── LCE-036: controlled manual publication ──────────────────────────────
// One post, now, at the owner's explicit request (typed phrase, checked by the
// API). Independent of auto-publish, blocked by the emergency stop. It reuses
// publishOne, so every gate and the duplicate protection above apply unchanged:
// a one-off consent is created and consumed in the same claim transaction.
// Before anything is sent, the token's member must be the configured author.

export type ManualReport = RunReport & { http: number; publication?: Record<string, unknown> | null };

export async function publishNow(env: Env, now: number, actor: string, postId: string, approvedHash: string,
                                 fetchImpl: FetchLike): Promise<ManualReport> {
  const settings = await loadSettings(env.DB);
  const refuse = (http: number, detail: string): ManualReport => ({ status: "refused", http, post_id: postId, detail });
  if (settings.emergency_stop) return refuse(409, "the emergency stop is on; release it first");
  if (settings.provider !== "linkedin_api") return refuse(409, "provider is not linkedin_api");
  const problems = configProblems(linkedinConfig(settings));
  if (problems.length) return refuse(409, `config: ${problems.join("; ")}`);
  const post = await env.DB.prepare("SELECT * FROM posts WHERE post_id = ?").bind(postId).first<PostRow>();
  if (!post) return refuse(404, "post not found in the cloud (push it first)");
  if (post.state !== "READY_TO_PUBLISH") return refuse(409, `post is ${post.state}, not READY_TO_PUBLISH`);
  if (post.approved_hash !== approvedHash) return refuse(409, "the text you confirmed is not the approved text");
  const busy = await env.DB.prepare("SELECT consent_id FROM consents WHERE post_id = ? AND status = 'active'")
    .bind(postId).first<{ consent_id: string }>();
  if (busy) return refuse(409, "this post is already scheduled; revoke that consent first");
  const who = await linkedinIdentity(env.LINKEDIN_TOKEN, settings, fetchImpl);
  if (!who.ok) return refuse(409, `LinkedIn identity not verified (${who.status}); nothing was sent`);
  if (who.person_urn_matches !== true) {
    return refuse(409, "the token's member is not the configured person_urn; nothing was sent");
  }
  const at = isoUtc(now);
  const id = `manual-${crypto.randomUUID()}`;
  const c: ConsentRow = { consent_id: id, post_id: postId, slot_id: id, slot_utc: at, approved_hash: approvedHash,
    status: "active", created_at: at, created_by: actor, resolved_at: null, reason: null };
  try {
    await env.DB.batch([
    env.DB.prepare(`INSERT INTO consents (consent_id, post_id, slot_id, slot_utc, approved_hash, status, created_at, created_by)
      VALUES (?, ?, ?, ?, ?, 'active', ?, ?)`).bind(id, postId, id, at, approvedHash, at, actor),
    env.DB.prepare(`INSERT INTO jobs (job_id, slot_id, slot_utc, state, post_id, consent_id, updated_at)
      VALUES (?, ?, ?, 'SCHEDULED', ?, ?, ?)`).bind(`job-${id}`, id, at, postId, id, at),
    event(env.DB, now, "publish.manual_requested", actor, postId, { consent_id: id }),
    ]);
  } catch (err) {
    if (String((err as Error).message).includes("UNIQUE")) return refuse(409, "another publication of this post is in progress");
    throw err;
  }
  const report = await publishOne(env, now, c, settings, fetchImpl, actor);
  const publication = await env.DB.prepare("SELECT state, remote_id, url, published_at, idempotency_key FROM publications WHERE post_id = ?")
    .bind(postId).first<Record<string, unknown>>();
  const http = report.status === "done" ? (report.outcome === "published" ? 200 : 502) : 409;
  return { ...report, http, publication };
}
