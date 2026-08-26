"""R-1 three-set coverage_star measurement. Fixtures + one live dest. No MiniLM."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import (
    LITERATURE_AGENT_TOOLS,
    LITERATURE_CORPUS_TOOLS,
    LITERATURE_INGEST_TOOLS,
    LITERATURE_MODE_SURFACE,
    LITERATURE_SCHOLARLY_TOOLS,
    offered_tool_names,
    tool_schemas,
)
from dissolve import (
    abstention_a3,
    abstention_r1,
    contaminants,
    dense_d2,
    engine_e2e,
    registry,
    research,
    t5_corpus_graph,
    thermodynamics,
)
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.gold_ensemble import file_sha256
from dissolve.text_chunk_metrics import GOLD_UNSEALED_PATH, GOLD_UNSEALED_SHA256
from dissolve.text_gold import DEFAULT_OUT_DIR, TextGoldError

MODULE = Path(abstention_r1.__file__).read_text(encoding="utf-8")
PAPER = "aa" * 32
HELD_PAPER = "bb" * 32
PIN_BEFORE = {
    GOLD_UNSEALED_PATH: GOLD_UNSEALED_SHA256,
    engine_e2e.CENSUS_PATH: t5_corpus_graph.CENSUS_SHA256,
    engine_e2e.STORE_PATH: t5_corpus_graph.STORE_SHA256,
    engine_e2e.MANIFEST_PATH: t5_corpus_graph.MANIFEST_SHA256,
    dense_d2.INDEX_GZIP_PATH: abstention_r1.GZIP_SHA256,
    t5_corpus_graph.GRAPH_PATH: "3e7914dc0fe42b7e3a779e9a24ce2942b91f807645f74dee2e62197bebaf0c25",
    DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json": abstention_r1.OFFDOMAIN_SHA256,
    DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json": abstention_r1.ABSTENTION_V1_SHA256,
    DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json": "2b5be504c4fac12af07b1cd366d2b290dedad1e8efbf52f2218d0e649c7c6718",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json": "8090143a69106ff5ba5db8bab70f7fbc7980fe4f54574d427c7815dca7379152",
    DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json": "53cabea779037236f8227c26b8d899d6cd4e444b1a29af3f7036594658d555b2",
    DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json": "1def352d26baa5bce481f8c5ca6b4b81ac41186471007b2976ee133b675610ad",
}


def _literature_names(session=None) -> set[str]:
    return {item["name"] for item in tool_schemas(session)} & set(LITERATURE_AGENT_TOOLS)


def _walk_keys(value, found: set[str]) -> None:
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            _walk_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_keys(item, found)


def _assert_pins_unmoved() -> None:
    for path, digest in PIN_BEFORE.items():
        assert file_sha256(path) == digest
    floor = json.loads(engine_e2e.MANIFEST_PATH.read_text(encoding="utf-8"))["abstention"]["floor"]
    assert floor == abstention_r1.SHIPPED_FLOOR
    assert "promoted" not in json.loads(engine_e2e.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert not (DEFAULT_OUT_DIR / "GOLD.v2.json").is_file()


def _chunk(chunk_id: str, body: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "d1",
        "paper_sha256": PAPER,
        "title": "Fixture",
        "body": body,
        "text": body,
        "section": "methods",
        "section_origin": "parser_supplied",
        "char_start": 0,
        "char_end": len(body),
        "page": 1,
    }


def _index(chunks: list[dict]) -> dict:
    return {
        "schema": research._INDEX_SCHEMA,
        "knowledgebase": research._PRODUCT_KNOWLEDGEBASE,
        "documents": [{"document_id": "d1", "sha256": PAPER, "title": "Fixture"}],
        "chunks": chunks,
        "dense": None,
    }


def _gold(fire: list[str], held: list[str]) -> dict:
    facts = []
    for query in fire:
        facts.append({"query": query, "paper_sha256": PAPER, "paper_status": "indexed"})
    for query in held:
        facts.append({"query": query, "paper_sha256": HELD_PAPER, "paper_status": "held_out"})
    return {
        "papers": [
            {"paper_sha256": PAPER, "paper_status": "indexed"},
            {"paper_sha256": HELD_PAPER, "paper_status": "held_out"},
        ],
        "facts": facts,
    }


def _off_payload(queries: list[str]) -> dict:
    return {
        "schema": "dissolve.offdomain.queries.v1",
        "set_digest": abstention_a3.SET_DIGEST,
        "queries": [
            {"id": f"od-fixture0001-{i:03d}", "query": query}
            for i, query in enumerate(queries, start=1)
        ],
    }


def _write_battery(tmp_path: Path, fire: list[str], held: list[str], off: list[str], *, floor: float) -> dict:
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps(_gold(fire, held)) + "\n", encoding="utf-8")
    off_path = tmp_path / "OFFDOMAIN.queries.v1.json"
    off_path.write_text(json.dumps(_off_payload(off)) + "\n", encoding="utf-8")
    manifest = tmp_path / "INDEX.t5.unsealed.v1.json"
    shutil.copy(engine_e2e.MANIFEST_PATH, manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["abstention"] = {
        **payload["abstention"],
        "floor": floor,
    }
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    census = tmp_path / "CENSUS.v3.json"
    shutil.copy(engine_e2e.CENSUS_PATH, census)
    store = tmp_path / "store.json"
    shutil.copy(engine_e2e.STORE_PATH, store)
    return {
        "gold": gold,
        "off": off_path,
        "manifest": manifest,
        "census": census,
        "store": store,
    }


def test_r1_module_keep_out():
    lowered = MODULE.casefold()
    assert "sentence_transformers" not in lowered
    assert "urllib" not in lowered
    assert "requests" not in lowered
    assert "emit_a2a4_product" not in MODULE
    assert "_write_manifest_abstention" not in MODULE
    assert "[:8]" not in MODULE
    assert "[:24]" not in MODULE
    assert "must_refuse[:" not in MODULE
    assert "GOLD.v2.json" in MODULE
    assert abstention_r1.SPEC_SHA256 == (
        "35f1385019a182700bbb4ba844d95c7ff3bf095b5cf839b819de99b241135fa1"
    )
    assert abstention_r1.EXPECTED_N_HELD == 29
    assert abstention_r1.EXPECTED_N_FIRE == 228
    assert abstention_r1.EXPECTED_N_OFF == 24
    assert abstention_r1.EXPECTED_N_HELD != abstention_r1.EXPECTED_N_OFF


def test_r1_overlap_fixture_is_r3_and_does_not_run_r2(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("R-1 must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    fire = [
        "polymer solvent screening solubility methods",
        "polymer solvent screening solubility polymer",
        "solvent screening solubility methods polymer",
    ]
    held = [
        "polymer solvent screening solubility methods",
        "polymer solvent screening solubility polymer",
        "solvent screening solubility methods polymer",
    ]
    off = ["zxqtitania rutile grafting forcefield", "zxqtitania adsorption rutile"]
    paths = _write_battery(tmp_path, fire, held, off, floor=0.35)
    index = _index([
        _chunk("c-a", "polymer solvent screening solubility methods"),
        _chunk("c-b", "polymer solvent screening solubility polymer"),
    ])
    before_man = file_sha256(paths["manifest"])
    result = abstention_r1.emit_r1_product(
        dest_dir=tmp_path,
        gold_path=paths["gold"],
        census_path=paths["census"],
        store_path=paths["store"],
        offdomain_path=paths["off"],
        manifest_path=paths["manifest"],
        index=index,
        fire_queries=fire,
        held_queries=held,
        offdomain_queries=off,
    )
    curves = json.loads(Path(result["curves_path"]).read_text(encoding="utf-8"))
    assert curves["held_out"]["n"] == 3
    assert curves["offdomain"]["n"] == 2
    assert curves["held_out"]["n"] != curves["offdomain"]["n"]
    assert curves["held_out"]["n"] != 8
    assert curves["next_work"] == "R-3"
    assert curves["r2_ran"] is False
    assert curves["new_floor_shipped"] is False
    assert curves["m5_established"] is False
    assert result["r2_ran"] is False
    assert file_sha256(paths["manifest"]) == before_man
    assert Path(result["figure_path"]).is_file()


def test_r1_held_below_fire_is_r2_eligible_and_does_not_run_r2(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("R-1 must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    fire = [
        "polymer solvent screening solubility methods",
        "polymer solvent screening solubility polymer",
        "solvent screening solubility methods polymer",
    ]
    held = [
        "polymer zxqheldaaa zxqheldbbb zxqheldccc",
        "solvent zxqheldddd zxqheldeee zxqheldfff",
        "screening zxqheldggg zxqheldhhh zxqheldiii",
    ]
    off = ["zxqtitania rutile grafting forcefield", "zxqtitania adsorption rutile"]
    paths = _write_battery(tmp_path, fire, held, off, floor=0.01)
    index = _index([
        _chunk("c-a", "polymer solvent screening solubility methods"),
        _chunk("c-b", "polymer solvent screening solubility polymer"),
    ])
    result = abstention_r1.emit_r1_product(
        dest_dir=tmp_path,
        gold_path=paths["gold"],
        census_path=paths["census"],
        store_path=paths["store"],
        offdomain_path=paths["off"],
        manifest_path=paths["manifest"],
        index=index,
        fire_queries=fire,
        held_queries=held,
        offdomain_queries=off,
    )
    curves = json.loads(Path(result["curves_path"]).read_text(encoding="utf-8"))
    assert curves["next_work"] == "R-2_eligible"
    assert curves["r2_ran"] is False
    assert curves["new_floor_shipped"] is False
    assert "recommended_floor" not in curves
    shipped = curves["at_shipped"]
    better = [
        row for row in curves["sweep"]
        if row["held_out_abstention"]["x"] > shipped["held_out_abstention"]["x"]
        and row["must_fire_keep"]["x"] >= shipped["must_fire_keep"]["x"]
    ]
    assert better
    assert abstention_r1.next_work_from_sweep(curves["sweep"]) == "R-2_eligible"


def test_r1_refuses_equal_n_held_and_off_on_non_canonical_counts():
    index = _index([_chunk("c-a", "polymer solvent screening")])
    with pytest.raises(TextGoldError, match="titania set"):
        abstention_r1.build_r1_artifact(
            index=index,
            fire_queries=["polymer solvent screening"],
            held_queries=["zxqtitania one", "zxqtitania two"],
            offdomain_queries=["zxqtitania one", "zxqtitania two"],
            shipped_floor=0.35,
            pins={"gold_sha256": "0" * 64},
        )


def test_r1_sweep_star_lt_floor_collapses_keep_at_high_floor():
    fire = [0.9, 0.8, 0.7]
    held = [0.85, 0.75]
    off = [0.0]
    row = abstention_r1.sweep_row(
        floor=1.1,
        fire_stars=fire,
        held_stars=held,
        off_stars=off,
        shipped_floor=0.35,
    )
    assert row["must_fire_keep"]["x"] == 0
    assert row["held_out_abstention"]["x"] == 2
    assert row["star_rule"] == "star < floor"


def test_r1_product_live_dest(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("R-1 must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    _assert_pins_unmoved()
    man_before = file_sha256(engine_e2e.MANIFEST_PATH)
    gold_before = file_sha256(GOLD_UNSEALED_PATH)
    gzip_before = file_sha256(dense_d2.INDEX_GZIP_PATH)
    off_before = file_sha256(abstention_a3.OFFDOMAIN_PATH)
    v1_before = file_sha256(DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json")
    result = abstention_r1.emit_r1_product()
    curves = json.loads(Path(result["curves_path"]).read_text(encoding="utf-8"))
    keys: set[str] = set()
    _walk_keys(curves, keys)
    assert curves["schema"] == abstention_r1.SCHEMA
    assert curves["spec_sha256"] == abstention_r1.SPEC_SHA256
    assert curves["statistic"] == "query_idf_coverage"
    assert curves["must_fire"]["n"] == 228
    assert curves["held_out"]["n"] == 29
    assert curves["offdomain"]["n"] == 24
    assert curves["held_out"]["n"] != 8
    assert curves["held_out"]["n"] != curves["offdomain"]["n"]
    assert len(curves["held_out"]["stars"]) == 29
    assert len(curves["must_fire"]["stars"]) == 228
    assert len(curves["offdomain"]["stars"]) == 24
    for name in ("must_fire", "held_out", "offdomain"):
        row = curves[name]
        assert row["min"] <= row["p5"] <= row["median"] <= row["p95"] <= row["max"]
    assert curves["next_work"] in {"R-3", "R-2_eligible"}
    assert curves["r2_ran"] is False
    assert curves["new_floor_shipped"] is False
    assert curves["m5_established"] is False
    assert curves["weights_retuned"] is False
    assert curves["held_out_in_index"] == 0
    assert curves["v4_must_refuse_is_not_abstention"] is True
    assert "must_refuse_at_k" not in curves
    assert curves["at_shipped"]["shipped"] is True
    assert curves["at_shipped"]["floor"] == abstention_r1.SHIPPED_FLOOR
    assert "held_out_abstention" in curves["at_shipped"]
    assert "offdomain_abstention" in curves["at_shipped"]
    assert "must_fire_keep" in curves["at_shipped"]
    assert curves["star_rule"] == "star < floor"
    assert any(row["star_rule"] == "star < floor" for row in curves["sweep"])
    assert "query" not in keys
    assert "needles" not in keys
    assert "fact_id" not in keys
    assert "evidence_quote" not in keys
    assert Path(result["figure_path"]).is_file()
    assert Path(result["curves_path"]).name == abstention_r1.CURVES_NAME
    assert file_sha256(engine_e2e.MANIFEST_PATH) == man_before == abstention_r1.MANIFEST_SHA256
    assert file_sha256(GOLD_UNSEALED_PATH) == gold_before
    assert file_sha256(dense_d2.INDEX_GZIP_PATH) == gzip_before == abstention_r1.GZIP_SHA256
    assert file_sha256(abstention_a3.OFFDOMAIN_PATH) == off_before == abstention_r1.OFFDOMAIN_SHA256
    assert file_sha256(DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json") == v1_before
    assert json.loads(engine_e2e.MANIFEST_PATH.read_text(encoding="utf-8"))["abstention"]["floor"] == (
        abstention_r1.SHIPPED_FLOOR
    )
    source = Path(research.__file__).read_text(encoding="utf-8")
    assert "_HYBRID_DENSE_WEIGHT = 0.55\n" in source
    assert "_HYBRID_SPARSE_WEIGHT = 0.40\n" in source
    _assert_pins_unmoved()


def test_r1_stays_off_agent_list_and_frozen_pins():
    names = _literature_names()
    assert names == set()
    assert LITERATURE_INGEST_TOOLS.isdisjoint(offered_tool_names())
    corpus = {"literature_mode": {"mode": "corpus"}}
    scholarly = {"literature_mode": {"mode": "scholarly"}}
    assert _literature_names(corpus) == set(LITERATURE_CORPUS_TOOLS)
    assert _literature_names(scholarly) == set(LITERATURE_SCHOLARLY_TOOLS)
    blocked = {
        "emit_r1_product",
        "build_r1_artifact",
        "render_r1_figure",
        "extract_t5_entity_graph",
        "derive_t5_multihop_pairs",
        "score_t5_graph_rag",
        "render_t5_graph_rag_figures",
        "promote_ingested_paper",
        "emit_t5_corpus_graph",
    }
    for mode in (None, corpus, scholarly, {"literature_mode": {"mode": "off"}}):
        offered = {item["name"] for item in tool_schemas(mode)}
        assert LITERATURE_INGEST_TOOLS.isdisjoint(offered)
        assert blocked.isdisjoint(offered)
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert blocked.isdisjoint(set(registry.BY_NAME))
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    thermo = file_sha256(_ROOT / "src" / "dissolve" / "thermo.py")
    leftover = file_sha256(_ROOT / "src" / "dissolve" / "cosmo_logp.py")
    agent = file_sha256(_ROOT / "agent_tools.py")
    assert thermo == "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"
    assert leftover == "49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766"
    assert agent == "762123b126e7a2a8005f8acd8887eba0eee4da7409de46ce4c404a3d970db18b"
    for rel in (
        "src/dissolve/thermodynamics.py",
        "src/dissolve/contaminants.py",
        "src/dissolve/cosmo_logp.py",
        "src/dissolve/thermo.py",
        "src/dissolve/t5_corpus_graph.py",
        "agent_tools.py",
    ):
        assert not subprocess.check_output(["git", "diff", "HEAD", "--", rel], cwd=_ROOT)
    assert file_sha256(Path(thermodynamics._ASSET)) == (
        "4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc"
    )
    assert file_sha256(Path(contaminants._ASSET)) == (
        "866d769b6a140bf289c5036fd5c0d7d2b6f424cb7994e71c76e16a1a1d9a4c5f"
    )
    _assert_pins_unmoved()
