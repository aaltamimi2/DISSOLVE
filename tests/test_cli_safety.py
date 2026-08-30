"""HAZARD_METHODS_CLI_SPEC.v1: /safety print-only copy, not a score rewrite."""
from __future__ import annotations

import copy
import hashlib
import inspect
import io
import json
import re
from pathlib import Path

from rich.console import Console

_ROOT = Path(__file__).resolve().parents[1]
import sys

for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dissolve.cli import (
    CliApp,
    PUBLISHED_HAZARD_METHODS_COPY,
    doctor_report,
    format_published_hazard_methods_copy,
    published_hazard_methods_doctor_check,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CENSUS_JSON = _ROOT / "audit" / "HAZARD_METHODS.v1.json"
_CENSUS_SHA256 = "8540e6fe2f3ee780989ea15beb1b9ce0fe2aac2c81a073e271fd073997ecb150"
_KEEP_OUTS = (
    # Card builder stamped CHEM21 SH&E (v2). Methods copy / duckdb / thermo stay.
    ("src/dissolve/safety.py", "fcb8c22383e46afb784769e6d0e2c7b08daa5cc43d69ef9c2b1937eb4d3034cc"),
    ("src/dissolve/data/safety.duckdb", "88ce0d09ac28de17045702a8a283de6610b5fe1ab33aa5f90bf6e98edfd75a74"),
    ("src/dissolve/thermo.py", "95998b72529e007bfe2415187af6f878cc1d5180227cfc42541b762c0416cf2b"),
    ("tests/test_thermo.py", "ae3fd7bf5cc2bdde11eca5f05984b4e5ae0c59f2a532ba344c48e8785ce503b7"),
    ("audit/HAZARD_METHODS.published.v1.md", "a3a74ccdfc47596bc8f335097056956224407e531d9a258cad0b2656f9e9b9f9"),
    ("audit/HAZARD_METHODS.v1.json", _CENSUS_SHA256),
)

_FENCE = (
    "safety  published Methods 5ec2521  measured 1d97a73\n"
    "enumerating_function=get_available_solvents  scope=all  origin=built_in  n=990\n"
    "served GSK=130  GreenSolventDB=840  neither=20\n"
    "both_table=128  leftover=1,2-dimethoxyethane (110-71-4), cis-decalin (493-01-6)\n"
    "average MAE=0.30  signed_mean=+0.01  green_lookup=LIMIT 1 no ORDER BY\n"
    "green_screen=screen_green_solvent_candidates  network=offline  floor=6.0 unsourced\n"
    "route=screen_route_solvent_substitutions  include_pubchem=True (default)\n"
    "card=get_solvent_safety_card  include_pubchem=True (default)\n"
)


def _strike_phrases() -> tuple[str, ...]:
    return (
        "562" + " screenable",
        "102 of the " + "562",
        "covers" + " 452",
        "8 have" + " neither",
        "100 solvents" + " where both",
        "differ by " + "0.28",
    )


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=80, color_system=None)
    app = CliApp(
        session_id="safety-session",
        store_root=tmp_path,
        console=console,
        persist=False,
    )
    return app, buf


def _plain(buf: io.StringIO) -> str:
    return _ANSI.sub("", buf.getvalue())


def test_safety_prints_the_published_fence(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/safety") is False
    shown = _plain(buf)
    assert shown == _FENCE
    assert format_published_hazard_methods_copy() == _FENCE
    assert shown.count("\n") == 8
    assert shown.split("\n")[8] == ""


def test_safety_extra_token_is_usage(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/safety extra") is False
    shown = _plain(buf)
    assert "usage: /safety" in shown
    assert "enumerating_function=" not in shown
    assert "published Methods" not in shown


def test_safety_does_not_write_session(tmp_path, monkeypatch):
    app, _buf = _app(tmp_path, monkeypatch)
    before = copy.deepcopy(dict(app.session))
    app.handle_command("/safety")
    assert dict(app.session) == before
    assert "safety" not in app.session
    assert "g_score" not in app.session
    assert "include_pubchem" not in app.session


def test_frozen_copy_matches_census_json():
    raw = _CENSUS_JSON.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _CENSUS_SHA256
    payload = json.loads(raw)
    census = payload["census"]
    copy_row = PUBLISHED_HAZARD_METHODS_COPY
    assert copy_row["n"] == census["n"]
    assert copy_row["served_gsk"] == census["served_gsk"]
    assert copy_row["served_green"] == census["served_green"]
    assert copy_row["served_neither"] == census["served_neither"]
    assert copy_row["both_table_hits"] == census["both_table_hits"]
    assert copy_row["mae_2dp"] == census["mae_2dp"]
    assert copy_row["signed_mean_2dp"] == census["signed_mean_2dp"]
    assert copy_row["default_minimum_g_score"] == census["default_minimum_g_score"]
    assert copy_row["measured_on_builder_sha"] == payload["measured_on_builder_sha"]
    assert [key for key, _cas in copy_row["leftover"]] == [
        row["interp_key"] for row in census["gsk_only"]
    ]
    assert [cas for _key, cas in copy_row["leftover"]] == [
        row["cas"] for row in census["gsk_only"]
    ]


def test_fence_and_incoming_omit_strike_phrases():
    phrases = _strike_phrases()
    blob = _FENCE + Path(__file__).read_text(encoding="utf-8")
    cli_src = (_ROOT / "src" / "dissolve" / "cli.py").read_text(encoding="utf-8")
    for phrase in phrases:
        assert phrase not in _FENCE
        assert phrase not in blob
        assert phrase not in cli_src


def test_banner_names_safety_after_solvents(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.banner()
    shown = _plain(buf)
    assert "/safety" in shown
    solvents_at = shown.index("/solvents")
    safety_at = shown.index("/safety")
    contaminant_at = shown.index("/contaminant")
    assert solvents_at < safety_at < contaminant_at


def test_safety_helper_is_format_only():
    src = inspect.getsource(format_published_hazard_methods_copy)
    src += inspect.getsource(CliApp._handle_safety_command)
    assert "duckdb" not in src
    assert "_gscore" not in src
    assert "import " not in inspect.getsource(format_published_hazard_methods_copy)


def test_keep_outs_ident_parent_tree():
    for rel, digest in _KEEP_OUTS:
        path = _ROOT / rel
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


_DOCTOR_DETAIL = (
    "n=990 GSK=130 GreenSolventDB=840 neither=20 both=128 "
    "MAE=0.30 signed=+0.01 floor=6.0 unsourced"
)


def test_doctor_published_hazard_methods_after_assets(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    names = [c["name"] for c in report["checks"]]
    assets_at = names.index("Scientific assets")
    assert names[assets_at + 1] == "Published hazard Methods"
    assert names[assets_at + 2] == "Tool registry"
    assert names[:2] == ["Model provider", "Scientific assets"]


def test_doctor_published_hazard_methods_detail_and_facts(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in report["checks"] if c["name"] == "Published hazard Methods")
    copy_row = PUBLISHED_HAZARD_METHODS_COPY
    assert check["status"] == "pass"
    assert check["detail"] == _DOCTOR_DETAIL
    assert check["n"] == copy_row["n"] == 990
    assert check["served_gsk"] == copy_row["served_gsk"] == 130
    assert check["served_green"] == copy_row["served_green"] == 840
    assert check["served_neither"] == copy_row["served_neither"] == 20
    assert check["both_table_hits"] == copy_row["both_table_hits"] == 128
    assert check["mae_2dp"] == copy_row["mae_2dp"] == 0.3
    assert check["signed_mean_2dp"] == copy_row["signed_mean_2dp"] == 0.01
    assert check["default_minimum_g_score"] == copy_row["default_minimum_g_score"] == 6.0
    assert check["measured_on_builder_sha"] == copy_row["measured_on_builder_sha"]
    assert check["census_json"] == _CENSUS_SHA256


def test_doctor_check_helper_is_format_only():
    src = inspect.getsource(published_hazard_methods_doctor_check)
    assert "connect" not in src
    assert "_gscore" not in src
    assert "dissolve.safety" not in src
    assert "import " not in src


def test_doctor_detail_omits_strike_phrases():
    for phrase in _strike_phrases():
        assert phrase not in _DOCTOR_DETAIL
