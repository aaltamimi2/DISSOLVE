"""Sensitivity sweeps dissolution_capacity and labor_cost. Not a live child."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import tea


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _route_c1() -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == "ldpe-route-c1"
    )


def _public_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        "precipitation_temperature_c": cfg["precipitation_temperature_c"],
        "solvent_price_usd_per_kg": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost_usd_per_employee_yr": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("capacity/labour sensitivity must not start live")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_unknown_parameter_lists_capacity_and_labour(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="recovery",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unsupported_parameter"
    supported = list(payload.get("supported_parameters") or [])
    assert supported == sorted(tea._NUMERIC_FIELDS)
    assert "dissolution_capacity" in supported
    assert "labor_cost" in supported


def test_energy_case_is_still_unsupported(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="energy_case",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unsupported_parameter"


def test_dissolution_capacity_is_no_longer_unsupported(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    discovered = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="dissolution_capacity",
        engine_mode="cache",
    ))
    assert discovered.get("error_code") != "unsupported_parameter"
    assert discovered.get("error_code") == "insufficient_sensitivity_values"
    swept = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="dissolution_capacity",
        values=[
            float(record["config"]["dissolution_capacity"]),
            float(record["config"]["dissolution_capacity"]) + 1.0,
        ],
        engine_mode="cache",
    ))
    assert swept.get("error_code") != "unsupported_parameter"
    assert swept.get("error_code") == "insufficient_sensitivity_results"
    rows = list(swept.get("sensitivity_rows") or [])
    assert len(rows) == 2
    assert {row["parameter"] for row in rows} == {"dissolution_capacity"}
    successes = [row for row in rows if row.get("success")]
    failures = [row for row in rows if not row.get("success")]
    assert len(successes) == 1
    assert len(failures) == 1
    assert successes[0]["dissolution_capacity"] == float(
        record["config"]["dissolution_capacity"],
    )
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert name in successes[0]


def test_labor_cost_public_alias_is_no_longer_unsupported(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    discovered = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="labor_cost_usd_per_employee_yr",
        engine_mode="cache",
    ))
    assert discovered.get("error_code") != "unsupported_parameter"
    assert discovered.get("error_code") == "insufficient_sensitivity_values"
    swept = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="labor_cost",
        values=[
            float(record["config"]["labor_cost"]),
            float(record["config"]["labor_cost"]) + 1000.0,
        ],
        engine_mode="cache",
    ))
    assert swept.get("error_code") != "unsupported_parameter"
    assert swept.get("error_code") == "insufficient_sensitivity_results"
    rows = list(swept.get("sensitivity_rows") or [])
    assert {row["parameter"] for row in rows} == {"labor_cost"}
    assert any(
        row.get("labor_cost_usd_per_employee_yr")
        == float(record["config"]["labor_cost"])
        and row.get("success")
        for row in rows
    )


def test_solvent_price_sweep_still_serves(monkeypatch):
    record = _route_c1()
    _forbid_live(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert len(payload["sensitivity_rows"]) >= 2
