"""Text normalization, hashing and lightweight NLP helpers (deterministic, no LLM)."""

from __future__ import annotations

import hashlib
import re
import unicodedata

WORD_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
HASHTAG_RE = re.compile(r"(?<![\w#])#[^\W_][\w-]*", re.UNICODE)
URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w.])\+\d{1,3}[\s-]?\(?\d{1,4}\)?(?:[\s-]?\d{2,4}){2,4}(?![\w.])")
EMOJI_RE = re.compile(
    "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F\U0001F900-\U0001F9FF\U0001FA70-\U0001FAFF☀-⛿✀-➿]"
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"”’)])\s+|\n+")
# A number that could carry a factual claim: currency, percentages, multipliers,
# decimals, thousands separators. Group 1 is the numeric core.
NUMBER_RE = re.compile(
    r"(?<![\w.])[$€£]?\s?(\d{1,3}(?:[,.]\d{3})+|\d+(?:[.,]\d+)?)"
    r"\s?(%|percent\b|x\b|×|k\b|m\b|bn\b|million\b|billion\b|hours?\b|days?\b|weeks?\b|"
    r"minutes?\b|months?\b|years?\b)?",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    """Canonical form used for hashing: NFC, LF line endings, no trailing spaces."""
    t = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in t.split("\n")]
    return "\n".join(lines).strip() + "\n"


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def canonical(text: str) -> str:
    """Punctuation-, case-, hashtag- and emoji-insensitive form for exact-duplicate checks."""
    t = HASHTAG_RE.sub(" ", URL_RE.sub(" ", text))
    return " ".join(words(t))


def shingles(tokens: list[str], k: int = 3) -> set[tuple[str, ...]]:
    if len(tokens) < k:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set, b: set) -> float:
    """Share of the smaller set contained in the larger one."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s and s.strip()]


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def count_emojis(text: str) -> int:
    return len(EMOJI_RE.findall(text))


def hashtags(text: str) -> list[str]:
    return HASHTAG_RE.findall(text)


def slugify(text: str, max_len: int = 40) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "post"


def _core(num: str) -> str:
    n = num.replace(",", "").replace(" ", "")
    if n.count(".") > 1:  # European thousands separators, e.g. 1.200.000
        n = n.replace(".", "")
    return n.rstrip("0").rstrip(".") if "." in n else n


def claim_numbers(text: str) -> list[str]:
    """Numbers that would need support if stated as fact.

    Ignored: plain integers up to 10 without a unit (e.g. "3 lessons"), years
    1900-2100, and list markers at the start of a line ("1.", "2)").
    """
    found = []
    for m in NUMBER_RE.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "")
        start = m.start(1)
        line_start = text.rfind("\n", 0, start) + 1
        prefix = text[line_start:start].strip()
        after = text[m.end(1) : m.end(1) + 1]
        if not prefix and after in {".", ")"} and not unit:
            continue
        core = _core(raw)
        is_plain_int = core.isdigit() and not unit and not text[m.start() : start].strip()
        if is_plain_int and int(core) <= 10:
            continue
        if is_plain_int and len(core) == 4 and 1900 <= int(core) <= 2100:
            continue
        found.append(core)
    return found
