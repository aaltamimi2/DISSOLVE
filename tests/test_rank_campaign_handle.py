"""A campaign lookup handle is a process_rows rank source. Not a live child."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import SYSTEM_PROMPT, dispatch, source_basis_for
from dissolve import campaign_consume, landscape, tea
from dissolve.session import bind_tool_session, new_session, store_handle

_SEALED = Path(
    "/home/aaltamimi2/dissolve-v12-campaign/"
    "polymer-solvent-tea-lca-20260818"
)
_CANONICAL = (
    "ef62efb3a708d782ce58cac3295cc9de71b0a7e8d076f6ca79f1ba5830a29636"
)
_APPEND_LOG = (
    "8b11ee68ce9948418872d892076bf275cb8b0dc1f95e72b0af80bbd2ac1eee3a"
)


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _write_registry(tmp_path: Path, entries: dict) -> dict:
    path = tmp_path / "campaign_registry.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _sealed_entry() -> dict:
    manifest = _SEALED / "manifest.json"
    return {
        "manifest_path": str(manifest),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "append_log_aliases": [_APPEND_LOG],
    }


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("campaign-handle rank must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _count_jsonl(monkeypatch):
    reads = {"load_filtered_rows": 0}
    real = landscape.load_filtered_rows

    def wrapped(*args, **kwargs):
        reads["load_filtered_rows"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(landscape, "load_filtered_rows", wrapped)
    return reads


def _record_by_label(label: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == label
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


def test_campaign_lookup_handle_ranks_without_rereading_jsonl(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    reads = _count_jsonl(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            source="campaign",
            campaign_fingerprint=_CANONICAL,
        ))
        assert lookup.get("success") is True
        assert len(lookup["comparison_rows"]) == 462
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        payload = _data(tea.rank_landscape(handle=handle))
    assert payload.get("success") is True
    assert payload["analysis_type"] == "campaign_process_rows_landscape"
    assert payload["n_usable"] == 408
    assert payload["n_groups"] == 10
    assert len(payload["grouped_fronts"]) == 10
    assert payload["ingested_into_admitted_cache"] is False
    assert payload["campaign_fingerprint"] == _CANONICAL
    assert payload["campaign_basis"]["n_pairs"] == 462
    assert reads["load_filtered_rows"] == 0
    assert len(tea._records()) == 24


def test_campaign_handle_held_mismatch_is_not_a_ranking(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    reads = _count_jsonl(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            source="campaign",
            campaign_fingerprint=_CANONICAL,
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        burned = _data(tea.rank_landscape(
            handle=handle,
            process_config={"burn_leftover_plastic": True},
        ))
        shifted = _data(tea.rank_landscape(
            handle=handle,
            process_config={
                "target_mass_percent": 60.0,
                "processing_capacity_mt_per_yr": 15_000.0,
            },
            energy_cases=["C2"],
        ))
    assert burned["error_code"] == "campaign_basis_mismatch"
    assert burned["mismatches"][0]["field"] == "burn_leftover_plastic"
    assert burned["mismatches"][0]["campaign_value"] is False
    assert burned["mismatches"][0]["requested_value"] is True
    assert "landscape_points" not in burned
    assert "grouped_fronts" not in burned
    assert shifted["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in shifted["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]
    assert shifted["live_rerun_quote"]["n_pairs"] == 462
    assert "landscape_points" not in shifted
    assert reads["load_filtered_rows"] == 0


def test_ldpe_campaign_lookup_handle_is_one_polymer_front(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    reads = _count_jsonl(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            source="campaign",
            campaign_fingerprint=_CANONICAL,
            target_polymer="LDPE",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        payload = _data(tea.rank_landscape(handle=handle))
    assert lookup["matching_row_count"] == 60
    assert payload.get("success") is True
    assert payload["n_usable"] == 54
    assert payload["n_landscape_points"] == 54
    assert payload["n_landscape_points"] == len(payload["landscape_points"])
    assert {point["target_polymer"] for point in payload["landscape_points"]} == {
        "LDPE",
    }
    assert "grouped_fronts" not in payload
    assert reads["load_filtered_rows"] == 0


def test_disagreeing_fingerprint_on_campaign_handle_mismatches(
    monkeypatch, tmp_path,
):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            source="campaign",
            campaign_fingerprint=_CANONICAL,
            target_polymer="LDPE",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        payload = _data(tea.rank_landscape(
            handle=handle,
            campaign_fingerprint="ff" * 32,
        ))
    assert payload["error_code"] == "campaign_fingerprint_mismatch"
    assert "grouped_fronts" not in payload
    assert "landscape_points" not in payload


def test_evaluate_handle_still_ignores_campaign_held_constraints(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_tea_lca_scenarios(
            [_public_from_record(c1), _public_from_record(c2)],
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        payload = _data(tea.rank_landscape(
            handle=handle,
            process_config={"burn_leftover_plastic": True},
        ))
    assert payload.get("success") is True
    assert payload["n_usable"] == 2
    assert payload.get("error_code") != "campaign_basis_mismatch"
    assert payload["analysis_type"] == "process_rows_landscape"


def test_campaign_lookup_basis_is_not_cache_exact():
    campaign = {
        "success": True,
        "source": "campaign",
        "engine_mode": "campaign",
        "comparison_rows": [{"polymer": "LDPE"}, {"polymer": "PET"}],
    }
    cache = {
        "success": True,
        "engine_mode": "cache",
        "cache_match_status": "exact",
        "comparison_rows": [{"engine_mode": "cache"}],
    }
    assert source_basis_for(
        "evaluate_process", campaign, {},
    ) == "campaign_process_rows"
    assert source_basis_for(
        "evaluate_process", cache, {},
    ) == "tea_cache_exact"
    ranked = {
        "success": True,
        "source": "process_rows",
        "engine_mode": "campaign",
        "grouped_fronts": [{"target_polymer": "LDPE"}],
    }
    assert source_basis_for("rank_landscape", ranked, {}) == "campaign_process_rows"
    assert "campaign_process_rows" in SYSTEM_PROMPT
    assert "not tea_cache_exact" in SYSTEM_PROMPT


def test_dispatch_mints_a_campaign_lookup_handle(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry))
    _forbid_live(monkeypatch)
    reads = _count_jsonl(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = dispatch(
            "evaluate_process",
            mode="lookup",
            lookup_filter={
                "source": "campaign",
                "campaign_fingerprint": _CANONICAL,
            },
        )
        assert lookup.get("available") is True
        assert lookup.get("refusal") != "no_honest_basis"
        assert lookup["source_basis"] == "campaign_process_rows"
        assert lookup["source_basis"] != "tea_cache_exact"
        handle = lookup.get("handle")
        assert handle
        assert lookup["total"] == 462
        ranked = dispatch("rank_landscape", handle=handle)
    assert ranked.get("available") is True
    assert ranked.get("refusal") != "no_honest_basis"
    assert ranked["source_basis"] == "campaign_process_rows"
    data = ranked.get("data") or {}
    assert data.get("n_usable") == 408
    assert data.get("n_groups") == 10
    assert data.get("ingested_into_admitted_cache") is False
    assert reads["load_filtered_rows"] == 0
    assert len(tea._records()) == 24
