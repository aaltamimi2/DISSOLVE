"""PubChem snapshot cache and explicit CID fetch. Not a test_safety*.py inventory."""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import urllib.parse
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
import sys

for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_tools import PUBCHEM, source_basis_for
from dissolve import registry, safety
from dissolve.cli import EXPECTED_REGISTRY_NAMES, doctor_report
from dissolve.contracts import parse_tool_result

_SNAPSHOT = Path.home() / "dissolve-v12-audit/safety/pubchem_safety_snapshot.v2.duckdb"
_SNAPSHOT_SHA256 = (
    "9f4082e0844ab18698fd229986d215fc7403767961c53bc5ac548875daaae0f7"
)
_LIVE_HEADINGS = (
    "Flash Point", "Autoignition Temperature", "Vapor Pressure",
    "GHS Classification", "Non-Human Toxicity Values",
    "Environmental Biodegradation",
    "NIOSH Recommendations", "OSHA Standards",
)
_FETCHED_AT = "2026-08-28T21:29:23.217777+00:00"
_TOLUENE = "Toluene"
_TOLUENE_CID = 1140
_MISS_CID = 1


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _forbid_network(monkeypatch):
    def _raise(_url: str):
        raise RuntimeError("network forbidden")

    monkeypatch.setattr(safety, "_request_json", _raise)
    safety._heading.cache_clear()
    safety._pubchem.cache_clear()


def _empty_payload(_url: str) -> dict:
    parsed = urllib.parse.urlparse(_url)
    heading = urllib.parse.parse_qs(parsed.query).get("heading", [None])[0]
    if heading == "Biodegradation":
        raise AssertionError("CID path must not fetch heading Biodegradation")
    return {}


def test_registry_keeps_cid_tool_after_thermal_estimator_retirement():
    names = {tool.name for tool in registry.REGISTRY}
    assert len(registry.REGISTRY) == 29
    assert len(EXPECTED_REGISTRY_NAMES) == 29
    assert names == EXPECTED_REGISTRY_NAMES
    assert "fetch_solvent_safety_by_cid" in names
    assert "estimate_thermal_properties" not in names
    assert "fetch_solvent_safety_by_cid" not in PUBCHEM
    assert safety._HEADINGS == _LIVE_HEADINGS
    assert "Biodegradation" not in safety._HEADINGS


def test_cached_card_via_registry_is_snapshot_with_network_forbidden(monkeypatch):
    _forbid_network(monkeypatch)
    raw = registry.call(
        "get_solvent_safety_card",
        solvent_name=_TOLUENE,
        include_pubchem=True,
    )
    payload = _data(raw)
    assert payload["success"] is True
    profile = payload["safety_profile"]
    physical = profile["physical_properties"]
    assert physical["flash_point_c"] is not None
    assert profile["ghs"].get("signal_word")
    assert profile["occupational_exposure_limits"]
    assert profile["toxicity"]["biodegradation"]
    origin = payload["field_origin"]
    for key in (
        "flash_point_c", "ghs", "occupational_exposure_limits", "biodegradation",
    ):
        assert origin[key]["source"] == "snapshot"
        assert origin[key]["fetched_at"] == _FETCHED_AT
    assert profile["provenance"]["pubchem_source"] == "snapshot"
    assert "source: live" not in json.dumps(payload)
    assert source_basis_for(
        "get_solvent_safety_card", payload, {"include_pubchem": True},
    ) == "safety_local"


def test_screens_and_compare_complete_offline(monkeypatch):
    _forbid_network(monkeypatch)
    green = _data(registry.call(
        "screen_green_solvent_candidates",
        feed_polymers=["LDPE", "PP"],
        target_polymer="LDPE",
        limit=3,
    ))
    assert green["success"] is True
    compare = _data(registry.call(
        "compare_solvent_safety_at_conditions",
        candidates=[{"solvent_name": _TOLUENE, "operating_temp_c": 80}],
        include_pubchem=True,
    ))
    assert compare["success"] is True
    assert "source: live" not in json.dumps(compare)
    route = _data(registry.call(
        "screen_route_solvent_substitutions",
        feed_polymers=["LDPE", "PP"],
        route_steps=[{
            "dissolved_polymer": "LDPE",
            "solvent": _TOLUENE,
            "temperature_c": 80,
        }],
        include_pubchem=True,
    ))
    assert route["success"] is True
    assert "source: live" not in json.dumps(route)


def test_cid_outside_snapshot_is_named_miss(monkeypatch):
    _forbid_network(monkeypatch)
    monkeypatch.setattr(
        safety, "_local_properties",
        lambda query: {"name": query, "cid": _MISS_CID},
    )
    payload = _data(registry.call(
        "get_solvent_safety_card",
        solvent_name="not-in-snapshot",
        include_pubchem=True,
    ))
    assert payload["success"] is False
    assert payload["error_code"] == "safety_snapshot_miss"
    assert payload["cid"] == _MISS_CID
    assert str(_MISS_CID) in payload.get("error", "")
    assert "safety_profile" not in payload


def test_decoy_same_size_file_is_digest_mismatch(tmp_path, monkeypatch):
    decoy = tmp_path / "decoy.duckdb"
    decoy.write_bytes(b"\x00" * _SNAPSHOT.stat().st_size)
    assert decoy.stat().st_size == _SNAPSHOT.stat().st_size
    assert hashlib.sha256(decoy.read_bytes()).hexdigest() != _SNAPSHOT_SHA256
    monkeypatch.setenv("DISSOLVE_SAFETY_SNAPSHOT", str(decoy))
    payload = _data(registry.call(
        "get_solvent_safety_card",
        solvent_name=_TOLUENE,
        include_pubchem=True,
    ))
    assert payload["success"] is False
    assert payload["error_code"] == "snapshot_digest_mismatch"
    assert payload.get("digest") != _SNAPSHOT_SHA256


def test_snapshot_internal_mismatch_count_for_all_987():
    loaded = safety._load_snapshot()
    connection = loaded["connection"]
    typed_rows = connection.execute("SELECT * FROM pubchem_safety").fetchall()
    columns = loaded["columns"]
    mismatches = 0
    for values in typed_rows:
        record = dict(zip(columns, values))
        cid = int(record["cid"])
        served = safety._snapshot_pubchem(cid)
        typed_map = {
            "flash_point_c": record.get("flash_point_c"),
            "autoignition_c": record.get("autoignition_c"),
            "vapor_pressure_kpa": record.get("vapor_pressure_kpa"),
            "ghs_signal_word": record.get("ghs_signal_word"),
            "ghs_pictograms": safety._json_field(record.get("ghs_pictograms")) or [],
            "ghs_hazard_statements": safety._json_field(
                record.get("ghs_hazard_statements")
            ) or [],
            "occupational_exposure_limits": safety._json_field(
                record.get("occupational_exposure_limits")
            ) or [],
            "ld50_values": safety._json_field(record.get("ld50_values")) or [],
            "lc50_values": safety._json_field(record.get("lc50_values")) or [],
            "biodegradation": safety._json_field(record.get("biodegradation")) or [],
        }
        if isinstance(typed_map["biodegradation"], str):
            typed_map["biodegradation"] = [typed_map["biodegradation"]]
        pairs = (
            ("flash_point_c", served.get("flash_point_c"), typed_map["flash_point_c"]),
            ("autoignition_c", served.get("autoignition_c"), typed_map["autoignition_c"]),
            (
                "vapor_pressure_kpa",
                served.get("vapor_pressure_kpa"),
                typed_map["vapor_pressure_kpa"],
            ),
            (
                "ghs_signal_word",
                (served.get("ghs") or {}).get("signal_word"),
                typed_map["ghs_signal_word"],
            ),
            (
                "ghs_pictograms",
                (served.get("ghs") or {}).get("pictograms") or [],
                typed_map["ghs_pictograms"] if isinstance(typed_map["ghs_pictograms"], list) else [],
            ),
            (
                "ghs_hazard_statements",
                (served.get("ghs") or {}).get("hazard_statements") or [],
                typed_map["ghs_hazard_statements"] if isinstance(typed_map["ghs_hazard_statements"], list) else [],
            ),
            (
                "occupational_exposure_limits",
                served.get("occupational_exposure_limits") or [],
                typed_map["occupational_exposure_limits"] if isinstance(typed_map["occupational_exposure_limits"], list) else [],
            ),
            (
                "ld50_values",
                served.get("ld50_values") or [],
                typed_map["ld50_values"] if isinstance(typed_map["ld50_values"], list) else [],
            ),
            (
                "lc50_values",
                served.get("lc50_values") or [],
                typed_map["lc50_values"] if isinstance(typed_map["lc50_values"], list) else [],
            ),
            (
                "biodegradation",
                served.get("biodegradation") or [],
                typed_map["biodegradation"] if isinstance(typed_map["biodegradation"], list) else [],
            ),
        )
        for _name, got, want in pairs:
            if _empty(got) and _empty(want):
                continue
            if _empty(want) and not _empty(got):
                mismatches += 1
                continue
            if not _same(got, want):
                mismatches += 1
    assert mismatches == 0


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _same(got, want) -> bool:
    if isinstance(got, (int, float)) and isinstance(want, (int, float)):
        return math.isclose(float(got), float(want), rel_tol=1e-9, abs_tol=1e-9)
    return got == want


def test_biodegradation_after_rename_vs_broken_heading(monkeypatch):
    _forbid_network(monkeypatch)
    payload = _data(registry.call(
        "get_solvent_safety_card",
        solvent_name=_TOLUENE,
        include_pubchem=True,
    ))
    served = payload["safety_profile"]["toxicity"]["biodegradation"]
    assert served
    loaded = safety._load_snapshot()
    broken = loaded["connection"].execute(
        "SELECT biodegradation_broken_heading FROM pubchem_safety WHERE cid = ?",
        [_TOLUENE_CID],
    ).fetchone()[0]
    parsed = safety._json_field(broken) or []
    assert parsed == [] or _empty(parsed)


def test_cid_path_cassette_fetches_eight_names_never_biodegradation(monkeypatch):
    seen: list[str] = []

    def fake_request(url: str) -> dict:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        heading = qs.get("heading", [None])[0]
        seen.append(heading)
        assert heading != "Biodegradation"
        return _empty_payload(url)

    monkeypatch.setattr(safety, "_request_json", fake_request)
    safety._heading.cache_clear()
    safety._pubchem.cache_clear()
    before = hashlib.sha256(_SNAPSHOT.read_bytes()).hexdigest()
    payload = _data(registry.call("fetch_solvent_safety_by_cid", cid=_TOLUENE_CID))
    after = hashlib.sha256(_SNAPSHOT.read_bytes()).hexdigest()
    assert payload["success"] is True
    assert payload["persisted"] is False
    assert after == before == _SNAPSHOT_SHA256
    assert len(seen) == 8
    assert set(seen) == set(_LIVE_HEADINGS)
    assert "Biodegradation" not in seen
    assert payload["safety_profile"]["provenance"]["pubchem_source"] == "live"
    assert payload["safety_profile"]["provenance"]["pubchem_fetched_at"]
    assert payload["field_origin"] or payload["safety_profile"]["provenance"]["pubchem_source"] == "live"
    signature = inspect.signature(safety.fetch_solvent_safety_by_cid)
    assert list(signature.parameters) == ["cid"]
    missing = _data(registry.call("fetch_solvent_safety_by_cid"))
    assert missing["success"] is False
    assert missing["error_code"] == "missing_cid"
    invalid = _data(registry.call("fetch_solvent_safety_by_cid", cid="toluene"))
    assert invalid["success"] is False
    assert invalid["error_code"] == "invalid_cid"


def test_doctor_adds_snapshot_pin_after_registry_only(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    monkeypatch.delenv("DISSOLVE_SAFETY_SNAPSHOT", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    names = [c["name"] for c in report["checks"]]
    assets_at = names.index("Scientific assets")
    assert names[assets_at] == "Scientific assets"
    assert names[assets_at + 1] == "Published hazard Methods"
    assert names[assets_at + 2] == "Tool registry"
    assert names[assets_at + 3] == "PubChem safety snapshot"
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["Scientific assets"]["checked"] == 6
    assert by_name["Scientific assets"]["detail"] == "6 checksums verified"
    assert by_name["Tool registry"]["detail"] == "29 registered names"
    snap = by_name["PubChem safety snapshot"]
    assert snap["status"] == "pass"
    assert snap["digest"] == _SNAPSHOT_SHA256
    assert snap["expected"] == _SNAPSHOT_SHA256
    assert os.path.expanduser(snap["path"]) == str(_SNAPSHOT)
    methods = by_name["Published hazard Methods"]
    assert methods["detail"] == (
        "n=990 GSK=130 GreenSolventDB=840 neither=20 both=128 "
        "MAE=0.30 signed=+0.01 floor=6.0 unsourced"
    )
    assert methods["n"] == 990
    assert methods["served_gsk"] == 130
    assert methods["served_green"] == 840
    assert methods["served_neither"] == 20
    assert methods["both_table_hits"] == 128
    assert methods["mae_2dp"] == 0.3
    assert methods["default_minimum_g_score"] == 6.0
