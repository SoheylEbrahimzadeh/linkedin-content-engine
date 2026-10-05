"""No stronger position than the owner's confirmed material (owner rule 2026-10-05).

A confirmed owner item may be paraphrased naturally when the meaning stays the same. The writer
must not make it stronger, narrower (a specific operational position), broader, or different,
and must not turn it into first-person agreement or a personal action it does not contain.

Deterministic and local (no model, no network). Word overlap is only one signal; the verdict
comes from what the sentence adds to the item:

- stronger: an absolute ("always", "never", "every", "must", ...) the item does not contain;
- narrower: a first-person commitment to a concrete action ("I'd keep", "I'd require",
  "I wouldn't cut") that is not one of the item's actions;
- broader: a domain widening ("everything", "any system", "in general") the item does not make;
- different: a flipped polarity or an antonym on what the item says, or agreement with a
  person the item does not record;
- unsupported: a first-person position that is not close to any referenced item at all.

The owner's own boundaries, recorded with the item at confirmation time, come first: a sentence
close to a forbidden interpretation fails, one close to an allowed paraphrase passes (unless it
adds one of the escalations above). Ambiguous cases fail closed; the Voice Gate's manual review
remains the final reading.
"""

from __future__ import annotations

import re

from lce.textutil import words

VERDICTS = ("faithful", "stronger", "narrower", "broader", "different", "unsupported")
CODES = {
    "stronger": "pov.stronger_than_owner",
    "narrower": "pov.narrower_than_owner",
    "broader": "pov.broader_than_owner",
    "different": "pov.different_from_owner",
    "unsupported": "pov.unsupported_by_owner",
}
MESSAGES = {
    "stronger": "makes the owner's position stronger than the recorded material",
    "narrower": "turns the owner's material into a specific operational position it does not contain",
    "broader": "widens the owner's material beyond what it covers",
    "different": "says something different from the owner's material",
    "unsupported": "a first-person position that is not in the owner's recorded material",
}

SYNONYMS = {
    "human": ("human", "person", "people", "someone", "somebody", "humans", "persons", "colleague"),
    "control": (
        "control",
        "oversight",
        "charge",
        "supervision",
        "management",
        "judgment",
        "judgement",
        "check",
        "checks",
        "checking",
        "review",
        "watch",
    ),
    "ai": (
        "ai",
        "agent",
        "agents",
        "model",
        "models",
        "bot",
        "bots",
        "chatbot",
        "chatbots",
        "copilot",
        "llm",
    ),
    "problem": ("problem", "problems", "issue", "issues", "flaw", "flaws", "cause", "root"),
    "team": ("team", "teams", "colleagues", "together"),
    "need": ("need", "needs", "needed", "must", "should", "require", "requires", "required", "has", "have"),
    "mistake": ("mistake", "mistakes", "error", "errors", "wrong", "fails", "failure"),
    "replace": ("replace", "replacing", "replacement", "migrate", "migration", "migrating", "move", "moving"),
    "cost": ("cost", "costs", "price", "loss", "losses", "disruption", "disruptions"),
    "test": ("test", "tested", "testing", "try", "tried", "trial", "pilot"),
    "tool": ("tool", "tools", "software", "product", "platform"),
    "system": ("system", "systems", "platform", "component"),
}
CANON = {w: c for c, ws in SYNONYMS.items() for w in ws}
STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "of",
    "to",
    "in",
    "on",
    "at",
    "for",
    "with",
    "by",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "it",
    "its",
    "it's",
    "this",
    "that",
    "there",
    "these",
    "those",
    "so",
    "as",
    "from",
    "into",
    "when",
    "what",
    "which",
    "who",
    "how",
    "than",
    "then",
    "too",
    "very",
    "just",
    "also",
    "still",
    "can",
    "could",
    "would",
    "will",
    "do",
    "does",
    "did",
    "i",
    "i'd",
    "i'm",
    "we",
    "you",
    "they",
    "he",
    "she",
    "my",
    "our",
    "your",
    "their",
    "his",
    "her",
    "me",
    "us",
    "them",
    "not",
    "no",
    "don't",
    "doesn't",
    "isn't",
    "aren't",
    "wasn't",
    "won't",
    "wouldn't",
    "get",
    "gets",
    "got",
    "make",
    "makes",
    "made",
    "take",
    "takes",
    "taken",
    "lot",
    "much",
    "many",
    "more",
    "some",
    "about",
    "over",
    "up",
    "out",
    "own",
    "one",
    "all",
    "every",
    "always",
    "never",
    "any",
}
# Plain negation only: "never"/"without" are absolutes (stronger), not a flipped meaning.
NEGATORS = {"not", "no", "don't", "doesn't", "isn't", "aren't", "wasn't", "can't", "cannot", "didn't"}
# Polarity is compared only on what an item claims (its predicates), not on every shared word.
PREDICATES = {
    "need",
    "control",
    "test",
    "replace",
    "problem",
    "work",
    "help",
    "trust",
    "depend",
    "cost",
    "decide",
    "matter",
    "right",
    "safe",
    "good",
    "ready",
}
ANTONYMS = [
    ({"good", "great", "useful", "helpful", "worth"}, {"bad", "useless", "pointless", "waste", "harmful"}),
    ({"safe", "safer"}, {"unsafe", "risky", "dangerous"}),
    ({"right", "correct"}, {"wrong", "incorrect"}),
    ({"before", "first"}, {"after", "afterwards", "later", "quickly", "immediately", "straight", "asap"}),
    ({"slow", "slower"}, {"fast", "faster", "quick", "quicker"}),
]
NOT_ABSOLUTE_RE = re.compile(
    r"\b(?:not|n't|isn't|doesn't|don't|aren't|wasn't)\s+(?:always|every|all)\b", re.I
)
ABSOLUTE_RE = re.compile(
    r"\b(?:always|never|every|each and every|all the time|in every case|without exception|"
    r"under no circumstances|no matter what|must|only ever|at all times|100%|completely|"
    r"absolutely|any time|whatever happens)\b",
    re.I,
)
BROAD_RE = re.compile(
    r"\b(?:everything|anything|every (?:system|project|process|company|team|tool|case)|"
    r"any (?:system|project|process|tool|company|team|case)|all (?:systems|projects|processes|automation|"
    r"ai|tools|companies|teams|cases)|in general|across the board|no matter the)\b",
    re.I,
)
COMMIT_RE = re.compile(
    r"\bi(?:'d|'ll|’d|’ll| would| will| wouldn't| won't| wouldn’t| won’t| always| never| don't| do not|"
    r" don’t)?(?:\s+(?:always|never|not|also|just|still|personally|definitely|rather))?\s+"
    r"(keep|require|enable|disable|insist|cut|approve|reject|scale|sign|buy|ban|block|allow|let|turn|"
    r"use|demand|refuse|mandate|automate|stop|deploy|replace|put|ask|check|look|start|want|need|push|"
    r"leave|recommend|say|choose|pick|add|remove|drop|wait|go|trust|accept|fund|back|support)\b",
    re.I,
)
AGREE_RE = re.compile(
    r"\bi (?:fully |completely |totally |partly )?(?:agree|disagree|side) with\s+([\w.' -]{1,40})", re.I
)


def _tokens(text: str) -> list[str]:
    return [w.lower().strip("'’") for w in words(text)]


def _canon(w: str) -> str:
    w = CANON.get(w, w)
    if w not in CANON.values() and len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = CANON.get(w[:-1], w[:-1])
    return w


def concepts(text: str) -> set[str]:
    """Canonical content concepts of a text (synonym groups folded, light singularisation)."""
    return {_canon(w) for w in _tokens(text) if (w not in STOP and len(w) > 2) or w == "ai"}


def similarity(sentence: str, ref: str) -> float:
    a, b = concepts(sentence), concepts(ref)
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < 2:
        return 0.0
    return max(len(shared) / len(a), len(shared) / len(b))


def _polarity(tokens: list[str], concept: str) -> bool | None:
    for i, w in enumerate(tokens):
        if _canon(w) == concept:
            return not any(t in NEGATORS for t in tokens[max(0, i - 2) : i])
    return None


def _item_texts(item: dict) -> list[str]:
    scope = item.get("scope") or {}
    out = [str(item.get(k) or "") for k in ("text", "why", "instead", "original")]
    out += list(scope.get("meaning") or []) + list(item.get("allowed_paraphrases") or [])
    return [t for t in out if t.strip()]


def check(sentence: str, items: list[dict]) -> dict:
    """How a sentence relates to the referenced owner items (best match wins)."""
    if not items:
        return {"verdict": "unsupported", "item": None, "similarity": 0.0, "reasons": ["no owner material"]}
    scored = []
    for it in items:
        sims = [similarity(sentence, t) for t in _item_texts(it)]
        scored.append((max(sims or [0.0]), it))
    sim, item = max(scored, key=lambda x: x[0])
    scope = item.get("scope") or {}
    corpus = " ".join(_item_texts(item))
    reasons: dict[str, list[str]] = {}

    for f in item.get("forbidden_interpretations") or []:
        if similarity(sentence, f) >= 0.6:
            reasons.setdefault("stronger", []).append(f"close to a forbidden interpretation: {f!r}")

    s_clean = NOT_ABSOLUTE_RE.sub(" ", sentence)
    c_clean = NOT_ABSOLUTE_RE.sub(" ", corpus)
    abs_new = {m.lower() for m in ABSOLUTE_RE.findall(s_clean)} - {
        m.lower() for m in ABSOLUTE_RE.findall(c_clean)
    }
    if abs_new and scope.get("strength") != "absolute":
        reasons.setdefault("stronger", []).append(f"adds {', '.join(sorted(abs_new))}")

    allowed_actions = {a.lower() for a in scope.get("actions") or []}
    corpus_words = set(_tokens(corpus))
    for verb in (m.lower() for m in COMMIT_RE.findall(sentence)):
        if verb in allowed_actions or verb in corpus_words or any(verb in a.split() for a in allowed_actions):
            continue
        reasons.setdefault("narrower", []).append(f"first-person commitment to {verb!r}")

    if BROAD_RE.search(sentence) and not BROAD_RE.search(corpus):
        reasons.setdefault("broader", []).append(f"widens to {BROAD_RE.search(sentence).group(0)!r}")

    agreed = [a.lower() for a in scope.get("agrees_with") or []]
    for who in AGREE_RE.findall(sentence):
        if not any(a in who.lower() for a in agreed):
            reasons.setdefault("different", []).append(f"agreement with {who.strip()!r} is not recorded")

    st, ct = _tokens(sentence), _tokens(corpus)
    for c in (concepts(sentence) & concepts(corpus)) & PREDICATES:
        ps, pc = _polarity(st, c), _polarity(ct, c)
        if ps is not None and pc is not None and ps != pc:
            reasons.setdefault("different", []).append(f"flips {c!r}")
            break
    sw, cw = set(st), set(ct)
    for pos, neg in ANTONYMS:
        if (sw & pos and cw & neg) or (sw & neg and cw & pos):
            if not ((sw & pos and cw & pos) or (sw & neg and cw & neg)):
                reasons.setdefault("different", []).append("uses the opposite of the owner's word")

    if sim < 0.34 and not reasons:
        reasons["unsupported"] = [f"not close to any referenced owner item (similarity {sim:.2f})"]
    for v in ("stronger", "narrower", "broader", "different", "unsupported"):
        if v in reasons:
            every = [
                r
                for k in ("stronger", "narrower", "broader", "different", "unsupported")
                for r in reasons.get(k, [])
            ]
            return {"verdict": v, "item": item.get("id"), "similarity": round(sim, 2), "reasons": every}
    return {"verdict": "faithful", "item": item.get("id"), "similarity": round(sim, 2), "reasons": []}
