"""P-1 gated-top-20 rerank. Injected scorers. No gold needles in asserts."""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import tool_schemas
from dissolve import dense_d2, engine_e2e, recall_r5rr, research, rerank, text_chunk_metrics
from dissolve.gold_ensemble import file_sha256
from dissolve.text_gold import DEFAULT_OUT_DIR, TextGoldError

IDX = "aa" * 32
HOLD = "bb" * 32
FIRE_A = "tg-synth-a"
FIRE_B = "tg-synth-b"
FIRE_C = "tg-synth-c"
HOLD_ID = "tg-synth-hold"
NEEDLE_A = "ZXQALPHA"
NEEDLE_B = "ZXQBETA"
NEEDLE_C = "ZXQGAMA"
NEEDLE_HOLD = "ZXQHOLD"
PLANT = "PLANTZXQTOKEN"
_AGENT_TOOLS_SHA256 = (
    "02259d2ab680d7613731013fc6f96d3b100739d0bed7f95d114bf208e1cee293"
)


def _unit(first: float) -> list[float]:
    return [first] + [0.0] * 383


def _chunk(chunk_id: str, token: str, title: str = "Fixture") -> dict:
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
        "title": title,
    }


def _hybrid_index(n: int = 4) -> dict:
    chunks = [_chunk(f"c-{i:04d}", f"ZXQTOK{i:04d}", title=f"T{i}") for i in range(n)]
    chunks[0]["body"] = f"alpha {PLANT} methods"
    chunks[0]["text"] = chunks[0]["body"]
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": engine_e2e.KNOWLEDGEBASE_ID,
        "chunks": chunks,
        "dense": {
            "model": engine_e2e.MINILM_ID,
            "dim": 384,
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "vectors": [_unit(1.0 if i == 0 else 0.0) for i in range(n)],
        },
    }


def _fake_minilm():
    def fake(texts, model_name=None):
        return engine_e2e.MINILM_ID, [_unit(1.0) for _ in texts]

    return fake


def _tuple(chunk_id: str, score: float):
    chunk = _chunk(chunk_id, chunk_id)
    return (score, score, 0.0, 0.0, chunk, score)


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
            _fact(HOLD_ID, HOLD, "held_out", NEEDLE_HOLD),
        ],
    }


def _index_for_gold() -> dict:
    chunks = [
        _chunk("c-a", NEEDLE_A),
        _chunk("c-b", NEEDLE_B),
        _chunk("c-c", NEEDLE_C),
    ]
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
                return [by_id[chunk_id] for chunk_id in hits.get(fact["fact_id"], []) if chunk_id in by_id]
        return []

    return rank


def _pins() -> dict[str, str]:
    return {
        "gold": "g" * 64,
        "census": "c" * 64,
        "store": "s" * 64,
        "curves_v4": "v" * 64,
    }


def test_hook_is_after_hybrid_sort_not_passage_parts():
    src = inspect.getsource(research._search_index)
    hybrid = src.split('if mode == "hybrid":', 1)[1]
    sort_at = hybrid.find("ranked.sort")
    rerank_at = hybrid.find("rerank.reorder_window")
    serve_at = hybrid.find("_ranked_search_rows")
    assert 0 <= sort_at < rerank_at < serve_at
    parts = inspect.getsource(research._hybrid_passage_parts)
    assert "rerank" not in parts
    assert "reorder_window" not in inspect.getsource(research._bm25)
    assert "CrossEncoder" not in Path(_ROOT / "src" / "dissolve" / "research.py").read_text()
    loader = inspect.getsource(rerank._load_cross_encoder)
    assert "CrossEncoder(CROSS_ENCODER_ID)" in loader
    preamble = Path(_ROOT / "src" / "dissolve" / "rerank.py").read_text().split("def _load_cross_encoder")[0]
    assert "sentence_transformers" not in preamble


def test_named_cross_encoder_id_is_exact():
    assert rerank.CROSS_ENCODER_ID == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert "MiniLM-L6-v2" not in Path(_ROOT / "src" / "dissolve" / "rerank.py").read_text()


def test_off_is_identity_and_sparse_does_not_rerank(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    index = _hybrid_index()
    first = research._search_index(index, PLANT, 5, "hybrid", rerank_mode="off")
    second = research._search_index(index, PLANT, 5, "hybrid", rerank_mode="off")
    assert [row["chunk_id"] for row in first] == [row["chunk_id"] for row in second]
    called = []

    def boom(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("sparse must not rerank")

    monkeypatch.setattr(rerank, "reorder_window", boom)
    sparse = research._search_index(index, PLANT, 5, "sparse")
    assert sparse
    assert not called


def test_fake_cross_encoder_reorders_top_window(monkeypatch):
    ranked = [_tuple("c-a", 3.0), _tuple("c-b", 2.0), _tuple("c-c", 1.0)]
    monkeypatch.setattr(rerank, "_score_pairs", lambda query, passages: [0.0, 10.0, 5.0])
    got = rerank.reorder_window("q", ranked, "cross_encoder")
    assert [item[4]["chunk_id"] for item in got] == ["c-b", "c-c", "c-a"]
    assert got[0][0] == 10.0


def test_blocked_does_not_return_hybrid_order(monkeypatch):
    def boom():
        raise rerank.RerankBlocked("rerank_blocked")

    monkeypatch.setattr(rerank, "_load_cross_encoder", boom)
    ranked = [_tuple("c-a", 3.0), _tuple("c-b", 2.0)]
    try:
        rerank.reorder_window("q", ranked, "cross_encoder")
    except rerank.RerankBlocked as error:
        assert error.code == "rerank_blocked"
        assert str(error) == "rerank_blocked"
    else:
        raise AssertionError("missing model must BLOCKED, not substitute")
    assert [item[4]["chunk_id"] for item in rerank.reorder_window("q", ranked, "off")] == ["c-a", "c-b"]


def test_shuffle_is_deterministic_and_not_identity():
    ranked = [_tuple(f"c-{i:02d}", float(20 - i)) for i in range(20)]
    first = rerank.reorder_window("q", ranked, "shuffle")
    second = rerank.reorder_window("q", ranked, "shuffle")
    ids = [item[4]["chunk_id"] for item in first]
    assert ids == [item[4]["chunk_id"] for item in second]
    assert ids != [item[4]["chunk_id"] for item in ranked]
    assert set(ids) == {item[4]["chunk_id"] for item in ranked}


def test_mrr_at_20_is_reciprocal_of_first_hit():
    chunks = [_chunk("c-miss", "ZXQNOPE"), _chunk("c-hit", NEEDLE_A)]
    needles = {"subject": NEEDLE_A, "value": NEEDLE_A}
    assert text_chunk_metrics.mrr_at_k(chunks, needles, k=20) == 0.5
    assert text_chunk_metrics.mrr_at_k(chunks[:1], needles, k=20) == 0.0


def test_no_rerank_ident_fails_on_drifted_counts():
    drifted = {"n_retrievable_at_k": {"1": 0, "3": 0, "5": 0, "10": 0, "20": 0}}
    try:
        recall_r5rr._assert_no_rerank_ident(drifted, 29)
    except TextGoldError as error:
        assert error.code == "no_rerank_ident"
    else:
        raise AssertionError("drifted no-rerank counts must fail IDENT")
    ok = {
        "n_retrievable_at_k": dict(recall_r5rr.V4_HYBRID_N_RETRIEVABLE),
        "n_must_refuse_at_k": {str(k): 29 for k in text_chunk_metrics.RETRIEVAL_KS},
        "n_leaks_at_k": {str(k): 0 for k in text_chunk_metrics.RETRIEVAL_KS},
    }
    recall_r5rr._assert_no_rerank_ident(ok, 29)


def test_rerank_bar_helper_fires_on_net_loss():
    pair = {"fixed": ["tg-1"], "broken": [f"tg-b-{i}" for i in range(10)], "net": -9}
    row = {
        "n_retrievable_at_k": {"5": 199},
        "n_must_refuse_at_k": {str(k): 29 for k in text_chunk_metrics.RETRIEVAL_KS},
        "n_leaks_at_k": {str(k): 0 for k in text_chunk_metrics.RETRIEVAL_KS},
    }
    try:
        recall_r5rr._assert_rerank_meets_bar(pair, row, 29)
    except TextGoldError as error:
        assert error.code == "rerank_bar"
    else:
        raise AssertionError("a net-loss rerank arm must fail the bar helper")
def test_shuffled_bar_fails_when_control_accidentally_wins():
    pair = {"fixed": [f"tg-{i}" for i in range(12)], "broken": [], "net": 12}
    try:
        recall_r5rr._assert_shuffled_fails_bar(pair)
    except TextGoldError as error:
        assert error.code == "shuffled_bar"
    else:
        raise AssertionError("a shuffled arm that meets the bar must fail")
    recall_r5rr._assert_shuffled_fails_bar({"fixed": [], "broken": ["tg-1"], "net": -1})


def test_forbidden_keys_are_key_only_and_fact_id_is_allowed():
    forbidden = set(recall_r5rr._FORBIDDEN_R5RR_KEYS)
    assert text_chunk_metrics._contains_forbidden_keys({"query": "synthetic secret"}, forbidden)
    assert not text_chunk_metrics._contains_forbidden_keys({"note": "synthetic secret"}, forbidden)
    assert not text_chunk_metrics._contains_forbidden_keys({"fact_id": FIRE_A, "fixed": [FIRE_A]}, forbidden)


def test_synthetic_artifact_has_three_arms_and_no_gold_keys():
    rankers = {
        "rerank": _ranker({FIRE_A: ["c-a"], FIRE_B: ["c-b"], FIRE_C: ["c-c"], HOLD_ID: []}),
        "no_rerank": _ranker({FIRE_A: ["c-a"], FIRE_B: ["c-b"], FIRE_C: [], HOLD_ID: []}),
        "shuffled": _ranker({FIRE_A: [], FIRE_B: [], FIRE_C: [], HOLD_ID: []}),
    }
    artifact = recall_r5rr.build_r5rr_artifact(
        gold=_gold(),
        index=_index_for_gold(),
        pins=_pins(),
        rankers=rankers,
        enforce_live_gates=False,
    )
    assert artifact["schema"] == recall_r5rr.CURVES_R5RR_SCHEMA
    assert artifact["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}
    assert artifact["cross_encoder"] == rerank.CROSS_ENCODER_ID
    assert [row["arm"] for row in artifact["series"]] == ["rerank", "no_rerank", "shuffled"]
    by_arm = {row["arm"]: row for row in artifact["series"]}
    assert by_arm["rerank"]["shipped"] is True
    assert by_arm["shuffled"]["shipped"] is False
    assert by_arm["no_rerank"]["shipped"] is False
    versus = artifact["versus_no_rerank"]
    assert versus["no_rerank"] == {"fixed": [], "broken": [], "net": 0}
    assert FIRE_C in versus["rerank"]["fixed"]
    assert versus["rerank"]["broken"] == []
    blob = json.dumps(artifact)
    assert NEEDLE_A not in blob
    assert not text_chunk_metrics._contains_forbidden_keys(artifact, set(recall_r5rr._FORBIDDEN_R5RR_KEYS))
    if recall_r5rr.GOLD_VALUE_GUARD_PATH.is_file():
        assert artifact["gold_value_guard"]["landed"] is True
        assert artifact["gold_value_guard"]["violations"] == 0
    else:
        assert artifact["gold_value_guard"]["landed"] is False


def test_agent_surface_unmoved():
    assert len(tool_schemas()) == 24
    digest = hashlib.sha256((_ROOT / "agent_tools.py").read_bytes()).hexdigest()
    assert digest == _AGENT_TOOLS_SHA256


def test_value_guard_runs_when_landed():
    if not recall_r5rr.GOLD_VALUE_GUARD_PATH.is_file():
        pytest.skip("gold_value_guard.py has not landed")
    artifact = {"note": "no gold here", "fixed": [FIRE_A]}
    result = recall_r5rr.apply_gold_value_guard(artifact)
    assert result["landed"] is True
    assert result["violations"] == 0


def test_value_guard_must_fire_on_gold_under_allowed_key():
    path = recall_r5rr.GOLD_VALUE_GUARD_PATH
    if not path.is_file():
        pytest.skip("gold_value_guard.py has not landed")
    spec = importlib.util.spec_from_file_location("dissolve_gold_value_guard_mf", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    gold = module.load_gold()
    planted = None
    for fact in module._facts(gold):
        for value in module._string_leaves(fact.get("needles")):
            if str(value).strip():
                planted = str(value)
                break
        if planted:
            break
    assert planted is not None
    assert module.count_violations({"note": planted}) >= 1
    assert module.count_violations({"note": "no gold here"}) == 0


def test_r5rr_live_emit_ident_bar_guards_and_v4_unmoved():
    v4_before = file_sha256(dense_d2.CURVES_V4_PATH)
    r1 = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.r1.json"
    r3 = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.r3.json"
    r1_before = file_sha256(r1) if r1.is_file() else None
    r3_before = file_sha256(r3) if r3.is_file() else None
    result = recall_r5rr.emit_r5rr_product()
    artifact = json.loads(Path(result["curves_path"]).read_text(encoding="utf-8"))
    by_arm = {row["arm"]: row for row in artifact["series"]}
    control = by_arm["no_rerank"]
    assert control["n_retrievable_at_k"] == {
        str(k): int(recall_r5rr.V4_HYBRID_N_RETRIEVABLE[str(k)])
        for k in text_chunk_metrics.RETRIEVAL_KS
    }
    assert control["n_must_refuse_at_k"] == {str(k): 29 for k in text_chunk_metrics.RETRIEVAL_KS}
    assert control["n_leaks_at_k"] == {str(k): 0 for k in text_chunk_metrics.RETRIEVAL_KS}
    versus = artifact["versus_no_rerank"]
    assert versus["no_rerank"]["fixed"] == []
    assert versus["no_rerank"]["broken"] == []
    assert versus["no_rerank"]["net"] == 0
    assert versus["shuffled"]["bar_met"] is False
    assert not recall_r5rr.meets_bar_at_5(versus["shuffled"]["fixed"], versus["shuffled"]["broken"])
    assert by_arm["rerank"]["n_must_refuse_at_k"] == {str(k): 29 for k in text_chunk_metrics.RETRIEVAL_KS}
    assert by_arm["shuffled"]["n_must_refuse_at_k"] == {str(k): 29 for k in text_chunk_metrics.RETRIEVAL_KS}
    assert artifact["n_held_out_facts"] == 29
    assert artifact["hybrid_weights"] == {"dense": 0.55, "sparse": 0.40}
    for arm in recall_r5rr.ARMS:
        for fact_id in by_arm[arm]["hits_at_5_ids"]:
            assert str(fact_id).startswith("tg-")
        for fact_id in versus[arm]["fixed"] + versus[arm]["broken"]:
            assert str(fact_id).startswith("tg-")
    assert not text_chunk_metrics._contains_forbidden_keys(artifact, set(recall_r5rr._FORBIDDEN_R5RR_KEYS))
    assert file_sha256(dense_d2.CURVES_V4_PATH) == v4_before == recall_r5rr.CURVES_V4_SHA256
    if r1_before is not None:
        assert file_sha256(r1) == r1_before
    if r3_before is not None:
        assert file_sha256(r3) == r3_before
    assert artifact["gold_value_guard"]["landed"] is True
    assert artifact["gold_value_guard"]["violations"] == 0
