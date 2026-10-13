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
import { qualityFor } from "./quality";
import { contentHash, sha256Bytes, sha256Hex, stripText, toLittle, visibleCredit } from "./text";
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

// LCE-061: the switches are re-read inside the very statement that claims (D1 runs a batch as one SQLite
// transaction and serializes writers): a stop committed before the claim makes it change nothing. A stop
// committed after the claim cannot recall a request already sent to LinkedIn.
const STOP_OFF = "NOT EXISTS (SELECT 1 FROM settings WHERE key = 'emergency_stop' AND value = 'true')";
const AUTO_ON = "EXISTS (SELECT 1 FROM settings WHERE key = 'auto_publish' AND value = 'true')";

async function switchesNow(env: Env): Promise<{ stop: boolean; auto: boolean }> {
  const rows = (await env.DB.prepare("SELECT key, value FROM settings WHERE key IN ('emergency_stop', 'auto_publish')")
    .all<{ key: string; value: string }>()).results;
  const v = (k: string) => rows.find((r) => r.key === k)?.value === "true";
  return { stop: v("emergency_stop"), auto: v("auto_publish") };
}

// LCE-060: every check that can reject a publication, in one place, with no writes. publishNow runs it
// before anything is written (so a rejection leaves an existing schedule untouched); publishOne runs it
// again right before the claim (the state may have changed in between).
type Preflight = { ok: true; post: PostRow; image?: ImageInput; cfg: LinkedInConfig; token: string } | { ok: false; reason: string };

async function preflight(env: Env, now: number, postId: string, approvedHash: string, settings: Settings): Promise<Preflight> {
  const no = (reason: string): Preflight => ({ ok: false, reason });
  if (settings.provider !== "linkedin_api") return no("provider_not_enabled");
  const cfg = linkedinConfig(settings);
  const problems = configProblems(cfg);
  if (problems.length) return no(`config: ${problems.join("; ")}`);
  const token = env.LINKEDIN_TOKEN;
  if (!token) return no("token_missing");
  if (settings.token_expires_at && parseIsoUtc(settings.token_expires_at) <= now) return no("token_expired");
  const post = await env.DB.prepare("SELECT * FROM posts WHERE post_id = ?").bind(postId).first<PostRow>();
  if (!post) return no("post_missing");
  if (post.state !== "READY_TO_PUBLISH") return no(`post_is_${post.state}`);
  const h = await contentHash(post.text);
  if (h !== post.approved_hash || h !== post.content_hash || h !== approvedHash) return no("hash_mismatch");
  if (stripText(post.text).length > cfg.maxChars) return no("text_too_long");
  // LCE-053: the approved text is published unchanged, so a text that still shows an image/source label
  // (approved before the rule) is not published at all; the owner refreshes it and approves the clean text.
  if (visibleCredit(post.text)) return no("visible_image_credit");
  // LCE-059: Humanity PASS and Voice Gate PASS for exactly the approved text, or nothing is published.
  const quality = await qualityFor(env, post.post_id, post.approved_hash);
  if (!quality.ok) return no(`quality: ${quality.why}`);
  const img = await env.DB.prepare("SELECT data, sha256, alt_text FROM post_images WHERE post_id = ?")
    .bind(post.post_id).first<{ data: ArrayBuffer; sha256: string; alt_text: string }>();
  let image: ImageInput | undefined;
  if (img) {
    const data = new Uint8Array(img.data);
    if ((await sha256Bytes(data)) !== img.sha256) return no("image_hash_mismatch");
    image = { data, alt: img.alt_text };
  }
  return { ok: true, post, image, cfg, token };
}

async function publishOne(env: Env, now: number, c: ConsentRow, settings: Settings,
                          fetchImpl: FetchLike, actor = ACTOR, manual = false): Promise<RunReport> {
  const pf = await preflight(env, now, c.post_id, c.approved_hash, settings);
  if (!pf.ok) return invalidateAs(env, now, c, pf.reason, "FAILED", actor);
  const { post, image, cfg, token } = pf;
  const h = post.approved_hash;                      // preflight proved it equals the hash of post.text

  const at = isoUtc(now);
  const commentary = toLittle(stripText(post.text));
  const attempt = { attempt: 1, intent_at: at, outcome: "pending", runtime: "cloud" };
  const claim = await env.DB.batch([
    env.DB.prepare(
      `UPDATE posts SET state = 'PUBLISHING', updated_at = ?
       WHERE post_id = ? AND state = 'READY_TO_PUBLISH' AND approved_hash = ?
         AND EXISTS (SELECT 1 FROM consents WHERE consent_id = ? AND status = 'active' AND approved_hash = ?)
         AND ${STOP_OFF}${manual ? "" : ` AND ${AUTO_ON}`}`,
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
    // LCE-061: a switch changed after the first check: nothing was claimed and nothing is sent. A scheduled
    // consent stays as it was (it waits, like any emergency stop); a one-off manual consent is withdrawn.
    const sw = await switchesNow(env);
    const why = sw.stop ? "emergency_stop" : !manual && !sw.auto ? "kill_switch_off" : null;
    if (why) {
      if (manual) await invalidateAs(env, now, c, `${why}_at_claim`, "SKIPPED", actor);
      return { status: why, consent_id: c.consent_id, post_id: post.post_id, detail: "changed before the claim; nothing was sent" };
    }
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
                                 fetchImpl: FetchLike, opts: { replaceSchedule?: boolean } = {}): Promise<ManualReport> {
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
  const busy = await env.DB.prepare("SELECT consent_id, slot_id FROM consents WHERE post_id = ? AND status = 'active'")
    .bind(postId).first<{ consent_id: string; slot_id: string }>();
  const busyJob = busy ? await env.DB.prepare("SELECT state, reason, updated_at FROM jobs WHERE slot_id = ?")
    .bind(busy.slot_id).first<{ state: string; reason: string | null; updated_at: string }>() : null;
  // LCE-060: a scheduled post can be published now when the owner says so explicitly (replace_schedule);
  // its scheduled consent is revoked in the same transaction that creates the manual one, so the post is
  // never scheduled twice and the schedule is never dropped without a publication attempt.
  if (busy && !opts.replaceSchedule) return refuse(409, "this post is already scheduled; revoke that consent first");
  // LCE-059: refused before anything is written (publishOne checks it again)
  const quality = await qualityFor(env, postId, approvedHash);
  if (!quality.ok) return refuse(409, `quality gate: ${quality.why}; nothing was sent`);
  // LCE-060: every other check that could reject it, still before anything is written, so a rejection
  // never revokes an existing schedule
  const pf = await preflight(env, now, postId, approvedHash, settings);
  if (!pf.ok) return refuse(409, `refused before anything changed (${pf.reason}); nothing was sent`);
  const who = await linkedinIdentity(env.LINKEDIN_TOKEN, settings, fetchImpl);
  if (!who.ok) return refuse(409, `LinkedIn identity not verified (${who.status}); nothing was sent`);
  if (who.person_urn_matches !== true) {
    return refuse(409, "the token's member is not the configured person_urn; nothing was sent");
  }
  const at = isoUtc(now);
  const id = `manual-${crypto.randomUUID()}`;
  const c: ConsentRow = { consent_id: id, post_id: postId, slot_id: id, slot_utc: at, approved_hash: approvedHash,
    status: "active", created_at: at, created_by: actor, resolved_at: null, reason: null };
  let res: D1Result[];
  try {
    res = await env.DB.batch([
    // LCE-061: every write is conditional on the emergency stop being off at this moment (one transaction):
    // a stop that arrived after the first check changes nothing here.
    ...(busy ? [
      env.DB.prepare(`UPDATE consents SET status = 'revoked', resolved_at = ?, reason = 'replaced_by_publish_now'
        WHERE consent_id = ? AND status = 'active' AND ${STOP_OFF}`).bind(at, busy.consent_id),
      env.DB.prepare(`UPDATE jobs SET state = 'SKIPPED', reason = 'replaced_by_publish_now', updated_at = ?
        WHERE slot_id = ? AND state = 'SCHEDULED' AND ${STOP_OFF}`).bind(at, busy.slot_id),
    ] : []),
    env.DB.prepare(`INSERT INTO consents (consent_id, post_id, slot_id, slot_utc, approved_hash, status, created_at, created_by)
      SELECT ?, ?, ?, ?, ?, 'active', ?, ? WHERE ${STOP_OFF}`).bind(id, postId, id, at, approvedHash, at, actor),
    env.DB.prepare(`INSERT INTO jobs (job_id, slot_id, slot_utc, state, post_id, consent_id, updated_at)
      SELECT ?, ?, ?, 'SCHEDULED', ?, ?, ? WHERE EXISTS (SELECT 1 FROM consents WHERE consent_id = ?)`)
      .bind(`job-${id}`, id, at, postId, id, at, id),
    ...(busy ? [env.DB.prepare(`INSERT INTO events (at, event, actor, post_id, detail) SELECT ?, 'consent.revoked', ?, ?, ?
      WHERE EXISTS (SELECT 1 FROM consents WHERE consent_id = ?)`)
      .bind(at, actor, postId, JSON.stringify({ consent_id: busy.consent_id, reason: "replaced_by_publish_now" }), id)] : []),
    env.DB.prepare(`INSERT INTO events (at, event, actor, post_id, detail) SELECT ?, 'publish.manual_requested', ?, ?, ?
      WHERE EXISTS (SELECT 1 FROM consents WHERE consent_id = ?)`).bind(at, actor, postId, JSON.stringify({ consent_id: id }), id),
    ]);
  } catch (err) {
    if (String((err as Error).message).includes("UNIQUE")) return refuse(409, "another publication of this post is in progress");
    throw err;
  }
  const inserted = res[busy ? 2 : 0].meta.changes;
  if (inserted !== 1) {
    // the emergency stop was switched on after the first check: nothing above changed
    return refuse(409, "the emergency stop was switched on while this request ran; nothing was changed or sent");
  }
  if (busy && res[0].meta.changes !== 1) {
    // the scheduled consent was used or changed between the check and the batch: withdraw the manual one
    await invalidateAs(env, now, { consent_id: id, post_id: postId, slot_id: id, slot_utc: at, approved_hash: approvedHash,
      status: "active", created_at: at, created_by: actor, resolved_at: null, reason: null }, "schedule_changed_concurrently", "SKIPPED", actor);
    return refuse(409, "the scheduled publication changed while this request ran; nothing was sent by this one");
  }
  const report = await publishOne(env, now, c, settings, fetchImpl, actor, true);
  if (report.status === "emergency_stop" && busy) {
    // LCE-061: the stop arrived between this request's writes and the claim; nothing was sent, so the schedule
    // this request replaced is restored exactly (the manual consent is already withdrawn; events keep the trail)
    await env.DB.batch([
      env.DB.prepare(`UPDATE consents SET status = 'active', resolved_at = NULL, reason = NULL
        WHERE consent_id = ? AND status = 'revoked' AND reason = 'replaced_by_publish_now'`).bind(busy.consent_id),
      ...(busyJob ? [env.DB.prepare(`UPDATE jobs SET state = ?, reason = ?, updated_at = ?
        WHERE slot_id = ? AND reason = 'replaced_by_publish_now'`).bind(busyJob.state, busyJob.reason, busyJob.updated_at, busy.slot_id)] : []),
      event(env.DB, now, "consent.restored", actor, postId, { consent_id: busy.consent_id, reason: "emergency_stop_before_claim" }),
    ]);
  }
  const publication = await env.DB.prepare("SELECT state, remote_id, url, published_at, idempotency_key FROM publications WHERE post_id = ?")
    .bind(postId).first<Record<string, unknown>>();
  const http = report.status === "done" ? (report.outcome === "published" ? 200 : 502) : 409;
  return { ...report, http, publication };
}
