"""C5 A+B assembler. Constructed fixtures. No vision. Gold v1 unmoved."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import gold_ensemble, research

CENSUS_V2 = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CENSUS.v2.json")
CEILING_V2 = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CEILING.v2.json")
CENSUS_V3 = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CENSUS.v3.json")
CEILING_V3 = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CEILING.v3.json")
GOLD_V1 = Path("/home/aaltamimi2/dissolve-v12-audit/one_paper_experiment/gold_facts.v1.json")
GOLD_V1_SHA256 = "345b426bd66f995b3b78a10e796afb6df013299dd6769379192b59d0d97dfaab"

TOKEN_P = "PEZXQ"
TOKEN_S = "SOLZXQ"
TOKEN_V = "12.875"
TOKEN_ONLY_B = "8.25"


def _write_text_pdf(path: Path, items: list[tuple[float, float, str]]) -> None:
    def escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT", "/F1 12 Tf"]
    for x_pos, y_pos, text in items:
        ops.append(f"1 0 0 1 {x_pos:.1f} {y_pos:.1f} Tm ({escape(text)}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", "replace")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        ),
        b"4 0 obj << /Length %d >> stream\n" % len(stream) + stream + b"\nendstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    header = b"%PDF-1.4\n"
    offsets = []
    cursor = len(header)
    body = b""
    for obj in objects:
        offsets.append(cursor)
        body += obj
        cursor += len(obj)
    xref_offset = cursor
    xref = [b"xref\n0 6\n0000000000 65535 f \n"]
    for offset in offsets:
        xref.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    trailer = (
        b"trailer << /Size 6 /Root 1 0 R >>\n"
        b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    path.write_bytes(header + body + b"".join(xref) + trailer)


def _canon_paragraph(text: str) -> dict:
    return {
        "schema": "dissolve.canonical-document.v1",
        "source_pdf_sha256": "ab" * 32,
        "canonical_text": text,
        "blocks": [{
            "block_id": "B1", "kind": "paragraph", "text": text,
            "char_start": 0, "char_end": len(text), "page": 1,
            "nearest_preceding_heading": [], "caption_ref": None,
            "caption_ref_origin": "unbound", "bbox": None, "confidence": 1.0,
            "footnote_refs": [],
        }],
        "tables": [],
        "pages": 1,
        "parser_backend": "fixture",
        "parser_version": "0",
        "fallback_reason": None,
    }


def _chunks_for(text: str) -> list[dict]:
    return [{
        "chunk_id": "c1", "text": text, "body": text, "header": "",
        "char_start": 0, "char_end": len(text),
    }]


def test_gold_v1_digest_unmoved():
    assert GOLD_V1.is_file()
    assert hashlib.sha256(GOLD_V1.read_bytes()).hexdigest() == GOLD_V1_SHA256


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def _option2_ceiling() -> dict:
    return {
        "PEAK_RSS_CEILING_BYTES": gold_ensemble.START_GUARD_PEAK_RSS_BYTES,
        "RETRACTION": {"status": "FALSE — RETRACTED"},
        "basis": "option 2 start-guard keep of 3501953024",
        "measurement_method": "keep PEAK_RSS_CEILING_BYTES 3501953024",
    }


def _option1_label_only() -> dict:
    return {
        "PEAK_RSS_CEILING_BYTES": 3_710_992_384,
        "RETRACTION": {"status": "FALSE — RETRACTED"},
        "basis": "quiet-machine re-measure of in-scope worst cases",
        "measurement_method": "/usr/bin/time -v",
    }


def _time_v_persist(path: Path, *, rss_kb: int = 3429642) -> dict:
    text = (
        "Command being timed: \"true\"\n"
        f"Maximum resident set size (kbytes): {rss_kb}\n"
    )
    path.write_text(text, encoding="utf-8")
    return {
        "PEAK_RSS_CEILING_BYTES": rss_kb * 1024,
        "RETRACTION": {"status": "FALSE — RETRACTED"},
        "basis": "quiet-machine re-measure of in-scope worst cases",
        "measurement_method": "/usr/bin/time -v",
        "time_v_persist_path": str(path),
        "time_v_persist_sha256": gold_ensemble.file_sha256(path),
    }


def _c3_raise_ceiling() -> dict:
    return {
        "PEAK_RSS_CEILING_BYTES": 3_710_992_384,
        "RETRACTION": {"status": "FALSE — RETRACTED"},
        "basis": "All 21 in-scope papers, peak_rss_bytes measured per document during C3.",
        "measurement_method": "C3 persist",
    }


def _classified_rows() -> list[dict]:
    return [
        {
            "filename": "paper.pdf",
            "sha256": "aa" * 32,
            "status": "indexed",
        },
        {
            "filename": "contaminant.pdf",
            "sha256": gold_ensemble.CONTAMINANT_SHA256,
            "status": "excluded",
        },
    ]


def test_objected_c1v2_does_not_unlock_c5():
    """Accept test 7 / 1c: digest pin plus RETRACTED is not a close."""
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.require_c1_v2(CENSUS_V2, CEILING_V2)
    assert caught.value.code == "c1v2_objected"


def test_run_c5_ab_refuses_objected_c1v2_and_writes_nothing(tmp_path):
    out = tmp_path / "GOLD.v2.ab_draft.json"
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.run_c5_ab(
            census_path=CENSUS_V2,
            ceiling_path=CEILING_V2,
            pdf_root=tmp_path,
            out_path=out,
        )
    assert caught.value.code == "c1v2_objected"
    assert not out.exists()


def test_c3_raise_with_retraction_is_not_a_close(tmp_path):
    census = _write_json(tmp_path / "census.json", {"papers": _classified_rows()})
    ceiling = _write_json(tmp_path / "ceiling.json", _c3_raise_ceiling())
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.require_c1_v2(census, ceiling)
    assert caught.value.code == "c35_not_closed"


def test_unclassified_contaminant_refuses_even_with_option2(tmp_path):
    rows = _classified_rows()
    rows[1]["status"] = "NEW-UNCLASSIFIED"
    census = _write_json(tmp_path / "census.json", {"papers": rows})
    ceiling = _write_json(tmp_path / "ceiling.json", _option2_ceiling())
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.require_c1_v2(census, ceiling)
    assert caught.value.code == "unclassified_paper"


def test_census_v3_option2_unlocks():
    loaded = gold_ensemble.require_c1_v2(CENSUS_V3, CEILING_V3)
    assert loaded["c35_close"] == "option_2_start_guard"
    assert loaded["census_sha256"] == gold_ensemble.CENSUS_V3_SHA256
    assert loaded["ceiling_sha256"] == gold_ensemble.CEILING_V3_SHA256
    rows = gold_ensemble.c3_papers(loaded["census"])
    shas = {row["sha256"] for row in rows}
    assert gold_ensemble.CONTAMINANT_SHA256 in shas
    assert len(rows) == 22
    assert all(row["status"] in {"indexed", "held_out"} for row in rows)


def test_option2_start_guard_and_classified_papers_unlock(tmp_path):
    census = _write_json(tmp_path / "census.json", {"papers": _classified_rows()})
    ceiling = _write_json(tmp_path / "ceiling.json", _option2_ceiling())
    loaded = gold_ensemble.require_c1_v2(census, ceiling)
    assert loaded["c35_close"] == "option_2_start_guard"


def test_option1_label_only_does_not_close_c35(tmp_path):
    """PASS f6726ab residual: a measurement_method string is not a re-measure."""
    census = _write_json(tmp_path / "census.json", {"papers": _classified_rows()})
    ceiling = _write_json(tmp_path / "ceiling.json", _option1_label_only())
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.require_c1_v2(census, ceiling)
    assert caught.value.code == "c35_option1_label_only"


def test_option1_time_v_persist_unlocks(tmp_path):
    census = _write_json(tmp_path / "census.json", {"papers": _classified_rows()})
    payload = _time_v_persist(tmp_path / "time-v.txt")
    ceiling = _write_json(tmp_path / "ceiling.json", payload)
    loaded = gold_ensemble.require_c1_v2(census, ceiling)
    assert loaded["c35_close"] == "option_1_remeasure"


def test_option1_persist_rss_must_equal_ceiling(tmp_path):
    """3bae660 residual: 100 kB persist cannot justify 3710992384."""
    census = _write_json(tmp_path / "census.json", {"papers": _classified_rows()})
    payload = _time_v_persist(tmp_path / "time-v.txt", rss_kb=100)
    payload["PEAK_RSS_CEILING_BYTES"] = 3_710_992_384
    ceiling = _write_json(tmp_path / "ceiling.json", payload)
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.require_c1_v2(census, ceiling)
    assert caught.value.code == "c35_option1_rss_mismatch"


def test_during_c3_is_case_insensitive(tmp_path):
    census = _write_json(tmp_path / "census.json", {"papers": _classified_rows()})
    payload = _c3_raise_ceiling()
    payload["basis"] = "peak_rss_bytes measured per document DURING c3."
    ceiling = _write_json(tmp_path / "ceiling.json", payload)
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.require_c1_v2(census, ceiling)
    assert caught.value.code == "c35_not_closed"


def test_indexed_contaminant_requires_c3_extension():
    census = {"papers": [
        {"sha256": "aa" * 32, "status": "indexed"},
        {"sha256": gold_ensemble.CONTAMINANT_SHA256, "status": "indexed"},
    ]}
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.c3_papers(census, covered={"aa" * 32})
    assert caught.value.code == "c3_extension_required"


def test_wrong_census_with_objected_ceiling_still_refuses(tmp_path):
    fake = tmp_path / "CENSUS.v2.json"
    fake.write_text('{"papers":[]}\n', encoding="utf-8")
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.require_c1_v2(fake, CEILING_V2)
    assert caught.value.code == "c1v2_objected"


def test_stage_must_be_exactly_one_pdf(tmp_path):
    pdf = tmp_path / "src.pdf"
    _write_text_pdf(pdf, [(72, 720, f"{TOKEN_P} {TOKEN_V}")])
    digest = gold_ensemble.file_sha256(pdf)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "extra.txt").write_text("no", encoding="utf-8")
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.stage_one_pdf(pdf, stage, digest)
    assert caught.value.code == "stage_not_empty"


def test_firewall_source_path_refuses_canonical():
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.refuse_source_path(Path("/tmp/corpus/canonical/x.pdf"))
    assert caught.value.code == "firewall_source_path"
    assert gold_ensemble.scan_firewall_text("see parses/ and gold_facts") == [
        "parses/", "gold_facts",
    ]


def test_vision_channels_refuse():
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.channel_c(page_image=b"not-a-call")
    assert caught.value.code == "vision_not_authorized"
    with pytest.raises(gold_ensemble.GoldEnsembleError):
        gold_ensemble.channel_c_prime(page_image=b"not-a-call")


def test_docling_channel_label_fails():
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.validate_fact({
            "fact_id": "bad",
            "kind": "token",
            "scoring_class": "string_existence",
            "channels_used": ["A", "docling"],
            "needles": {},
        })
    assert caught.value.code == "docling_in_channels"


def test_table_cell_cannot_be_string_existence():
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.validate_fact({
            "fact_id": "bad-tc",
            "kind": "table_cell",
            "locus": "Table 2",
            "scoring_class": "string_existence",
            "channels_used": ["A", "B"],
            "needles": {},
        })
    assert caught.value.code == "table_cell_not_bound_fact"


def test_assemble_ab_agreement_candidates_and_disputes():
    paper = {
        "sha256": "cd" * 32,
        "status": "indexed",
        "genre": "experimental",
        "filename": "fixture.pdf",
    }
    channel_a = {"pages": [f"prose {TOKEN_P} {TOKEN_V} and 4.50"]}
    channel_b = {"pages": [
        f"Table 2\n{TOKEN_P}    {TOKEN_S}    {TOKEN_V}    4.50\nonly {TOKEN_ONLY_B}"
    ]}
    assembled = gold_ensemble.assemble_facts(
        paper=paper, channel_a=channel_a, channel_b=channel_b,
    )
    tokens = {fact["tokens"][0] for fact in assembled["string_existence"]}
    assert TOKEN_V in tokens
    assert "4.50" in tokens
    assert TOKEN_ONLY_B not in tokens
    assert all(fact["scoring_class"] == "string_existence" for fact in assembled["string_existence"])
    assert all(fact["needles"] == {} for fact in assembled["string_existence"])
    assert assembled["n_disputes"] >= 1
    dispute = next(fact for fact in assembled["disputes"] if fact["tokens"] == [TOKEN_ONLY_B])
    assert dispute["status"] == "disputed"
    assert dispute["readings"]["A"] is None
    assert dispute["readings"]["B"] == TOKEN_ONLY_B
    assert assembled["n_table_cell_candidates"] >= 1
    candidate = assembled["table_cell_candidates"][0]
    assert candidate["scoring_class"] == "bound_fact"
    assert candidate["status"] == "awaiting_C"
    assert candidate["readings"]["B"]
    assert candidate["readings"]["C"] is None
    assert candidate["kind"] == "table_cell"
    assert candidate["locus"] == "Table 2"


def test_string_existence_cannot_satisfy_contain_bound_fact():
    """Accept test 2 / 1c: stuffed needles in a string_existence row stay unbound."""
    text = f"{TOKEN_P} {TOKEN_S} {TOKEN_V} {TOKEN_ONLY_B}"
    fact = {
        "fact_id": "se-1c",
        "scoring_class": "string_existence",
        "kind": "token",
        "locus": "",
        "page": 1,
        "query": f"{TOKEN_P} {TOKEN_V}",
        "needles": {
            "polymer": TOKEN_P,
            "solvent": TOKEN_S,
            "temperature": TOKEN_V,
            "value": TOKEN_ONLY_B,
        },
    }
    assert research.official_contain_bound_fact_eligible(fact) is False
    scored = research.score_chunks_against_facts(
        _chunks_for(text), [fact], _canon_paragraph(text), strategy="S2_block_pack",
    )
    row = scored["facts"][0]
    assert row["contain_value"] is True
    assert row["contain_bound_fact"] is False
    assert row["contain_bound_fact_rebound"] is False
    assert row["retrievable"] is False


def test_awaiting_c_table_cell_cannot_satisfy_contain_bound_fact():
    text = f"{TOKEN_P} {TOKEN_S} {TOKEN_V}"
    fact = {
        "fact_id": "tc-await",
        "scoring_class": "bound_fact",
        "status": "awaiting_C",
        "kind": "table_cell",
        "locus": "Table 2",
        "page": 1,
        "query": TOKEN_V,
        "needles": {
            "polymer": TOKEN_P, "solvent": TOKEN_S,
            "temperature": TOKEN_V, "value": TOKEN_V,
        },
    }
    scored = research.score_chunks_against_facts(
        _chunks_for(text), [fact], _canon_paragraph(text), strategy="S2_block_pack",
    )
    assert scored["facts"][0]["contain_bound_fact"] is False


def test_gold_v1_shape_still_eligible():
    assert research.official_contain_bound_fact_eligible({
        "fact_id": "legacy",
        "needles": {"polymer": TOKEN_P, "value": TOKEN_V},
    }) is True


def test_ab_channels_on_staged_pdf(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    _write_text_pdf(pdf, [
        (72, 720, f"{TOKEN_P} {TOKEN_V}"),
        (72, 700, f"{TOKEN_P}    {TOKEN_V}    4.50"),
    ])
    digest = gold_ensemble.file_sha256(pdf)
    paper = {
        "sha256": digest, "status": "indexed", "genre": "experimental",
        "filename": "paper.pdf",
    }
    imported: list[str] = []
    real_import = __import__

    def spy(name, globals=None, locals=None, fromlist=(), level=0):
        imported.append(name)
        root = name.split(".", 1)[0]
        if root in {"docling", "sentence_transformers", "openai", "anthropic"}:
            raise AssertionError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", spy)
    assembled = gold_ensemble.run_one_paper(paper, pdf, tmp_path / "stages")
    assert assembled["stage_listing"] == [f"{digest}.pdf"]
    assert assembled["n_string_existence"] >= 1
    assert any(fact["tokens"] == [TOKEN_V] for fact in assembled["string_existence"])
    assert assembled["sealed"] is False
    assert "docling" not in imported
    assert "sentence_transformers" not in imported


def test_pdftotext_only_subprocess(monkeypatch, tmp_path):
    pdf = tmp_path / "paper.pdf"
    _write_text_pdf(pdf, [(72, 720, TOKEN_V)])

    def fake_which(_name):
        return "/usr/bin/curl"

    monkeypatch.setattr(gold_ensemble.shutil, "which", fake_which)
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_ensemble.channel_b_pdftotext(pdf)
    assert caught.value.code == "subprocess_refused"
