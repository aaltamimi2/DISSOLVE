"""C4: production parse raises on Docling failure. DeepDoc and pypdf are not a fallback."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import literature_ingest, research


def _acquire(packed_path: Path, sha: str = "ab" * 32) -> dict:
    return {
        "schema": "dissolve.acquire.v1",
        "library_id": "bridge-probe",
        "document": {
            "document_id": "DOC-BRIDGE",
            "library_id": "bridge-probe",
            "content_sha256": sha,
            "document_kind": "paper",
            "acquired_at": "2026-08-21T00:00:00+00:00",
        },
        "artifacts": [{
            "artifact_id": "ART-1",
            "sha256": sha,
            "packed_path": str(packed_path),
        }],
    }


def _docling_bridge():
    return {
        "backend": "docling",
        "version": "2.121.0",
        "items": [{
            "id": "#/texts/0", "label": "paragraph", "order": 0, "page": 1,
            "text": "production docling prose", "confidence": 1.0,
        }],
        "tables": [],
        "quality_flags": [],
        "fallback_reason": None,
    }


def test_production_parse_raises_when_docling_fails_and_does_not_call_fallbacks(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 forced-failure")
    calls = {"pypdf": 0, "deepdoc": 0}

    def boom(path):
        raise research.LiteratureContractError(
            "parser_backend_failed", "forced Docling failure", backend="docling",
        )

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a production Docling failure")

    def count_deepdoc(*_args, **_kwargs):
        calls["deepdoc"] += 1
        raise AssertionError("DeepDoc must not run on a production Docling failure")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(research, "_run_deepdoc", count_deepdoc)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(pdf))
    assert caught.value.code == "parser_backend_failed"
    assert caught.value.details.get("backend") == "docling"
    assert calls == {"pypdf": 0, "deepdoc": 0}


def test_production_structure_parse_does_not_call_deepdoc_on_docling_failure(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 forced-failure")
    calls = {"deepdoc": 0, "pypdf": 0}

    def boom(path):
        raise research.LiteratureContractError(
            "parser_backend_failed", "forced Docling failure", backend="docling",
        )

    def count_deepdoc(*_args, **_kwargs):
        calls["deepdoc"] += 1
        raise AssertionError("DeepDoc must not run")

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(research, "_run_deepdoc", count_deepdoc)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        research.parse_document_structure(_acquire(pdf))
    assert caught.value.code == "parser_backend_failed"
    assert calls == {"deepdoc": 0, "pypdf": 0}


def test_corrupt_pdf_failure_is_loud_and_names_docling(monkeypatch, tmp_path):
    pdf = tmp_path / "corrupt.pdf"
    pdf.write_bytes(b"not-a-pdf")
    calls = {"pypdf": 0, "deepdoc": 0}

    class _FailingConverter:
        def convert(self, path):
            raise RuntimeError("invalid PDF header")

    orig_import = research.importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "docling.document_converter":
            return SimpleNamespace(DocumentConverter=_FailingConverter)
        if name == "docling":
            return SimpleNamespace(__version__="2.121.0")
        return orig_import(name, *args, **kwargs)

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a corrupt PDF")

    def count_deepdoc(*_args, **_kwargs):
        calls["deepdoc"] += 1
        raise AssertionError("DeepDoc must not run on a corrupt PDF")

    monkeypatch.setattr(research.importlib, "import_module", fake_import)
    monkeypatch.setattr(research, "_run_deepdoc", count_deepdoc)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(pdf))
    assert caught.value.code == "parser_backend_failed"
    assert caught.value.details.get("backend") == "docling"
    assert calls == {"pypdf": 0, "deepdoc": 0}


def test_production_parse_refuses_a_non_docling_bridge(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    calls = {"pypdf": 0, "deepdoc": 0}

    def lie(_path):
        return {
            "backend": "pypdf",
            "version": "6.6.0",
            "items": [{
                "id": "PDF-P0001-B00001", "label": "paragraph", "order": 0,
                "page": 1, "text": "fallback prose", "confidence": 0.75,
            }],
            "tables": [],
            "quality_flags": ["layout_degraded"],
            "fallback_reason": "docling_parser_backend_failed",
        }

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf bridge must not be the production fallback")

    def count_deepdoc(*_args, **_kwargs):
        calls["deepdoc"] += 1
        raise AssertionError("DeepDoc must not run")

    monkeypatch.setattr(research, "_run_docling", lie)
    monkeypatch.setattr(research, "_run_deepdoc", count_deepdoc)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(pdf))
    assert caught.value.code in {"parser_identity_lie", "undisclosed_parser_fallback"}
    assert calls["pypdf"] == 0
    assert calls["deepdoc"] == 0


def test_production_parse_keeps_a_named_docling_success(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 ok")
    calls = {"pypdf": 0, "deepdoc": 0}

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a Docling success")

    def count_deepdoc(*_args, **_kwargs):
        calls["deepdoc"] += 1
        raise AssertionError("DeepDoc must not run on a Docling success")

    monkeypatch.setattr(research, "_run_docling", lambda _path: _docling_bridge())
    monkeypatch.setattr(research, "_run_deepdoc", count_deepdoc)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)

    parsed = literature_ingest._parse(_acquire(pdf))
    assert parsed["parser_backend"] == "docling"
    assert parsed["fallback_reason"] is None
    assert parsed["blocks"][0]["text"] == "production docling prose"
    assert calls == {"pypdf": 0, "deepdoc": 0}


def _suffix_fallback_spies(monkeypatch):
    calls = {"docling": 0, "pypdf": 0, "deepdoc": 0}

    def count_docling(*_args, **_kwargs):
        calls["docling"] += 1
        raise AssertionError("Docling must not run on a JATS/local-text suffix")

    def count_pypdf(*_args, **_kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a JATS/local-text suffix")

    def count_deepdoc(*_args, **_kwargs):
        calls["deepdoc"] += 1
        raise AssertionError("DeepDoc must not run on a JATS/local-text suffix")

    monkeypatch.setattr(research, "_run_docling", count_docling)
    monkeypatch.setattr(research, "_run_deepdoc", count_deepdoc)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)
    return calls


def test_production_parse_refuses_xml_jats_suffix_and_does_not_call_fallbacks(monkeypatch, tmp_path):
    xml = tmp_path / "probe.xml"
    xml.write_text(
        "<article><article-title>Suffix probe</article-title>"
        "<p>jats body text for the identity check</p></article>",
        encoding="utf-8",
    )
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(xml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "jats"
    assert calls == {"docling": 0, "pypdf": 0, "deepdoc": 0}


def test_production_parse_refuses_xhtml_as_jats_not_docling(monkeypatch, tmp_path):
    """08b2f0f residual: .xhtml must stamp jats, not fall through to Docling."""
    xhtml = tmp_path / "probe.xhtml"
    xhtml.write_text(
        "<article><article-title>XHTML probe</article-title>"
        "<p>xhtml body text for the identity check</p></article>",
        encoding="utf-8",
    )
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(xhtml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "jats"
    assert calls == {"docling": 0, "pypdf": 0, "deepdoc": 0}


def test_production_parse_refuses_nxml_as_jats_not_local_text(monkeypatch, tmp_path):
    """b45a1b4 residual: .nxml must stamp jats, not fall through to Docling or local_text."""
    nxml = tmp_path / "probe.nxml"
    nxml.write_text(
        "<article><article-title>NXML probe</article-title>"
        "<p>nxml body text for the identity check</p></article>",
        encoding="utf-8",
    )
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(nxml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "jats"
    assert calls == {"docling": 0, "pypdf": 0, "deepdoc": 0}


def test_production_parse_refuses_txt_local_text_suffix_and_does_not_call_fallbacks(monkeypatch, tmp_path):
    txt = tmp_path / "probe.txt"
    txt.write_text("local text body for the identity check\n", encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(txt))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "local_text"
    assert calls == {"docling": 0, "pypdf": 0, "deepdoc": 0}


def test_production_parse_refuses_md_local_text_suffix_and_does_not_call_fallbacks(monkeypatch, tmp_path):
    md = tmp_path / "probe.md"
    md.write_text("markdown body for the identity check\n", encoding="utf-8")
    calls = _suffix_fallback_spies(monkeypatch)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(md))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "local_text"
    assert calls == {"docling": 0, "pypdf": 0, "deepdoc": 0}


def test_production_parse_suffix_refuse_names_the_stamped_backend(monkeypatch, tmp_path):
    """1c: a hardcoded jats refuse from the suffix would stay green. The stamp must bind."""
    xml = tmp_path / "probe.xml"
    xml.write_text(
        "<article><p>suffix stamp body</p></article>",
        encoding="utf-8",
    )
    calls = _suffix_fallback_spies(monkeypatch)
    jats_calls = {"n": 0}

    def lie_bridge(_path):
        jats_calls["n"] += 1
        return {
            "backend": "pypdf",
            "version": "6.6.0",
            "items": [{
                "id": "PDF-P0001-B00001", "label": "paragraph", "order": 0,
                "page": 1, "text": "forced suffix stamp", "confidence": 0.75,
            }],
            "tables": [],
            "quality_flags": ["layout_degraded"],
            "fallback_reason": "forced_suffix_stamp",
        }

    monkeypatch.setattr(literature_ingest, "_jats_bridge", lie_bridge)

    with pytest.raises(research.LiteratureContractError) as caught:
        literature_ingest._parse(_acquire(xml))
    assert caught.value.code == "parser_identity_lie"
    assert caught.value.details.get("parser_backend") == "pypdf"
    assert caught.value.details.get("fallback_reason") == "forced_suffix_stamp"
    assert jats_calls["n"] == 1
    assert calls == {"docling": 0, "pypdf": 0, "deepdoc": 0}
