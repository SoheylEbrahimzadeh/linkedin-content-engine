"""Generate a genuinely relevant visual from the post's own recorded evidence.

`lce image chart <post>` turns the post's recorded, sourced claims into a clean
square chart (PNG): percentages become bars on a 0–100 scale, other figures
become number cards. Every label is the claim's verbatim text, and the source
domain is printed on the image. Nothing is invented: only recorded claims with
a number are drawn, and the image decision (kind chart, origin own_creation,
generation method, relation, alt text) is recorded automatically so the image
goes through the normal image check and is bound to approval.

Needs the optional `visuals` extra (matplotlib).
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from urllib.parse import urlparse

from lce import images
from lce.store import DataStore, StoreError
from lce.textutil import NUMBER_RE, claim_numbers

DEFAULT_ACCENT = "#1f3a5f"
MAX_CLAIMS = 4
SIZE_PX, DPI = 1200, 200


def _figure(claim: dict) -> dict | None:
    """First supported number in the claim, with its unit as written (e.g. '58%')."""
    supported = set(claim_numbers(claim["text"]))
    for m in NUMBER_RE.finditer(claim["text"]):
        raw, unit = m.group(1), (m.group(2) or "")
        core = raw.replace(",", "")
        if core in supported or raw in supported:
            value = float(core) if unit in {"%", "percent"} else None
            shown = f"{raw}%" if unit in {"%", "percent"} else f"{raw} {unit}".strip()
            return {"text": claim["text"], "source": urlparse(claim["source_url"]).netloc,
                    "number": shown, "percent": value}
    return None


def _figures(claims: list[dict], pick: list[int] | None) -> list[dict]:
    chosen = [claims[i] for i in pick] if pick else claims
    return [f for f in (_figure(c) for c in chosen) if f][:MAX_CLAIMS]


def render(figures: list[dict], out: Path, accent: str = DEFAULT_ACCENT) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError as exc:
        raise StoreError("charts need the optional visuals extra: "
                         "pip install 'linkedin-content-engine[visuals]'") from exc
    inch = SIZE_PX / DPI
    fig = plt.figure(figsize=(inch, inch), dpi=DPI, facecolor="white")
    n = len(figures)
    left, width = 0.09, 0.82
    top = {1: 0.66, 2: 0.84}.get(n, 0.90)
    block = (top - 0.12) / n
    for i, f in enumerate(figures):
        y = top - i * block
        big = 40 if n == 1 else 22 if n == 2 else 15
        fig.text(left, y, f["number"], fontsize=big, fontweight="bold", color=accent, va="top")
        lines = textwrap.wrap(f["text"], 40 if n == 1 else 60)
        cap_size = 12 if n == 1 else 8 if n == 2 else 7
        num_h = big / 72 / inch * 1.25
        fig.text(left, y - num_h - 0.01, "\n".join(lines), fontsize=cap_size, color="#1c1c1c",
                 va="top", linespacing=1.35)
        if f["percent"] is not None:
            bar_y = y - num_h - 0.03 - len(lines) * cap_size / 72 / inch * 1.4
            height = 0.035 if n == 1 else 0.022
            fig.patches.append(FancyBboxPatch((left, bar_y - height), width, height,
                                              boxstyle="round,pad=0,rounding_size=0.008",
                                              transform=fig.transFigure, color="#e6e9ee"))
            fig.patches.append(FancyBboxPatch((left, bar_y - height),
                                              width * min(f["percent"], 100) / 100, height,
                                              boxstyle="round,pad=0,rounding_size=0.008",
                                              transform=fig.transFigure, color=accent))
    sources = sorted({f["source"] for f in figures})
    fig.text(left, 0.05, "Source: " + ", ".join(sources), fontsize=7, color="#666666")
    fig.savefig(out, format="png", dpi=DPI, metadata={"Software": None})
    plt.close(fig)


def chart(store: DataStore, post_id: str, *, claims: list[int] | None = None) -> dict:
    post = store.load_post(post_id)
    recorded = post.get("claims") or []
    if claims and any(i < 0 or i >= len(recorded) for i in claims):
        raise StoreError(f"claim index out of range (post has {len(recorded)} claims)")
    figures = _figures(recorded, claims)
    if not figures:
        raise StoreError("the post has no recorded claim with a number; a chart would have "
                         "nothing true to show (choose another image kind or none)")
    accent = (store.settings().get("visuals") or {}).get("accent", DEFAULT_ACCENT)
    import matplotlib

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "chart.png"
        render(figures, f, accent)
        sources = sorted({x["source"] for x in figures})
        numbers = ", ".join(x["number"] for x in figures)
        return images.decide(
            store, post_id, kind="chart",
            rationale="the post rests on the cited figure(s); the chart shows them with their "
                      "source",
            source_file=str(f),
            relation=f"visualizes the cited figure(s) {numbers} from {', '.join(sources)}",
            alt_text="Chart. " + " ".join(x["text"] for x in figures)
                     + f" Source: {', '.join(sources)}.",
            provenance={"origin": "own_creation", "usage": "owned",
                        "generation": {"method": f"lce image chart (matplotlib "
                                                 f"{matplotlib.__version__})"}},
            decided_by="agent")
