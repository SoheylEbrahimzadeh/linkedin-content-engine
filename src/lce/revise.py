"""Writing gate: the text that reaches the dashboard must already read like a person wrote it.

The writer (the Routine) drafts; the engine does not leave a weak draft for the owner to fix.
Every package goes through three steps before it can become a candidate:

1. `autofix`: safe, meaning-preserving mechanical fixes (contractions outside quotes);
2. `report`: the exact sentences to rewrite (too long, editorial or stiff wording, uncontracted
   forms, polished transitions, forced takeaways, symmetric constructions, invented or stronger
   personal positions), so the writer can revise in one pass;
3. `gate`: QA errors or any failing mechanical humanity criterion refuse the package. The writer
   revises and resubmits. Failures that honest writing cannot fix (the owner's voice, when the
   owner material is thin) need a stated limitation instead of an invented opinion.

Deterministic and local (no model, no network). The engine never writes new prose: it fixes
contractions and tells the writer what to rewrite.
"""

from __future__ import annotations

import re

# Safe contractions. "I have" is left alone ("I've a ..." reads wrong), and "it is" / "that is"
# only before a word (never "..., it is." at the end of a clause).
CONTRACTIONS = [
    (r"\bdo not\b", "don't"),
    (r"\bdoes not\b", "doesn't"),
    (r"\bdid not\b", "didn't"),
    (r"\bis not\b", "isn't"),
    (r"\bare not\b", "aren't"),
    (r"\bwas not\b", "wasn't"),
    (r"\bwere not\b", "weren't"),
    (r"\bcannot\b", "can't"),
    (r"\bwill not\b", "won't"),
    (r"\bwould not\b", "wouldn't"),
    (r"\bshould not\b", "shouldn't"),
    (r"\bcould not\b", "couldn't"),
    (r"\bhas not\b", "hasn't"),
    (r"\bhave not\b", "haven't"),
    (r"\bit is(?= [a-z])", "it's"),
    (r"\bthat is(?= [a-z])", "that's"),
    (r"\bthere is(?= [a-z])", "there's"),
    (r"\bwhat is(?= [a-z])", "what's"),
    (r"\bI am\b", "I'm"),
    (r"\bI would\b", "I'd"),
    (r"\bwe are\b", "we're"),
    (r"\bthey are\b", "they're"),
    (r"\byou are\b", "you're"),
    (r"\blet us\b", "let's"),
]
QUOTE_RE = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
CREDIT_RE = re.compile(r"^\s*(?:sources?|image|photo|credit|via)\s*:.*$|^\s*#\S", re.I)
MECHANICAL = (
    "real_person",
    "spoken",
    "variation",
    "no_stiffness",
    "no_symmetry",
    "no_manufactured_opinion",
    "no_over_explaining",
    "genuine_ending",
)
LIMITABLE = ("owner_voice", "substance")


def _keep_case(src: str, repl: str) -> str:
    return repl[0].upper() + repl[1:] if src[:1].isupper() else repl


def autofix(text: str) -> tuple[str, list[str]]:
    """Contractions outside quotes, credit lines and hashtags. Returns (text, changes)."""
    changes: list[str] = []
    out_lines = []
    for line in text.split("\n"):
        if CREDIT_RE.match(line):
            out_lines.append(line)
            continue
        parts, last = [], 0
        for m in QUOTE_RE.finditer(line):
            parts.append((line[last : m.start()], True))
            parts.append((m.group(0), False))
            last = m.end()
        parts.append((line[last:], True))
        fixed = []
        for chunk, editable in parts:
            if editable:
                for pat, repl in CONTRACTIONS:

                    def sub(m, repl=repl):
                        changes.append(f"{m.group(0)} → {_keep_case(m.group(0), repl)}")
                        return _keep_case(m.group(0), repl)

                    chunk = re.sub(pat, sub, chunk, flags=re.I if not pat.startswith(r"\bI ") else 0)
            fixed.append(chunk)
        out_lines.append("".join(fixed))
    return "\n".join(out_lines), changes


def report(text: str, *, findings: list, humanity: dict, rules: dict) -> list[str]:
    """Concrete revision items, sentence by sentence, for the writer."""
    from lce.humanity import _body, signals
    from lce.textutil import sentences, words

    items: list[str] = []
    for s in sentences(_body(text)):
        n = len(words(s))
        if n > 28:
            items.append(f"split this {n}-word sentence: {s!r}")
    seen = set()
    for f in findings:
        code, msg = (f.code, f.message) if hasattr(f, "code") else (f[0], f[2])
        if code.startswith(
            ("style.", "voice.", "pattern.", "pov.", "ending.", "phrase.", "claim.", "network.")
        ):
            if code == "style.long_sentence" or (code, msg) in seen:
                continue
            seen.add((code, msg))
            items.append(f"{code}: {msg}")
    sig = signals(text, rules)
    if sig["uncontracted"] and sig["uncontracted"] > sig["contractions"]:
        items.append(
            f"{sig['uncontracted']} uncontracted forms left (inside quotes or 'I have'): use contractions "
            "where you would say them"
        )
    for c in humanity.get("criteria", []):
        if c["result"] == "fail" and c["id"] in MECHANICAL:
            items.append(f"humanity {c['id']}: {c['why']}")
    return items


def gate(humanity: dict, errors: set[str], limitation: str | None = None) -> dict:
    """Pass only when there are no QA errors and every mechanical criterion holds. A failing
    owner_voice/substance criterion needs a stated limitation (never an invented opinion)."""
    by = {c["id"]: c for c in humanity.get("criteria", [])}
    mech = [k for k in MECHANICAL if by.get(k, {}).get("result") == "fail"]
    limitable = [k for k in LIMITABLE if by.get(k, {}).get("result") == "fail"]
    reasons = []
    if errors:
        reasons.append("QA errors: " + ", ".join(sorted(errors)))
    if mech:
        reasons.append("writing: " + ", ".join(mech))
    if limitable and not (limitation or "").strip():
        reasons.append("needs a rewrite or a stated limitation: " + ", ".join(limitable))
    return {
        "pass": not reasons,
        "reasons": reasons,
        "score": humanity.get("score"),
        "of": humanity.get("of"),
        "verdict": humanity.get("verdict"),
        "limitation": (limitation or "").strip() or None,
    }
