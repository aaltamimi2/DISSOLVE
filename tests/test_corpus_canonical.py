"""C3: 21 canonical documents. Not C8. Loads persist; does not call Docling."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import research

CENSUS = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CENSUS.v1.json")
MANIFEST = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CANONICAL_MANIFEST.v1.json")
CANON_DIR = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/canonical")
PARSED_DIR = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/parsed")
PROBE_SHA = "1af857ee2e8299d6d0a586216ead5109a9b4293505585edf76da3bca9772ad21"
PATENT_SHA = "b95603201907ce4de4f4a62671d0ba8c20879b723da7b15fee5d8af2fa2f199f"
TEMP_RE = research._CANONICAL_TEMPERATURE_RE


pytestmark = pytest.mark.skipif(not MANIFEST.is_file(), reason="C3 canonical manifest not built")


def _census_in_scope() -> set[str]:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    return {
        row["sha256"]
        for row in census["papers"]
        if row["status"] in {"indexed", "held_out"}
    }


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _canonical(sha: str) -> dict:
    return json.loads((CANON_DIR / f"{sha}.v1.json").read_text(encoding="utf-8"))


def _parsed(sha: str) -> dict:
    return json.loads((PARSED_DIR / f"{sha}.v1.json").read_text(encoding="utf-8"))


def _assert_1a(doc: dict) -> None:
    canonical = doc["canonical_text"]
    for block in doc["blocks"]:
        assert canonical[block["char_start"]:block["char_end"]] == block["text"]


def _repairs_precede_offsets(events: list[str]) -> bool:
    repairs = [i for i, name in enumerate(events) if name == "repair"]
    offsets = [i for i, name in enumerate(events) if name == "offset"]
    if not repairs or not offsets:
        return False
    return max(repairs) < min(offsets)


def _install_order_spies(monkeypatch) -> list[str]:
    events: list[str] = []
    original = research._m3_repair

    def repair(text: str) -> str:
        events.append("repair")
        return original(text)

    def mark(char_start: int, char_end: int) -> None:
        events.append("offset")
        del char_start, char_end

    monkeypatch.setattr(research, "_m3_repair", repair)
    monkeypatch.setattr(research, "_mark_offset_assignment", mark)
    return events


def test_manifest_sha_set_equals_census_indexed_union_held_out():
    manifest = _manifest()
    shas = [row["pdf_sha256"] for row in manifest["documents"]]
    assert manifest["n_documents"] == 21
    assert len(shas) == 21
    assert len(set(shas)) == 21
    assert set(shas) == _census_in_scope()
    assert PATENT_SHA not in set(shas)


def test_every_row_is_docling_with_null_fallback():
    for row in _manifest()["documents"]:
        assert row["parser_backend"] == "docling"
        assert row["fallback_reason"] is None
        assert row["peak_rss_bytes"] > 0
        assert row["wall_s"] > 0
        assert row["mem_available_before_bytes"] >= (
            research.PEAK_RSS_CEILING_BYTES + research.PEAK_RSS_HEADROOM_BYTES
        )


def test_1a_on_all_21_persisted_canonicals():
    for row in _manifest()["documents"]:
        doc = _canonical(row["pdf_sha256"])
        _assert_1a(doc)
        assert doc["source_pdf_sha256"] == row["pdf_sha256"]
        assert doc["parser_backend"] == "docling"
        assert doc["fallback_reason"] is None
        assert "figure_artifacts_missing" not in json.dumps(doc)
        for block in doc["blocks"]:
            assert "section_path" not in block
            origin = block.get("nearest_preceding_heading_origin")
            assert origin in {"parser_supplied", "inherited_from_stack"}
        for table in doc["tables"]:
            matches = [
                b for b in doc["blocks"]
                if b.get("kind") == "table" and b.get("block_id") == table.get("table_id")
            ]
            assert len(matches) == 1
            assert (matches[0]["char_start"], matches[0]["char_end"]) == (
                table["char_start"], table["char_end"],
            )


def test_1b_rebuild_from_saved_parsed_on_all_21(monkeypatch):
    """1b is a property of build_canonical_document. Re-run it; do not re-Docling."""
    events = _install_order_spies(monkeypatch)
    for row in _manifest()["documents"]:
        events.clear()
        parsed = _parsed(row["pdf_sha256"])
        metrics = {
            "wall_s": row["wall_s"],
            "peak_rss_bytes": row["peak_rss_bytes"],
        }
        doc = research.build_canonical_document(parsed, parse_metrics=metrics)
        assert events.count("repair") >= 1
        assert events.count("offset") == len(doc["blocks"])
        assert _repairs_precede_offsets(events)
        _assert_1a(doc)


def test_c25_glyphs_on_all_21_and_report_temperatures():
    counts = []
    for row in _manifest()["documents"]:
        doc = _canonical(row["pdf_sha256"])
        text = doc["canonical_text"]
        degree = text.count("\u00b0")
        white = text.count("\u25e6")
        hyphen = text.count("\u00ad")
        temps = len(TEMP_RE.findall(text))
        assert white == 0
        assert hyphen == 0
        assert temps == row["temperature_count"]
        counts.append({
            "pdf_sha256": row["pdf_sha256"],
            "temperature_count": temps,
            "degree_count": degree,
        })
    probe = next(item for item in counts if item["pdf_sha256"] == PROBE_SHA)
    assert probe["temperature_count"] >= 18
    assert probe["degree_count"] > 0
    assert len(counts) == 21


def test_ceiling_breaches_are_recorded_not_hidden():
    """C1's two-paper sample is not a corpus bound. A breach is recorded, not swallowed."""
    manifest = _manifest()
    ceiling = research.PEAK_RSS_CEILING_BYTES
    over = [row for row in manifest["documents"] if row["peak_rss_bytes"] > ceiling]
    listed = {item["pdf_sha256"] for item in manifest.get("ceiling_breaches") or []}
    assert listed == {row["pdf_sha256"] for row in over}
    for row in over:
        assert row.get("exceeded_c1_ceiling") is True
