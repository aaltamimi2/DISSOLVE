"""corpus.py: the T5 build-and-grow path, on synthetic canonical documents (no papers needed)."""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import pytest

from dissolve import corpus, research

SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


def _canonical(sha: str, blocks: list[tuple[str, dict]]) -> dict:
    """Blocks joined by blank lines, with offsets into the joined text (as canonical documents are)."""
    text, rows = "", []
    for index, (body, extra) in enumerate(blocks):
        if text:
            text += "\n\n"
        rows.append({"block_id": f"b{index}", "char_start": len(text), "char_end": len(text) + len(body), **extra})
        text += body
    return {"source_pdf_sha256": sha, "canonical_text": text, "blocks": rows, "tables": []}


def _paper(sha: str, token: str) -> dict:
    return _canonical(sha, [
        (f"{token} opening paragraph " + "x" * 580, {"page": 1, "kind": "text"}),
        ("second paragraph " + "y" * 583, {"page": 2, "kind": "text", "nearest_preceding_heading": ["Methods"],
                                           "nearest_preceding_heading_origin": "parser_supplied"}),
        ("third paragraph " + "z" * 584, {"page": 2, "kind": "table"}),
    ])


@pytest.fixture
def embeds(monkeypatch):
    """Stand-in embedder: records each call's batch and returns unit vectors of the model's dimension."""
    calls: list[list[str]] = []

    def fake(texts, model_name=None):
        calls.append(list(texts))
        dim = research._compatible_embedding_dim(model_name)
        return model_name, [[1.0] + [0.0] * (dim - 1) for _ in texts]

    monkeypatch.setattr(research, "_dense_vectors", fake)
    return calls


@pytest.fixture
def papers(monkeypatch):
    docs = {SHA_A: _paper(SHA_A, "alphaword"), SHA_B: _paper(SHA_B, "betaword"), SHA_C: _paper(SHA_C, "gammaword")}
    monkeypatch.setattr(corpus, "_paper", lambda pdf: (str(pdf), docs[str(pdf)]))
    return docs


def test_t5_packs_whole_blocks_up_to_the_target():
    doc = _paper(SHA_A, "alphaword")
    first, second, third = [(b["char_start"], b["char_end"]) for b in doc["blocks"]]
    assert corpus.t5_spans(doc) == [(first[0], second[1]), third]
    assert all(end - start <= corpus.T5_TARGET for start, end in corpus.t5_spans(doc))


def test_t5_never_splits_a_block():
    doc = _canonical(SHA_A, [("short", {}), ("w" * 2000, {}), ("tail", {})])
    spans = corpus.t5_spans(doc)
    assert [doc["canonical_text"][s:e] for s, e in spans] == ["short", "w" * 2000, "tail"]


def test_chunk_records_stamp_page_section_kind_and_identity():
    first, second = corpus.chunk_records(_paper(SHA_A, "alphaword"), SHA_A)
    assert first["chunk_id"] == f"T5-{SHA_A[:12]}-0001" and second["chunk_id"] == f"T5-{SHA_A[:12]}-0002"
    assert first["document_id"] == f"D{SHA_A[:16]}"
    assert first["page"] == "1-2" and second["page"] == 2
    assert first["kind"] == "text" and second["kind"] == "table"
    assert first["section"] == "" and first["section_origin"] is None
    assert first["text"] == first["body"] and first["token_estimate"] == -(-len(first["body"]) // 4)
    import hashlib
    assert first["sha256"] == hashlib.sha256(first["body"].encode()).hexdigest()


def test_section_comes_from_a_parser_supplied_heading():
    doc = _canonical(SHA_A, [("body text", {"nearest_preceding_heading": ["Results"],
                                             "nearest_preceding_heading_origin": "parser_supplied"})])
    (row,) = corpus.chunk_records(doc, SHA_A)
    assert row["section"] == research._chunk_header(["Results"]) and row["section_origin"] == "parser_supplied"


def test_build_then_add_one_paper_at_a_time(tmp_path, embeds, papers):
    corpus.build([Path(SHA_A)], tmp_path)
    assert len(embeds) == 1
    manifest = corpus.add([Path(SHA_B), Path(SHA_C), Path(SHA_A)], tmp_path)
    assert len(embeds) == 3, "each added paper is embedded on its own; papers already in the base are skipped"
    base = corpus.read_index(tmp_path / "indexes" / f"{corpus.BASE_KB}.json.gz")
    sidecar = corpus.read_index(tmp_path / "indexes" / f"{corpus.SIDECAR_KB}.json.gz")
    assert [d["sha256"] for d in base["documents"]] == [SHA_A]
    assert [d["sha256"] for d in sidecar["documents"]] == [SHA_B, SHA_C]
    assert sidecar["dense"]["chunk_ids"] == [row["chunk_id"] for row in sidecar["chunks"]]
    assert manifest["promoted"]["n_papers"] == 2 and manifest["n_papers"] == 1
    assert manifest["abstention"]["floor"] == corpus.ABSTENTION["floor"]


def test_manifest_chunk_list_is_text_free_and_verifies(tmp_path, embeds, papers):
    manifest = corpus.build([Path(SHA_A), Path(SHA_B)], tmp_path)
    assert all(set(row) == {"chunk_id", "paper_sha256", "char_start", "char_end", "sha256"} for row in manifest["chunks"])
    assert corpus.verify(tmp_path, manifest)["matching"] == len(manifest["chunks"])
    tampered = json.loads(json.dumps(manifest))
    tampered["chunks"][0]["sha256"] = "0" * 64
    assert corpus.verify(tmp_path, tampered)["differing"] == [manifest["chunks"][0]["chunk_id"]]


def test_build_index_refuses_a_substituted_model(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", lambda texts, model_name=None: ("other/model", [[1.0] * 384 for _ in texts]))
    with pytest.raises(research.LiteratureContractError):
        corpus.build_index([(SHA_A, _paper(SHA_A, "alphaword"))], corpus.BASE_KB)


def test_build_index_refuses_misshapen_vectors(monkeypatch):
    monkeypatch.setattr(research, "_dense_vectors", lambda texts, model_name=None: (model_name, [[1.0] * 3 for _ in texts]))
    with pytest.raises(research.LiteratureContractError):
        corpus.build_index([(SHA_A, _paper(SHA_A, "alphaword"))], corpus.BASE_KB)


def test_the_agent_serves_a_built_corpus_with_its_sidecar(tmp_path, monkeypatch, embeds, papers):
    corpus.build([Path(SHA_A)], tmp_path)
    corpus.add([Path(SHA_B)], tmp_path)
    monkeypatch.setenv("DISSOLVE_CORPUS_DIR", str(tmp_path))
    monkeypatch.delenv("DISSOLVE_RESEARCH_HOME", raising=False)
    index = research._load_index(corpus.BASE_KB)
    assert {row["paper_sha256"] for row in index["chunks"]} == {SHA_A, SHA_B}
    assert index["abstention"]["floor"] == corpus.ABSTENTION["floor"]
    for token, sha in (("alphaword", SHA_A), ("betaword", SHA_B)):
        rows = research._search_index(index, token, 3, "sparse")
        assert rows and rows[0]["paper_sha256"] == sha


def test_bge_artifact_reuses_known_vectors_and_encodes_the_rest(tmp_path, embeds, papers):
    corpus.build([Path(SHA_A)], tmp_path)
    corpus.add([Path(SHA_B)], tmp_path)
    base = corpus.read_index(tmp_path / "indexes" / f"{corpus.BASE_KB}.json.gz")
    reuse = tmp_path / "reuse.json.gz"
    known = [row["chunk_id"] for row in base["chunks"]]
    reuse.write_bytes(gzip.compress(json.dumps({"dense": {"chunk_ids": known, "vectors": [[0.5] * 768 for _ in known]}}).encode()))
    embeds.clear()
    manifest = corpus.bge_artifact(tmp_path, tmp_path / "bge", reuse=reuse)
    assert len(embeds) == 1 and len(embeds[0]) == manifest["n_chunks"] - len(known)
    index = corpus.read_index(tmp_path / "bge" / "index.json.gz")
    assert index["dense"]["model"] == research._BGE_MODEL_ID and index["dense"]["vectors"][0] == [0.5] * 768
    assert manifest["n_documents"] == 2 and manifest["abstention"] == {"floor": corpus.ABSTENTION["floor"]}


@pytest.mark.skipif(not os.getenv("DISSOLVE_CORPUS_REFERENCE"), reason="set DISSOLVE_CORPUS_REFERENCE to a served corpus")
def test_known_answer_rebuild_matches_the_reference_corpus(tmp_path):
    """Rebuild a served corpus from its canonical documents; every index must match byte for byte.

    DISSOLVE_CORPUS_REFERENCE is a directory holding canonical/<sha>.v1.json and the served
    indexes/ (the base index and, if present, the promoted sidecar).
    """
    ref = Path(os.environ["DISSOLVE_CORPUS_REFERENCE"])

    def papers_of(name):
        served = corpus.read_index(ref / "indexes" / name)
        order = list(dict.fromkeys(row["paper_sha256"] for row in served["chunks"]))
        return [(sha, json.loads((ref / "canonical" / f"{sha}.v1.json").read_text())) for sha in order]

    base = corpus.build_index(papers_of(f"{corpus.BASE_KB}.json.gz"), corpus.BASE_KB)
    sidecar = None
    if (ref / "indexes" / f"{corpus.SIDECAR_KB}.json.gz").is_file():
        for paper in papers_of(f"{corpus.SIDECAR_KB}.json.gz"):
            sidecar = corpus.build_index([paper], corpus.SIDECAR_KB, previous=sidecar)
    corpus._write(tmp_path, base, sidecar)
    for kb in (corpus.BASE_KB, corpus.SIDECAR_KB):
        if (ref / "indexes" / f"{kb}.json.gz").is_file():
            ours = gzip.decompress((tmp_path / "indexes" / f"{kb}.json.gz").read_bytes())
            assert ours == gzip.decompress((ref / "indexes" / f"{kb}.json.gz").read_bytes())
