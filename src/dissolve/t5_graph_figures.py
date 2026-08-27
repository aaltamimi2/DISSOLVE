"""G-3 analysis figures. Not paper-figure ingest. Distinct palettes. Black 10 pt text."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from . import t5_graph_extract, t5_graph_lookup, text_chunk_metrics
from .t5_corpus_graph import GRAPH_PATH
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = t5_graph_extract.SPEC_SHA256
DEGREE_NAME = "FIGURES.graph-rag.degree.v1.png"
CONNECT_NAME = "FIGURES.graph-rag.connectivity.v1.png"
HEAD_NAME = "FIGURES.graph-rag.headtohead.v1.png"
DEGREE_PATH = DEFAULT_OUT_DIR / DEGREE_NAME
CONNECT_PATH = DEFAULT_OUT_DIR / CONNECT_NAME
HEAD_PATH = DEFAULT_OUT_DIR / HEAD_NAME
_FORBIDDEN = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_PROTECTED_PNG = frozenset({
    "CURVES.retrieval.d3.png",
    "CURVES.retrieval.v3.png",
    "CURVES.retrieval.v4.png",
})


def _refuse_gold_v2() -> None:
    t5_graph_lookup._refuse_gold_v2()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _figure_dir(dest: Path) -> Path:
    if dest == t5_graph_extract.OVERLAY_PATH.resolve():
        return DEFAULT_OUT_DIR
    return dest.parent


def _style(ax) -> None:
    ax.tick_params(labelsize=10, colors="black")
    ax.xaxis.label.set_size(10)
    ax.yaxis.label.set_size(10)
    ax.xaxis.label.set_color("black")
    ax.yaxis.label.set_color("black")
    ax.title.set_size(10)
    ax.title.set_color("black")
    for spine in ax.spines.values():
        spine.set_color("black")


def render_t5_graph_rag_figures(
    *,
    dest: str | Path,
    curves_path: str | Path | None = None,
) -> dict[str, Any]:
    """Write three analysis PNGs. Does not write d3/v3/v4 figures."""
    _refuse_gold_v2()
    dest_path = Path(dest).expanduser().resolve()
    t5_graph_lookup._refuse_url(str(dest_path))
    if dest_path == GRAPH_PATH.resolve():
        raise TextGoldError("persist_graph_refused", "G-3 figures do not take persist GRAPH as dest.")
    graph = _load_json(dest_path)
    out_dir = _figure_dir(dest_path)
    for name in _PROTECTED_PNG:
        if (out_dir / name).resolve() == dest_path:
            raise TextGoldError("persist_graph_refused", "G-3 figures do not overwrite d3/v3/v4.")
    curves_file = (
        Path(curves_path).expanduser().resolve()
        if curves_path is not None
        else dest_path.with_name("CURVES.graph-rag.v1.json")
    )
    if dest_path == t5_graph_extract.OVERLAY_PATH.resolve() and curves_path is None:
        curves_file = DEFAULT_OUT_DIR / "CURVES.graph-rag.v1.json"
    curves = _load_json(curves_file) if curves_file.is_file() else {}

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    degrees: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for node in graph.get("nodes") or []:
        if str(node.get("node_type") or "") != "entity":
            continue
        node_id = str(node.get("node_id") or "")
        payload = node.get("payload") if isinstance(node.get("payload"), Mapping) else {}
        labels[node_id] = str(payload.get("canonical_key") or node.get("canonical_key") or "")
        degrees[node_id] = 0
    for edge in graph.get("edges") or []:
        if str(edge.get("edge_type") or "") != "mentions":
            continue
        target = str(edge.get("target_node_id") or "")
        if target in degrees:
            degrees[target] += 1

    fig1, ax1 = plt.subplots(figsize=(8.5, 5.2))
    values = sorted(degrees.values())
    bins = range(0, (max(values) + 2) if values else 2)
    ax1.hist(values, bins=list(bins), color="#4C78A8", edgecolor="black", log=True)
    ax1.set_xlabel("entity degree (mentions)")
    ax1.set_ylabel("count (log)")
    ax1.set_title("Entity degree distribution")
    top = degrees.most_common(6)
    note = ", ".join(f"{labels.get(key, key)}={count}" for key, count in top)
    if note:
        ax1.text(
            0.02, 0.95, note, transform=ax1.transAxes, fontsize=10, color="black",
            va="top", ha="left", wrap=True,
        )
    _style(ax1)
    fig1.tight_layout()
    degree_path = out_dir / DEGREE_NAME
    fig1.savefig(degree_path, dpi=120)
    plt.close(fig1)

    weights: dict[tuple[str, str], int] = defaultdict(int)
    papers: set[str] = set()
    for node in graph.get("nodes") or []:
        if str(node.get("node_type") or "") == "paper":
            papers.add(str(node.get("canonical_key") or ""))
    for edge in graph.get("edges") or []:
        if str(edge.get("edge_type") or "") != "same_entity":
            continue
        left = str(edge.get("source_node_id") or "").removeprefix("paper:")
        right = str(edge.get("target_node_id") or "").removeprefix("paper:")
        if not left or not right:
            continue
        pair = tuple(sorted((left, right)))
        weights[pair] += 1
        papers.add(left)
        papers.add(right)
    ordered = sorted(papers)
    index = {paper: i for i, paper in enumerate(ordered)}
    fig2, ax2 = plt.subplots(figsize=(7.2, 7.2))
    n = max(len(ordered), 1)
    coords = []
    for i in range(len(ordered)):
        angle = 2 * math.pi * i / n - math.pi / 2
        coords.append((math.cos(angle), math.sin(angle)))
    for (left, right), weight in weights.items():
        x0, y0 = coords[index[left]]
        x1, y1 = coords[index[right]]
        ax2.plot(
            [x0, x1], [y0, y1], color="#B279A2", linewidth=max(0.6, min(4.0, 0.15 * weight)),
            alpha=0.45, zorder=1,
        )
    xs = [item[0] for item in coords]
    ys = [item[1] for item in coords]
    ax2.scatter(xs, ys, s=40, color="#B279A2", edgecolors="black", zorder=2)
    for i, _paper in enumerate(ordered):
        ax2.text(
            coords[i][0] * 1.12, coords[i][1] * 1.12, f"p{i + 1:02d}",
            fontsize=10, color="black", ha="center", va="center",
        )
    ax2.set_title("Cross-paper shared-entity connectivity")
    ax2.set_aspect("equal")
    ax2.axis("off")
    _style(ax2)
    fig2.tight_layout()
    connect_path = out_dir / CONNECT_NAME
    fig2.savefig(connect_path, dpi=120)
    plt.close(fig2)

    ks = list(text_chunk_metrics.RETRIEVAL_KS)
    labels_k = [str(k) for k in ks]
    series = {str(row.get("arm") or ""): row for row in (curves.get("series") or [])}
    fig3, axes = plt.subplots(1, 2, figsize=(11.4, 4.8), sharex=True)
    colors = {
        "hybrid": "#54A24B",
        "graph_matched": "#E45756",
        "hybrid_at_union_mean": "#F58518",
        "graph_unbounded": "#E8C547",
    }
    styles = {
        "hybrid": "solid",
        "graph_matched": "solid",
        "hybrid_at_union_mean": "solid",
        "graph_unbounded": "dashed",
    }
    display = {
        "hybrid": "hybrid (k)",
        "graph_matched": "graph matched-k",
        "hybrid_at_union_mean": "hybrid at union-mean",
        "graph_unbounded": "graph unbounded-budget",
    }
    contest = str(curves.get("pair_contest") or "")
    if contest == "NO_PAIRS" or not series:
        axes[0].text(
            0.5, 0.5, "NO_PAIRS", fontsize=10, color="black",
            ha="center", va="center", transform=axes[0].transAxes,
        )
    else:
        for arm, color in colors.items():
            row = series.get(arm) or {}
            if not row:
                continue
            ys = []
            lo = []
            hi = []
            for label in labels_k:
                cell = (row.get("recall_at_k") or {}).get(label)
                band = (row.get("recall_ci_at_k") or {}).get(label) or {}
                ys.append(float(cell) if cell is not None else 0.0)
                lo.append(float(band["lo"]) if band.get("lo") is not None else ys[-1])
                hi.append(float(band["hi"]) if band.get("hi") is not None else ys[-1])
            axes[0].plot(
                ks, ys, marker="o", color=color, linestyle=styles[arm],
                label=display[arm],
            )
            axes[0].fill_between(ks, lo, hi, color=color, alpha=0.12)
        axes[0].legend(fontsize=10)
    axes[0].set_ylabel("pair recall@k")
    axes[0].set_title("Head-to-head recall (matched vs unbounded)")
    for arm, color in colors.items():
        row = series.get(arm) or {}
        if not row:
            continue
        refuse = [(row.get("must_refuse_at_k") or {}).get(label) for label in labels_k]
        ys = [float(item) if item is not None else 0.0 for item in refuse]
        axes[1].plot(
            ks, ys, marker="o", color=color, linestyle=styles[arm],
            label=display[arm],
        )
    axes[1].set_ylabel("must-refuse@k")
    axes[1].set_title("Must-refuse")
    if series:
        axes[1].legend(fontsize=10)
    for ax in axes:
        ax.set_xlabel("k")
        ax.set_xticks(ks)
        ax.set_ylim(-0.05, 1.05)
        ax.grid(True, alpha=0.25)
        _style(ax)
    fig3.tight_layout()
    head_path = out_dir / HEAD_NAME
    fig3.savefig(head_path, dpi=120)
    plt.close(fig3)

    header = {
        "degree_path": str(degree_path),
        "connectivity_path": str(connect_path),
        "headtohead_path": str(head_path),
        "degree_bytes": degree_path.stat().st_size,
        "connectivity_bytes": connect_path.stat().st_size,
        "headtohead_bytes": head_path.stat().st_size,
        "spec_sha256": SPEC_SHA256,
    }
    if text_chunk_metrics._contains_forbidden_keys(header, set(_FORBIDDEN)):
        raise TextGoldError("gold_quoted", "Figure return must not carry gold identifiers.")
    return header
