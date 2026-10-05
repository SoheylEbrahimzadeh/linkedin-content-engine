"""The owner's Voice Gate (owner rule 2026-10-05).

A post can pass every mechanical humanity check and still fail the owner's voice. The gate
keeps three judgements apart and never derives a pass from the humanity score:

1. natural English: spoken, varied, plain (the mechanical humanity criteria are evidence here,
   not the verdict);
2. owner-grounded meaning: every owner sentence is a faithful paraphrase of a referenced
   owner-confirmed item (never stronger, narrower, broader or different: `lce.owner_scope`), and
   no personal language is added to a source-heavy post to make it feel personal;
3. owner-recognizable phrasing and reasoning: the owner's sentences sound like the owner's own
   samples. Only a person can judge this; the engine shows the evidence and asks for a review.

Each prose sentence is classed as `source` (attributed to, or taken from, the source), `owner`
(a faithful paraphrase of referenced owner material), `writer` (connective framing that adds no
personal meaning) or `invented_personal` (a personal position, or a writer judgement, that the
owner's material does not contain). Any invented personal sentence fails the gate. When the owner's
share is small, the post is classified as source-heavy with insufficient owner voice: that is
an honest label, not a failure to be fixed by inventing "I would …", "For me …" or "my approach".

A manual review is recorded on the post (`voice_gate_review`) bound to the text's content hash;
a review of an older text is stale and ignored.
"""

from __future__ import annotations

import re

from lce.textutil import content_hash, sentences, words

SOURCE_HEAVY = "source_heavy_insufficient_owner_voice"
LABELS = {
    SOURCE_HEAVY: "Source-heavy — insufficient owner voice",
    "owner_voiced": "Owner-voiced",
}
MIN_OWNER_SHARE = 0.25
KINDS = ("source", "owner", "writer", "invented_personal")
REVIEW_FIELDS = ("natural_english", "owner_grounded_meaning", "owner_phrasing")
LEGACY_REVIEW_FIELDS = {"owner_grounded_opinion": "owner_grounded_meaning"}
REVIEW_VALUES = ("pass", "fail", "insufficient")
RULE = "A post can pass the mechanical humanity checks and still fail the owner's Voice Gate."

ATTRIBUTION_RE = re.compile(
    r"\b(?:he|she|they|it|the (?:company|vendor|team|authors?))\s+(?:also\s+)?"
    r"(?:says?|said|argues?|writes?|wrote|notes?|warns?|points? out|suggests?|asks?|recommends?|"
    r"adds?|finds?|found|puts?|calls?|describes?|explains?|reports?|estimates?|expects?|'d rather|"
    r"would rather)\b"
    r"|\baccording to\b|\bas (?:he|she|they) (?:points? out|says?|puts? it)\b"
    r"|\b(?:the|a|this|that) (?:report|survey|study|article|piece|post|changelog|research|analysis)\b"
    r"|\b[A-Z][\w.&-]+(?:'s)? (?:own )?(?:says|said|recommends|warns|found|finds|reports|announced|"
    r"has added|added|launched|expects|estimates)\b"
    r"|\bwrote (?:in|for|on|about)\b|\bwalked through\b|\bin (?:a|his|her|their) (?:piece|post|article)\b",
)
FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|my|me|mine)\b", re.I)
PERSONALISING_RE = re.compile(
    r"\b(?:for me|personally|my approach|my take|the way i see it|from where i sit|if you ask me|"
    r"i(?:'d| would) (?:say|ask|look|want|start|check|keep|leave|push|insist)|i want|i need|"
    r"i think|i believe|i agree|i(?:'m| am) (?:convinced|wary|skeptical))\b",
    re.I,
)
VIEW_KINDS = ("opinions", "disagreements", "approaches", "observations", "principles", "noticings",
              "priorities")
# A writer sentence that judges (instead of connecting) adds personal meaning nobody confirmed.
NORMATIVE_RE = re.compile(
    r"\b(?:should(?:n't)?|must|ought to|needs? to|have to|has to|the (?:right|wrong|better|best|real) "
    r"(?:way|question|answer|move|call|choice)|is (?:a )?(?:mistake|wrong|right|the answer)|"
    r"don't|never|always)\b",
    re.I,
)
NATURAL_CRITERIA = (
    "real_person", "spoken", "variation", "no_stiffness", "no_symmetry", "no_over_explaining",
)


def _cw(text: str) -> set[str]:
    """Content words, crudely singularised so 'depends' meets 'depend'."""
    from lce.humanity import _content_words

    return {w[:-1] if w.endswith("s") and not w.endswith("ss") else w for w in _content_words(text)}


def _overlap(sentence: str, ref: str) -> float:
    """Share of the sentence's content words found in ref (0 when fewer than two are shared)."""
    a = _cw(sentence)
    shared = a & _cw(ref)
    return len(shared) / len(a) if a and len(shared) >= 2 else 0.0


def owner_items(post: dict, golden_items: dict) -> list[dict]:
    refs = (post.get("opinions_used") or []) + (post.get("observations_used") or [])
    return [golden_items[r] for r in refs if golden_items.get(r, {}).get("kind") in VIEW_KINDS]


def owner_material(post: dict, golden_items: dict, stories: dict) -> list[str]:
    """Texts of the owner material the post references (confirmed items, PUBLIC story claims)."""
    out = [" ".join(str(i.get(k) or "") for k in ("text", "why", "instead"))
           for i in owner_items(post, golden_items)]
    for s in post.get("stories_used") or []:
        st = stories.get(s) or {}
        if st.get("publication_status") == "PUBLIC":
            out += [str(c.get("text", c)) for c in st.get("allowed_claims") or []]
            out.append(str(st.get("summary") or ""))
    return [t for t in out if t.strip()]


def classify_sentences(text: str, post: dict, golden_items: dict, stories: dict) -> list[dict]:
    from lce import owner_scope
    from lce.humanity import _body

    items = owner_items(post, golden_items)
    material = owner_material(post, golden_items, stories)
    claims = [c.get("text", "") for c in post.get("claims") or []]
    rows = []
    for s in (x for x in sentences(_body(text)) if words(x)):
        first = bool(FIRST_PERSON_RE.search(s))
        own = max((_overlap(s, m) for m in material), default=0.0)
        src = max((_overlap(s, c) for c in claims), default=0.0)
        scope = owner_scope.check(s, items) if items else None
        own = max(own, scope["similarity"] if scope else 0.0)
        source_first = src >= 0.3 and src >= own   # a sentence that is the source's stays the source's
        attributed = bool(ATTRIBUTION_RE.search(s))
        note = ""
        if material and (first or own >= 0.34) and not attributed and not source_first:
            if scope and scope["verdict"] != "faithful":
                kind, note = "invented_personal", f"{scope['verdict']}: {'; '.join(scope['reasons'])}"
            else:
                kind = "owner"
        elif attributed or src >= 0.3:
            kind = "source"
        elif first:
            kind, note = "invented_personal", "first-person position without owner material"
        elif NORMATIVE_RE.search(s):
            kind, note = "invented_personal", "writer judgement that no owner material or source states"
        else:
            kind = "writer"
        rows.append({"sentence": s, "kind": kind, "owner_overlap": round(own, 2),
                     "source_overlap": round(src, 2), **({"note": note} if note else {})})
    return rows


def _phrasing_evidence(owner_rows: list[dict], samples: list[str], material: list[str]) -> list[str]:
    """Word pairs the owner's sentences share with the owner's own samples and confirmed items."""
    def pairs(t):
        w = words(t)
        return {f"{a} {b}" for a, b in zip(w, w[1:], strict=False)}

    ref = set().union(*(pairs(x) for x in samples + material)) if samples or material else set()
    common = {"of the", "in the", "it is", "to the", "and the", "on the", "is a", "for the", "that the"}
    hits = set()
    for r in owner_rows:
        hits |= (pairs(r["sentence"]) & ref) - common
    return sorted(hits)


def review_of(post: dict, text: str) -> dict | None:
    rev = post.get("voice_gate_review")
    if not rev:
        return None
    rev = {LEGACY_REVIEW_FIELDS.get(k, k): v for k, v in rev.items()}
    return {**rev, "stale": rev.get("content_hash") != content_hash(text)}


def assess(
    text: str,
    *,
    post: dict,
    golden_items: dict,
    stories: dict,
    samples: list[str],
    humanity: dict,
    codes: set[str],
) -> dict:
    from lce.humanity import MANUFACTURED_CODES

    rows = classify_sentences(text, post, golden_items, stories)
    n = len(rows) or 1
    owner = [r for r in rows if r["kind"] == "owner"]
    unbacked = [r for r in rows if r["kind"] == "invented_personal"]
    share = round(len(owner) / n, 2)
    classification = "owner_voiced" if owner and share >= MIN_OWNER_SHARE else SOURCE_HEAVY
    by = {c["id"]: c for c in humanity.get("criteria", [])}

    # 1. natural English (mechanical evidence; a person still reads it aloud)
    bad = [k for k in NATURAL_CRITERIA if by.get(k, {}).get("result") == "fail"]
    natural = {
        "result": "fail" if bad else "review",
        "why": ("mechanical failures: " + ", ".join(bad)) if bad
        else "no mechanical failure; read it aloud: mechanical checks are not proof",
    }

    # 2. owner-grounded meaning
    manufactured = sorted(codes & set(MANUFACTURED_CODES))
    personalising = [
        r["sentence"] for r in rows if r["kind"] != "owner" and PERSONALISING_RE.search(r["sentence"])
    ]
    if manufactured or unbacked or personalising:
        opinion = {
            "result": "fail",
            "why": "; ".join(
                x for x in (
                    ", ".join(manufactured),
                    f"{len(unbacked)} invented personal sentence(s): {unbacked[0].get('note', '')}"
                    if unbacked else "",
                    f"personal language outside the owner's material: {personalising[0][:80]!r}"
                    if personalising else "",
                ) if x
            ),
        }
    elif owner:
        opinion = {
            "result": "pass",
            "why": f"{len(owner)} sentence(s) from referenced owner material, none invented",
        }
    else:
        opinion = {"result": "insufficient", "why": "no owner material in the post (nothing invented either)"}

    # 3. owner-recognizable phrasing / reasoning (a person decides)
    material = owner_material(post, golden_items, stories)
    evidence = _phrasing_evidence(owner, samples, material)
    if not owner:
        phrasing = {"result": "insufficient", "why": "no owner sentence to recognise", "evidence": []}
    else:
        phrasing = {
            "result": "review",
            "why": f"{len(evidence)} phrase(s) shared with your samples or confirmed items; "
            "only a person can say whether it sounds like you",
            "evidence": evidence[:12],
        }

    review = review_of(post, text)
    dims = {"natural_english": natural, "owner_grounded_meaning": opinion, "owner_phrasing": phrasing}
    if review and not review["stale"]:
        for k in REVIEW_FIELDS:
            if dims[k]["result"] == "fail":
                continue          # a review never overrides a mechanical failure
            who = review.get("reviewer", "reviewer")
            dims[k] = {**dims[k], "result": review.get(k, dims[k]["result"]),
                       "why": f"{who} review: {review.get(k)}; " + dims[k]["why"]}

    results = [d["result"] for d in dims.values()]
    if "fail" in results:
        verdict = "FAIL"
    elif classification == SOURCE_HEAVY:
        verdict = "SOURCE_HEAVY"
    elif all(r == "pass" for r in results):
        verdict = "PASS"
    else:
        verdict = "REVIEW_NEEDED"
    needs_input = classification == SOURCE_HEAVY or "insufficient" in results
    return {
        "rule": RULE,
        "verdict": verdict,
        "needs_owner_input": needs_input,
        "owner_input_reason": (
            "not enough confirmed owner material on this topic; answer the owner intake"
            if needs_input else ""
        ),
        "classification": classification,
        "classification_label": LABELS[classification],
        "owner_share": share,
        "counts": {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS},
        "dimensions": dims,
        "sentences": rows,
        "review": review,
        "humanity_score": f"{humanity.get('score')}/{humanity.get('of')}",
    }


def make_review(text: str, *, reviewer: str, values: dict, notes: str = "", at: str | None = None) -> dict:
    """A manual Voice Gate review bound to this exact text."""
    from lce.store import now_iso

    if reviewer not in ("owner", "writer"):
        raise ValueError("reviewer must be owner or writer")
    values = {LEGACY_REVIEW_FIELDS.get(k, k): v for k, v in values.items()}
    for k in REVIEW_FIELDS:
        if values.get(k) not in REVIEW_VALUES:
            raise ValueError(f"{k} must be one of {', '.join(REVIEW_VALUES)}")
    return {
        "content_hash": content_hash(text),
        "reviewer": reviewer,
        "at": at or now_iso(),
        **{k: values[k] for k in REVIEW_FIELDS},
        "notes": notes,
    }
