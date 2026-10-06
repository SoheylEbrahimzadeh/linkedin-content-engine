"""LCE-052 (owner rule 2026-10-06): no visible image attribution in the post text.

A post's image appears on its own. The pipeline never writes a source label for it into the
text (`Image: CIO.com`, `Photo: ...`, `Credit: ...`, `Image source: ...`). Provenance stays where
it belongs, in the image record (`image.yaml`: source URL, creator, licence, attribution,
retrieval), for auditing, copyright tracking and compliance; only the visible line goes.

`Source:` and `Via` lines are ambiguous (a text citation is allowed); they count as an image
label only when they name the image's own credit, publisher or attribution.
"""

from __future__ import annotations

import re

# A whole line that labels an image: "Image: X", "Photo credit: X", "Image source: X", "Credit: X" ...
IMAGE_LABEL_RE = re.compile(
    r"^\s*(?:(?:header|cover|featured|hero|lead)\s+)?"
    r"(?:images?|photos?|pictures?|illustrations?|graphics?|visuals?|credits?)"
    r"(?:\s+(?:source|credit|courtesy|by)s?)?\s*:\s*\S.*$",
    re.I,
)
SOURCE_LINE_RE = re.compile(r"^\s*(?:sources?\s*:|via\b:?)\s*(?P<who>\S.*?)\s*$", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower().removeprefix("image:").removeprefix("photo:"))


def image_credit_names(provenance: dict | None) -> set[str]:
    """What the image record calls its source (normalized), for `Source:` / `Via` lines."""
    prov = provenance or {}
    names = {prov.get(k) or "" for k in ("credit", "creator", "attribution", "source_name")}
    return {n for n in (_norm(x) for x in names) if len(n) >= 3}


def is_image_label(line: str, names: set[str] = frozenset()) -> bool:
    if IMAGE_LABEL_RE.match(line):
        return True
    m = SOURCE_LINE_RE.match(line)
    return bool(m and names and _norm(m.group("who")) in names)


def find_image_labels(text: str, names: set[str] = frozenset()) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if is_image_label(ln, names)]


def strip_image_labels(text: str, names: set[str] = frozenset()) -> tuple[str, list[str]]:
    """The text without visible image-attribution lines (blank-line structure kept tidy)."""
    removed = find_image_labels(text, names)
    if not removed:
        return text, []
    kept = [ln for ln in text.split("\n") if not is_image_label(ln, names)]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    return out + ("\n" if text.endswith("\n") else ""), removed
