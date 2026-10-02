"""Human approval gate.

Rules enforced here:
- An approval artifact can only be prepared when QA and the duplicate check
  both passed for the exact current text.
- Approval is bound to the SHA-256 of that text and to the image decision
  (the image file's SHA-256, or an explicit "none"). The approver must type the
  hash prefix and a confirmation phrase in an interactive terminal; the command
  refuses to run without a TTY, so an automated agent cannot approve.
- Nothing here publishes. READY_TO_PUBLISH is the end of Phase 1.
"""

from __future__ import annotations

import getpass
import json
import sys
from collections.abc import Callable

from lce import images
from lce.posts import current_text, reopen, set_state
from lce.state import PostState
from lce.store import DataStore, StoreError, now_iso
from lce.textutil import content_hash

S = PostState
MIN_PREFIX = 12


def _require_consistent(store: DataStore, post: dict) -> str:
    text = current_text(store, post["post_id"])
    h = content_hash(text)
    if post.get("content_hash") != h:
        raise StoreError("post.md does not match the recorded content hash; save it again")
    for check in ("qa", "duplicate"):
        block = post.get(check, {})
        if block.get("status") != "passed" or block.get("content_hash") != h:
            raise StoreError(f"{check} has not passed for the current text")
    return h


def render_artifact(store: DataStore, post: dict, text: str, h: str) -> str:
    qa = json.loads((store.post_dir(post["post_id"]) / "qa.json").read_text("utf-8"))
    dup = json.loads((store.post_dir(post["post_id"]) / "duplicate.json").read_text("utf-8"))
    lines = [
        f"# Approval request — {post['post_id']}",
        "",
        "> **Not published.** Approving records your decision for this exact text only.",
        "> Phase 1 has no publisher; nothing is sent to LinkedIn.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Topic | {post.get('topic', '')} |",
        f"| Content pillar | {post.get('pillar', '')} |",
        f"| Format | {post.get('format', '')} |",
        f"| Angle | {post.get('angle', '')} |",
        f"| Language | {post['language']} |",
        f"| Planned date | {post.get('plan_date', '')} |",
        f"| Generated at | {now_iso()} |",
        f"| Characters | {len(text.strip())} |",
        f"| Content hash (SHA-256) | `{h}` |",
        "| Approval state | **pending** |",
        "",
        "## Proposed post",
        "",
        "```text",
        text.rstrip("\n"),
        "```",
        "",
        "## Sources",
        "",
    ]
    lines += [f"- {s.get('title') or s['url']} — {s['url']}" for s in post.get("sources", [])] or [
        "- none"
    ]
    lines += ["", "## Image", ""]
    img = images.load(store, post["post_id"]) or {}
    if img.get("kind", images.NO_IMAGE) == images.NO_IMAGE:
        lines.append(f"- no image — {img.get('rationale', '')}")
    else:
        prov = img.get("provenance") or {}
        lines += [f"- kind: {img['kind']} — file `{img['file']}` (SHA-256 `{img['sha256']}`)",
                  f"- relation: {img.get('relation', '')}",
                  f"- alt text: {img.get('alt_text', '')}",
                  f"- origin: {prov.get('origin')}; usage: {prov.get('usage')}"
                  + (f"; license: {prov['license']}" if prov.get("license") else "")
                  + (f"; source: {prov['source_url']}" if prov.get("source_url") else "")
                  + (f"; generated with: {prov['generation']['method']}"
                     if prov.get("generation") else ""),
                  f"- rationale: {img.get('rationale', '')}"]
    lines += ["", "## Stories used", ""]
    lines += [f"- {s}" for s in post.get("stories_used", [])] or ["- none"]
    lines += ["", f"## QA — {qa['status']} ({len(qa['errors'])} errors, "
                  f"{len(qa['warnings'])} warnings)", ""]
    lines += [f"- [{w['severity']}] {w['code']}: {w['message']}"
              for w in qa["errors"] + qa["warnings"]] or ["- no findings"]
    lines += ["", f"## Duplicate check — {dup['status']} "
                  f"(compared against {dup['compared_against']} posts)", ""]
    for key in ("exact", "near", "similar", "story_reuse", "angle_reuse", "topic_reuse"):
        if dup.get(key):
            lines.append(f"- {key}: {json.dumps(dup[key], ensure_ascii=False)}")
    if not any(dup.get(k) for k in ("exact", "near", "similar", "story_reuse", "angle_reuse",
                                     "topic_reuse")):
        lines.append("- no matches")
    lines += [
        "",
        "## Decide",
        "",
        "Run in **your own terminal** (it requires interactive confirmation):",
        "",
        f"    lce approve {post['post_id']} --hash {h[:MIN_PREFIX]}",
        f"    lce reject {post['post_id']} --reason \"...\"",
        "",
    ]
    return "\n".join(lines)


def prepare(store: DataStore, post_id: str) -> tuple[dict, str]:
    post = store.load_post(post_id)
    if PostState(post["state"]) != S.DUPLICATE_CHECKED:
        raise StoreError(f"approval needs a DUPLICATE_CHECKED post; this one is {post['state']}")
    h = _require_consistent(store, post)
    errors, _ = images.check(store, post_id)
    if errors:
        raise StoreError("image: " + "; ".join(errors))
    artifact = render_artifact(store, post, current_text(store, post_id), h)
    path = store.post_dir(post_id) / "APPROVAL.md"
    store.write_text(path, artifact)
    post["approval"] = {"state": "pending", "artifact_hash": content_hash(artifact),
                        "image_hash": images.approval_hash(store, post_id)}
    set_state(store, post, S.AWAITING_APPROVAL, "approval artifact prepared")
    return post, str(path)


def approve(store: DataStore, post_id: str, hash_prefix: str, *,
            confirm: Callable[[str], str] = input, is_tty: Callable[[], bool] | None = None) -> dict:
    tty = is_tty if is_tty is not None else (lambda: sys.stdin.isatty() and sys.stdout.isatty())
    if not tty():
        raise StoreError("approval requires an interactive terminal; run it yourself, not via "
                         "a script or an agent")
    post = store.load_post(post_id)
    if PostState(post["state"]) != S.AWAITING_APPROVAL:
        raise StoreError(f"only AWAITING_APPROVAL posts can be approved; this one is {post['state']}")
    h = _require_consistent(store, post)
    prefix = hash_prefix.strip().lower()
    if len(prefix) < MIN_PREFIX or not h.startswith(prefix):
        raise StoreError(f"hash prefix does not match the current text (need ≥{MIN_PREFIX} chars)")
    artifact = store.post_text(post_id, "APPROVAL.md") or ""
    if content_hash(artifact) != post.get("approval", {}).get("artifact_hash"):
        raise StoreError("APPROVAL.md changed after it was prepared; prepare it again")
    image_hash = post.get("approval", {}).get("image_hash")
    errors, _ = images.check(store, post_id)
    if errors or images.approval_hash(store, post_id) != image_hash:
        raise StoreError("the image changed after APPROVAL.md was prepared; prepare it again")
    phrase = f"APPROVE {post_id}"
    typed = confirm(f"Type '{phrase}' to approve this exact text (hash {h[:MIN_PREFIX]}…): ")
    if typed.strip() != phrase:
        raise StoreError("confirmation phrase did not match; nothing was approved")
    post["approval"] = {"state": "approved", "approved_hash": h, "approved_at": now_iso(),
                        "approved_by": f"local-tty:{getpass.getuser()}",
                        "artifact_hash": post["approval"]["artifact_hash"],
                        "image_hash": image_hash}
    store.log_event("approval", post_id=post_id, decision="approved", content_hash=h)
    return set_state(store, post, S.APPROVED, "approved by human")


def approve_recorded(store: DataStore, post_id: str, content_hash_: str, image_sha256: str | None, *,
                     approver: str, decision_id: str) -> dict:
    """Apply an approval the owner made in the cloud Control Center (LCE-036).

    The human step happened there: a person signed in through Cloudflare Access,
    reviewed the text, and typed `APPROVE <post>`; the Worker bound the decision
    to the full hash of that text. Here the same checks as `approve` run against
    the git-tracked files; any difference refuses the decision. Never publishes."""
    post = store.load_post(post_id)
    if PostState(post["state"]) != S.AWAITING_APPROVAL:
        raise StoreError(f"only AWAITING_APPROVAL posts can be approved; this one is {post['state']}")
    h = _require_consistent(store, post)
    if content_hash_ != h:
        raise StoreError("the approved text is not the current text; nothing was approved")
    artifact = store.post_text(post_id, "APPROVAL.md") or ""
    if content_hash(artifact) != post.get("approval", {}).get("artifact_hash"):
        raise StoreError("APPROVAL.md changed after it was prepared; prepare it again")
    image_hash = post.get("approval", {}).get("image_hash")
    errors, _ = images.check(store, post_id)
    if errors or images.approval_hash(store, post_id) != image_hash:
        raise StoreError("the image changed after APPROVAL.md was prepared; prepare it again")
    if (image_sha256 or images.NO_IMAGE) != image_hash:
        raise StoreError("the image you reviewed is not the current image; nothing was approved")
    post["approval"] = {"state": "approved", "approved_hash": h, "approved_at": now_iso(),
                        "approved_by": f"cloud-access:{approver}", "decision_id": decision_id,
                        "artifact_hash": post["approval"]["artifact_hash"], "image_hash": image_hash}
    store.log_event("approval", post_id=post_id, decision="approved", content_hash=h,
                    via="cloud", decision_id=decision_id)
    return set_state(store, post, S.APPROVED, "approved by human (cloud Control Center)")


def reject(store: DataStore, post_id: str, reason: str) -> dict:
    post = store.load_post(post_id)
    post["approval"] = {"state": "rejected", "reason": reason}
    store.log_event("approval", post_id=post_id, decision="rejected")
    return set_state(store, post, S.REJECTED, f"rejected: {reason}")


def mark_ready(store: DataStore, post_id: str) -> dict:
    """APPROVED → READY_TO_PUBLISH after re-verifying the approved hash. Publishes nothing."""
    post = store.load_post(post_id)
    if PostState(post["state"]) != S.APPROVED:
        raise StoreError(f"only APPROVED posts can be marked ready; this one is {post['state']}")
    h = content_hash(current_text(store, post_id))
    if post.get("approval", {}).get("approved_hash") != h:
        reopen(store, post_id, "text changed after approval")
        raise StoreError("text changed after approval; the approval was discarded")
    if not image_unchanged(store, post):
        reopen(store, post_id, "image changed after approval")
        raise StoreError("image changed after approval; the approval was discarded")
    return set_state(store, post, S.READY_TO_PUBLISH, "ready; no publisher configured")


def image_unchanged(store: DataStore, post: dict) -> bool:
    """True if the approved image decision still holds. Approvals from before the
    image stage carry no image_hash and are treated as text-only."""
    approved = (post.get("approval") or {}).get("image_hash")
    if approved is None:
        return images.load(store, post["post_id"]) is None
    try:
        return images.approval_hash(store, post["post_id"]) == approved
    except (StoreError, OSError):
        return False
