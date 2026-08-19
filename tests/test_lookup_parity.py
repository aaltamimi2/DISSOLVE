"""§10.1 lookup capability-parity. Do not retire lookup_admitted_process_records."""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import dispatch, tool_schemas
from dissolve import campaign_consume, registry, tea

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
_LOOKUP_METRIC_TOKENS = (
    "etox", "htc", "htnc", "electricity", "heating", "cooling", "energy",
)
_ROUTE_SUFFIXES = ("-route-c1", "-route-c2", "-route-c3")


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("lookup parity must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _schema(name: str) -> dict:
    return next(item for item in tool_schemas() if item["name"] == name)


def _axis_labels_for(polymer: str) -> set[str]:
    labels = set()
    for record in tea._records():
        config = record.get("config") or {}
        label = str(record.get("label") or "")
        if str(config.get("target_plastic") or "") != polymer:
            continue
        if tea._sensitivity_axis_for_record(label):
            labels.add(label)
    return labels


def _write_registry(tmp_path: Path, entries: dict) -> Path:
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


def test_lookup_name_is_still_registered():
    assert "lookup_admitted_process_records" in registry.BY_NAME
    assert "evaluate_process" not in registry.BY_NAME


def test_missing_polymer_is_named_not_a_cache_dump(monkeypatch):
    _forbid_live(monkeypatch)
    cache_n = len(tea._records())
    assert cache_n > 0
    omitted = _data(tea.lookup_admitted_process_records())
    blank = _data(tea.lookup_admitted_process_records(target_polymer=""))
    empty_list = _data(tea.lookup_admitted_process_records(target_polymer=[]))
    whitespace = _data(tea.lookup_admitted_process_records(target_polymer="  "))
    for payload in (omitted, blank, empty_list, whitespace):
        assert payload.get("success") is False
        assert payload.get("error_code") == "missing_target_polymer"
        assert payload.get("error_code") != "invalid_admitted_record_query"
        assert "comparison_rows" not in payload
        assert payload.get("record_count") is None
    unknown = _data(tea.lookup_admitted_process_records(
        target_polymer="not-a-stored-polymer",
    ))
    assert unknown.get("success") is False
    assert unknown.get("error_code") == "invalid_admitted_record_query"
    assert unknown.get("error_code") != "missing_target_polymer"
    dispatched = dispatch("lookup_admitted_process_records")
    assert dispatched.get("available") is False
    assert dispatched.get("refusal") == "missing_target_polymer"
    assert "handle" not in dispatched
    assert len(tea._records()) == cache_n


def test_campaign_lookup_may_omit_polymer(monkeypatch, tmp_path):
    _forbid_live(monkeypatch)
    registry_path = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(campaign_consume.REGISTRY_ENV, str(registry_path))
    payload = _data(tea.lookup_admitted_process_records(
        source="campaign",
        campaign_fingerprint=_CANONICAL,
    ))
    assert payload.get("success") is True
    assert payload.get("error_code") != "missing_target_polymer"
    assert payload.get("n_rows_consumed") == 462
    assert len(payload["comparison_rows"]) == 462
    assert payload.get("ingested_into_admitted_cache") is False


def test_default_population_excludes_sensitivity_axis_records(monkeypatch):
    _forbid_live(monkeypatch)
    axis_labels = _axis_labels_for("LDPE")
    assert axis_labels
    payload = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
    ))
    assert payload.get("success") is True
    assert payload["analysis_type"] == "admitted_process_energy_case_records"
    labels = [str(row.get("label") or "") for row in payload["comparison_rows"]]
    assert labels
    assert not (set(labels) & axis_labels)
    assert all(label.casefold().endswith(_ROUTE_SUFFIXES) for label in labels)
    assert all(
        tea._sensitivity_axis_for_record(label) is None for label in labels
    )


def test_sensitivity_selectors_stay_on_lookup(monkeypatch):
    _forbid_live(monkeypatch)
    lookup_params = inspect.signature(tea.lookup_admitted_process_records).parameters
    for name in (
        "sensitivity_labels",
        "sensitivity_axes",
        "sensitivity_level_selector",
    ):
        assert name in lookup_params
        assert name not in inspect.signature(
            tea.evaluate_tea_lca_scenarios,
        ).parameters
        assert name not in inspect.signature(
            tea.analyze_tea_sensitivity,
        ).parameters
        assert name not in _schema("evaluate_tea_lca_scenarios")[
            "parameters"
        ]["properties"]
        assert name not in _schema("analyze_tea_sensitivity")[
            "parameters"
        ]["properties"]
    payload = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        sensitivity_axes=["solvent_price"],
    ))
    assert payload.get("success") is True
    assert payload["analysis_type"] == "admitted_process_sensitivity_records"
    labels = {str(row.get("label") or "") for row in payload["comparison_rows"]}
    assert "ldpe-route-c1" in {item.casefold() for item in labels}
    assert any(
        tea._sensitivity_axis_for_record(label) == "solvent_price"
        for label in labels
    )
    assert not any(
        str(label).casefold().endswith(("-route-c2", "-route-c3"))
        for label in labels
    )


def test_requested_metrics_keep_lca_and_operations_tokens(monkeypatch):
    _forbid_live(monkeypatch)
    lookup_schema = _schema("lookup_admitted_process_records")
    metric_enum = (
        lookup_schema["parameters"]["properties"]["requested_metrics"]
        ["items"]["enum"]
    )
    for token in _LOOKUP_METRIC_TOKENS:
        assert token in metric_enum
    default = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
    ))
    assert default.get("success") is True
    for token in _LOOKUP_METRIC_TOKENS:
        assert token in default["requested_metrics"]
        assert token in default["metric_units"]
    narrowed = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        requested_metrics=["etox", "energy"],
    ))
    assert narrowed.get("success") is True
    assert narrowed["requested_metrics"] == ["etox", "energy"]
    assert set(narrowed["metric_units"]) == {"etox", "energy"}
    eval_props = _schema("evaluate_tea_lca_scenarios")["parameters"]["properties"]
    assert "requested_metrics" not in eval_props


def test_lookup_energy_cases_are_a_list(monkeypatch):
    _forbid_live(monkeypatch)
    lookup_props = _schema("lookup_admitted_process_records")[
        "parameters"
    ]["properties"]
    eval_props = _schema("evaluate_tea_lca_scenarios")["parameters"]["properties"]
    sensitivity_props = _schema("analyze_tea_sensitivity")[
        "parameters"
    ]["properties"]
    assert lookup_props["energy_cases"]["type"] == "array"
    assert "energy_case" not in lookup_props
    assert "energy_cases" not in eval_props
    assert "energy_cases" not in sensitivity_props
    one = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        energy_cases=["C1"],
    ))
    assert one.get("success") is True
    labels = [str(row.get("label") or "").casefold() for row in one["comparison_rows"]]
    assert labels
    assert all(label.endswith("-route-c1") for label in labels)
    assert one["requested_energy_cases"] == ["C1"]
    two = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        energy_cases=["C1", "C2"],
    ))
    assert two.get("success") is True
    two_labels = {
        str(row.get("label") or "").casefold() for row in two["comparison_rows"]
    }
    assert any(label.endswith("-route-c1") for label in two_labels)
    assert any(label.endswith("-route-c2") for label in two_labels)
    assert not any(label.endswith("-route-c3") for label in two_labels)


def test_gap_names_available_pairs_and_cached_polymers(monkeypatch):
    _forbid_live(monkeypatch)
    missing_polymer = _data(tea.lookup_admitted_process_records(
        target_polymer="PET",
    ))
    assert missing_polymer.get("success") is False
    assert missing_polymer.get("error_code") == "admitted_process_record_gap"
    assert missing_polymer["analysis_type"] == "admitted_process_record_gap"
    assert "available_pair_configurations" in missing_polymer
    assert "LDPE" in missing_polymer["cached_target_polymers"]
    assert "EVOH" in missing_polymer["cached_target_polymers"]
    assert "PET" not in missing_polymer["cached_target_polymers"]
    c1 = next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == "ldpe-route-c1"
    )
    other_capacity = float(c1["config"]["processing_capacity"]) * 2
    missing_capacity = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        processing_capacity_mt_per_yr=other_capacity,
    ))
    assert missing_capacity.get("error_code") == "admitted_process_record_gap"
    assert missing_capacity["available_pair_configurations"]
    assert any(
        str(item.get("record_id") or "").casefold() == "ldpe-route-c1"
        for item in missing_capacity["available_pair_configurations"]
    )


def test_record_form_is_echoed_and_does_not_reshape(monkeypatch):
    _forbid_live(monkeypatch)
    per_record = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        record_form="per_record",
    ))
    grouped = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        record_form="grouped_comparison",
    ))
    assert per_record.get("success") is True
    assert grouped.get("success") is True
    assert per_record["record_form"] == "per_record"
    assert grouped["record_form"] == "grouped_comparison"
    assert per_record["record_count"] == grouped["record_count"]
    assert [
        row["label"] for row in per_record["comparison_rows"]
    ] == [
        row["label"] for row in grouped["comparison_rows"]
    ]
    unknown = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        record_form="not-a-record-form",
    ))
    assert unknown.get("success") is False
    assert unknown.get("error_code") == "unknown_admitted_record_form"
