"""A-3 off-domain mint. Fixtures only. No MiniLM. No gold needles. No billed mint."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import abstention_a3, research, text_chunk_metrics, text_gold
from dissolve.gold_ensemble import GoldEnsembleError, file_sha256

ZXQ = "zxqtitania forcefield abstract methods results adsorption rutile surface"


def _pdf_bytes() -> bytes:
    return b"%PDF-1.1 fixture\n"


def _write_pdf(root: Path, relpath: str, body: bytes | None = None) -> tuple[Path, str]:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = body if body is not None else _pdf_bytes() + relpath.encode()
    path.write_bytes(blob)
    return path, hashlib.sha256(blob).hexdigest()


def _extract(text: str):
    def reader(pdf: Path):
        return {"channel": "B", "read_by": "pdftotext -layout", "text": text, "pages": [text], "n_pages": 1}

    return reader


def test_a3_set_digest_is_the_freeze():
    assert abstention_a3.set_digest() == abstention_a3.SET_DIGEST
    assert abstention_a3.set_digest() == "7081eedb65f97f2e8e4714fe0337b6ac47d6b1f1a678a199d222bb8c4fffeb90"
    assert abstention_a3.SPEC_SHA256 == "734f4a9c7b64ff700ab3c5f99b0844c00b8180adb016430f24ecd9637ae3e0da"


def test_a3_live_pdf_sha_set_identity():
    root = abstention_a3.A3_ROOT
    rows = []
    for relpath, digest in abstention_a3.A3_PDFS:
        path = root / relpath
        assert path.is_file()
        assert file_sha256(path) == digest
        rows.append((relpath, digest))
    assert abstention_a3.set_digest(rows) == abstention_a3.SET_DIGEST
    assert {rel for rel, _ in abstention_a3.A3_PDFS} == {
        "references/Luan2015_TiO2-LJ-forcefield_JCP-142-234102.pdf",
        "references/Monti-Walsh-2010-PMF-aminoacid-analogues-aqueous-titania-JPCC.pdf",
        "references/Predota2004_rutile110-EDL_JPCB-108-12049.pdf",
        "references/titania-grafting-1.pdf",
    }


def test_a3_title_and_windows_cap_at_six():
    text = "ZXQTITANIA SURFACE ADSORPTION TITLE LINE\n" + (" ".join(["zxqtoken"] * 80))
    rows = abstention_a3.mint_queries_from_text(
        text, paper_sha256="ab" * 32, relpath="references/fixture.pdf",
    )
    assert len(rows) == 6
    assert rows[0]["kind"] == "title_query"
    assert rows[0]["query"].startswith("ZXQTITANIA")
    assert rows[0]["id"] == "od-" + ("ab" * 32)[:12] + "-001"
    assert [row["kind"] for row in rows[1:]] == ["body_window"] * 5
    assert [row["window_k"] for row in rows[1:]] == [0, 1, 2, 3, 4]
    assert all(len(row["query"].split()) >= 6 for row in rows[1:])
    assert "needles" not in json.dumps(rows)
    assert "fact_id" not in json.dumps(rows)


def test_a3_short_window_is_dropped_not_padded():
    text = "ZXQTITLE8\n zxqaaa zxqbbb zxqccc zxqddd zxqeee zxqfff"
    rows = abstention_a3.mint_queries_from_text(
        text, paper_sha256="cd" * 32, relpath="references/short.pdf",
    )
    assert rows[0]["kind"] == "title_query"
    windows = [row for row in rows if row["kind"] == "body_window"]
    assert len(windows) == 1
    assert windows[0]["window_k"] == 0
    assert len(rows) < 6


def test_a3_emit_uses_unpaid_extractor_and_skips_mint_one_paper(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("A-3 must not call mint_one_paper")

    monkeypatch.setattr(text_gold, "mint_one_paper", boom)
    relpath = "references/fixture-titania.pdf"
    _path, digest = _write_pdf(tmp_path, relpath)
    table = [(relpath, digest)]
    artifact = abstention_a3.build_offdomain_artifact(
        root=tmp_path,
        rows=table,
        extractor=_extract("ZXQTITANIA ADSORPTION ON RUTILE SURFACE\n" + ZXQ),
    )
    assert artifact["n_pdfs"] == 1
    assert artifact["n_queries"] >= 2
    assert artifact["queries"][0]["kind"] == "title_query"
    assert all(row["id"].startswith("od-") for row in artifact["queries"])
    assert "needles" not in json.dumps(artifact)
    assert "fact_id" not in json.dumps(artifact)
    source = Path(abstention_a3.__file__).read_text()
    assert "mint_one_paper" not in source


def test_a3_pdftotext_failure_does_not_invent_a_query(tmp_path):
    relpath = "references/broken.pdf"
    _path, digest = _write_pdf(tmp_path, relpath)

    def fail(pdf: Path):
        raise GoldEnsembleError("pdftotext_failed", "pdftotext -layout failed.")

    try:
        abstention_a3.build_offdomain_artifact(
            root=tmp_path, rows=[(relpath, digest)], extractor=fail,
        )
    except text_gold.TextGoldError as error:
        assert error.code == "pdftotext_failed"
    else:
        raise AssertionError("pdftotext failure must OBJECT that file")


def test_a3_refuse_protected_persist_and_gold_v2(tmp_path, monkeypatch):
    monkeypatch.setattr(text_chunk_metrics, "GOLD_V2_PATH", tmp_path / "GOLD.v2.json")
    (tmp_path / "GOLD.v2.json").write_text("{}\n")
    try:
        abstention_a3.emit_a3_product(dest_dir=tmp_path)
    except text_gold.TextGoldError as error:
        assert error.code == "gold_v2_present"
    else:
        raise AssertionError("GOLD.v2.json must be an owner stop")


def test_a3_emit_beside_gold_does_not_overwrite_it(tmp_path):
    relpath = "references/fixture-titania.pdf"
    _path, digest = _write_pdf(tmp_path, relpath)
    gold_before = file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH)
    result = abstention_a3.emit_a3_product(
        root=tmp_path,
        dest_dir=tmp_path,
        rows=[(relpath, digest)],
        extractor=_extract("ZXQTITANIA ADSORPTION TITLE LINE HERE\n" + ZXQ),
    )
    dest = Path(result["offdomain_path"])
    assert dest.name == "OFFDOMAIN.queries.v1.json"
    assert dest.parent == tmp_path
    payload = json.loads(dest.read_text())
    assert payload["n_pdfs"] == 1
    assert file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH) == gold_before
    assert gold_before == text_chunk_metrics.GOLD_UNSEALED_SHA256
    source = Path(research.__file__).read_text()
    assert "_HYBRID_DENSE_WEIGHT = 0.55\n" in source
    assert "_HYBRID_SPARSE_WEIGHT = 0.40\n" in source


def test_a3_out_of_battery_paths_are_not_in_the_freeze():
    names = " ".join(rel for rel, _ in abstention_a3.A3_PDFS)
    assert "SAFT-references" not in names
    assert "PE-" not in names
    assert "saft-" not in names
    assert "Kanduc" not in names
    assert "ATPS" not in names
    assert "Schematic" not in names


def test_a3_extra_pdf_on_the_live_root_is_refused():
    extra = list(abstention_a3.A3_PDFS) + [
        ("references/SAFT-not-in-battery.pdf", "aa" * 32),
    ]
    try:
        abstention_a3.build_offdomain_artifact(root=abstention_a3.A3_ROOT, rows=extra)
    except text_gold.TextGoldError as error:
        assert error.code == "a3_set_extra"
    else:
        raise AssertionError("extra PDF on the A-3 root must be refused")


def test_a3_od_ids_are_unique_across_papers():
    text = "ZXQTITANIA ADSORPTION TITLE LINE HERE\n" + ZXQ
    first = abstention_a3.mint_queries_from_text(
        text, paper_sha256="11" * 32, relpath="references/one.pdf",
    )
    second = abstention_a3.mint_queries_from_text(
        text, paper_sha256="22" * 32, relpath="references/two.pdf",
    )
    ids = [row["id"] for row in first + second]
    assert all(item.startswith("od-") for item in ids)
    assert len(ids) == len(set(ids))
    assert "query" in first[0]
    assert "fact_id" not in first[0]
    assert "needles" not in first[0]


def test_a3_live_unpaid_pdftotext_mint_does_not_touch_persist(tmp_path):
    gold_before = file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH)
    artifact = abstention_a3.build_offdomain_artifact()
    dest = tmp_path / abstention_a3.OFFDOMAIN_NAME
    dest.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n")
    assert artifact["set_digest"] == abstention_a3.SET_DIGEST
    assert artifact["n_pdfs"] == 4
    assert 4 <= artifact["n_queries"] <= 24
    assert all(paper["n_queries"] <= 6 for paper in artifact["pdfs"])
    assert all(row["id"].startswith("od-") for row in artifact["queries"])
    assert all("query" in row and "fact_id" not in row for row in artifact["queries"])
    assert "mint_one_paper" not in Path(abstention_a3.__file__).read_text()
    assert file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH) == gold_before
    source = Path(research.__file__).read_text()
    assert "query_idf_coverage" not in source
    assert not Path("/home/aaltamimi2/dissolve-v12-audit/corpus/text_chunking/CURVES.retrieval.abstention.v1.json").exists()

