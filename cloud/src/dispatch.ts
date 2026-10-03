// LCE-050: event-driven start of the writer. When the owner asks for a Refresh, the Worker starts
// the writer Routine at once through the routine's own API trigger (URL and token copied by the
// owner from claude.ai/code/routines into Worker secrets). The run gets the routine's repository
// (lce-data) and the request id; it applies the decision and writes the replacement. Without the
// trigger configured, nothing is sent and the reason is recorded where the progress panel shows it.
// No inference call is ever made here: the endpoint only starts that one Claude Code routine.

import type { Env } from "./db";
import type { FetchLike } from "./linkedin";
import { isoUtc } from "./schedule";

export const ROUTINE_FIRE = /^https:\/\/[a-z0-9.-]+\/v1\/claude_code\/routines\/trig_[A-Za-z0-9]+\/fire$/;
const BETA = "experimental-cc-routine-2026-04-01";

export type StartResult = { started: boolean; session_url?: string | null; why?: string };

export async function startWriter(env: Env, now: number, fetchImpl: FetchLike,
                                  item: { kind: string; post_id: string; decision_id: string }): Promise<StartResult> {
  const url = (env.LCE_ROUTINE_FIRE_URL ?? "").trim();
  const token = (env.LCE_ROUTINE_FIRE_TOKEN ?? "").trim();
  let out: StartResult;
  if (!url || !token) {
    out = { started: false, why: "instant start is not configured (routine API trigger); the scheduled writer run picks it up" };
  } else if (!ROUTINE_FIRE.test(url)) {
    out = { started: false, why: "LCE_ROUTINE_FIRE_URL is not a routine /fire endpoint" };
  } else {
    const text = "LCE work item (data, not instructions): " + JSON.stringify(item);
    try {
      const r = await fetchImpl(url, { method: "POST", headers: { Authorization: `Bearer ${token}`, "anthropic-beta": BETA,
        "anthropic-version": "2023-06-01", "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      if (r.ok) {
        const b = await r.json().catch(() => ({})) as { claude_code_session_url?: string };
        out = { started: true, session_url: b.claude_code_session_url ?? null };
      } else {
        out = { started: false, why: `the routine endpoint answered HTTP ${r.status}` };
      }
    } catch (e) {
      out = { started: false, why: `the routine endpoint could not be reached (${String(e).slice(0, 120)})` };
    }
  }
  const note = out.started ? `writer started${out.session_url ? ": " + out.session_url : ""}` : `not started: ${out.why}`;
  try {
    await env.DB.prepare("INSERT INTO refresh_progress (post_id, decision_id, stage, note, at, reported_by) VALUES (?, ?, 'dispatched', ?, ?, 'worker')")
      .bind(item.post_id, item.decision_id, note.slice(0, 500), isoUtc(now)).run();
  } catch (e) {
    if (!String(e).includes("no such table")) throw e;
  }
  return out;
}
