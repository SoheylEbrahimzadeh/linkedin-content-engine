"""Semantic media relevance (LCE-041).

A generated visual must communicate the post's idea, not re-type the post. This
module evaluates a visual's specification (the exact strings it draws, its
concept and visual form) against the post and records a machine-checkable
`media_relevance` decision:

- concept / visual_type / relevance_reason must be stated (the author has to
  say what idea the visual carries and why it helps);
- labels are short (a label, not a sentence), no copied questions, a bounded
  amount of text in the image;
- `copied_post_text_ratio`: share of the image's non-factual words that sit in
  a 4-word sequence also found in the post. A text dump scores near 1.0, a
  conceptual diagram with its own short labels near 0;
- every factual string (a number) must be a recorded, sourced claim, and the
  image must then carry a source line;
- alt text must describe the visual, not repeat the post.

Any problem → `media_decision: rejected` (regenerate the visual or choose
text-only). "The PNG rendered" is never treated as proof of relevance.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from lce.store import now_iso
from lce.textutil import claim_numbers

VISUAL_TYPES = (
    "flow",
    "process",
    "decision_tree",
    "framework",
    "relationship_map",
    "comparison",
    "matrix",
    "chart",
    "photo",
    "screenshot",
    "illustration",
)
MAX_LABEL_WORDS = 6
MAX_NOTE_WORDS = 8
MAX_IMAGE_WORDS = 60
MAX_COPIED_RATIO = 0.35
MAX_ALT_COPIED_RATIO = 0.5
SHINGLE = 4
MIN_CONCEPT, MIN_REASON, MIN_ALT = 15, 30, 40

_WORD = re.compile(r"[a-z0-9%$€£]+(?:['’][a-z]+)?")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _shingles(ws: list[str]) -> set[tuple[str, ...]]:
    return {tuple(ws[i : i + SHINGLE]) for i in range(len(ws) - SHINGLE + 1)}


def copied_ratio(strings: list[str], post_text: str) -> float:
    """Share of the strings' words covered by a 4-word sequence that also occurs in the post."""
    post = _shingles(words(post_text))
    total = covered = 0
    for s in strings:
        ws = words(s)
        total += len(ws)
        hit = [False] * len(ws)
        for i in range(len(ws) - SHINGLE + 1):
            if tuple(ws[i : i + SHINGLE]) in post:
                for j in range(i, i + SHINGLE):
                    hit[j] = True
        covered += sum(hit)
    return round(covered / total, 3) if total else 0.0


def spec_strings(spec: dict) -> dict:
    """The strings a visual draws, by role (labels, notes, outcomes, title, footer)."""
    nodes = spec.get("nodes") or [{"label": i} for i in spec.get("items") or []]
    return {
        "title": [x for x in (spec.get("title"), spec.get("center"), spec.get("decision_label")) if x],
        "labels": [n["label"] for n in nodes if n.get("label")],
        "notes": [n["note"] for n in nodes if n.get("note")],
        "outcomes": list(spec.get("outcomes") or []),
        "footer": [spec["footer"]] if spec.get("footer") else [],
    }


def evaluate(spec: dict, post: dict, text: str, *, alt_text: str = "", method: str = "") -> dict:
    """The media_relevance record for a visual drawn from `spec`."""
    problems: list[str] = []
    concept = (spec.get("concept") or "").strip()
    vtype = spec.get("visual_type")
    reason = (spec.get("relevance_reason") or "").strip()
    if len(concept) < MIN_CONCEPT:
        problems.append("no concept: state the idea the visual communicates")
    if vtype not in VISUAL_TYPES:
        problems.append(f"visual_type must be one of {', '.join(VISUAL_TYPES)}")
    if len(reason) < MIN_REASON:
        problems.append("no relevance reason: say what the visual adds beyond the text")
    parts = spec_strings(spec)
    if vtype != "chart" and len(parts["labels"]) < 3:
        problems.append("a conceptual visual needs at least 3 labelled elements")
    is_fact = lambda s: bool(claim_numbers(s))  # noqa: E731 - factual strings are checked below
    for label in parts["labels"] + parts["outcomes"]:
        if not is_fact(label) and len(words(label)) > MAX_LABEL_WORDS:
            problems.append(f"label is a sentence, not a label ({len(words(label))} words): {label!r}")
    for note in parts["notes"]:
        if not is_fact(note) and len(words(note)) > MAX_NOTE_WORDS:
            problems.append(f"note too long ({len(words(note))} words): {note!r}")
    post_words = " ".join(words(text))
    for s in parts["title"] + parts["labels"] + parts["notes"] + parts["outcomes"]:
        if s.strip().endswith("?") and len(words(s)) >= 3 and " ".join(words(s)) in post_words:
            problems.append(f"question copied from the post into the image: {s!r}")

    # Factual strings: anything with a number must be a recorded, sourced claim.
    claims = post.get("claims") or []
    claim_nums = {n for c in claims for n in claim_numbers(c.get("text", ""))}
    factual, nonfactual = [], []
    for s in parts["title"] + parts["labels"] + parts["notes"] + parts["outcomes"] + parts["footer"]:
        nums = claim_numbers(s)
        if nums:
            ok = all(n in claim_nums for n in nums)
            match = next((c for c in claims if set(nums) <= set(claim_numbers(c.get("text", "")))), None)
            factual.append(
                {
                    "text": s,
                    "numbers": nums,
                    "supported": ok,
                    "source_url": match.get("source_url") if match else None,
                }
            )
            if not ok:
                problems.append(f"unsupported number in the image (not a recorded claim): {s!r}")
        elif s not in parts["footer"]:
            nonfactual.append(s)
    publishers = sorted({urlparse(f["source_url"]).netloc for f in factual if f.get("source_url")})
    source_line = (spec.get("source_line") or "").strip()
    requirements = [f"source shown on the image: {p}" for p in publishers]
    if factual and not source_line:
        problems.append("the image states a figure but has no source line")

    ratio = copied_ratio(nonfactual, text)
    if ratio > MAX_COPIED_RATIO:
        problems.append(
            f"text dump: {ratio:.0%} of the image's words are copied from the post "
            f"(max {MAX_COPIED_RATIO:.0%}); draw the idea, not the text"
        )
    image_words = sum(len(words(s)) for group in parts.values() for s in group if not is_fact(s))
    if image_words > MAX_IMAGE_WORDS:
        problems.append(f"too much text in the image ({image_words} words, max {MAX_IMAGE_WORDS})")
    if alt_text:
        # Recorded claims may be quoted in the alt text; the rest must describe the visual.
        alt_rest = alt_text.lower()
        for c in claims:
            alt_rest = alt_rest.replace(c.get("text", "").lower().rstrip("."), " ")
        alt_ratio = copied_ratio([alt_rest], text)
        if len(alt_text.strip()) < MIN_ALT:
            problems.append("alt text is too short to describe the visual")
        if alt_ratio > MAX_ALT_COPIED_RATIO:
            problems.append("alt text repeats the post instead of describing the visual")
    else:
        alt_ratio = None
        problems.append("alt text is required")
    return {
        "concept": concept or None,
        "visual_type": vtype,
        "relevance_reason": reason or None,
        "copied_post_text_ratio": ratio,
        "alt_copied_ratio": alt_ratio,
        "image_words": image_words,
        "factual_claims": factual,
        "source_requirements": requirements,
        "media_decision": "rejected" if problems else "accepted",
        "problems": problems,
        "text_checked": True,
        "checked_at": now_iso(),
        **({"method": method} if method else {}),
    }


def declared(*, concept: str, visual_type: str, reason: str, alt_text: str, post: dict, text: str) -> dict:
    """Relevance of an image whose drawn text the engine cannot read (a photo, a
    screenshot, a Commons file, a diagram made elsewhere): the concept, form and
    reason are stated by whoever chose it; the alt text is still checked."""
    rec = evaluate(
        {
            "concept": concept,
            "visual_type": visual_type,
            "relevance_reason": reason,
            "nodes": [{"label": "x"}] * 3,
        },
        post,
        text,
        alt_text=alt_text,
    )
    rec.update(
        {
            "copied_post_text_ratio": None,
            "image_words": None,
            "text_checked": False,
            "note": "text inside the image is not machine-readable; concept and reason as declared",
        }
    )
    return rec


def legacy_spec(doc: dict) -> dict | None:
    """A spec for images made before LCE-041 (verbatim checklist diagrams)."""
    if doc.get("spec"):
        return doc["spec"]
    rel = doc.get("relation") or ""
    if doc.get("kind") == "diagram" and "verbatim:" in rel:
        body = rel.split("verbatim:", 1)[1]
        title, _, rest = body.partition("—")
        return {"title": title.strip(), "items": [s.strip() for s in rest.split(";") if s.strip()]}
    return None
