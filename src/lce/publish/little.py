"""LinkedIn `little` text format for Posts API `commentary`.

Reserved characters must be backslash-escaped to be rendered as plain text:
| { } @ [ ] ( ) < > # \\ * _ ~
We send plain text only (no mentions, no templates). A `#` that starts a
hashtag (`#word` at the start or after whitespace) is left as a HashtagElement;
every other reserved character is escaped.
Source: learn.microsoft.com/linkedin/marketing/community-management/shares/little-text-format
"""

from __future__ import annotations

import re

RESERVED = set("|{}@[]()<>#\\*_~")
HASHTAG_RE = re.compile(r"(?:(?<=^)|(?<=\s))#(?=[^\W_])", re.UNICODE)


def to_little(text: str) -> str:
    hashtag_positions = {m.start() for m in HASHTAG_RE.finditer(text)}
    out = []
    for i, ch in enumerate(text):
        if ch in RESERVED and i not in hashtag_positions:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def from_little(text: str) -> str:
    """Inverse of to_little for the plain-text subset we produce (used in tests/dry run)."""
    return re.sub(r"\\(.)", r"\1", text, flags=re.S)


def unescaped_reserved(text: str) -> list[int]:
    """Positions of reserved characters that are not escaped (excluding hashtags)."""
    bad, i = [], 0
    tags = {m.start() for m in HASHTAG_RE.finditer(text)}
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        if ch in RESERVED and i not in tags:
            bad.append(i)
        i += 1
    return bad
