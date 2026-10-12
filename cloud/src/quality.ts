// LCE-059: publication-time quality gate. Approval is hash-bound and checked in the repository, but the
// quality checks (Humanity test, the owner's Voice Gate) can change after an approval (new rules, new owner
// material). A post is published, scheduled or manual, only when the pipeline mirror holds a result for
// exactly the approved text (its content hash) with Humanity PASS and Voice Gate PASS.
//
// Fail closed: no mirror, no result for the post, a result for another text (stale or mismatched), a scoring
// error, any verdict other than PASS (FAIL, PARTIAL, SOURCE_HEAVY, REVIEW_NEEDED), or a missing field all block.
// SOURCE_HEAVY never passes: it needs genuine owner-confirmed material, never generated opinion.
// This gate only ever blocks; every other gate (approval hash, state, consent, identity, emergency stop,
// label) applies unchanged.

import type { Env } from "./db";

export type QualityResult = { ok: boolean; why: string };

type MirrorQuality = { post_id: string; humanity?: { content_hash?: unknown; verdict?: unknown; error?: unknown;
  voice_gate?: { verdict?: unknown } | null } | null };

const HEX64 = /^[0-9a-f]{64}$/;

/** The rule itself (pure; the browser mirrors it in ui/lib.txt `qualityGate`). */
export function qualityVerdict(humanity: MirrorQuality["humanity"], approvedHash: string): QualityResult {
  if (!HEX64.test(approvedHash)) return { ok: false, why: "no approved hash to check quality against" };
  if (!humanity || typeof humanity !== "object") return { ok: false, why: "no quality result for this post" };
  if (humanity.error) return { ok: false, why: "the quality check could not run for this post" };
  if (typeof humanity.content_hash !== "string" || !HEX64.test(humanity.content_hash)) {
    return { ok: false, why: "the quality result is not tied to a text hash" };
  }
  if (humanity.content_hash !== approvedHash) return { ok: false, why: "the quality result is for a different text than the approved one" };
  if (humanity.verdict !== "PASS") return { ok: false, why: `Humanity is ${String(humanity.verdict ?? "missing")}, not PASS` };
  const vg = humanity.voice_gate?.verdict;
  if (vg !== "PASS") return { ok: false, why: `Voice Gate is ${String(vg ?? "missing")}, not PASS` };
  return { ok: true, why: "Humanity PASS and Voice Gate PASS for the approved text" };
}

export async function qualityFor(env: Env, postId: string, approvedHash: string): Promise<QualityResult> {
  const row = await env.DB.prepare("SELECT body FROM pipeline_snapshot WHERE id = 1").first<{ body: string }>();
  if (!row) return { ok: false, why: "no pipeline mirror in the cloud yet" };
  let snap: { posts?: MirrorQuality[] };
  try {
    snap = JSON.parse(row.body);
  } catch {
    return { ok: false, why: "the pipeline mirror cannot be read" };
  }
  const p = (snap.posts ?? []).find((x) => x && x.post_id === postId);
  if (!p) return { ok: false, why: "the post is not in the pipeline mirror" };
  return qualityVerdict(p.humanity, approvedHash);
}
