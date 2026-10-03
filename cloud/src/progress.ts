// LCE-048: real Refresh progress. Components report events as they happen; the Control Center
// reads the request (D1 decision), the events and the private-pipeline mirror for one post.

import type { Env } from "./db";
import { isoUtc } from "./schedule";

export const STAGES = [
  "worker_started", "researching", "writing", "media", "humanization", "qa", "duplicate_check",
  "approval_prepared", "replacement_ready", "failed",
] as const;

export class ProgressError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const POST_ID = /^[a-z0-9][a-z0-9-]{2,120}$/;

export async function putProgress(env: Env, now: number, actor: string, postId: string, b: Record<string, unknown>) {
  if (!POST_ID.test(postId)) throw new ProgressError(400, "bad post id");
  const stage = String(b.stage ?? "");
  if (!(STAGES as readonly string[]).includes(stage)) throw new ProgressError(400, `stage must be one of ${STAGES.join(", ")}`);
  const decision = b.decision_id == null ? null : String(b.decision_id);
  if (decision !== null && !/^d-[0-9a-f-]{36}$/.test(decision)) throw new ProgressError(400, "bad decision id");
  const note = b.note == null ? null : String(b.note).slice(0, 500);
  const at = isoUtc(now);
  await env.DB.prepare("INSERT INTO refresh_progress (post_id, decision_id, stage, note, at, reported_by) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(postId, decision, stage, note, at, actor).run();
  return { post_id: postId, stage, at };
}

type Mirror = { meta?: { generated_at?: string }; posts?: Array<Record<string, unknown>> };

/** Everything the page needs to show one post's Refresh truthfully: the request, the reported
 *  events since it was made, and what the mirror (GitHub's state) says about the post now. */
export async function refreshStatus(env: Env, now: number, postId: string) {
  if (!POST_ID.test(postId)) throw new ProgressError(400, "bad post id");
  const decision = await env.DB.prepare(`SELECT decision_id, status, created_at, resolved_at, result FROM decisions
    WHERE post_id = ? AND action = 'refresh' AND status IN ('pending', 'applied') ORDER BY created_at DESC LIMIT 1`)
    .bind(postId).first<{ decision_id: string; status: string; created_at: string; resolved_at: string | null; result: string | null }>();
  let events: Array<Record<string, unknown>> = [];
  if (decision) try {
    const since = decision.created_at;
    events = (await env.DB.prepare(`SELECT stage, note, at, reported_by, decision_id FROM refresh_progress
      WHERE post_id = ? AND at >= ? ORDER BY at, id`).bind(postId, since).all<Record<string, unknown>>()).results;
  } catch (e) {
    if (!String(e).includes("no such table")) throw e;   // until migration 0009 is applied
  }
  const row = await env.DB.prepare("SELECT body, received_at FROM pipeline_snapshot WHERE id = 1").first<{ body: string; received_at: string }>();
  let mirror: Record<string, unknown> | null = null;
  if (row) {
    const snap = JSON.parse(row.body) as Mirror;
    const p = (snap.posts ?? []).find((x) => x.post_id === postId) ?? null;
    mirror = {
      generated_at: snap.meta?.generated_at ?? null, received_at: row.received_at,
      post: p ? { state: p.state, refresh_request: p.refresh_request ?? null, refresh: p.refresh ?? null,
        versions: Array.isArray(p.versions) ? p.versions.length : 0, actual_hash: p.actual_hash ?? null } : null,
    };
  }
  return { now: isoUtc(now), post_id: postId, decision: decision ?? null, events, mirror };
}
