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
        doc = images.decide(
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
    from lce import relevance
    from lce.posts import current_text

    spec = {"visual_type": "chart", "concept": f"the cited figure(s) {numbers} at a glance",
            "relevance_reason": "the post rests on these sourced figures; the chart shows their size "
                                "and source, which the text alone does not",
            "nodes": [{"label": x["text"]} for x in figures], "source_line": "Source: " + ", ".join(sources)}
    doc["spec"] = spec
    doc["media_relevance"] = relevance.evaluate(spec, store.load_post(post_id), current_text(store, post_id),
                                                alt_text=doc["alt_text"], method="lce image chart")
    store.write_doc(images.path(store, post_id), "image", doc)
    return doc


# ── LCE-041: conceptual diagrams (the idea, not the post's text) ─────────
RENDERABLE = {"flow", "process", "decision_tree", "framework", "relationship_map"}
OUTCOME_COLORS = ("#2f6f4e", "#9a6a12", "#8a2d2d", "#4b5563")


def _wrap(s: str, n: int) -> str:
    return "\n".join(textwrap.wrap(s, n, break_on_hyphens=False)) or s


def render_concept(spec: dict, out: Path, accent: str = DEFAULT_ACCENT) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch
    except ImportError as exc:
        raise StoreError("diagrams need the optional visuals extra: "
                         "pip install 'linkedin-content-engine[visuals]'") from exc
    vtype = spec["visual_type"]
    if vtype not in RENDERABLE:
        raise StoreError(f"no renderer for visual_type {vtype!r} "
                         f"(use one of {', '.join(sorted(RENDERABLE))})")
    inch = SIZE_PX / DPI
    fig = plt.figure(figsize=(inch, inch), dpi=DPI, facecolor="white")
    ink, muted, soft = "#18181b", "#52525b", "#f1f3f6"
    left, right = 0.08, 0.92
    title = _wrap(spec["title"], 38)
    fig.text(left, 0.93, title, fontsize=15, fontweight="bold", color=ink, va="top", linespacing=1.2)
    top = 0.93 - 0.055 * (title.count("\n") + 1) - 0.03
    nodes = spec["nodes"]
    outcomes = spec.get("outcomes") or []
    footer = " ".join(x for x in [spec.get("footer", ""), spec.get("source_line", "")] if x).strip()
    bottom = 0.11 if footer else 0.06

    def box(x, y, w, h, face, edge="none", lw=0):
        fig.patches.append(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.018",
                                          transform=fig.transFigure, facecolor=face, edgecolor=edge,
                                          linewidth=lw))

    def arrow(x0, y0, x1, y1, color=muted):
        fig.patches.append(FancyArrowPatch((x0, y0), (x1, y1), transform=fig.transFigure,
                                           arrowstyle="-|>", mutation_scale=11, color=color, lw=1.4))

    if vtype in {"flow", "process", "decision_tree"}:
        out_h = 0.15 if outcomes else 0
        avail = top - bottom - out_h
        n = len(nodes)
        gap = 0.028
        h = min(0.115, (avail - gap * (n - 1)) / n)
        y = top
        for i, nd in enumerate(nodes):
            box(left, y - h, right - left, h, soft)
            fig.patches.append(Circle((left + 0.05, y - h / 2), 0.026, transform=fig.transFigure,
                                      facecolor=accent, edgecolor="none"))
            fig.text(left + 0.05, y - h / 2, str(i + 1), fontsize=10, fontweight="bold", color="white",
                     ha="center", va="center")
            has_note = bool(nd.get("note"))
            fig.text(left + 0.105, y - h / 2 + (0.016 if has_note else 0), nd["label"], fontsize=11.5,
                     fontweight="bold", color=ink, va="center")
            if has_note:
                fig.text(left + 0.105, y - h / 2 - 0.022, nd["note"], fontsize=8, color=muted, va="center")
            if i < n - 1:
                arrow(left + 0.05, y - h - 0.002, left + 0.05, y - h - gap + 0.002)
            y -= h + gap
        if outcomes:
            k = len(outcomes)
            oy = y - 0.03
            span = right - left
            w = (span - 0.03 * (k - 1)) / k
            hub = (left + span / 2, oy + 0.012)
            fig.text(hub[0], oy + 0.03, spec.get("decision_label", "Decision"), fontsize=8.5, color=muted,
                     ha="center", va="center", fontweight="bold")
            for j, o in enumerate(outcomes):
                x = left + j * (w + 0.03)
                arrow(hub[0], hub[1], x + w / 2, oy - 0.035, OUTCOME_COLORS[j % len(OUTCOME_COLORS)])
                box(x, oy - 0.095, w, 0.06, "white", OUTCOME_COLORS[j % len(OUTCOME_COLORS)], 1.6)
                fig.text(x + w / 2, oy - 0.065, _wrap(o, 16), fontsize=9.5, fontweight="bold",
                         color=OUTCOME_COLORS[j % len(OUTCOME_COLORS)], ha="center", va="center")
    else:  # framework / relationship_map: a centre with its elements around it
        import math

        cx, cy = 0.5, (top + bottom) / 2
        r = min(0.30, (top - bottom) / 2 - 0.07)
        fig.patches.append(Circle((cx, cy), 0.11, transform=fig.transFigure, facecolor=accent,
                                  edgecolor="none"))
        fig.text(cx, cy, _wrap(spec.get("center") or spec["title"], 12), fontsize=10, fontweight="bold",
                 color="white", ha="center", va="center")
        n = len(nodes)
        bw, bh = 0.27, 0.12
        for i, nd in enumerate(nodes):
            a = math.pi / 2 - 2 * math.pi * i / n
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            fig.lines.append(plt.Line2D([cx, x], [cy, y], transform=fig.transFigure, color="#c4c8cf",
                                        lw=1.4, zorder=0.5))
            box(x - bw / 2, y - bh / 2, bw, bh, soft)
            if nd.get("note"):
                fig.text(x, y + 0.004, _wrap(nd["label"], 18), fontsize=9.5, fontweight="bold", color=ink,
                         ha="center", va="bottom", linespacing=1.1)
                fig.text(x, y - 0.006, _wrap(nd["note"], 28), fontsize=6.5, color=muted, ha="center",
                         va="top", linespacing=1.2)
            else:
                fig.text(x, y, _wrap(nd["label"], 18), fontsize=10, fontweight="bold", color=ink,
                         ha="center", va="center")
    if footer:
        fig.text(left, 0.045, _wrap(footer, 95), fontsize=6.5, color=muted, va="bottom")
    fig.savefig(out, format="png", dpi=DPI, metadata={"Software": None})
    plt.close(fig)


def _publishers(post: dict, urls: set[str] | None = None) -> list[str]:
    return sorted({s.get("publisher") or urlparse(s["url"]).netloc for s in post.get("sources") or []
                   if urls is None or s["url"] in urls})


def concept(store: DataStore, post_id: str, spec: dict) -> dict:
    """Render a conceptual diagram from `spec` and attach it ONLY if the media
    relevance check accepts it (no text dump, facts sourced, alt text describes
    the visual). Spec keys: visual_type, concept, relevance_reason, title,
    nodes [{label, note?}], outcomes?, center?, decision_label?, footer_claim?
    (index of a recorded claim drawn verbatim as the footer), alt_text."""
    from lce import relevance
    from lce.posts import current_text

    post = store.load_post(post_id)
    text = current_text(store, post_id)
    spec = {k: v for k, v in spec.items() if v not in (None, "", [])}
    spec["nodes"] = [n if isinstance(n, dict) else {"label": str(n)} for n in spec.get("nodes") or []]
    claims = post.get("claims") or []
    if "footer_claim" in spec:
        i = int(spec.pop("footer_claim"))
        if not 0 <= i < len(claims):
            raise StoreError(f"footer_claim out of range (post has {len(claims)} claims)")
        spec["footer"] = claims[i]["text"].rstrip(".") + "."
        spec["source_line"] = "Source: " + ", ".join(_publishers(post, {claims[i]["source_url"]})
                                                      or [urlparse(claims[i]["source_url"]).netloc]) + "."
    alt = (spec.get("alt_text") or "").strip()
    import matplotlib

    method = f"lce image diagram (matplotlib {matplotlib.__version__}); conceptual {spec.get('visual_type')}"
    rec = relevance.evaluate(spec, post, text, alt_text=alt, method=method)
    if rec["media_decision"] != "accepted":
        raise StoreError("media relevance rejected; nothing attached:\n  - " + "\n  - ".join(rec["problems"]))
    accent = (store.settings().get("visuals") or {}).get("accent", DEFAULT_ACCENT)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "diagram.png"
        render_concept(spec, f, accent)
        doc = images.decide(
            store, post_id, kind="diagram",
            rationale=rec["relevance_reason"],
            source_file=str(f),
            relation=f"{spec['visual_type']} visual of the post's idea: {rec['concept']}",
            alt_text=alt,
            provenance={"origin": "own_creation", "usage": "owned", "generation": {"method": method}},
            decided_by="agent")
    doc["spec"] = {k: spec[k] for k in ("visual_type", "concept", "relevance_reason", "title", "nodes",
                                        "outcomes", "center", "decision_label", "footer", "source_line")
                   if spec.get(k)}
    doc["media_relevance"] = rec
    store.write_doc(images.path(store, post_id), "image", doc)
    return doc


def diagram(store: DataStore, post_id: str, *, title: str, items: list[str], footer: str = "") -> dict:
    """Pre-LCE-041 interface (title + items). It now goes through the same relevance
    gate as every visual: copying the post's sentences or questions into the image
    is rejected. Use `lce image diagram --spec` for a conceptual visual."""
    return concept(store, post_id, {"visual_type": "process", "title": title,
                                    "nodes": [{"label": i} for i in items],
                                    **({"footer": footer} if footer else {})})
