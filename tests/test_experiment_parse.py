"""Experiment parses name a backend. Docling failure must raise, not pypdf."""
from __future__ import annotations

import sys
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
        },
        "artifacts": [{
            "artifact_id": "ART-1",
            "sha256": sha,
            "packed_path": str(packed_path),
        }],
    }


def _pypdf_bridge_stub(path, fallback_reason):
    return {
        "backend": "pypdf",
        "version": "6.6.0",
        "items": [{
            "id": "PDF-P0001-B00001", "label": "paragraph", "order": 0,
            "page": 1, "text": "control-arm prose", "confidence": 0.75,
        }],
        "tables": [],
        "quality_flags": ["layout_degraded"],
        "fallback_reason": fallback_reason,
    }


def test_docling_failure_raises_instead_of_returning_pypdf(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 forced-failure")
    calls = {"pypdf": 0, "cascade": 0}

    def boom(path):
        raise research.LiteratureContractError(
            "parser_backend_failed", "forced Docling failure", backend="docling",
        )

    def count_pypdf(*args, **kwargs):
        calls["pypdf"] += 1
        raise AssertionError("pypdf must not run on a Docling experiment parse")


    def count_cascade(*args, **kwargs):
        calls["cascade"] += 1
        raise AssertionError("production _parse cascade must not run")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", count_pypdf)
    monkeypatch.setattr(literature_ingest, "_parse", count_cascade)

    with pytest.raises(research.LiteratureContractError) as caught:
        research.parse_experiment_document(_acquire(pdf), backend="docling")
    assert caught.value.code == "parser_backend_failed"
    assert calls == {"pypdf": 0, "cascade": 0}


def test_pypdf_control_arm_is_named_not_a_docling_fallback(monkeypatch, tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4 control-arm")

    def boom(path):
        raise AssertionError("Docling must not run on the named pypdf control arm")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(literature_ingest, "_pypdf_bridge", _pypdf_bridge_stub)

    parsed = research.parse_experiment_document(
        _acquire(pdf), backend="pypdf", parsed_at="2026-08-20T00:00:00+00:00",
    )
    assert parsed["parser_backend"] == "pypdf"
    assert parsed["parser_version"] == "6.6.0"
    assert parsed["fallback_reason"] == "explicit_control_arm"
    assert parsed["blocks"][0]["text"] == "control-arm prose"


def test_unknown_experiment_backend_raises(tmp_path):
    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(research.LiteratureContractError) as caught:
        research.parse_experiment_document(_acquire(pdf), backend="deepdoc")
    assert caught.value.code == "unknown_experiment_backend"


def test_docling_payload_with_fallback_reason_is_a_lie():
    payload = {
        "backend": "docling",
        "version": "2.121.0",
        "items": [{
            "id": "#/texts/0", "label": "paragraph", "order": 0, "page": 1,
            "text": "looks like Docling", "confidence": 1.0,
        }],
        "tables": [],
        "quality_flags": [],
        "fallback_reason": "docling_parser_backend_failed;deepdoc_parser_backend_unavailable",
    }
    pdf_sha = "ab" * 32
    acquisition = {
        "schema": "dissolve.acquire.v1",
        "library_id": "bridge-probe",
        "document": {
            "document_id": "DOC-BRIDGE",
            "library_id": "bridge-probe",
            "content_sha256": pdf_sha,
            "document_kind": "paper",
        },
        "artifacts": [{
            "artifact_id": "ART-1",
            "sha256": pdf_sha,
            "packed_path": "/tmp/bridge-probe.pdf",
        }],
    }
    with pytest.raises(research.LiteratureContractError) as caught:
        research.parse_document_structure(
            acquisition, parser_payload=payload, parsed_at="2026-08-20T00:00:00+00:00",
        )
    assert caught.value.code == "parser_identity_lie"
