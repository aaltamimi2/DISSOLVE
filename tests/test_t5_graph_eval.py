"""G-3 derived pairs and stub-ranked score. No MiniLM. No gold needles in source."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import t5_corpus_graph, t5_graph_eval, t5_graph_extract
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import TextGoldError

GRAPH = t5_corpus_graph.GRAPH_PATH
GOLD = t5_corpus_graph.GOLD_PATH
PAPER_A = "ab" * 32
PAPER_B = "cd" * 32
PROMPT = "G3PAIRPROMPTZXQ"
HELD = "ee" * 32
REFUSE_Q = "G3REFUSEZXQ"
REFUSE_NEEDLE = "NOMATCHZXQTOKEN"


def _needles(token: str) -> dict:
    return {"subject": token, "qualifier": "token"}


def _assert_pins_unmoved() -> None:
    from dissolve.text_gold import DEFAULT_OUT_DIR
    from dissolve.t5_corpus_graph import (
        CENSUS_PATH,
        GOLD_PATH,
        GRAPH_PATH,
        MANIFEST_PATH,
        STORE_PATH,
    )
    gzip_path = Path(json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["index_path"])
    assert file_sha256(GRAPH_PATH) == t5_graph_extract.GRAPH_SHA256
    assert file_sha256(STORE_PATH) == t5_corpus_graph.STORE_SHA256
    assert file_sha256(GOLD_PATH) == t5_corpus_graph.GOLD_SHA256
    assert file_sha256(CENSUS_PATH) == t5_corpus_graph.CENSUS_SHA256
    assert file_sha256(gzip_path) == t5_corpus_graph.GZIP_SHA256
    assert not (DEFAULT_OUT_DIR / "GOLD.v2.json").is_file()


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


def _write_mini(tmp_path: Path) -> tuple[Path, Path]:
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
    return store_path, graph_path


def _gold() -> dict:
    return {
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


def _gzip(tmp_path: Path, store_path: Path) -> Path:
    store = json.loads(store_path.read_text(encoding="utf-8"))
    payload = {
        "chunks": store["chunks"],
        "dense": {
            "model": "sentence-transformers/all-MiniLM-L6-v2",
            "dim": 384,
            "chunk_ids": [row["chunk_id"] for row in store["chunks"]],
        },
    }
    path = tmp_path / "mini.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def test_walk_depth_is_frozen_at_one():
    assert t5_graph_eval.ENTITY_HOP_DEPTH == 1


def test_derive_pairs_counts_and_no_quote_keys(tmp_path):
    store_path, graph_path = _write_mini(tmp_path)
    dest = tmp_path / "overlay.json"
    t5_graph_extract.extract_t5_entity_graph(
        dest=dest, graph_path=graph_path, store_path=store_path,
    )
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(_gold(), indent=2) + "\n", encoding="utf-8")
    first = t5_graph_eval.derive_t5_multihop_pairs(
        dest=dest, gold_path=gold_path, store_path=store_path,
    )
    second = t5_graph_eval.derive_t5_multihop_pairs(
        dest=dest, gold_path=gold_path, store_path=store_path,
    )
    assert first == second
    assert first["n_pairs"] == 1
    assert first["n_cross_paper_pairs"] == 1
    assert first["n_same_paper_pairs"] == 0
    assert first["n_join_miss"] == 1
    assert "fact_id" not in first
    assert "needles" not in first
    assert "query" not in first
    assert "evidence_quote" not in first
    _assert_pins_unmoved()


def test_score_stub_ranker_walk_and_refuse(tmp_path):
    store_path, graph_path = _write_mini(tmp_path)
    dest = tmp_path / "overlay.json"
    t5_graph_extract.extract_t5_entity_graph(
        dest=dest, graph_path=graph_path, store_path=store_path,
    )
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(_gold(), indent=2) + "\n", encoding="utf-8")
    gzip_path = _gzip(tmp_path, store_path)

    def ranker(index, query):
        if query == PROMPT:
            return list(index["chunks"])[:1]
        return []

    header = t5_graph_eval.score_t5_graph_rag(
        dest=dest,
        gold_path=gold_path,
        index_path=gzip_path,
        store_path=store_path,
        ranker=ranker,
    )
    assert header["walk_depth"] == 1
    assert header["n_pairs"] == 1
    curves = json.loads(Path(header["curves_path"]).read_text(encoding="utf-8"))
    assert curves["walk_depth"] == 1
    hybrid = next(row for row in curves["series"] if row["arm"] == "hybrid")
    graph = next(row for row in curves["series"] if row["arm"] == "graph")
    assert hybrid["n_retrievable_at_k"]["1"] == 0
    assert graph["n_retrievable_at_k"]["1"] == 1
    assert graph["must_refuse_at_k"]["5"] == 1.0
    assert "fact_id" not in curves
    dumped = json.dumps(curves)
    assert "needles" not in dumped
    _assert_pins_unmoved()


def test_score_refuse_leak_fails(tmp_path):
    store_path, graph_path = _write_mini(tmp_path)
    dest = tmp_path / "overlay.json"
    t5_graph_extract.extract_t5_entity_graph(
        dest=dest, graph_path=graph_path, store_path=store_path,
    )
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(_gold(), indent=2) + "\n", encoding="utf-8")
    gzip_path = _gzip(tmp_path, store_path)

    def ranker(index, query):
        if query == REFUSE_Q:
            return [{"chunk_id": "leak", "body": f"token {REFUSE_NEEDLE} here"}]
        return []

    with pytest.raises(TextGoldError) as caught:
        t5_graph_eval.score_t5_graph_rag(
            dest=dest,
            gold_path=gold_path,
            index_path=gzip_path,
            store_path=store_path,
            ranker=ranker,
        )
    assert caught.value.code == "refuse_leak"
    _assert_pins_unmoved()


def test_product_derive_is_deterministic_counts(tmp_path):
    dest = tmp_path / "product-overlay.json"
    t5_graph_extract.extract_t5_entity_graph(dest=dest)
    first = t5_graph_eval.derive_t5_multihop_pairs(dest=dest)
    second = t5_graph_eval.derive_t5_multihop_pairs(dest=dest)
    assert first == second
    assert first["n_pairs"] >= 0
    assert first["n_join_miss"] >= 0
    assert "query" not in first
    assert file_sha256(GRAPH) == t5_graph_extract.GRAPH_SHA256
    assert file_sha256(GOLD) == t5_corpus_graph.GOLD_SHA256
    _assert_pins_unmoved()


def test_product_score_graph_must_refuse(tmp_path):
    dest = tmp_path / "product-overlay.json"
    t5_graph_extract.extract_t5_entity_graph(dest=dest)
    with pytest.raises(TextGoldError) as caught:
        t5_graph_eval.score_t5_graph_rag(dest=dest)
    assert caught.value.code == "offdomain_leak"
    curves = json.loads((tmp_path / "CURVES.graph-rag.v1.json").read_text(encoding="utf-8"))
    graph = next(row for row in curves["series"] if row["arm"] == "graph")
    hybrid = next(row for row in curves["series"] if row["arm"] == "hybrid")
    for label in ("1", "3", "5", "10", "20"):
        assert graph["must_refuse_at_k"][label] == 1.0
        assert graph["n_leaks_at_k"][label] == 0
        assert label in curves["n_offdomain_introduced_at_k"]
    assert any(int(curves["n_offdomain_introduced_at_k"][label]) > 0 for label in ("1", "3", "5", "10", "20"))
    assert curves["n_pairs"] > 0
    assert curves["walk_depth"] == 1
    assert hybrid["n_pairs"] == graph["n_pairs"]
    assert file_sha256(GRAPH) == t5_graph_extract.GRAPH_SHA256
    _assert_pins_unmoved()
