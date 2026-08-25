"""G-3 analysis figures. No d3/v3/v4 overwrite. No gold needles."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import t5_corpus_graph, t5_graph_eval, t5_graph_extract, t5_graph_figures
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import DEFAULT_OUT_DIR

PAPER_A = "ab" * 32
PAPER_B = "cd" * 32
PROMPT = "G3PAIRPROMPTZXQ"
HELD = "ee" * 32
REFUSE_Q = "G3REFUSEZXQ"
REFUSE_NEEDLE = "NOMATCHZXQTOKEN"


def _needles(token: str) -> dict:
    return {"subject": token, "qualifier": "token"}
D3_PNG = DEFAULT_OUT_DIR / "CURVES.retrieval.d3.png"
V3_PNG = DEFAULT_OUT_DIR / "CURVES.retrieval.v3.png"
V4_PNG = DEFAULT_OUT_DIR / "CURVES.retrieval.v4.png"


def _chunk(chunk_id: str, paper: str, body: str, ordinal: int) -> dict:
    return {
        "chunk_id": chunk_id,
        "paper_sha256": paper,
        "ordinal": ordinal,
        "body": body,
        "char_start": 0,
        "char_end": len(body),
        "page": 1,
        "section": "Methods",
        "section_origin": "parser_supplied",
        "kind": "paragraph",
        "block_ids": [f"b-{ordinal}"],
    }


def test_render_three_pngs_and_does_not_touch_d3(tmp_path):
    store = {
        "schema": "dissolve.t5-chunk-store.unsealed.v1",
        "n_chunks": 2,
        "indexed_paper_sha256": [PAPER_A, PAPER_B],
        "chunks": [
            _chunk("T5-g3-a", PAPER_A, "PET dissolves in toluene during the screen.", 1),
            _chunk("T5-g3-b", PAPER_B, "PET and toluene appear together again.", 1),
        ],
    }
    store_path = tmp_path / "store.json"
    store_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    graph_path = tmp_path / "graph.json"
    t5_corpus_graph.emit_t5_corpus_graph(
        dest=graph_path,
        store_path=store_path,
        manifest_path=t5_corpus_graph.MANIFEST_PATH,
    )
    dest = tmp_path / "overlay.json"
    t5_graph_extract.extract_t5_entity_graph(
        dest=dest, graph_path=graph_path, store_path=store_path,
    )
    gold = {
        "schema": "dissolve.text-gold.v1.unsealed",
        "facts": [
            {
                "paper_sha256": PAPER_A,
                "char_start": 0,
                "char_end": 5,
                "paper_status": "indexed",
                "query": PROMPT,
                "needles": _needles("absent-a"),
            },
            {
                "paper_sha256": PAPER_B,
                "char_start": 0,
                "char_end": 5,
                "paper_status": "indexed",
                "query": PROMPT,
                "needles": _needles("absent-b"),
            },
            {
                "paper_sha256": HELD,
                "char_start": 0,
                "char_end": 5,
                "paper_status": "held_out",
                "query": REFUSE_Q,
                "needles": _needles(REFUSE_NEEDLE),
            },
        ],
        "papers": [
            {"paper_sha256": PAPER_A, "paper_status": "indexed"},
            {"paper_sha256": PAPER_B, "paper_status": "indexed"},
            {"paper_sha256": HELD, "paper_status": "held_out"},
        ],
    }
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold, indent=2) + "\n", encoding="utf-8")
    gz_path = tmp_path / "mini.json.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as handle:
        json.dump({
            "chunks": store["chunks"],
            "dense": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "dim": 384,
                "chunk_ids": [row["chunk_id"] for row in store["chunks"]],
            },
        }, handle)

    def ranker(index, query):
        if query == PROMPT:
            return list(index["chunks"])[:1]
        return []

    scored = t5_graph_eval.score_t5_graph_rag(
        dest=dest,
        gold_path=gold_path,
        index_path=gz_path,
        store_path=store_path,
        ranker=ranker,
    )
    d3_before = file_sha256(D3_PNG) if D3_PNG.is_file() else None
    v3_before = file_sha256(V3_PNG) if V3_PNG.is_file() else None
    v4_before = file_sha256(V4_PNG) if V4_PNG.is_file() else None
    header = t5_graph_figures.render_t5_graph_rag_figures(
        dest=dest, curves_path=scored["curves_path"],
    )
    for key in ("degree_path", "connectivity_path", "headtohead_path"):
        path = Path(header[key])
        assert path.is_file()
        assert path.stat().st_size > 0
        assert path.parent == dest.parent
        assert path.name.startswith("FIGURES.graph-rag.")
    if d3_before is not None:
        assert file_sha256(D3_PNG) == d3_before
    if v3_before is not None:
        assert file_sha256(V3_PNG) == v3_before
    if v4_before is not None:
        assert file_sha256(V4_PNG) == v4_before
    assert file_sha256(t5_corpus_graph.GRAPH_PATH) == t5_graph_extract.GRAPH_SHA256
