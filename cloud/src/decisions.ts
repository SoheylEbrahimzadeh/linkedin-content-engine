// LCE-036: owner decisions from the cloud Control Center. The Worker only RECORDS
// a decision; the private repository's workflow applies it to the git-tracked
// pipeline (re-checking every hash locally) and resolves it here. GitHub stays
// the source of truth, and nothing here publishes.
//
// - Only a person signed in through Cloudflare Access may decide (never a
//   service token). Approve and reject need the typed phrase.
// - Approve is bound to the exact text the owner saw: the hash must equal both
//   the mirror's recorded hash and the hash of the mirror's text.
// - One pending decision per post: a newer one supersedes the older.

import { event, type Env } from "./db";
import { isoUtc } from "./schedule";
import { contentHash } from "./text";

export class DecisionError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export const ACTIONS = ["approve", "reject", "edit", "regenerate", "reschedule", "skip", "duplicate", "refresh"] as const;
type Action = typeof ACTIONS[number];
const POST_ID_RE = /^\d{8}-[a-z0-9-]{1,56}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TERMINAL = new Set(["PUBLISHED", "REJECTED", "PUBLISHING", "NEEDS_RECONCILE"]);
const EDITABLE = new Set(["SELECTED", "DRAFTED", "HUMANIZED", "NEEDS_REVISION", "QA_PASSED", "DUPLICATE_CHECKED",
  "AWAITING_APPROVAL", "APPROVED", "READY_TO_PUBLISH"]);
const MAX_TEXT = 3000;

type MirrorPost = { post_id: string; state: string; text?: string | null; actual_hash?: string | null;
  plan_date?: string | null; image?: { sha256?: string } | null };
type MirrorEntry = { date?: string; topic?: string; status?: string; draft_ref?: string };

export type DecisionRow = { decision_id: string; post_id: string | null; plan_date: string | null; action: Action;
  content_hash: string | null; payload: string; status: string; created_at: string; created_by: string;
  resolved_at: string | null; resolved_by: string | null; result: string | null };

const phrase = (b: Record<string, unknown>, p: string) => {
  if (String(b.confirm ?? "").trim() !== p) throw new DecisionError(428, `type '${p}' to confirm`);
};
const str = (v: unknown, max: number, what: string, required = true): string => {
  const s = typeof v === "string" ? v.trim() : "";
  if (required && !s) throw new DecisionError(400, `${what} is required`);
  if (s.length > max) throw new DecisionError(400, `${what} is longer than ${max} characters`);
  return s;
};
const futureDate = (v: unknown, now: number): string => {
  const d = String(v ?? "");
  if (!DATE_RE.test(d) || Number.isNaN(Date.parse(`${d}T00:00:00Z`))) throw new DecisionError(400, "date must be YYYY-MM-DD");
  if (d < isoUtc(now - 86400e3).slice(0, 10)) throw new DecisionError(400, "date is in the past");
  return d;
};

async function mirror(env: Env): Promise<{ posts: MirrorPost[]; calendar: MirrorEntry[] }> {
  const row = await env.DB.prepare("SELECT body FROM pipeline_snapshot WHERE id = 1").first<{ body: string }>();
  if (!row) throw new DecisionError(409, "no pipeline mirror yet: the private workflow must sync first");
  const snap = JSON.parse(row.body) as { posts?: MirrorPost[]; calendar?: MirrorEntry[] };
  return { posts: snap.posts ?? [], calendar: snap.calendar ?? [] };
}

export async function createDecision(env: Env, now: number, who: { subject: string; human: boolean },
                                     b: Record<string, unknown>) {
  if (!who.human) throw new DecisionError(403, "decisions need a person signed in through Cloudflare Access, not a service token");
  const action = String(b.action ?? "") as Action;
  if (!ACTIONS.includes(action)) throw new DecisionError(400, `action must be one of ${ACTIONS.join(", ")}`);
  const { posts, calendar } = await mirror(env);
  const postId = b.post_id === undefined || b.post_id === null ? null : String(b.post_id);
  if (postId !== null && !POST_ID_RE.test(postId)) throw new DecisionError(400, "invalid post id");
  const post = postId ? posts.find((p) => p.post_id === postId) : undefined;
  if (postId && !post) throw new DecisionError(404, "post not in the pipeline mirror");
  const cloud = postId ? await env.DB.prepare("SELECT state FROM posts WHERE post_id = ?").bind(postId).first<{ state: string }>() : null;
  const delegated = Boolean(cloud && cloud.state !== "WITHDRAWN");
  let hash: string | null = null;
  let planDate: string | null = post?.plan_date ?? null;
  const payload: Record<string, unknown> = {};

  const needPost = () => { if (!post) throw new DecisionError(400, `${action} needs a post_id`); return post; };
  const notDelegated = () => {
    if (delegated) throw new DecisionError(409, "this post is in the cloud publisher; withdraw it there first");
  };
  switch (action) {
    case "approve": {
      const p = needPost();
      phrase(b, `APPROVE ${p.post_id}`);
      if (p.state !== "AWAITING_APPROVAL") throw new DecisionError(409, `only AWAITING_APPROVAL posts can be approved (${p.state})`);
      const h = String(b.content_hash ?? "");
      if (!HEX64.test(h)) throw new DecisionError(400, "content_hash must be the 64-hex hash of the text you reviewed");
      if (typeof p.text !== "string" || p.actual_hash !== h || (await contentHash(p.text)) !== h) {
        throw new DecisionError(409, "the text changed since you reviewed it; reload and review again");
      }
      hash = h;
      payload.image_sha256 = p.image?.sha256 ?? null;
      break;
    }
    case "reject": {
      const p = needPost();
      phrase(b, `REJECT ${p.post_id}`);
      if (TERMINAL.has(p.state)) throw new DecisionError(409, `a ${p.state} post cannot be rejected`);
      notDelegated();
      payload.reason = str(b.reason, 500, "reason");
      break;
    }
    case "edit": {
      const p = needPost();
      if (!EDITABLE.has(p.state)) throw new DecisionError(409, `a ${p.state} post cannot be edited`);
      notDelegated();
      const base = String(b.base_hash ?? "");
      if (!p.actual_hash || base !== p.actual_hash) throw new DecisionError(409, "the text changed since you opened it; reload first");
      const text = str(b.text, MAX_TEXT * 2, "text");
      if (text.length > MAX_TEXT) throw new DecisionError(400, `LinkedIn posts are limited to ${MAX_TEXT} characters`);
      if ((await contentHash(text)) === base) throw new DecisionError(400, "the text is unchanged");
      hash = base;
      payload.text = text;
      break;
    }
    case "regenerate": {
      const p = needPost();
      if (TERMINAL.has(p.state)) throw new DecisionError(409, `a ${p.state} post cannot be regenerated`);
      notDelegated();
      payload.reason = str(b.reason, 1000, "what should change");
      break;
    }
    case "refresh": {
      // LCE-041: regenerate the whole post package (text, sources, media). Only recorded here;
      // a writing session produces the new version, which then needs the owner's approval.
      const p = needPost();
      if (TERMINAL.has(p.state)) throw new DecisionError(409, `a ${p.state} post is not refreshed`);
      notDelegated();
      payload.note = str(b.note, 500, "note", false);
      payload.base_hash = p.actual_hash ?? null;
      break;
    }
    case "reschedule": {
      const p = needPost();
      if (TERMINAL.has(p.state)) throw new DecisionError(409, `a ${p.state} post cannot be rescheduled`);
      notDelegated();
      planDate = futureDate(b.date, now);
      payload.from = p.plan_date ?? null;
      break;
    }
    case "skip": {
      if (post) {
        if (TERMINAL.has(post.state)) throw new DecisionError(409, `a ${post.state} post cannot be skipped`);
        notDelegated();
      } else {
        const d = String(b.plan_date ?? "");
        const entry = calendar.find((e) => e.date === d && !e.draft_ref);
        if (!entry) throw new DecisionError(404, "no planned entry without a post on that date");
        planDate = d;
        payload.topic = entry.topic ?? null;
      }
      payload.reason = str(b.reason, 500, "reason", false);
      break;
    }
    case "duplicate": {
      const p = needPost();
      if (typeof p.text !== "string" || !p.text.trim()) throw new DecisionError(409, "this post has no text to copy");
      planDate = futureDate(b.date, now);
      break;
    }
  }
  const id = `d-${crypto.randomUUID()}`;
  const at = isoUtc(now);
  const key = postId ?? `plan:${planDate}`;
  await env.DB.batch([
    env.DB.prepare(`UPDATE decisions SET status = 'superseded', resolved_at = ?, resolved_by = ?
      WHERE status = 'pending' AND COALESCE(post_id, 'plan:' || plan_date) = ?`).bind(at, who.subject, key),
    env.DB.prepare(`INSERT INTO decisions (decision_id, post_id, plan_date, action, content_hash, payload, status, created_at, created_by)
      VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)`).bind(id, postId, planDate, action, hash, JSON.stringify(payload), at, who.subject),
    event(env.DB, now, `decision.${action}`, who.subject, postId, { decision_id: id }),
  ]);
  return { decision_id: id, action, post_id: postId, plan_date: planDate, status: "pending" };
}

export async function listDecisions(env: Env, status: string | null) {
  const rows = status
    ? (await env.DB.prepare("SELECT * FROM decisions WHERE status = ? ORDER BY created_at LIMIT 200").bind(status).all<DecisionRow>()).results
    : (await env.DB.prepare("SELECT * FROM decisions ORDER BY created_at DESC LIMIT 200").all<DecisionRow>()).results;
  return { decisions: rows.map((r) => ({ ...r, payload: JSON.parse(r.payload || "{}") })) };
}

// Called by the private workflow after it applied (or refused) a decision.
export async function resolveDecision(env: Env, now: number, actor: string, id: string, b: Record<string, unknown>) {
  const status = String(b.status ?? "");
  if (!["applied", "refused"].includes(status)) throw new DecisionError(400, "status must be applied or refused");
  const result = str(b.result, 2000, "result", false);
  const res = await env.DB.batch([
    env.DB.prepare("UPDATE decisions SET status = ?, resolved_at = ?, resolved_by = ?, result = ? WHERE decision_id = ? AND status = 'pending'")
      .bind(status, isoUtc(now), actor, result, id),
    event(env.DB, now, `decision.${status}`, actor, null, { decision_id: id }),
  ]);
  if (res[0].meta.changes !== 1) throw new DecisionError(409, "decision is not pending");
  return { decision_id: id, status };
}

export async function cancelDecision(env: Env, now: number, who: { subject: string; human: boolean }, id: string) {
  if (!who.human) throw new DecisionError(403, "only a signed-in person can cancel a decision");
  const res = await env.DB.prepare(
    "UPDATE decisions SET status = 'cancelled', resolved_at = ?, resolved_by = ? WHERE decision_id = ? AND status = 'pending'",
  ).bind(isoUtc(now), who.subject, id).run();
  if (res.meta.changes !== 1) throw new DecisionError(409, "decision is not pending");
  return { decision_id: id, status: "cancelled" };
}
