"""D-3 ablation. Injected rankers. No MiniLM. No gold needles in asserts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import dense_d2, dense_d3, engine_e2e, research, text_chunk_metrics
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import TextGoldError

IDX = "aa" * 32
HOLD = "bb" * 32
FIRE_A = "f-fire-a"
FIRE_B = "f-fire-b"
FIRE_C = "f-fire-c"
FIRE_D = "f-fire-d"
HOLD_ID = "f-hold-1"
NEEDLE_A = "ZXQALPHA"
NEEDLE_B = "ZXQBETA"
NEEDLE_C = "ZXQGAMA"
NEEDLE_D = "ZXQDELTA"
NEEDLE_HOLD = "ZXQHOLD"


def _k_map(value) -> dict[str, object]:
    return {str(k): value for k in text_chunk_metrics.RETRIEVAL_KS}


def _fact(fact_id: str, paper: str, status: str, token: str) -> dict:
    return {
        "fact_id": fact_id,
        "paper_sha256": paper,
        "paper_status": status,
        "query": f"query {token}",
        "needles": {"subject": token, "value": token},
    }


def _gold() -> dict:
    return {
        "papers": [
            {"paper_sha256": IDX, "paper_status": "indexed"},
            {"paper_sha256": HOLD, "paper_status": "held_out"},
        ],
        "facts": [
            _fact(FIRE_A, IDX, "indexed", NEEDLE_A),
            _fact(FIRE_B, IDX, "indexed", NEEDLE_B),
            _fact(FIRE_C, IDX, "indexed", NEEDLE_C),
            _fact(FIRE_D, IDX, "indexed", NEEDLE_D),
            _fact(HOLD_ID, HOLD, "held_out", NEEDLE_HOLD),
        ],
    }


def _census() -> dict:
    return {
        "papers": [
            {"sha256": IDX, "status": "indexed"},
            {"sha256": HOLD, "status": "held_out"},
        ]
    }


def _chunk(chunk_id: str, token: str) -> dict:
    body = f"body {token} body"
    return {
        "chunk_id": chunk_id,
        "paper_sha256": IDX,
        "body": body,
        "text": body,
        "section": "methods",
        "section_origin": "parser_supplied",
        "char_start": 0,
        "char_end": len(body),
        "page": 1,
        "title": "Fixture",
    }


def _store() -> dict:
    chunks = [
        _chunk("c-a", NEEDLE_A),
        _chunk("c-b", NEEDLE_B),
        _chunk("c-c", NEEDLE_C),
        _chunk("c-d", NEEDLE_D),
    ]
    return {"indexed_paper_sha256": [IDX], "chunks": chunks}


def _index() -> dict:
    chunks = _store()["chunks"]
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": engine_e2e.KNOWLEDGEBASE_ID,
        "chunks": chunks,
        "dense": {
            "model": engine_e2e.MINILM_ID,
            "dim": 384,
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "vectors": [[0.0] * 384 for _ in chunks],
        },
    }


def _ranker(hits: dict[str, list[str]]):
    gold_facts = _gold()["facts"]

    def rank(index, query: str) -> list:
        by_id = {chunk["chunk_id"]: chunk for chunk in index.get("chunks") or []}
        for fact in gold_facts:
            if str(fact.get("query") or "") == query:
                return [by_id[item] for item in hits.get(fact["fact_id"], []) if item in by_id]
        return []

    return rank


def _pins() -> dict[str, str]:
    return {
        "gold": "g" * 64,
        "census": "c" * 64,
        "store": "s" * 64,
        "curves_v3": "v" * 64,
        "curves_v4": "4" * 64,
        "error_analysis": "e" * 64,
    }


def _rankers(*, dense_hold: bool = False, hybrid_hold: bool = False, sparse_hits=None):
    sparse = {FIRE_A: ["c-a"], FIRE_B: [], FIRE_C: [], FIRE_D: [], HOLD_ID: []}
    if sparse_hits is not None:
        sparse = sparse_hits
    dense = {
        FIRE_A: ["c-a"],
        FIRE_B: ["c-b"],
        FIRE_C: ["c-c"],
        FIRE_D: ["c-d"],
        HOLD_ID: ["c-a"] if dense_hold else [],
    }
    hybrid = {
        FIRE_A: ["c-a"],
        FIRE_B: ["c-b"],
        FIRE_C: [],
        FIRE_D: [],
        HOLD_ID: ["c-a"] if hybrid_hold else [],
    }
    return {
        "sparse": _ranker(sparse),
        "dense": _ranker(dense),
        "hybrid": _ranker(hybrid),
    }


def test_d3_three_arms_and_no_weight_tune():
    def boom(*_args, **_kwargs):
        raise AssertionError("D-3 unit tests must not load MiniLM")

    research._dense_vectors = boom
    artifact = dense_d3.build_d3_artifact(
        gold=_gold(),
        index=_index(),
        pins=_pins(),
        rankers=_rankers(),
    )
    arms = [row["arm"] for row in artifact["series"]]
    assert arms == ["sparse", "dense", "hybrid"]
    by_arm = {row["arm"]: row for row in artifact["series"]}
    assert by_arm["sparse"]["n_retrievable_at_k"]["10"] == 1
    assert by_arm["dense"]["n_retrievable_at_k"]["10"] == 4
    assert by_arm["hybrid"]["n_retrievable_at_k"]["10"] == 2
    assert by_arm["hybrid"]["n_leaks_at_k"] == _k_map(0)
    assert artifact["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}
    assert artifact["weights_retuned"] is False
    assert artifact["finding"]["weights_retuned"] is False
    assert artifact["finding"]["k"] == 5
    assert artifact["refuse_rule"] == "sparse_gated"
    assert by_arm["sparse"]["embedder_in_retrieval"] is False
    assert by_arm["dense"]["embedder_in_retrieval"] is True
    assert by_arm["hybrid"]["embedder_in_retrieval"] is True
    blob = json.dumps(artifact)
    assert not text_chunk_metrics._contains_forbidden_keys(
        artifact, {"needles", "query", "evidence_quote", "canonical_text"}
    )
    assert NEEDLE_A not in blob
    versus = artifact["versus_at_k"]["10"]
    assert versus["dense_vs_sparse"]["delta"] > 0
    assert versus["hybrid_vs_sparse"]["delta"] > 0
    # n=4 Wilson intervals overlap even at 1/4 vs 4/4; TIED is the D-2 rule.
    assert versus["dense_vs_sparse"]["verdict"] == "TIED"


def test_d3_up_when_arm_intervals_do_not_overlap():
    left = {
        "recall_at_k": _k_map(0.2),
        "recall_ci_at_k": _k_map({"lo": 0.05, "hi": 0.35}),
    }
    right = {
        "recall_at_k": _k_map(0.9),
        "recall_ci_at_k": _k_map({"lo": 0.7, "hi": 0.99}),
    }
    versus = dense_d3._versus_arms({"sparse": left, "dense": right, "hybrid": right})
    assert versus["10"]["dense_vs_sparse"]["verdict"] == "UP"
    assert versus["10"]["hybrid_vs_sparse"]["verdict"] == "UP"
    assert versus["10"]["hybrid_vs_dense"]["verdict"] == "TIED"


def test_d3_tied_when_arm_intervals_overlap():
    same = {
        FIRE_A: ["c-a"],
        FIRE_B: ["c-b"],
        FIRE_C: [],
        FIRE_D: [],
        HOLD_ID: [],
    }
    artifact = dense_d3.build_d3_artifact(
        gold=_gold(),
        index=_index(),
        pins=_pins(),
        rankers={
            "sparse": _ranker(same),
            "dense": _ranker(same),
            "hybrid": _ranker(same),
        },
    )
    versus = artifact["versus_at_k"]["10"]
    assert versus["dense_vs_sparse"]["verdict"] == "TIED"
    assert versus["hybrid_vs_sparse"]["verdict"] == "TIED"
    assert versus["hybrid_vs_dense"]["verdict"] == "TIED"


def test_d3_hybrid_leak_fails():
    leaked = _index()
    leaked["chunks"][0]["body"] = f"body {NEEDLE_A} {NEEDLE_HOLD} body"
    leaked["chunks"][0]["text"] = leaked["chunks"][0]["body"]
    try:
        dense_d3.build_d3_artifact(
            gold=_gold(),
            index=leaked,
            pins=_pins(),
            rankers=_rankers(hybrid_hold=True),
        )
    except TextGoldError as error:
        assert error.code == "refuse_leak"
    else:
        raise AssertionError("hybrid hold-out leak must fail D-3")


def test_d3_dense_leak_is_reported_not_a_hybrid_fail():
    leaked = _index()
    leaked["chunks"][0]["body"] = f"body {NEEDLE_A} {NEEDLE_HOLD} body"
    leaked["chunks"][0]["text"] = leaked["chunks"][0]["body"]
    artifact = dense_d3.build_d3_artifact(
        gold=_gold(),
        index=leaked,
        pins=_pins(),
        rankers=_rankers(dense_hold=True),
    )
    by_arm = {row["arm"]: row for row in artifact["series"]}
    assert by_arm["dense"]["n_leaks_at_k"]["10"] == 1
    assert by_arm["hybrid"]["n_leaks_at_k"]["10"] == 0


def _emit_paths(tmp_path: Path) -> dict[str, Path]:
    gold_path = tmp_path / "GOLD.text.v1.unsealed.json"
    census_path = tmp_path / "CENSUS.v3.json"
    store_path = tmp_path / "CHUNKS.t5.indexed.unsealed.v1.json"
    v3_path = tmp_path / "CURVES.retrieval.v3.json"
    v4_path = tmp_path / "CURVES.retrieval.v4.json"
    analysis_path = tmp_path / "ERROR_ANALYSIS.k10.v3.json"
    gold_path.write_text(json.dumps(_gold()) + "\n")
    census_path.write_text(json.dumps(_census()) + "\n")
    store_path.write_text(json.dumps(_store()) + "\n")
    v3_path.write_text(json.dumps({"series": []}) + "\n")
    v4_path.write_text(json.dumps({"schema": "v4-fixture"}) + "\n")
    analysis_path.write_text(json.dumps({"arms": []}) + "\n")
    return {
        "gold": gold_path,
        "census": census_path,
        "store": store_path,
        "v3": v3_path,
        "v4": v4_path,
        "analysis": analysis_path,
    }


def test_d3_emit_writes_beside_v3_and_v4(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("emit unit test must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    paths = _emit_paths(tmp_path)
    monkeypatch.setattr(text_chunk_metrics, "GOLD_UNSEALED_SHA256", file_sha256(paths["gold"]))
    monkeypatch.setattr(dense_d2, "CENSUS_V3_SHA256", file_sha256(paths["census"]))
    monkeypatch.setattr(engine_e2e, "STORE_SHA256", file_sha256(paths["store"]))
    monkeypatch.setattr(engine_e2e, "CURVES_V3_SHA256", file_sha256(paths["v3"]))
    monkeypatch.setattr(engine_e2e, "ERROR_ANALYSIS_SHA256", file_sha256(paths["analysis"]))
    v3_before = file_sha256(paths["v3"])
    v4_before = file_sha256(paths["v4"])
    dest = tmp_path / "out"
    result = dense_d3.emit_d3_product(
        gold_path=paths["gold"],
        census_path=paths["census"],
        store_path=paths["store"],
        v3_path=paths["v3"],
        v4_path=paths["v4"],
        analysis_path=paths["analysis"],
        dest_dir=dest,
        rankers=_rankers(),
        index=_index(),
    )
    assert file_sha256(paths["v3"]) == v3_before
    assert file_sha256(paths["v4"]) == v4_before
    assert result["curves_v3_sha256"] == v3_before
    assert result["curves_v4_sha256"] == v4_before
    assert result["hybrid_n_leaks"] == 0
    assert Path(result["curves_path"]).name == "CURVES.retrieval.d3.json"
    assert Path(result["png_path"]).is_file()
    payload = json.loads(Path(result["curves_path"]).read_text())
    assert [row["arm"] for row in payload["series"]] == ["sparse", "dense", "hybrid"]
    assert payload["weights_retuned"] is False
    assert not text_chunk_metrics._contains_forbidden_keys(
        payload, {"needles", "query", "evidence_quote", "canonical_text"}
    )
