"""E2E-5 incremental ingest. Fixtures. No MiniLM. No gold needles in src asserts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import dense_d3, engine_e2e, engine_e2e5, research, text_chunk_metrics, text_gold
from dissolve.gold_ensemble import CENSUS_V3_SHA256, CONTAMINANT_SHA256, file_sha256

IDX = "aa" * 32
HOLD = "bb" * 32
CONTAM = CONTAMINANT_SHA256
PLANT = "PLANTZXQTOKEN"
NEW_PLANT = "NEWZXQINGESTTOKEN"
HOLD_TOKEN = "HOLDZXQREFUSETOKEN"


def _pdf_bytes(tag: str) -> bytes:
    return b"%PDF-1.4\n" + tag.encode("utf-8") + b"\n%%EOF\n"


def _write_pdf(path: Path, tag: str) -> Path:
    path.write_bytes(_pdf_bytes(tag))
    return path


def _canonical(paper_sha: str, token: str) -> dict:
    prefix = "Intro heading. "
    body = f"{token} lives in one block. " + ("word " * 320)
    suffix = "Next block stays whole."
    text = prefix + body + suffix
    start_a = 0
    end_a = len(prefix)
    start_b = end_a
    end_b = start_b + len(body)
    start_c = end_b
    end_c = len(text)
    return {
        "source_pdf_sha256": paper_sha,
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


def _gold() -> dict:
    return {
        "papers": [
            {"paper_sha256": IDX, "paper_status": "indexed"},
            {"paper_sha256": HOLD, "paper_status": "held_out"},
        ],
        "facts": [
            {
                "fact_id": "f-fire",
                "paper_sha256": IDX,
                "paper_status": "indexed",
                "query": f"where is {PLANT}",
                "needles": {"subject": PLANT},
            },
            {
                "fact_id": "f-hold",
                "paper_sha256": HOLD,
                "paper_status": "held_out",
                "query": f"where is {HOLD_TOKEN}",
                "needles": {"subject": HOLD_TOKEN},
            },
        ],
    }


def _census() -> dict:
    return {
        "papers": [
            {"filename": "indexed.pdf", "sha256": IDX, "status": "indexed"},
            {"filename": "held.pdf", "sha256": HOLD, "status": "held_out"},
            {"filename": "contam.pdf", "sha256": CONTAM, "status": "held_out"},
        ],
        "counts_by_status": {"indexed": 1, "held_out": 2},
    }


def _store() -> dict:
    chunks = engine_e2e.build_t5_store_chunks(
        [_canonical(IDX, PLANT)],
        indexed_shas={IDX},
        paper_order=[IDX],
        forbidden_shas={HOLD, CONTAM},
    )
    return {
        "schema": engine_e2e.STORE_SCHEMA,
        "n_chunks": len(chunks),
        "indexed_paper_sha256": [IDX],
        "ingested_paper_sha256": [],
        "chunks": chunks,
    }


def _fake_embedder(seen: list):
    def fake(texts, model_name=None):
        seen.extend(list(texts))
        vectors = []
        for index, text in enumerate(texts):
            row = [0.0] * 384
            row[0] = float(len(text) + index + 1)
            vectors.append(row)
        return engine_e2e.MINILM_ID, vectors
    return fake


def _boom(*_args, **_kwargs):
    raise AssertionError("E2E-5 unit tests must not load MiniLM")


def test_second_ingest_is_digest_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    seen = []
    pdf = _write_pdf(tmp_path / "paper.pdf", "new-one")
    sha = engine_e2e5.paper_bytes_sha256(pdf)
    dest_store = tmp_path / "store.json"
    dest_census = tmp_path / "census.json"
    first = engine_e2e5.ingest_one_paper(
        pdf,
        gold=_gold(),
        census=_census(),
        store=_store(),
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(sha, NEW_PLANT),
        embedder=_fake_embedder(seen),
    )
    assert first["noop"] is False
    assert first["chunks_added"] > 0
    assert first["census_status"] == engine_e2e5.INGESTED_STATUS
    assert first["store_sha256_after"] != first["store_sha256_before"]
    digest = file_sha256(dest_store)
    second = engine_e2e5.ingest_one_paper(
        pdf,
        gold=_gold(),
        census=json.loads(dest_census.read_text()),
        store=json.loads(dest_store.read_text()),
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(sha, NEW_PLANT),
        embedder=_fake_embedder(seen),
    )
    assert second["noop"] is True
    assert second["chunks_added"] == 0
    assert second["vectors_embedded"] == 0
    assert file_sha256(dest_store) == digest
    assert second["store_sha256_before"] == second["store_sha256_after"] == digest


def test_identity_is_sha_not_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    seen = []
    payload = "same-bytes"
    first_pdf = _write_pdf(tmp_path / "alpha.pdf", payload)
    sha = engine_e2e5.paper_bytes_sha256(first_pdf)
    dest_store = tmp_path / "store.json"
    dest_census = tmp_path / "census.json"
    engine_e2e5.ingest_one_paper(
        first_pdf,
        gold=_gold(),
        census=_census(),
        store=_store(),
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(sha, NEW_PLANT),
        embedder=_fake_embedder(seen),
    )
    digest = file_sha256(dest_store)
    n_seen = len(seen)
    renamed = tmp_path / "beta.pdf"
    renamed.write_bytes(first_pdf.read_bytes())
    again = engine_e2e5.ingest_one_paper(
        renamed,
        gold=_gold(),
        census=json.loads(dest_census.read_text()),
        store=json.loads(dest_store.read_text()),
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(sha, NEW_PLANT),
        embedder=_fake_embedder(seen),
    )
    assert again["noop"] is True
    assert again["paper_sha256"] == sha
    assert file_sha256(dest_store) == digest
    assert len(seen) == n_seen
    other = _write_pdf(tmp_path / "alpha.pdf", "different-bytes")
    other_sha = engine_e2e5.paper_bytes_sha256(other)
    assert other_sha != sha
    third = engine_e2e5.ingest_one_paper(
        other,
        gold=_gold(),
        census=json.loads(dest_census.read_text()),
        store=json.loads(dest_store.read_text()),
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(other_sha, "OTHERZXQTOKEN word " * 80),
        embedder=_fake_embedder(seen),
    )
    assert third["noop"] is False
    assert third["paper_sha256"] == other_sha
    assert file_sha256(dest_store) != digest
    assert len(seen) > n_seen


def test_does_not_reembed_existing_chunk_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    seen = []
    store = _store()
    indexed_ids = [row["chunk_id"] for row in store["chunks"]]
    pdf = _write_pdf(tmp_path / "new.pdf", "embed-only-new")
    sha = engine_e2e5.paper_bytes_sha256(pdf)
    dest_store = tmp_path / "store.json"
    dest_census = tmp_path / "census.json"
    result = engine_e2e5.ingest_one_paper(
        pdf,
        gold=_gold(),
        census=_census(),
        store=store,
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(sha, NEW_PLANT),
        embedder=_fake_embedder(seen),
    )
    payload = json.loads(dest_store.read_text())
    pending_ids = payload["pending_dense"]["chunk_ids"]
    assert result["vectors_embedded"] == len(pending_ids) == result["chunks_added"]
    assert result["vectors_embedded"] == len(seen)
    assert not set(indexed_ids) & set(pending_ids)
    assert any(NEW_PLANT in text for text in seen)
    assert all(PLANT not in text for text in seen)
    first_vectors = json.dumps(payload["pending_dense"]["vectors"])
    engine_e2e5.ingest_one_paper(
        pdf,
        gold=_gold(),
        census=json.loads(dest_census.read_text()),
        store=payload,
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(sha, NEW_PLANT),
        embedder=_fake_embedder(seen),
    )
    again = json.loads(dest_store.read_text())
    assert json.dumps(again["pending_dense"]["vectors"]) == first_vectors
    assert len(seen) == result["vectors_embedded"]


def test_new_paper_is_ingested_not_indexed(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    pdf = _write_pdf(tmp_path / "new.pdf", "not-indexed")
    sha = engine_e2e5.paper_bytes_sha256(pdf)
    dest_store = tmp_path / "store.json"
    dest_census = tmp_path / "census.json"
    base = _store()
    engine_e2e5.ingest_one_paper(
        pdf,
        gold=_gold(),
        census=_census(),
        store=base,
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(sha, HOLD_TOKEN),
        embedder=_fake_embedder([]),
    )
    census = json.loads(dest_census.read_text())
    store = json.loads(dest_store.read_text())
    assert engine_e2e5.census_status_for(census, sha) == engine_e2e5.INGESTED_STATUS
    assert sha in store["ingested_paper_sha256"]
    assert sha not in store["indexed_paper_sha256"]
    search = engine_e2e5.searchable_index(store, census)
    assert {row["paper_sha256"] for row in search["chunks"]} == {IDX}
    assert len(search["chunks"]) == len(base["chunks"])
    leaks = engine_e2e5.holdout_n_leaks(search, _gold())
    assert leaks["no_leaks_at_every_k"] is True
    assert leaks["n_held_out_facts"] == 1
    assert leaks["n_leaks_at_k"] == {str(k): 0 for k in text_chunk_metrics.RETRIEVAL_KS}


def test_held_out_sha_still_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    pdf = _write_pdf(tmp_path / "secret.pdf", "held-bytes")
    sha = engine_e2e5.paper_bytes_sha256(pdf)
    gold = _gold()
    gold["papers"].append({"paper_sha256": sha, "paper_status": "held_out"})
    census = _census()
    census["papers"].append({"filename": "secret.pdf", "sha256": sha, "status": "held_out"})
    dest_store = tmp_path / "store.json"
    dest_census = tmp_path / "census.json"
    dest_store.write_text(engine_e2e5.dump_store(_store()))
    before = file_sha256(dest_store)
    import pytest
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e5.ingest_one_paper(
            pdf,
            gold=gold,
            census=census,
            store=_store(),
            dest_store=dest_store,
            dest_census=dest_census,
            canonical=_canonical(sha, HOLD_TOKEN),
            embedder=_fake_embedder([]),
        )
    assert error.value.code == "held_out_in_store"
    assert file_sha256(dest_store) == before
    import pytest as _pytest
    with _pytest.raises(text_gold.TextGoldError) as chunk_error:
        engine_e2e.build_t5_store_chunks(
            [_canonical(sha, HOLD_TOKEN)],
            indexed_shas={sha},
            paper_order=[sha],
            forbidden_shas=engine_e2e5.forbidden_shas(gold, census),
        )
    assert chunk_error.value.code == "held_out_in_store"


def test_refuses_published_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    pdf = _write_pdf(tmp_path / "x.pdf", "protect")
    sha = engine_e2e5.paper_bytes_sha256(pdf)
    import pytest
    with pytest.raises(text_gold.TextGoldError) as error:
        engine_e2e5.ingest_one_paper(
            pdf,
            gold=_gold(),
            census=_census(),
            store=_store(),
            dest_store=engine_e2e.STORE_PATH,
            dest_census=tmp_path / "census.json",
            canonical=_canonical(sha, NEW_PLANT),
            embedder=_fake_embedder([]),
        )
    assert error.value.code == "protected_persist"


def test_holdout_guard_after_ingest_on_published_split(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", _boom)
    gold_path = text_chunk_metrics.GOLD_UNSEALED_PATH
    store_path = engine_e2e.STORE_PATH
    census_path = engine_e2e.CENSUS_PATH
    if not gold_path.is_file() or not store_path.is_file() or not census_path.is_file():
        return
    assert file_sha256(gold_path) == text_chunk_metrics.GOLD_UNSEALED_SHA256
    gold = json.loads(gold_path.read_text())
    census = json.loads(census_path.read_text())
    store = json.loads(store_path.read_text())
    pdf = _write_pdf(tmp_path / "added.pdf", "live-holdout-guard")
    sha = engine_e2e5.paper_bytes_sha256(pdf)
    dest_store = tmp_path / "store.json"
    dest_census = tmp_path / "census.json"
    engine_e2e5.ingest_one_paper(
        pdf,
        gold=gold,
        census=census,
        store=store,
        dest_store=dest_store,
        dest_census=dest_census,
        canonical=_canonical(sha, HOLD_TOKEN),
        embedder=_fake_embedder([]),
    )
    after_store = json.loads(dest_store.read_text())
    after_census = json.loads(dest_census.read_text())
    search = engine_e2e5.searchable_index(after_store, after_census)
    assert len(search["chunks"]) == engine_e2e.EXPECTED_N_CHUNKS
    leaks = engine_e2e5.holdout_n_leaks(search, gold)
    assert leaks["n_held_out_facts"] == 29
    assert leaks["no_leaks_at_every_k"] is True
    assert leaks["n_leaks_at_k"] == {str(k): 0 for k in text_chunk_metrics.RETRIEVAL_KS}
    assert file_sha256(store_path) == engine_e2e.STORE_SHA256
    assert file_sha256(census_path) == CENSUS_V3_SHA256
    blob = json.dumps(leaks)
    assert "fact_id" not in blob
    assert "needles" not in blob


def test_d3_finding_sidecar_does_not_move_d3(tmp_path):
    dest = tmp_path / "CURVES.retrieval.d3.finding.json"
    result = dense_d3.emit_d3_finding_sidecar(dest=dest)
    assert dest.is_file()
    assert result["dense_vs_sparse"] == "DOWN"
    assert result["hybrid_vs_sparse"] == "TIED"
    assert result["weights_retuned"] is False
    assert "over-weights the weaker signal" in result["note"]
    assert file_sha256(dense_d3.CURVES_D3_PATH) == dense_d3.CURVES_D3_SHA256
    import pytest
    with pytest.raises(text_gold.TextGoldError) as error:
        dense_d3.emit_d3_finding_sidecar(dest=dense_d3.CURVES_D3_PATH)
    assert error.value.code == "protected_persist"
