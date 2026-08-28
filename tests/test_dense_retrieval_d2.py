"""D-2 hybrid curves. Injected ranker. No MiniLM. No gold needles in asserts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import dense_d2, engine_e2e, research, text_chunk_metrics
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import TextGoldError

IDX = "aa" * 32
HOLD = "bb" * 32
FIRE_A = "f-fire-a"
FIRE_B = "f-lex-hit"
FIRE_C = "f-lex-miss"
SPLIT_ID = "f-split-1"
HOLD_ID = "f-hold-1"
NEEDLE_A = "ZXQALPHA"
NEEDLE_B = "ZXQBETA"
NEEDLE_C = "ZXQGAMA"
NEEDLE_SPLIT = "ZXQSPLIT"
NEEDLE_HOLD = "ZXQHOLD"


def _ci(lo: float, hi: float) -> dict[str, float]:
    return {"lo": lo, "hi": hi}


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
            _fact(SPLIT_ID, IDX, "indexed", NEEDLE_SPLIT),
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
        _chunk("c-s", NEEDLE_SPLIT),
    ]
    return {"indexed_paper_sha256": [IDX], "chunks": chunks}


def _index() -> dict:
    store = _store()
    chunks = store["chunks"]
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


def _v3(*, recall: float, lo: float, hi: float) -> dict:
    return {
        "series": [
            {
                "strategy": "T5",
                "params": {"target": 1400},
                "bucket": "all",
                "n_facts": 4,
                "n_held_out_facts": 1,
                "recall_at_k": _k_map(recall),
                "recall_ci_at_k": _k_map(_ci(lo, hi)),
            }
        ]
    }


def _analysis() -> dict:
    return {
        "arms": [
            {
                "strategy": "T0",
                "params": {"target": 1400, "overlap": 180},
                "misses": [{"fact_id": "t0-other", "class": "lexical_mismatch"}],
            },
            {
                "strategy": "T5",
                "params": {"target": 1400},
                "misses": [
                    {"fact_id": FIRE_B, "class": "lexical_mismatch"},
                    {"fact_id": FIRE_C, "class": "lexical_mismatch"},
                    {"fact_id": SPLIT_ID, "class": "needles_split"},
                ],
            },
        ]
    }


def _ranker(hits: dict[str, list[str]]):
    gold_facts = _gold()["facts"]

    def rank(index, query: str) -> list:
        by_id = {chunk["chunk_id"]: chunk for chunk in index.get("chunks") or []}
        for fact in gold_facts:
            if str(fact.get("query") or "") == query:
                return [by_id[chunk_id] for chunk_id in hits.get(fact["fact_id"], []) if chunk_id in by_id]
        return []

    return rank


def _pins() -> dict[str, str]:
    return {
        "gold": "g" * 64,
        "census": "c" * 64,
        "store": "s" * 64,
        "curves_v3": "v" * 64,
        "error_analysis": "e" * 64,
    }


def _hit_map() -> dict[str, list[str]]:
    return {
        FIRE_A: ["c-a"],
        FIRE_B: ["c-b"],
        FIRE_C: [],
        SPLIT_ID: [],
        HOLD_ID: [],
    }


def test_d2_t5_miss_set_is_t5_arm_not_t0_count():
    sets = dense_d2.t5_miss_sets(_analysis())
    assert sets["lexical_mismatch"] == {FIRE_B, FIRE_C}
    assert sets["needles_split"] == {SPLIT_ID}
    assert len(sets["lexical_mismatch"]) != 1


def test_d2_recovery_count_and_no_gold_quote():
    def boom(*_args, **_kwargs):
        raise AssertionError("D-2 unit tests must not load MiniLM")

    research._dense_vectors = boom
    artifact = dense_d2.build_v4_artifact(
        gold=_gold(),
        index=_index(),
        v3=_v3(recall=0.25, lo=0.01, hi=0.7),
        analysis=_analysis(),
        pins=_pins(),
        ranker=_ranker(_hit_map()),
    )
    recovery = artifact["recovery_at_10"]
    assert recovery["lexical_mismatch_set_size"] == 2
    assert recovery["lexical_mismatch_recovered_count"] == 1
    assert recovery["lexical_mismatch_recovered_ids"] == [FIRE_B]
    assert recovery["needles_split_set_size"] == 1
    assert recovery["needles_split_recovered_count"] == 0
    blob = json.dumps(artifact)
    assert not text_chunk_metrics._contains_forbidden_keys(
        artifact, {"needles", "query", "evidence_quote", "canonical_text"}
    )
    assert NEEDLE_A not in blob
    assert artifact["embedder_in_retrieval"] is True
    assert artifact["ranker"] == "hybrid"
    assert artifact["refuse_rule"] == "sparse_gated"
    assert artifact["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}
    assert artifact["series"][0]["n_leaks_at_k"] == _k_map(0)


def test_d2_tied_when_wilson_intervals_overlap():
    artifact = dense_d2.build_v4_artifact(
        gold=_gold(),
        index=_index(),
        v3=_v3(recall=0.5, lo=0.01, hi=0.99),
        analysis=_analysis(),
        pins=_pins(),
        ranker=_ranker(_hit_map()),
    )
    versus = artifact["versus_v3_t5"]
    assert versus["10"]["verdict"] == "TIED"
    assert "delta" in versus["10"]


def test_d2_up_when_intervals_do_not_overlap():
    full_v3 = {"recall_at_k": _k_map(0.2), "recall_ci_at_k": _k_map(_ci(0.05, 0.35))}
    versus = dense_d2._compare_k(full_v3, _k_map(0.9), _k_map(_ci(0.7, 0.99)))
    assert versus["10"]["verdict"] == "UP"
    assert versus["10"]["delta"] > 0


def test_d2_down_delta_reported_when_recall_falls():
    full_v3 = {"recall_at_k": _k_map(0.9), "recall_ci_at_k": _k_map(_ci(0.7, 0.99))}
    full_h = _k_map(0.2)
    full_ci = _k_map(_ci(0.05, 0.35))
    versus = dense_d2._compare_k(full_v3, full_h, full_ci)
    assert versus["10"]["verdict"] == "DOWN"
    assert versus["10"]["delta"] < 0


def test_d2_refuse_leak_fails_before_write(tmp_path, monkeypatch):
    leak_ranker = _ranker({**_hit_map(), HOLD_ID: ["c-a"]})
    leaked = _index()
    leaked["chunks"][0]["body"] = f"body {NEEDLE_A} {NEEDLE_HOLD} body"
    leaked["chunks"][0]["text"] = leaked["chunks"][0]["body"]
    try:
        dense_d2.build_v4_artifact(
            gold=_gold(),
            index=leaked,
            v3=_v3(recall=0.25, lo=0.01, hi=0.7),
            analysis=_analysis(),
            pins=_pins(),
            ranker=leak_ranker,
        )
    except TextGoldError as error:
        assert error.code == "refuse_leak"
    else:
        raise AssertionError("a hold-out leak must fail D-2")
    v3_path = tmp_path / "CURVES.retrieval.v3.json"
    v3_path.write_text(json.dumps(_v3(recall=0.25, lo=0.01, hi=0.7)) + "\n")
    before = file_sha256(v3_path)
    assert not (tmp_path / "CURVES.retrieval.v4.json").exists()
    assert file_sha256(v3_path) == before


def _emit_paths(tmp_path: Path) -> dict[str, Path]:
    gold_path = tmp_path / "GOLD.text.v1.unsealed.json"
    census_path = tmp_path / "CENSUS.v3.json"
    store_path = tmp_path / "CHUNKS.t5.indexed.unsealed.v1.json"
    v3_path = tmp_path / "CURVES.retrieval.v3.json"
    analysis_path = tmp_path / "ERROR_ANALYSIS.k10.v3.json"
    gold_path.write_text(json.dumps(_gold()) + "\n")
    census_path.write_text(json.dumps(_census()) + "\n")
    store_path.write_text(json.dumps(_store()) + "\n")
    v3_path.write_text(json.dumps(_v3(recall=0.25, lo=0.01, hi=0.7)) + "\n")
    analysis_path.write_text(json.dumps(_analysis()) + "\n")
    return {
        "gold": gold_path,
        "census": census_path,
        "store": store_path,
        "v3": v3_path,
        "analysis": analysis_path,
    }


def test_d2_emit_writes_beside_v3_and_keeps_v3_hash(tmp_path, monkeypatch):
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
    dest = tmp_path / "out"
    result = dense_d2.emit_v4_product(
        gold_path=paths["gold"],
        census_path=paths["census"],
        store_path=paths["store"],
        v3_path=paths["v3"],
        analysis_path=paths["analysis"],
        dest_dir=dest,
        ranker=_ranker(_hit_map()),
        index=_index(),
    )
    assert file_sha256(paths["v3"]) == v3_before
    assert result["curves_v3_sha256"] == v3_before
    assert result["n_leaks"] == 0
    assert result["lexical_mismatch_recovered_count"] == 1
    assert result["lexical_mismatch_set_size"] == 2
    assert Path(result["curves_path"]).name == "CURVES.retrieval.v4.json"
    assert Path(result["png_path"]).is_file()
    assert paths["v3"].read_bytes()
    payload = json.loads(Path(result["curves_path"]).read_text())
    assert not text_chunk_metrics._contains_forbidden_keys(
        payload, {"needles", "query", "evidence_quote", "canonical_text"}
    )
    assert payload["embedder_in_retrieval"] is True


def test_d2_pin_failure_does_not_write(tmp_path, monkeypatch):
    paths = _emit_paths(tmp_path)
    dest = tmp_path / "out"
    try:
        dense_d2.emit_v4_product(
            gold_path=paths["gold"],
            census_path=paths["census"],
            store_path=paths["store"],
            v3_path=paths["v3"],
            analysis_path=paths["analysis"],
            dest_dir=dest,
            ranker=_ranker(_hit_map()),
            index=_index(),
        )
    except TextGoldError as error:
        assert "sha" in error.code or "moved" in error.code
    else:
        raise AssertionError("unpinned fixture files must not emit")
    assert not (dest / "CURVES.retrieval.v4.json").exists()


def test_d2_chunk_id_mismatch_fails(tmp_path, monkeypatch):
    paths = _emit_paths(tmp_path)
    monkeypatch.setattr(text_chunk_metrics, "GOLD_UNSEALED_SHA256", file_sha256(paths["gold"]))
    monkeypatch.setattr(dense_d2, "CENSUS_V3_SHA256", file_sha256(paths["census"]))
    monkeypatch.setattr(engine_e2e, "STORE_SHA256", file_sha256(paths["store"]))
    monkeypatch.setattr(engine_e2e, "CURVES_V3_SHA256", file_sha256(paths["v3"]))
    monkeypatch.setattr(engine_e2e, "ERROR_ANALYSIS_SHA256", file_sha256(paths["analysis"]))
    broken = _index()
    broken["dense"]["chunk_ids"] = ["c-a", "c-b", "c-c", "c-OTHER"]
    try:
        dense_d2.emit_v4_product(
            gold_path=paths["gold"],
            census_path=paths["census"],
            store_path=paths["store"],
            v3_path=paths["v3"],
            analysis_path=paths["analysis"],
            dest_dir=tmp_path / "out",
            ranker=_ranker(_hit_map()),
            index=broken,
        )
    except TextGoldError as error:
        assert error.code == "chunk_id_set_mismatch"
    else:
        raise AssertionError("dense chunk_id set mismatch must fail")
