"""Source-first media (LCE-046): look at the visuals of the post's own source first.

A post backed by a specific article or report should carry a visual that belongs
to that source whenever the law allows it; "a picture generally about the topic"
is not the same thing. So, before any image search:

1. `inspect` fetches each source page (from GitHub Actions; never a scraper of
   image search engines) and records every visual on it: Open Graph / Twitter
   images, `<img>` elements and `<figure>` captions, with alt text and size, and
   saves small copies for an INSPECTION ONLY (they are never attached from here).
2. It records the page's rights signals: copyright notices, "may not be
   reproduced / without permission" wording, Creative Commons licence links,
   public-domain statements.
3. A reviewer decides, and the decision travels with the media package as
   `source_check`: `source_visual_used` (only with an explicit reuse licence or
   permission), `source_visual_unavailable_or_restricted` (a visual exists but its
   reuse is not permitted, or the page could not be inspected), or
   `no_source_visual`. Only then: the original creator's licensed asset, another
   licensed asset directly tied to the same subject, or text-only.

Visuals published on a page are never assumed reusable because they are visible.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from lce import commons
from lce.store import StoreError, now_iso

STATUSES = ("source_visual_used", "source_visual_unavailable_or_restricted", "no_source_visual")
ASSOCIATIONS = ("source_visual", "original_source_asset", "same_subject_licensed")
RIGHTS_PATTERNS = [
    ("copyright_notice", re.compile(r"(©|\(c\)|copyright)\s*\d{0,4}[^.\n]{0,160}", re.I)),
    (
        "no_reproduction",
        re.compile(
            r"[^.\n]{0,120}(may not be (reproduced|distributed)|without (the )?(prior )?"
            r"(written )?permission|all rights reserved)[^.\n]{0,160}",
            re.I,
        ),
    ),
    ("cc_licence", re.compile(r"creativecommons\.org/(licenses|publicdomain)/[a-z0-9/.\-]+", re.I)),
    ("public_domain", re.compile(r"[^.\n]{0,80}(public domain|not subject to copyright)[^.\n]{0,80}", re.I)),
]
MAX_VISUALS = 25
MAX_INSPECT_BYTES = 3_000_000
UA = commons.UA


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.imgs, self.captions, self.links, self.text = [], [], [], [], []
        self._fig = 0
        self._cap = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "meta" and (a.get("property") or a.get("name") or "").lower() in (
            "og:image",
            "og:image:url",
            "twitter:image",
            "og:image:alt",
            "twitter:image:alt",
        ):
            self.meta.append(((a.get("property") or a.get("name")).lower(), a.get("content") or ""))
        if tag == "img":
            src = a.get("src") or a.get("data-src") or (a.get("srcset") or "").split(" ")[0]
            if src:
                self.imgs.append(
                    {
                        "src": src,
                        "alt": (a.get("alt") or "").strip()[:300],
                        "width": a.get("width"),
                        "height": a.get("height"),
                        "in_figure": self._fig > 0,
                    }
                )
        if tag == "figure":
            self._fig += 1
        if tag == "figcaption":
            self._cap = []
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "figure" and self._fig:
            self._fig -= 1
        if tag == "figcaption" and self._cap is not None:
            self.captions.append(" ".join(self._cap).strip()[:400])
            self._cap = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._cap is not None:
            self._cap.append(data.strip())
        self.text.append(data)


def _fetch(url: str, transport=None) -> tuple[int, bytes]:
    if transport:
        return 200, transport(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,image/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.status, r.read(MAX_INSPECT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def inspect(url: str, out_dir: Path, transport=None) -> dict:
    """Every visual on the source page and its rights signals; copies saved for inspection only."""
    rec: dict = {
        "source_url": url,
        "inspected_at": now_iso(),
        "visuals": [],
        "rights_signals": [],
        "note": "visual copies are saved for inspection only and are never attached from here",
    }
    try:
        status, body = _fetch(url, transport)
    except OSError as exc:
        rec.update(http_status=None, reachable=False, error=str(exc)[:300])
        return rec
    rec["http_status"] = status
    rec["reachable"] = status == 200 and bool(body)
    if not rec["reachable"]:
        return rec
    page = _Page()
    page.feed(body.decode("utf-8", "replace"))
    text = re.sub(r"\s+", " ", " ".join(page.text))
    rec["title"] = (
        re.search(r"<title[^>]*>(.*?)</title>", body.decode("utf-8", "replace"), re.S | re.I) or [None, None]
    )[1]
    rec["title"] = re.sub(r"\s+", " ", rec["title"]).strip()[:300] if rec["title"] else None
    seen = set()
    visuals = []
    for k, v in page.meta:
        if k.endswith(":alt") or not v:
            continue
        alt = next((c for kk, c in page.meta if kk == k + ":alt"), "")
        visuals.append({"kind": k, "src": urljoin(url, v), "alt": alt[:300]})
    for im in page.imgs:
        visuals.append(
            {
                "kind": "figure_img" if im["in_figure"] else "img",
                "src": urljoin(url, im["src"]),
                "alt": im["alt"],
                "width": im["width"],
                "height": im["height"],
            }
        )
    for v in visuals:
        if v["src"] in seen or v["src"].startswith("data:") or len(rec["visuals"]) >= MAX_VISUALS:
            continue
        seen.add(v["src"])
        if re.search(r"(logo|icon|sprite|pixel|badge|avatar)", v["src"], re.I) and v["kind"] == "img":
            v["skipped"] = "site chrome (logo/icon)"
            rec["visuals"].append(v)
            continue
        try:
            st, data = _fetch(v["src"], transport)
            if st == 200 and data and len(data) <= MAX_INSPECT_BYTES:
                ext = (
                    ".png"
                    if data[:8] == b"\x89PNG\r\n\x1a\n"
                    else ".jpg"
                    if data[:3] == b"\xff\xd8\xff"
                    else ".bin"
                )
                name = f"source-{len(rec['visuals']) + 1:02d}{ext}"
                (out_dir / name).write_bytes(data)
                v.update(inspection_copy=name, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            else:
                v["fetch"] = f"HTTP {st}"
        except OSError as exc:
            v["fetch"] = str(exc)[:200]
        rec["visuals"].append(v)
    rec["figure_captions"] = page.captions[:10]
    for kind, rx in RIGHTS_PATTERNS:
        for m in rx.finditer(text + " " + " ".join(page.links)):
            snippet = m.group(0).strip()[:300]
            if snippet and {"kind": kind, "text": snippet} not in rec["rights_signals"]:
                rec["rights_signals"].append({"kind": kind, "text": snippet})
            if len([r for r in rec["rights_signals"] if r["kind"] == kind]) >= 4:
                break
    kinds = {r["kind"] for r in rec["rights_signals"]}
    rec["reuse_permitted_by_page"] = "cc_licence" in kinds or "public_domain" in kinds
    rec["assessment"] = (
        "an explicit open licence or public-domain statement was found; verify it covers the visual"
        if rec["reuse_permitted_by_page"]
        else "no reuse licence on the page; visuals are copyrighted unless permission is documented"
    )
    return rec


def validate_check(check: dict, *, has_sources: bool) -> None:
    """The source-first decision a media package must carry (LCE-046)."""
    if not check:
        if has_sources:
            raise StoreError(
                "media needs source_check: inspect the post's source for its own visual first "
                "(lce image search with source_urls), then record the decision"
            )
        return
    if check.get("status") not in STATUSES:
        raise StoreError(f"source_check.status must be one of {', '.join(STATUSES)}")
    if not str(check.get("source_url", "")).startswith("http"):
        raise StoreError("source_check needs the source_url that was inspected")
    if len((check.get("evidence") or "").strip()) < 40:
        raise StoreError("source_check needs evidence: what the source page shows and what its rights say")


def validate_association(sel: dict, check: dict) -> None:
    assoc = sel.get("association")
    if assoc not in ASSOCIATIONS:
        raise StoreError(f"the selection needs association: one of {', '.join(ASSOCIATIONS)}")
    for k in ("why_belongs_to_source", "why_legal"):
        if len((sel.get(k) or "").strip()) < 40:
            raise StoreError(f"the selection needs {k} (at least 40 characters)")
    if assoc == "source_visual" and (check or {}).get("status") != "source_visual_used":
        raise StoreError("a source visual is used only when source_check.status is source_visual_used")
