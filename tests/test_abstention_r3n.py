"""R-3N rank-1 coverage localization. Fixtures + one live dest. No floor bind."""
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
    LITERATURE_SCHOLARLY_TOOLS,
    offered_tool_names,
    tool_schemas,
)
from dissolve import (
    abstention_a3,
    abstention_r3,
    abstention_r3n,
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

MODULE = Path(abstention_r3n.__file__).read_text(encoding="utf-8")
PAPER = "aa" * 32
HELD_PAPER = "bb" * 32
PIN_BEFORE = {
    GOLD_UNSEALED_PATH: GOLD_UNSEALED_SHA256,
    engine_e2e.CENSUS_PATH: t5_corpus_graph.CENSUS_SHA256,
    engine_e2e.STORE_PATH: t5_corpus_graph.STORE_SHA256,
    engine_e2e.MANIFEST_PATH: t5_corpus_graph.MANIFEST_SHA256,
    dense_d2.INDEX_GZIP_PATH: abstention_r3n.GZIP_SHA256,
    t5_corpus_graph.GRAPH_PATH: "3e7914dc0fe42b7e3a779e9a24ce2942b91f807645f74dee2e62197bebaf0c25",
    DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json": abstention_r3n.OFFDOMAIN_SHA256,
    DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json": abstention_r3n.ABSTENTION_V1_SHA256,
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
    assert floor == abstention_r3n.SHIPPED_FLOOR
    assert "promoted" not in json.loads(engine_e2e.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert not (DEFAULT_OUT_DIR / "GOLD.v2.json").is_file()


def _unit(first: float) -> list[float]:
    return [first] + [0.0] * 383


def _chunk(chunk_id: str, body: str, *, section: str = "methods") -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "d1",
        "paper_sha256": PAPER,
        "title": "Fixture",
        "body": body,
        "text": body,
        "section": section,
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
        "dense": {
            "model": engine_e2e.MINILM_ID,
            "dim": 384,
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "vectors": [_unit(1.0 - 0.1 * i) for i, _chunk in enumerate(chunks)],
            "refuse_rule": "sparse_gated",
        },
    }


def _fake_minilm():
    def fake(texts, model_name=None):
        return engine_e2e.MINILM_ID, [_unit(1.0) for _ in texts]

    return fake


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


def _write_battery(tmp_path: Path, fire: list[str], held: list[str], off: list[str]) -> dict:
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps(_gold(fire, held)) + "\n", encoding="utf-8")
    off_path = tmp_path / "OFFDOMAIN.queries.v1.json"
    off_path.write_text(json.dumps(_off_payload(off)) + "\n", encoding="utf-8")
    manifest = tmp_path / "INDEX.t5.unsealed.v1.json"
    shutil.copy(engine_e2e.MANIFEST_PATH, manifest)
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


def test_r3n_module_keep_out():
    lowered = MODULE.casefold()
    assert "sentence_transformers" not in lowered
    assert "urllib" not in lowered
    assert "requests" not in lowered
    assert "_write_manifest_abstention" not in MODULE
    assert "[:8]" not in MODULE
    assert "[:24]" not in MODULE
    assert "must_refuse[:" not in MODULE
    assert abstention_r3n.SPEC_SHA256 == (
        "7d925d2cf9956078b6dc34a86ed6290bd68d262b9a649b03b1ac7729ddc841aa"
    )
    assert abstention_r3n.NAMED_STATISTICS == (
        "rank1_coverage", "rank1_coverage_ratio", "rank1_rare_coverage", "rare_star",
    )
    assert set(abstention_r3n.NAMED_STATISTICS).isdisjoint(abstention_r3.NAMED_STATISTICS)
    assert abstention_r3n.EXPECTED_N_HELD == 29
    assert abstention_r3n.EXPECTED_N_HELD != abstention_r3n.EXPECTED_N_OFF


def test_r3n_strip_floor_pops_abstention():
    index = _index([_chunk("c-a", "polymer solvent screening")])
    index["abstention"] = {"floor": 0.35, "statistic": "query_idf_coverage"}
    stripped = abstention_r3n.strip_floor(index)
    assert "abstention" not in stripped
    assert "abstention" in index


def test_r3n_rank1_coverage_ident_search_index(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    index = abstention_r3n.strip_floor(_index([
        _chunk("c-a", "polymer solvent screening solubility methods"),
        _chunk("c-b", "zxqother token sequence methods"),
    ]))
    query = "polymer solvent screening solubility methods"
    rows = research._search_index(index, query, len(index["chunks"]), "hybrid")
    assert rows
    assert abstention_r3n.rank1_coverage(index, query) == float(rows[0]["query_idf_coverage"])
    assert rows[0]["chunk_id"] == "c-a"


def test_r3n_ratio_ident_coverage_star(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    index = abstention_r3n.strip_floor(_index([
        _chunk("c-a", "polymer solvent screening solubility methods"),
        _chunk("c-b", "polymer solvent screening"),
    ]))
    query = "polymer solvent screening solubility methods"
    top = abstention_r3n.rank1_coverage(index, query)
    star = research.coverage_star(index, query)
    want = 0.0 if star <= 0 else top / star
    assert abstention_r3n.rank1_coverage_ratio(index, query) == want


def test_r3n_rank1_rare_ident_same_chunk_id(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    index = abstention_r3n.strip_floor(_index([
        _chunk("c-a", "polymer solvent screening solubility methods"),
        _chunk("c-b", "polymer solvent screening"),
    ]))
    query = "polymer solvent screening solubility methods"
    rows = research._search_index(index, query, len(index["chunks"]), "hybrid")
    got = abstention_r3n.rank1_rare_coverage(index, query)
    chunks = list(index["chunks"])
    rare, rare_mass, n_docs, df, _sets = abstention_r3n._rare_query_tokens(chunks, query)
    chunk = next(item for item in chunks if item["chunk_id"] == rows[0]["chunk_id"])
    tokens = set(research._tokens(research.chunk_sparse_corpus(chunk)))
    want = abstention_r3n._rare_mass_on_tokens(rare, rare_mass, tokens, n_docs, df)
    assert got == want


def test_r3n_rare_star_does_not_load_minilm(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("rare_star must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    index = _index([_chunk("c-a", "polymer solvent screening solubility methods")])
    value = abstention_r3n.rare_star(index, "polymer solvent screening")
    assert value >= 0.0


def test_r3n_coverage_star_does_not_load_minilm(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("coverage_star must not load MiniLM")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    index = _index([_chunk("c-a", "polymer solvent screening solubility methods")])
    assert research.coverage_star(index, "polymer solvent screening") >= 0.0


def test_r3n_score_query_ident_named_scorers(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    index = abstention_r3n.strip_floor(_index([
        _chunk("c-a", "polymer solvent screening solubility methods"),
        _chunk("c-b", "polymer solvent screening"),
    ]))
    query = "polymer solvent screening solubility methods"
    scored = abstention_r3n.score_query(index, query)
    assert scored["rank1_coverage"] == abstention_r3n.rank1_coverage(index, query)
    assert scored["rank1_coverage_ratio"] == abstention_r3n.rank1_coverage_ratio(index, query)
    assert scored["rank1_rare_coverage"] == abstention_r3n.rank1_rare_coverage(index, query)
    assert scored["rare_star"] == abstention_r3n.rare_star(index, query)
    eligible = {
        "rank1_coverage": False,
        "rank1_coverage_ratio": False,
        "rank1_rare_coverage": False,
        "rare_star": True,
    }
    assert abstention_r3n.next_work_from_eligibility(eligible) == "live_floor_stands"
    eligible["rank1_coverage"] = True
    assert abstention_r3n.next_work_from_eligibility(eligible) == "owner_operating_point"


def test_r3n_emit_tmp_does_not_write_index(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _fake_minilm())
    fire = ["polymer solvent screening solubility methods"]
    held = ["polymer solvent screening solubility methods"]
    off = ["zxqtitania rutile grafting forcefield", "zxqtitania adsorption rutile"]
    paths = _write_battery(tmp_path, fire, held, off)
    index = _index([
        _chunk("c-a", "polymer solvent screening solubility methods"),
        _chunk("c-b", "polymer solvent screening"),
    ])
    before_man = file_sha256(paths["manifest"])
    planted_r1 = tmp_path / "CURVES.retrieval.abstention.r1.json"
    planted_r1.write_bytes(b'{"schema": "dissolve.retrieval.abstention.r1", "planted": true}\n')
    planted_r3 = tmp_path / "CURVES.retrieval.abstention.r3.json"
    planted_r3.write_bytes(b'{"schema": "dissolve.retrieval.abstention.r3", "planted": true}\n')
    r1_before = planted_r1.read_bytes()
    r3_before = planted_r3.read_bytes()
    result = abstention_r3n.emit_r3n_product(
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
    keys: set[str] = set()
    _walk_keys(curves, keys)
    assert "sparse_score" not in keys
    assert set(curves["statistics"]).isdisjoint(abstention_r3.NAMED_STATISTICS)
    assert curves["held_out_in_index"] == 0
    assert curves["r3_bound"] is False
    assert curves["r3n_bound"] is False
    assert curves["new_floor_shipped"] is False
    assert curves["r2_ran"] is False
    assert curves["query_idf_coverage_retired"] is False
    assert curves["m5_established"] is False
    assert curves["next_work"] in {"owner_operating_point", "live_floor_stands"}
    assert curves["statistics"]["rare_star"]["held_out"]["n"] == 1
    assert curves["r1_at_shipped"]["must_fire_keep"]["x"] == 216
    assert file_sha256(paths["manifest"]) == before_man
    assert planted_r1.read_bytes() == r1_before
    assert planted_r3.read_bytes() == r3_before
    assert Path(result["curves_path"]).name == "CURVES.retrieval.abstention.r3n.json"
    assert Path(result["figure_path"]).is_file()


def test_r3n_product_live_dest(monkeypatch):
    _assert_pins_unmoved()
    r3_path = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.r3.json"
    r1_path = DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.r1.json"
    man_before = file_sha256(engine_e2e.MANIFEST_PATH)
    gold_before = file_sha256(GOLD_UNSEALED_PATH)
    gzip_before = file_sha256(dense_d2.INDEX_GZIP_PATH)
    r3_before = file_sha256(r3_path)
    result = abstention_r3n.emit_r3n_product()
    curves = json.loads(Path(result["curves_path"]).read_text(encoding="utf-8"))
    keys: set[str] = set()
    _walk_keys(curves, keys)
    assert curves["schema"] == abstention_r3n.SCHEMA
    assert curves["spec_sha256"] == abstention_r3n.SPEC_SHA256
    for name in abstention_r3n.NAMED_STATISTICS:
        arm = curves["statistics"][name]
        assert arm["must_fire"]["n"] == 228
        assert arm["held_out"]["n"] == 29
        assert arm["offdomain"]["n"] == 24
        assert arm["held_out"]["n"] != 8
        assert len(arm["held_out"]["values"]) == 29
    assert set(curves["statistics"]).isdisjoint(abstention_r3.NAMED_STATISTICS)
    assert curves["next_work"] in {"owner_operating_point", "live_floor_stands"}
    assert curves["r3n_bound"] is False
    assert curves["query_idf_coverage_retired"] is False
    assert curves["m5_established"] is False
    assert "must_refuse_at_k" not in curves
    assert "sparse_score" not in keys
    assert "query" not in keys
    assert "fact_id" not in keys
    assert file_sha256(engine_e2e.MANIFEST_PATH) == man_before == abstention_r3n.MANIFEST_SHA256
    assert file_sha256(GOLD_UNSEALED_PATH) == gold_before
    assert file_sha256(dense_d2.INDEX_GZIP_PATH) == gzip_before
    assert file_sha256(r3_path) == r3_before
    r3_payload = json.loads(r3_path.read_text(encoding="utf-8"))
    assert r3_payload["schema"] == "dissolve.retrieval.abstention.r3"
    assert r3_payload["next_work"] == "R-3_next_candidate"
    r1_payload = json.loads(r1_path.read_text(encoding="utf-8"))
    assert r1_payload["schema"] == "dissolve.retrieval.abstention.r1"
    assert r1_payload["next_work"] == "R-3"
    assert r1_payload["at_shipped"]["must_fire_keep"]["x"] == 216
    assert r1_payload["at_shipped"]["held_out_abstention"]["x"] == 9
    assert r1_payload["at_shipped"]["offdomain_abstention"]["x"] == 20
    assert json.loads(engine_e2e.MANIFEST_PATH.read_text(encoding="utf-8"))["abstention"]["floor"] == (
        abstention_r3n.SHIPPED_FLOOR
    )
    source = Path(research.__file__).read_text(encoding="utf-8")
    assert "_HYBRID_DENSE_WEIGHT = 0.55\n" in source
    assert "_HYBRID_SPARSE_WEIGHT = 0.40\n" in source
    assert "if floor is not None and star < floor:" in source
    _assert_pins_unmoved()


def test_r3n_stays_off_agent_list_and_frozen_pins():
    names = _literature_names()
    assert names == set()
    assert LITERATURE_INGEST_TOOLS.isdisjoint(offered_tool_names())
    corpus = {"literature_mode": {"mode": "corpus"}}
    scholarly = {"literature_mode": {"mode": "scholarly"}}
    assert _literature_names(corpus) == set(LITERATURE_CORPUS_TOOLS)
    assert _literature_names(scholarly) == set(LITERATURE_SCHOLARLY_TOOLS)
    blocked = {
        "emit_r3n_product",
        "build_r3n_artifact",
        "render_r3n_figure",
        "emit_r3_product",
        "emit_r1_product",
        "promote_ingested_paper",
        "extract_t5_entity_graph",
    }
    for mode in (None, corpus, scholarly, {"literature_mode": {"mode": "off"}}):
        offered = {item["name"] for item in tool_schemas(mode)}
        assert LITERATURE_INGEST_TOOLS.isdisjoint(offered)
        assert blocked.isdisjoint(offered)
    assert blocked.isdisjoint(set(registry.BY_NAME))
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert research._HYBRID_DENSE_WEIGHT == 0.55
    assert research._HYBRID_SPARSE_WEIGHT == 0.40
    assert file_sha256(_ROOT / "src" / "dissolve" / "thermo.py") == (
        "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"
    )
    assert file_sha256(_ROOT / "src" / "dissolve" / "cosmo_logp.py") == (
        "49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766"
    )
    assert file_sha256(_ROOT / "agent_tools.py") == (
        "02259d2ab680d7613731013fc6f96d3b100739d0bed7f95d114bf208e1cee293"
    )
    for rel in (
        "src/dissolve/thermodynamics.py",
        "src/dissolve/contaminants.py",
        "src/dissolve/cosmo_logp.py",
        "src/dissolve/thermo.py",
        "agent_tools.py",
        "src/dissolve/research.py",
    ):
        assert not subprocess.check_output(["git", "diff", "HEAD", "--", rel], cwd=_ROOT)
    assert file_sha256(Path(thermodynamics._ASSET)) == (
        "4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc"
    )
    assert file_sha256(Path(contaminants._ASSET)) == (
        "866d769b6a140bf289c5036fd5c0d7d2b6f424cb7994e71c76e16a1a1d9a4c5f"
    )
    _assert_pins_unmoved()
