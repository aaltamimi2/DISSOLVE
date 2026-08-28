"""W-1 five-arm hybrid weight sweep. Injected rankers. No MiniLM. No gold needles in asserts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import dense_d2, dense_w1, engine_e2e, research, text_chunk_metrics
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
        "curves_d3": "d" * 64,
        "error_analysis": "e" * 64,
    }


def _arm_rankers(*, leak_arm: str | None = None, hits=None):
    base = {FIRE_A: ["c-a"], FIRE_B: ["c-b"], FIRE_C: [], FIRE_D: [], HOLD_ID: []}
    if hits is not None:
        base = hits
    leaked = dict(base)
    leaked[HOLD_ID] = ["c-a"]
    out = {}
    for arm_id, _dense, _sparse in dense_w1.NAMED_ARMS:
        out[arm_id] = _ranker(leaked if arm_id == leak_arm else base)
    return out


def test_w1_named_grid_is_five_pairs_on_the_095_line():
    grid = dense_w1.named_grid()
    assert [arm_id for arm_id, _, _ in grid] == [
        "w_prod", "w_swap", "w_sparse_heavy", "w_sparse_dom", "w_dense_heavy",
    ]
    assert grid[0] == ("w_prod", 0.55, 0.40)
    assert all(abs(dense + sparse - 0.95) < 1e-9 for _arm, dense, sparse in grid)


def test_w1_does_not_retune_production_constants():
    source = Path(research.__file__).read_text()
    assert "_HYBRID_DENSE_WEIGHT = 0.55\n" in source
    assert "_HYBRID_SPARSE_WEIGHT = 0.40\n" in source
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40


def test_w1_five_arms_and_no_weight_bind():
    def boom(*_args, **_kwargs):
        raise AssertionError("W-1 unit tests must not load MiniLM")

    research._dense_vectors = boom
    artifact = dense_w1.build_w1_artifact(
        gold=_gold(),
        index=_index(),
        pins=_pins(),
        rankers=_arm_rankers(),
    )
    arms = [row["arm"] for row in artifact["series"]]
    assert arms == ["w_prod", "w_swap", "w_sparse_heavy", "w_sparse_dom", "w_dense_heavy"]
    by_arm = {row["arm"]: row for row in artifact["series"]}
    assert by_arm["w_prod"]["n_retrievable_at_k"]["10"] == 2
    assert by_arm["w_prod"]["n_leaks_at_k"] == _k_map(0)
    assert by_arm["w_prod"]["arm_failed"] is False
    assert by_arm["w_prod"]["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}
    assert artifact["weights_retuned"] is False
    assert artifact["finding"]["weights_retuned"] is False
    assert artifact["refuse_rule"] == "sparse_gated"
    assert artifact["refuse_gate"] == "sparse_raw_score > 0"
    assert artifact["spec_sha256"] == dense_w1.SWEEP_SPEC_SHA256
    blob = json.dumps(artifact)
    assert not text_chunk_metrics._contains_forbidden_keys(artifact, set(dense_w1._FORBIDDEN_W1_KEYS))
    assert NEEDLE_A not in blob
    assert FIRE_A not in blob
    versus = artifact["versus_w_prod_at_k"]["10"]
    assert "w_prod" not in versus
    assert versus["w_swap"]["verdict"] == "TIED"


def test_w1_up_when_intervals_do_not_overlap():
    prod = {
        "recall_at_k": _k_map(0.2),
        "recall_ci_at_k": _k_map({"lo": 0.05, "hi": 0.35}),
    }
    better = {
        "recall_at_k": _k_map(0.9),
        "recall_ci_at_k": _k_map({"lo": 0.7, "hi": 0.99}),
    }
    rows = {
        "w_prod": prod,
        "w_swap": better,
        "w_sparse_heavy": prod,
        "w_sparse_dom": prod,
        "w_dense_heavy": prod,
    }
    versus = dense_w1._versus_w_prod(rows)
    assert versus["10"]["w_swap"]["verdict"] == "UP"
    assert versus["10"]["w_dense_heavy"]["verdict"] == "TIED"


def test_w1_non_prod_leak_is_arm_failed_not_sha_fail():
    leaked = _index()
    leaked["chunks"][0]["body"] = f"body {NEEDLE_A} {NEEDLE_HOLD} body"
    leaked["chunks"][0]["text"] = leaked["chunks"][0]["body"]
    artifact = dense_w1.build_w1_artifact(
        gold=_gold(),
        index=leaked,
        pins=_pins(),
        rankers=_arm_rankers(leak_arm="w_swap"),
    )
    by_arm = {row["arm"]: row for row in artifact["series"]}
    assert by_arm["w_prod"]["arm_failed"] is False
    assert by_arm["w_swap"]["arm_failed"] is True
    assert by_arm["w_swap"]["fail_reason"] == "leak"
    assert "w_swap" not in artifact["finding"]["up_vs_w_prod"]


def test_w1_prod_leak_fails_the_sha():
    leaked = _index()
    leaked["chunks"][0]["body"] = f"body {NEEDLE_A} {NEEDLE_HOLD} body"
    leaked["chunks"][0]["text"] = leaked["chunks"][0]["body"]
    try:
        dense_w1.build_w1_artifact(
            gold=_gold(),
            index=leaked,
            pins=_pins(),
            rankers=_arm_rankers(leak_arm="w_prod"),
        )
    except TextGoldError as error:
        assert error.code == "w_prod_failed"
    else:
        raise AssertionError("w_prod hold-out leak must fail W-1")


def test_w1_prod_ident_helper_matches_d3_hybrid_counts():
    row = {"n_retrievable_at_k": dict(dense_w1.D3_HYBRID_N_RETRIEVABLE)}
    dense_w1._assert_w_prod_ident(row, dense_w1.D3_HYBRID_N_RETRIEVABLE)
    drifted = {"n_retrievable_at_k": {"1": 0, "3": 0, "5": 0, "10": 0, "20": 0}}
    try:
        dense_w1._assert_w_prod_ident(drifted, dense_w1.D3_HYBRID_N_RETRIEVABLE)
    except TextGoldError as error:
        assert error.code == "w_prod_ident"
    else:
        raise AssertionError("drifted w_prod counts must fail IDENT")


def test_search_index_explicit_production_weights_match_defaults(monkeypatch):
    seen = []

    def fake(texts, model_name=None):
        seen.extend(list(texts))
        return engine_e2e.MINILM_ID, [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    index = {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": engine_e2e.KNOWLEDGEBASE_ID,
        "chunks": [_chunk("c-a", NEEDLE_A), _chunk("c-b", NEEDLE_B)],
        "dense": {
            "model": engine_e2e.MINILM_ID,
            "dim": 384,
            "chunk_ids": ["c-a", "c-b"],
            "vectors": [[1.0] + [0.0] * 383, [0.0] * 384],
        },
    }
    default_rows = research._search_index(index, f"query {NEEDLE_A}", 5, "hybrid")
    explicit_rows = research._search_index(
        index, f"query {NEEDLE_A}", 5, "hybrid", w_dense=0.55, w_sparse=0.40,
    )
    assert default_rows == explicit_rows
    assert default_rows


def _emit_paths(tmp_path: Path) -> dict[str, Path]:
    gold_path = tmp_path / "GOLD.text.v1.unsealed.json"
    census_path = tmp_path / "CENSUS.v3.json"
    store_path = tmp_path / "CHUNKS.t5.indexed.unsealed.v1.json"
    v3_path = tmp_path / "CURVES.retrieval.v3.json"
    v4_path = tmp_path / "CURVES.retrieval.v4.json"
    d3_path = tmp_path / "CURVES.retrieval.d3.json"
    finding_path = tmp_path / "CURVES.retrieval.d3.finding.json"
    analysis_path = tmp_path / "ERROR_ANALYSIS.k10.v3.json"
    gold_path.write_text(json.dumps(_gold()) + "\n")
    census_path.write_text(json.dumps(_census()) + "\n")
    store_path.write_text(json.dumps(_store()) + "\n")
    v3_path.write_text(json.dumps({"series": []}) + "\n")
    v4_path.write_text(json.dumps({"schema": "v4-fixture"}) + "\n")
    d3_path.write_text(json.dumps({"schema": "d3-fixture", "series": []}) + "\n")
    finding_path.write_text(json.dumps({"schema": "d3-finding-fixture"}) + "\n")
    analysis_path.write_text(json.dumps({"arms": []}) + "\n")
    return {
        "gold": gold_path,
        "census": census_path,
        "store": store_path,
        "v3": v3_path,
        "v4": v4_path,
        "d3": d3_path,
        "finding": finding_path,
        "analysis": analysis_path,
    }


def test_w1_emit_writes_beside_v3_v4_d3(tmp_path, monkeypatch):
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
    d3_before = file_sha256(paths["d3"])
    dest = tmp_path / "out"
    result = dense_w1.emit_w1_product(
        gold_path=paths["gold"],
        census_path=paths["census"],
        store_path=paths["store"],
        v3_path=paths["v3"],
        v4_path=paths["v4"],
        d3_path=paths["d3"],
        finding_path=paths["finding"],
        analysis_path=paths["analysis"],
        dest_dir=dest,
        rankers=_arm_rankers(),
        index=_index(),
    )
    assert file_sha256(paths["v3"]) == v3_before
    assert file_sha256(paths["v4"]) == v4_before
    assert file_sha256(paths["d3"]) == d3_before
    assert Path(result["curves_path"]).name == "CURVES.retrieval.weights.v1.json"
    assert Path(result["png_path"]).is_file()
    payload = json.loads(Path(result["curves_path"]).read_text())
    assert [row["arm"] for row in payload["series"]] == [
        "w_prod", "w_swap", "w_sparse_heavy", "w_sparse_dom", "w_dense_heavy",
    ]
    assert payload["weights_retuned"] is False
    assert not text_chunk_metrics._contains_forbidden_keys(payload, set(dense_w1._FORBIDDEN_W1_KEYS))


def test_w1_gold_v2_is_owner_stop(tmp_path, monkeypatch):
    v2 = tmp_path / "GOLD.v2.json"
    v2.write_text("{}\n")
    monkeypatch.setattr(text_chunk_metrics, "GOLD_V2_PATH", v2)
    try:
        dense_w1.emit_w1_product(dest_dir=tmp_path / "out")
    except TextGoldError as error:
        assert error.code == "gold_v2_present"
    else:
        raise AssertionError("GOLD.v2.json must stop W-1")


def test_w1_protected_names_keep_d3_v3_v4():
    assert "CURVES.retrieval.d3.json" in dense_w1._PROTECTED_NAMES
    assert "CURVES.retrieval.v3.json" in dense_w1._PROTECTED_NAMES
    assert "CURVES.retrieval.v4.json" in dense_w1._PROTECTED_NAMES
    assert "CURVES.retrieval.weights.v1.json" not in dense_w1._PROTECTED_NAMES
