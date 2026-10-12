// LCE-058: Approve & publish now, in one owner-confirmed request, without waiting for the hourly
// private run. Before this, Publish now itself was immediate, but an approval recorded in the
// dashboard reached the publisher only when the private workflow applied it (hourly).
//
// One request from a signed-in person (typed phrase, the exact text hash and image hash shown):
//   1. records the normal `approve` decision (createDecision: AWAITING_APPROVAL in the mirror,
//      hash of the mirror text = the hash shown), so the repository applies the approval later
//      exactly as before and stays the source of truth;
//   2. places that exact text in the publisher (posts table), with the image copied from the
//      synced preview only when its bytes hash to the image hash the owner saw;
//   3. runs publishNow → publishOne: emergency stop, LinkedIn identity, label gate, approved-hash
//      gate, idempotency and audit events, exactly as for any publication.
// Nothing here reads or changes auto-publish, and nothing is ever published without this request.
// When the repository later pushes the same approval, the Worker answers "unchanged".

import { createDecision, DecisionError } from "./decisions";
import { loadSettings, type Env } from "./db";
import type { FetchLike } from "./linkedin";
import { publishNow, type ManualReport } from "./runner";
import { isoUtc } from "./schedule";
import { contentHash, sha256Bytes, visibleCredit } from "./text";

const HEX64 = /^[0-9a-f]{64}$/;

type MirrorPost = { post_id: string; state: string; text?: string | null; actual_hash?: string | null;
  plan_date?: string | null; language?: string | null; image?: { sha256?: string | null; alt_text?: string | null } | null;
  archived?: unknown };

export type DirectReport = ManualReport & { decision_id?: string };

async function mirrorPost(env: Env, id: string): Promise<MirrorPost | null> {
  const row = await env.DB.prepare("SELECT body FROM pipeline_snapshot WHERE id = 1").first<{ body: string }>();
  if (!row) return null;
  const snap = JSON.parse(row.body) as { posts?: MirrorPost[] };
  return (snap.posts ?? []).find((p) => p.post_id === id) ?? null;
}

export async function approveAndPublish(env: Env, now: number, who: { subject: string; human: boolean }, id: string,
                                        b: Record<string, unknown>, fetchImpl: FetchLike): Promise<DirectReport> {
  const refuse = (http: number, detail: string): DirectReport => ({ status: "refused", http, post_id: id, detail });
  if (!who.human) return refuse(403, "only a signed-in person can approve and publish");
  if (String(b.confirm ?? "").trim() !== `APPROVE AND PUBLISH ${id}`) return refuse(428, `type 'APPROVE AND PUBLISH ${id}' to confirm`);
  const h = String(b.content_hash ?? "");
  if (!HEX64.test(h)) return refuse(400, "content_hash must be the 64-hex hash of the text you reviewed");
  const shownImage = b.image_sha256 === null || b.image_sha256 === undefined ? null : String(b.image_sha256);

  // Gates that must hold before anything is recorded (publishOne checks them again).
  const settings = await loadSettings(env.DB);
  if (settings.emergency_stop) return refuse(409, "the emergency stop is on; release it first");
  const p = await mirrorPost(env, id);
  if (!p) return refuse(404, "post not in the pipeline mirror");
  if (p.archived) return refuse(409, "the post is archived");
  if (p.state !== "AWAITING_APPROVAL") return refuse(409, `only a post awaiting approval can be approved and published (${p.state})`);
  if (typeof p.text !== "string" || p.actual_hash !== h || (await contentHash(p.text)) !== h) {
    return refuse(409, "the text changed since you reviewed it; reload and review again");
  }
  const credit = visibleCredit(p.text);
  if (credit) return refuse(409, `the text shows an image/source label (${credit.slice(0, 80)}); it is never published`);
  const imageSha = p.image?.sha256 ?? null;
  if (imageSha !== shownImage) return refuse(409, "the image changed since you reviewed it; reload and review again");
  let image: { data: Uint8Array; alt: string } | null = null;
  if (imageSha) {
    const pm = await env.DB.prepare("SELECT data, sha256, alt_text FROM preview_media WHERE post_id = ?").bind(id)
      .first<{ data: ArrayBuffer | number[]; sha256: string; alt_text: string }>();
    const data = pm ? new Uint8Array(pm.data as ArrayBuffer) : null;   // D1 may return a BLOB as a number array
    if (!pm || !data || pm.sha256 !== imageSha || (await sha256Bytes(data)) !== imageSha) {
      return refuse(409, "the approved image is not synced to the Control Center yet; nothing was recorded");
    }
    image = { data, alt: p.image?.alt_text ?? pm.alt_text ?? "" };
  }
  const cloud = await env.DB.prepare("SELECT state, approved_hash FROM posts WHERE post_id = ?").bind(id)
    .first<{ state: string; approved_hash: string }>();
  if (cloud && cloud.state !== "WITHDRAWN") {
    return refuse(409, `the post is already in the publisher (${cloud.state}); use Publish now`);
  }

  // 1. the normal approval decision (the repository applies it later, hash-checked against the files)
  let decisionId: string;
  try {
    const d = await createDecision(env, now, who, { action: "approve", post_id: id, content_hash: h, confirm: `APPROVE ${id}`,
      request_id: typeof b.request_id === "string" ? b.request_id : undefined });
    decisionId = d.decision_id;
  } catch (err) {
    if (err instanceof DecisionError) return refuse(err.status, err.message);
    throw err;
  }
  // 2. the exact approved text (and hash-checked image) into the publisher, atomically (one D1 batch =
  // one transaction). A per-request token in pushed_by marks the row this request placed; the image and
  // audit rows are written only when the posts row carries that token, so a concurrent request that lost
  // the race never touches the image, and the post is never READY_TO_PUBLISH without its image.
  const at = isoUtc(now);
  const token = `dashboard:${who.subject}:${crypto.randomUUID()}`;
  const mine = "EXISTS (SELECT 1 FROM posts WHERE post_id = ? AND pushed_by = ? AND state = 'READY_TO_PUBLISH' AND approved_hash = ?)";
  const place = cloud
    ? env.DB.prepare(`UPDATE posts SET text = ?, language = ?, plan_date = ?, state = 'READY_TO_PUBLISH', content_hash = ?,
        approved_hash = ?, approved_at = ?, pushed_at = ?, pushed_by = ?, updated_at = ? WHERE post_id = ? AND state = 'WITHDRAWN'`)
      .bind(p.text, p.language ?? "en", p.plan_date ?? null, h, h, at, at, token, at, id)
    : env.DB.prepare(`INSERT INTO posts (post_id, text, language, plan_date, state, content_hash, approved_hash,
        approved_at, pushed_at, pushed_by, updated_at) VALUES (?, ?, ?, ?, 'READY_TO_PUBLISH', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO NOTHING`)
      .bind(id, p.text, p.language ?? "en", p.plan_date ?? null, h, h, at, at, token, at);
  let res: D1Result[];
  try {
    res = await env.DB.batch([
      place,
      env.DB.prepare(`DELETE FROM post_images WHERE post_id = ? AND ${mine}`).bind(id, id, token, h),
      ...(image ? [env.DB.prepare(`INSERT INTO post_images (post_id, data, sha256, alt_text, bytes)
          SELECT ?, ?, ?, ?, ? WHERE ${mine}`).bind(id, image.data, imageSha, image.alt, image.data.length, id, token, h)] : []),
      env.DB.prepare(`INSERT INTO events (at, event, actor, post_id, detail) SELECT ?, 'post.approved_direct', ?, ?, ? WHERE ${mine}`)
        .bind(at, who.subject, id, JSON.stringify({ approved_hash: h, image_sha256: imageSha, decision_id: decisionId }), id, token, h),
    ]);
  } catch (err) {
    return refuse(409, `the post could not be placed in the publisher (${String((err as Error).message).slice(0, 120)}); nothing was sent`);
  }
  const placed = res[0].meta.changes;
  const imaged = image ? res[2].meta.changes : 1;
  const audited = res[res.length - 1].meta.changes;
  if (placed !== 1) {
    // another request placed (or changed) this post first: this one sends nothing
    return { ...refuse(409, "another request placed this post in the publisher first; nothing was sent by this one"), decision_id: decisionId };
  }
  if (imaged !== 1 || audited !== 1) {
    // cannot happen while the batch is atomic; refuse to publish rather than publish without the approved image
    // or without its audit event. The row this request placed is withdrawn again (only if it is still its own).
    const back = await env.DB.prepare("UPDATE posts SET state = 'WITHDRAWN', updated_at = ? WHERE post_id = ? AND pushed_by = ? AND state = 'READY_TO_PUBLISH'")
      .bind(at, id, token).run();
    const what = imaged !== 1 ? "the approved image" : "the audit event";
    return { ...refuse(500, `${what} could not be stored with the post; nothing was sent` +
      (back.meta.changes === 1 ? "" : "; the publisher row could not be withdrawn, check it before publishing")), decision_id: decisionId };
  }
  // 3. the existing publisher, now
  const report = await publishNow(env, now, who.subject, id, h, fetchImpl);
  return { ...report, decision_id: decisionId };
}
