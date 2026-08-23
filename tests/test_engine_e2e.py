"""ENGINE_E2E_SPEC.v1 E2E-0 / E2E-1. Fixtures. No MiniLM. No gold needles."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import engine_e2e, research, text_chunk_metrics, text_chunking, text_gold
from dissolve.gold_ensemble import CONTAMINANT_SHA256

IDX = "aa" * 32
HOLD = "bb" * 32
CONTAM = CONTAMINANT_SHA256
PLANT = "PLANTZXQTOKEN"


def _ci(rate: float) -> dict[str, float]:
    return {"lo": max(0.0, rate - 0.05), "hi": min(1.0, rate + 0.05)}


def _series(strategy: str, params: dict, bucket: str, n_facts: int, n_chunks: int, rates: dict[str, float]) -> dict:
    return {
        "strategy": strategy,
        "params": params,
        "bucket": bucket,
        "n_facts": n_facts,
        "n_chunks": n_chunks,
        "recall_at_k": rates,
        "recall_ci_at_k": {k: _ci(v) for k, v in rates.items()},
    }


def _rates(r5: float) -> dict[str, float]:
    return {"1": r5 - 0.1, "3": r5 - 0.05, "5": r5, "10": min(1.0, r5 + 0.05), "20": min(1.0, r5 + 0.08)}


def _artifact() -> dict:
    t5 = {"target": 1400}
    return {
        "n_papers": 21,
        "n_facts": 257,
        "n_indexed_papers": 19,
        "n_indexed_facts": 228,
        "n_held_out_facts": 29,
        "series": [
            _series("T0", {"target": 1400, "overlap": 180}, "all", 228, 10, _rates(0.79)),
            _series("T1", {"size": 2000, "overlap_frac": 0.25}, "all", 228, 8, _rates(0.846)),
            _series("T5", t5, "all", 228, 4, _rates(0.855)),
            _series("T6", {"percentile": 95}, "all", 228, 7, _rates(0.776)),
            _series("T5", t5, "<200", 37, 4, _rates(0.81)),
            _series("T5", t5, "200-600", 163, 4, _rates(0.86)),
            _series("T5", t5, "600-1500", 27, 4, _rates(0.70)),
            _series("T5", t5, ">1500", 1, 4, _rates(1.0)),
            _series("T2", {"size": 800, "overlap_frac": 0.0}, "all", 228, 12, _rates(0.60)),
        ],
    }


def _indexed_canonical() -> dict:
    prefix = "Intro heading. "
    body = f"{PLANT} lives in one block. " + ("word " * 320)
    suffix = "Next block stays whole."
    text = prefix + body + suffix
    start_a = 0
    end_a = len(prefix)
    start_b = end_a
    end_b = start_b + len(body)
    start_c = end_b
    end_c = len(text)
    return {
        "source_pdf_sha256": IDX,
        "parser_backend": "docling",
        "canonical_text": text,
        "blocks": [
            {
                "block_id": "b-head",
                "char_start": start_a,
                "char_end": end_a,
                "kind": "heading",
                "page": 1,
                "nearest_preceding_heading": ["Methods"],
                "nearest_preceding_heading_origin": "parser_supplied",
            },
            {
                "block_id": "b-plant",
                "char_start": start_b,
                "char_end": end_b,
                "kind": "paragraph",
                "page": 1,
                "nearest_preceding_heading": ["Methods"],
                "nearest_preceding_heading_origin": "parser_supplied",
            },
            {
                "block_id": "b-tail",
                "char_start": start_c,
                "char_end": end_c,
                "kind": "paragraph",
                "page": 2,
                "nearest_preceding_heading": ["Results"],
                "nearest_preceding_heading_origin": "inherited_from_stack",
            },
        ],
        "tables": [],
    }


def _held_canonical() -> dict:
    text = "held out paper must not be stored"
    return {
        "source_pdf_sha256": HOLD,
        "parser_backend": "docling",
        "canonical_text": text,
        "blocks": [{
            "block_id": "h1",
            "char_start": 0,
            "char_end": len(text),
            "kind": "paragraph",
            "page": 1,
            "nearest_preceding_heading": [],
            "nearest_preceding_heading_origin": "parser_supplied",
        }],
        "tables": [],
    }


def _gold() -> dict:
    return {
        "papers": [
            {"paper_sha256": IDX, "paper_status": "indexed"},
            {"paper_sha256": HOLD, "paper_status": "held_out"},
        ],
    }


def _census() -> dict:
    return {
        "papers": [
            {"sha256": IDX, "status": "indexed"},
            {"sha256": HOLD, "status": "held_out"},
            {"sha256": CONTAM, "status": "held_out"},
        ],
    }


def test_palettes_do_not_share_a_colour():
    assert engine_e2e.palettes_are_disjoint()
    assert set(engine_e2e.STRATEGY_COLORS) == {"T0", "T1", "T5", "T6"}
    assert set(engine_e2e.BUCKET_COLORS) == set(text_gold.SPAN_BUCKETS)


def test_figure_title_uses_indexed_counts_not_leaked_21_257():
    title = engine_e2e.figure_suptitle(_artifact())
    assert title == "19 indexed papers, 228 must-fire facts"
    assert "21" not in title
    assert "257" not in title
    assert "29" not in title


def test_right_panel_is_t5_only_and_named():
    rows = engine_e2e.t5_bucket_series(_artifact())
    assert {row["bucket"] for row in rows} == set(text_gold.SPAN_BUCKETS)
    assert all(row["strategy"] == "T5" for row in rows)
    assert all(row["params"] == {"target": 1400} for row in rows)
    named = engine_e2e.named_strategy_series(_artifact())
    assert {row["strategy"] for row in named} == {"T0", "T1", "T5", "T6"}
    assert engine_e2e.T5_RIGHT_TITLE == "T5 block-pack, target 1400"


def test_render_refuses_protected_v3_png(tmp_path):
    import pytest
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e.render_strategies_spans_png(
            _artifact(), text_chunk_metrics.CURVES_V3_PNG_PATH,
        )
    assert error.value.code == "protected_persist"
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e.render_strategies_spans_png(
            _artifact(), engine_e2e.CURVES_T5_LEFTOVER_PNG_PATH,
        )
    assert error.value.code == "protected_persist"
    dest = tmp_path / "CURVES.retrieval.v3.strategies_spans.png"
    engine_e2e.render_strategies_spans_png(_artifact(), dest)
    assert dest.is_file()
    assert dest.stat().st_size > 1000


def test_store_chunk_id_is_paper_and_ordinal_not_content():
    first = engine_e2e.t5_store_chunk_id(IDX, 1)
    second = engine_e2e.t5_store_chunk_id(IDX, 2)
    assert first == f"T5-{IDX[:12]}-0001"
    assert second != first
    assert PLANT not in first


def test_store_persists_bodies_and_excludes_holdout(tmp_path):
    canon = _indexed_canonical()
    packed = text_chunking.chunk_t5(canon, target=text_chunking.T5_TARGET)
    assert packed
    gold_path = tmp_path / "gold.json"
    census_path = tmp_path / "census.json"
    spec_path = tmp_path / "spec.md"
    gold_path.write_text("{}\n")
    census_path.write_text("{}\n")
    spec_path.write_text("e2e fixture\n")
    dest = tmp_path / "CHUNKS.t5.indexed.unsealed.v1.json"
    curves = {
        "series": [{
            "strategy": "T5",
            "params": {"target": 1400},
            "bucket": "all",
            "n_chunks": len(packed),
        }],
    }
    result = engine_e2e.emit_e2e_1_store(
        gold=_gold(),
        gold_path=gold_path,
        census=_census(),
        census_path=census_path,
        curves=curves,
        dest=dest,
        canonicals=[canon],
        expected_n_indexed=1,
        expected_n_chunks=len(packed),
        spec_path=spec_path,
        skip_pin_check=True,
    )
    store = json.loads(dest.read_text())
    assert store["schema"] == "dissolve.t5-chunk-store.unsealed.v1"
    assert store["n_chunks"] == len(packed) == result["n_chunks"]
    assert store["indexed_paper_sha256"] == [IDX]
    assert HOLD not in store["indexed_paper_sha256"]
    assert CONTAM not in json.dumps(store)
    text = canon["canonical_text"]
    origins = set()
    for row in store["chunks"]:
        assert row["body"] == text[row["char_start"]:row["char_end"]]
        assert row["paper_sha256"] == IDX
        assert row["chunk_id"] == engine_e2e.t5_store_chunk_id(IDX, row["ordinal"])
        assert row["section_origin"] in {"parser_supplied", "inherited_from_stack"}
        origins.add(row["section_origin"])
        assert row["block_ids"]
        assert "fact_id" not in row
        assert "needles" not in row
        assert "query" not in row
    assert "parser_supplied" in origins
    assert "inherited_from_stack" in origins
    assert any(PLANT in row["body"] for row in store["chunks"])
    blob = json.dumps(store)
    assert "fact_id" not in blob
    assert "needles" not in blob
    assert '"query"' not in blob


def test_store_refuses_a_held_out_canonical():
    import pytest
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e.build_t5_store_chunks(
            [_indexed_canonical(), _held_canonical()],
            indexed_shas={IDX},
            paper_order=[IDX],
            forbidden_shas={HOLD, CONTAM},
        )
    assert error.value.code == "unexpected_paper"


def test_store_refuses_sha_set_mismatch(tmp_path):
    import pytest
    gold_path = tmp_path / "gold.json"
    census_path = tmp_path / "census.json"
    spec_path = tmp_path / "spec.md"
    gold_path.write_text("{}\n")
    census_path.write_text("{}\n")
    spec_path.write_text("x\n")
    census = {"papers": [{"sha256": "cc" * 32, "status": "indexed"}]}
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e.emit_e2e_1_store(
            gold=_gold(),
            gold_path=gold_path,
            census=census,
            census_path=census_path,
            curves={"series": []},
            dest=tmp_path / "store.json",
            canonicals=[_indexed_canonical()],
            expected_n_indexed=1,
            expected_n_chunks=1,
            spec_path=spec_path,
            skip_pin_check=True,
        )
    assert error.value.code == "index_sha_mismatch"


def test_store_refuses_engine_chunks_name(tmp_path):
    import pytest
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e.emit_e2e_1_store(
            gold=_gold(),
            gold_path=tmp_path / "g.json",
            census=_census(),
            census_path=tmp_path / "c.json",
            curves={"series": []},
            dest=tmp_path / "CHUNKS.engine.v1.json",
            canonicals=[_indexed_canonical()],
            expected_n_indexed=1,
            expected_n_chunks=1,
            spec_path=tmp_path / "s.md",
            skip_pin_check=True,
        )
    assert error.value.code == "protected_persist"


def test_n_chunks_mismatch_is_loud(tmp_path):
    import pytest
    canon = _indexed_canonical()
    packed = text_chunking.chunk_t5(canon, target=text_chunking.T5_TARGET)
    gold_path = tmp_path / "gold.json"
    census_path = tmp_path / "census.json"
    spec_path = tmp_path / "spec.md"
    gold_path.write_text("{}\n")
    census_path.write_text("{}\n")
    spec_path.write_text("x\n")
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e.emit_e2e_1_store(
            gold=_gold(),
            gold_path=gold_path,
            census=_census(),
            census_path=census_path,
            curves={"series": [{
                "strategy": "T5", "params": {"target": 1400},
                "bucket": "all", "n_chunks": len(packed),
            }]},
            dest=tmp_path / "store.json",
            canonicals=[canon],
            expected_n_indexed=1,
            expected_n_chunks=len(packed) + 5,
            spec_path=spec_path,
            skip_pin_check=True,
        )
    assert error.value.code == "n_chunks_mismatch"


def test_e2e2_live_index_matches_store_sha_set_and_retrieves_plant(tmp_path):
    canon = _indexed_canonical()
    packed = text_chunking.chunk_t5(canon, target=text_chunking.T5_TARGET)
    gold_path = tmp_path / "gold.json"
    census_path = tmp_path / "census.json"
    spec_path = tmp_path / "spec.md"
    gold_path.write_text(json.dumps(_gold()) + "\n")
    census_path.write_text(json.dumps(_census()) + "\n")
    spec_path.write_text("e2e2 fixture\n")
    store_dest = tmp_path / "store.json"
    engine_e2e.emit_e2e_1_store(
        gold=_gold(),
        gold_path=gold_path,
        census=_census(),
        census_path=census_path,
        curves={"series": [{
            "strategy": "T5", "params": {"target": 1400},
            "bucket": "all", "n_chunks": len(packed),
        }]},
        dest=store_dest,
        canonicals=[canon],
        expected_n_indexed=1,
        expected_n_chunks=len(packed),
        spec_path=spec_path,
        skip_pin_check=True,
    )
    manifest = tmp_path / "INDEX.t5.unsealed.v1.json"
    result = engine_e2e.emit_e2e_2(
        store_path=store_dest,
        gold_path=gold_path,
        census_path=census_path,
        dest=manifest,
        index_home=tmp_path / "indexes",
        expected_n_indexed=1,
        expected_n_chunks=len(packed),
        spec_path=spec_path,
        skip_pin_check=True,
    )
    payload = json.loads(manifest.read_text())
    assert payload["schema"] == "dissolve.t5-index-manifest.unsealed.v1"
    assert payload["knowledgebase"] == "t5-indexed-unsealed"
    assert payload["indexed_paper_sha256"] == [IDX]
    assert HOLD not in payload["indexed_paper_sha256"]
    assert payload["embedder_in_index"] is False
    assert payload["dense"] is None
    assert "recall_at_k" not in payload
    assert result["planted_in_top5"] is True
    import os
    previous = os.environ.get("DISSOLVE_RESEARCH_HOME")
    os.environ["DISSOLVE_RESEARCH_HOME"] = str(tmp_path / "indexes")
    try:
        live = research._load_index("t5-indexed-unsealed")
    finally:
        if previous is None:
            os.environ.pop("DISSOLVE_RESEARCH_HOME", None)
        else:
            os.environ["DISSOLVE_RESEARCH_HOME"] = previous
    assert {row["sha256"] for row in live["documents"]} == {IDX}
    assert len(live["chunks"]) == len(packed)
    assert live["dense"] is None
    top = engine_e2e.planted_sparse_top5(live, chunk_id=live["chunks"][0]["chunk_id"])
    assert live["chunks"][0]["chunk_id"] in top


def test_e2e2_refuses_user_library():
    import pytest
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e.store_to_literature_index(
            {
                "n_chunks": 1,
                "indexed_paper_sha256": [IDX],
                "chunks": [{
                    "chunk_id": "T5-aa-0001",
                    "paper_sha256": IDX,
                    "body": "x",
                    "char_start": 0,
                    "char_end": 1,
                }],
            },
            knowledgebase="user-library",
        )
    assert error.value.code == "protected_persist"


def _fake_minilm(seen):
    def fake(texts, model_name=None):
        seen.extend(list(texts))
        return engine_e2e.MINILM_ID, [[1.0] + [0.0] * 383 for _ in texts]
    return fake


def test_e2e3_embeds_rebound_not_body():
    seen = []
    index = engine_e2e.store_to_literature_index({
        "n_chunks": 1,
        "indexed_paper_sha256": [IDX],
        "chunks": [{
            "chunk_id": engine_e2e.t5_store_chunk_id(IDX, 1),
            "paper_sha256": IDX,
            "body": "BODYONLY TOKEN",
            "char_start": 0,
            "char_end": 14,
            "body_plus_rebound": f"BODYONLY TOKEN {engine_e2e.REBOUND_NOTE_TOKEN}",
            "page": 1,
            "section": "",
            "section_origin": "inherited_from_stack",
            "kind": "table",
        }],
    })
    texts = engine_e2e.embed_inputs_for_index(index)
    assert engine_e2e.REBOUND_NOTE_TOKEN in texts[0]
    assert engine_e2e.REBOUND_NOTE_TOKEN not in index["chunks"][0]["body"]
    embedded = engine_e2e.embed_t5_index(index, embedder=_fake_minilm(seen))
    assert engine_e2e.REBOUND_NOTE_TOKEN in seen[0]
    assert len(embedded["dense"]["vectors"]) == 1
    assert embedded["dense"]["dim"] == 384
    assert embedded["dense"]["chunk_ids"] == [index["chunks"][0]["chunk_id"]]
    assert embedded["dense"]["model"] == engine_e2e.MINILM_ID


def test_e2e3_hybrid_stops_raising(monkeypatch, tmp_path):
    import os
    from dissolve.contracts import parse_tool_result
    canon = _indexed_canonical()
    packed = text_chunking.chunk_t5(canon, target=text_chunking.T5_TARGET)
    gold_path = tmp_path / "gold.json"
    census_path = tmp_path / "census.json"
    spec_path = tmp_path / "spec.md"
    gold_path.write_text(json.dumps(_gold()) + "\n")
    census_path.write_text(json.dumps(_census()) + "\n")
    spec_path.write_text("e2e3 fixture\n")
    store_dest = tmp_path / "store.json"
    engine_e2e.emit_e2e_1_store(
        gold=_gold(),
        gold_path=gold_path,
        census=_census(),
        census_path=census_path,
        curves={"series": [{
            "strategy": "T5", "params": {"target": 1400},
            "bucket": "all", "n_chunks": len(packed),
        }]},
        dest=store_dest,
        canonicals=[canon],
        expected_n_indexed=1,
        expected_n_chunks=len(packed),
        spec_path=spec_path,
        skip_pin_check=True,
    )
    manifest = tmp_path / "INDEX.t5.unsealed.v1.json"
    engine_e2e.emit_e2e_2(
        store_path=store_dest,
        gold_path=gold_path,
        census_path=census_path,
        dest=manifest,
        index_home=tmp_path / "indexes",
        expected_n_indexed=1,
        expected_n_chunks=len(packed),
        spec_path=spec_path,
        skip_pin_check=True,
    )
    seen = []
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm(seen))
    result = engine_e2e.emit_e2e_3(
        index_home=tmp_path / "indexes",
        manifest_path=manifest,
        expected_n_chunks=len(packed),
        skip_pin_check=True,
    )
    assert result["n_vectors"] == len(packed)
    assert result["dim"] == 384
    assert result["hybrid_ok"] is True
    updated = json.loads(manifest.read_text())
    assert updated["embedder_in_index"] is True
    assert updated["dense"]["n_vectors"] == len(packed)
    assert "recall_at_k" not in updated
    assert "delta" not in updated
    previous = os.environ.get("DISSOLVE_RESEARCH_HOME")
    os.environ["DISSOLVE_RESEARCH_HOME"] = str(tmp_path / "indexes")
    try:
        hybrid = parse_tool_result(research.search_literature_corpus(
            "E2E3HYBRIDPROBE", knowledgebase="t5-indexed-unsealed", retrieval_mode="hybrid",
        ))
        dense = parse_tool_result(research.search_literature_corpus(
            "E2E3DENSEPROBE", knowledgebase="t5-indexed-unsealed", retrieval_mode="dense",
        ))
    finally:
        if previous is None:
            os.environ.pop("DISSOLVE_RESEARCH_HOME", None)
        else:
            os.environ["DISSOLVE_RESEARCH_HOME"] = previous
    assert hybrid["data"]["success"] is True
    assert dense["data"]["success"] is True
    assert hybrid["data"].get("error_code") != "dense_index_unavailable"
    assert dense["data"].get("error_code") != "dense_index_unavailable"


def test_page_range_when_pack_crosses_a_page():
    canon = _indexed_canonical()
    chunks = engine_e2e.build_t5_store_chunks(
        [canon],
        indexed_shas={IDX},
        paper_order=[IDX],
        forbidden_shas={HOLD, CONTAM},
    )
    assert chunks
    pages = {row["page"] for row in chunks}
    assert pages & {1, 2, "1-2"}
