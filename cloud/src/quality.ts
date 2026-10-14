// LCE-059: publication-time quality gate. Approval is hash-bound and checked in the repository, but the
// quality checks (Humanity test, the owner's Voice Gate) can change after an approval (new rules, new owner
// material). A post is published, scheduled or manual, only when the pipeline mirror holds a result for
// exactly the approved text (its content hash) that shows nothing invented and no QA error.
//
// LCE-068 (owner decision 2026-10-14, "Option A"): SOURCE_HEAVY alone no longer blocks. A source-grounded
// post that the owner approved in its exact version is publishable; the owner's approval of that text is the
// human reading the Voice Gate asks for. What still blocks, fail closed:
// - no mirror, no result for the post, a result for another text (stale or mismatched), a scoring error;
// - Humanity other than PASS;
// - any QA error in the result (unsupported number, missing source, personal claim without a story, privacy, ...);
// - Voice Gate FAIL, or any verdict other than PASS / SOURCE_HEAVY / REVIEW_NEEDED, or a missing verdict;
// - any invented personal sentence (a first-person position or writer judgement the owner never confirmed),
//   or an owner-grounded-meaning failure, or a result that does not carry those fields.
// This gate only ever blocks; every other gate (approval hash, state, consent, identity, emergency stop,
// label, auto-publish for the scheduled path) applies unchanged.

import type { Env } from "./db";

export type QualityResult = { ok: boolean; why: string };

type MirrorQuality = { post_id: string; humanity?: { content_hash?: unknown; verdict?: unknown; error?: unknown;
  errors?: unknown; voice_gate?: { verdict?: unknown; counts?: { invented_personal?: unknown } | null;
    dimensions?: { owner_grounded_meaning?: { result?: unknown } | null } | null } | null } | null };

const HEX64 = /^[0-9a-f]{64}$/;
/** Voice Gate verdicts a hash-bound owner approval may publish (LCE-068); FAIL and anything else block. */
export const PUBLISHABLE_VOICE = ["PASS", "SOURCE_HEAVY", "REVIEW_NEEDED"] as const;

/** The rule itself (pure; the browser mirrors it in ui/lib.txt `qualityGate`; keep both identical). */
export function qualityVerdict(humanity: MirrorQuality["humanity"], approvedHash: string): QualityResult {
  if (!HEX64.test(approvedHash)) return { ok: false, why: "no approved hash to check quality against" };
  if (!humanity || typeof humanity !== "object") return { ok: false, why: "no quality result for this post" };
  if (humanity.error) return { ok: false, why: "the quality check could not run for this post" };
  if (typeof humanity.content_hash !== "string" || !HEX64.test(humanity.content_hash)) {
    return { ok: false, why: "the quality result is not tied to a text hash" };
  }
  if (humanity.content_hash !== approvedHash) return { ok: false, why: "the quality result is for a different text than the approved one" };
  if (humanity.verdict !== "PASS") return { ok: false, why: `Humanity is ${String(humanity.verdict ?? "missing")}, not PASS` };
  if (!Array.isArray(humanity.errors)) return { ok: false, why: "the quality result does not list its QA errors" };
  if (humanity.errors.length) return { ok: false, why: `QA errors in the approved text: ${humanity.errors.join(", ")}` };
  const g = humanity.voice_gate;
  const vg = g?.verdict;
  if (!(PUBLISHABLE_VOICE as readonly unknown[]).includes(vg)) return { ok: false, why: `Voice Gate is ${String(vg ?? "missing")}` };
  const invented = g?.counts?.invented_personal;
  if (typeof invented !== "number") return { ok: false, why: "the Voice Gate result does not count invented sentences" };
  if (invented > 0) return { ok: false, why: `Voice Gate found ${invented} invented personal sentence(s)` };
  const meaning = g?.dimensions?.owner_grounded_meaning?.result;
  if (typeof meaning !== "string") return { ok: false, why: "the Voice Gate result has no owner-grounded meaning check" };
  if (meaning === "fail") return { ok: false, why: "Voice Gate: the owner's meaning is changed or invented" };
  return { ok: true, why: `Humanity PASS, no QA error, nothing invented (Voice Gate ${String(vg)}) for the approved text` };
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
