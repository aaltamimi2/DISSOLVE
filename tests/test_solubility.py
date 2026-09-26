"""Solubility tests."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from dissolve import agent, analysis, tea, tea_ranking
from dissolve import thermodynamics as thermo
from dissolve.agent import dispatch, tool_schemas
from dissolve.cli import EXPECTED_REGISTRY_NAMES, CliApp, _parse_solvents_slash
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session
from dissolve.thermodynamics import solubility_query


@pytest.fixture(autouse=True)
def _live_tea_works(monkeypatch, tmp_path):
    """TEA answers only when live TEA works; these tests stand in a working engine (tests of the check itself
    override this) and never see this checkout's own live environment (.venv-tea, vendor/plastics)."""
    monkeypatch.setattr(tea, "_live_tea_blocker", lambda: None)
    monkeypatch.setattr(tea, "_REPO_TEA_PYTHON", tmp_path / "no-venv-tea" / "python")
    monkeypatch.setattr(tea.tea_polymer_parameters, "VENDORED_PLASTICS", tmp_path / "no-vendored-plastics")

# --- from test_lookup_parity.py: §10.1 lookup capability-parity on evaluate_process(mode=lookup).
_SEALED = tea_ranking.SHIPPED_CAMPAIGN


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


def test_lookup_name_is_unregistered_successor_stays():
    assert "lookup_admitted_process_records" not in agent.BY_NAME
    assert "evaluate_process" in agent.BY_NAME
    assert callable(tea.lookup_admitted_process_records)


def test_missing_polymer_is_named_not_a_cache_dump(monkeypatch):
    _forbid_live(monkeypatch)
    cache_n = len(tea._records())
    assert cache_n > 0
    omitted = _data(tea.lookup_admitted_process_records())
    blank = _data(tea.lookup_admitted_process_records(target_polymer=""))
    empty_list = _data(tea.lookup_admitted_process_records(target_polymer=[]))
    whitespace = _data(tea.lookup_admitted_process_records(target_polymer="  "))
    wrapped = _data(tea.evaluate_process(mode="lookup"))
    for payload in (omitted, blank, empty_list, whitespace, wrapped):
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
    wrapped_unknown = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "not-a-stored-polymer"},
    ))
    assert wrapped_unknown.get("error_code") == "invalid_admitted_record_query"
    retired = dispatch("lookup_admitted_process_records")
    assert retired.get("available") is False
    assert retired.get("refusal") == "unknown_tool"
    dispatched = dispatch("evaluate_process", mode="lookup")
    assert dispatched.get("available") is False
    assert dispatched.get("refusal") == "missing_target_polymer"
    assert "handle" not in dispatched
    assert len(tea._records()) == cache_n


def test_campaign_lookup_may_omit_polymer(monkeypatch, tmp_path):
    _forbid_live(monkeypatch)
    registry_path = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    monkeypatch.setenv(tea_ranking.REGISTRY_ENV, str(registry_path))
    payload = _data(tea.lookup_admitted_process_records(
        source="campaign",
        campaign_fingerprint=_CANONICAL,
    ))
    wrapped = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "source": "campaign",
            "campaign_fingerprint": _CANONICAL,
        },
    ))
    assert payload.get("success") is True
    assert wrapped.get("success") is True
    assert wrapped.get("tool_name") == "evaluate_process"
    assert payload.get("error_code") != "missing_target_polymer"
    assert wrapped.get("n_rows_consumed") == 462
    assert len(payload["comparison_rows"]) == 462
    assert len(wrapped["comparison_rows"]) == 462
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
        assert name not in _schema("evaluate_process")[
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
    lookup_params = inspect.signature(tea.lookup_admitted_process_records).parameters
    assert "requested_metrics" in lookup_params
    default = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
    ))
    wrapped_default = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE", "solvent": "Dodecane"},
    ))
    assert default.get("success") is True
    assert wrapped_default.get("success") is True
    for token in _LOOKUP_METRIC_TOKENS:
        assert token in default["requested_metrics"]
        assert token in default["metric_units"]
        assert token in wrapped_default["requested_metrics"]
    narrowed = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        requested_metrics=["etox", "energy"],
    ))
    wrapped_narrowed = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "requested_metrics": ["etox", "energy"],
        },
    ))
    assert narrowed.get("success") is True
    assert narrowed["requested_metrics"] == ["etox", "energy"]
    assert set(narrowed["metric_units"]) == {"etox", "energy"}
    assert wrapped_narrowed["requested_metrics"] == ["etox", "energy"]
    eval_props = _schema("evaluate_process")["parameters"]["properties"]
    assert "requested_metrics" not in eval_props


def test_lookup_energy_cases_are_a_list(monkeypatch):
    _forbid_live(monkeypatch)
    lookup_params = inspect.signature(tea.lookup_admitted_process_records).parameters
    eval_props = _schema("evaluate_process")["parameters"]["properties"]
    sensitivity_params = inspect.signature(tea.analyze_tea_sensitivity).parameters
    assert "energy_cases" in lookup_params
    assert "energy_case" not in lookup_params
    assert "energy_cases" not in eval_props
    assert "energy_cases" not in sensitivity_params
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


# --- from test_solvent_scope.py: `/solvents` toggle — first wiring slice against SOLVENTS_TOGGLE_SPEC.rev2.
def _data_solvent_scope(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def test_freeze_set_is_closed():
    roster = thermo._available_solvents()
    assert len(thermo.COMMON_INTERP_KEYS) == 69
    assert thermo.COMMON_INTERP_KEYS <= roster
    assert "h2o" not in thermo.COMMON_INTERP_KEYS
    assert "h2o" in roster
    assert "triethylamine" in thermo.COMMON_INTERP_KEYS
    assert len(thermo.get_available_solvents()) == 990
    assert thermo.get_solvent_catalog_provenance()["fitted_solvent_count"] == 990


def test_common_universe_is_intersection_not_a_second_policy():
    with thermo.bind_query_solvent_scope("common"):
        active = thermo.get_available_solvents()
        assert active == set(thermo._available_solvents()) & thermo.COMMON_INTERP_KEYS
        assert len(active) == 69
        assert "h2o" not in active
        assert "toluene" in active
        assert thermo.get_solvent_catalog_provenance()["fitted_solvent_count"] == 990
        stamp = thermo.solvent_scope_stamp()
        assert stamp == {
            "solvent_scope": "common",
            "solvent_scope_origin": "query",
            "solvent_scope_n": 69,
        }
        polymer_set = thermo.get_available_solvents_for_polymer("LDPE")
        assert polymer_set <= active


def test_named_out_of_scope_refuses_not_unknown():
    with bind_tool_session(new_session()):
        with thermo.bind_query_solvent_scope("common"):
            payload = _data_solvent_scope(solubility_query(
                polymers=["LDPE"], solvents=["n-methyl-2-pyrrolidinone"],
                temperatures=[80.0],
            ))
    assert payload["success"] is False
    assert payload["error_code"] == "solvent_not_in_scope"
    assert payload["solvent_scope"] == "common"
    assert payload["solvent_scope_origin"] == "query"
    water = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["h2o"], temperatures=[80.0],
        solvent_scope="common",
    ))
    assert water["success"] is False
    assert water["error_code"] == "solvent_not_in_scope"
    unknown = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["not-a-real-solvent-xx"], temperatures=[80.0],
        solvent_scope="common",
    ))
    assert unknown["success"] is False
    assert unknown["error_code"] == "unknown_solvents"
    assert unknown["available_count"] == 990
    assert "available_solvents" not in unknown
    unknown_all = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["not-a-real-solvent-xx"], temperatures=[80.0],
        solvent_scope="all",
    ))
    assert unknown_all["error_code"] == "unknown_solvents"
    assert unknown_all["available_count"] == 990


def test_surviving_solvent_number_identity():
    all_payload = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["toluene"], temperatures=[80.0],
        solvent_scope="all",
    ))
    common_payload = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["toluene"], temperatures=[80.0],
        solvent_scope="common",
    ))
    assert all_payload["success"] is True
    assert common_payload["success"] is True
    assert all_payload["results"][0]["solubility_pct"] == (
        common_payload["results"][0]["solubility_pct"]
    )
    assert common_payload["solvent_scope"] == "common"
    assert common_payload["solvent_scope_n"] == 69
    assert all_payload["solvent_scope"] == "all"
    assert all_payload["solvent_scope_n"] == 990


def test_omit_solvents_common_shrinks_axis_not_fitted_count():
    payload = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], temperatures=[80.0], solvent_scope="common",
    ))
    assert payload["success"] is True
    assert payload["selection"]["solvent_count"] == 69
    assert payload["solvent_scope_n"] == 69
    provenance = payload.get("solvent_catalog_provenance") or {}
    # query payload may not include provenance; fitted stays 990 on the API
    assert thermo.get_solvent_catalog_provenance()["fitted_solvent_count"] == 990


def test_under_coverage_denominator_is_scope_n():
    from dissolve.thermodynamics import screen_polymer_separation

    payload = _data_solvent_scope(screen_polymer_separation(
        feed_polymers=["LDPE", "PP"],
        temperature_min_c=80.0,
        temperature_max_c=80.0,
        solvent_scope="common",
    ))
    assert payload["success"] is True
    assert payload["solvent_scope"] == "common"
    assert payload["solvent_scope_n"] == 69
    provenance = payload["solvent_catalog_provenance"]
    assert provenance["fitted_solvent_count"] == 990
    under = provenance.get("under_covered_polymers") or {}
    for count in under.values():
        assert count < 0.90 * 69


def test_unknown_solvents_available_count_stays_on_the_990():
    from dissolve.thermodynamics import screen_polymer_separation

    query = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["not-a-real-solvent-xx"],
        temperatures=[80.0], solvent_scope="common",
    ))
    assert query["error_code"] == "unknown_solvents"
    assert query["available_count"] == 990
    screen = _data_solvent_scope(screen_polymer_separation(
        feed_polymers=["LDPE", "PP"],
        solvents=["not-a-real-solvent-xx"],
        temperature_min_c=80.0,
        temperature_max_c=80.0,
        solvent_scope="common",
    ))
    assert screen["error_code"] == "unknown_solvents"
    assert screen["available_count"] == 990
    nmp = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["n-methyl-2-pyrrolidinone"],
        temperatures=[80.0], solvent_scope="common",
    ))
    assert nmp["error_code"] == "solvent_not_in_scope"


def test_session_default_and_clear(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    app = CliApp(
        session_id="solvents-session",
        store_root=tmp_path,
        console=Console(file=io.StringIO()),
    )
    assert _parse_solvents_slash([]) is None
    assert _parse_solvents_slash(["common"]) == {"scope": "common"}
    try:
        _parse_solvents_slash(["nope"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert app.handle_command("/solvents common") is False
    assert app.session.get("solvent_scope") == {"scope": "common"}
    session = dict(app.session)
    with bind_tool_session(session):
        payload = _data_solvent_scope(solubility_query(polymers=["LDPE"], temperatures=[80.0]))
    assert payload["solvent_scope"] == "common"
    assert payload["solvent_scope_origin"] == "session_default"
    assert payload["selection"]["solvent_count"] == 69
    assert app.handle_command("/clear") is False
    assert app.session["solvent_scope"] == {"scope": "common"}  # a new session screens the curated solvents
    assert "handle_command" not in inspect.getsource(app.ask)


def test_a_session_can_live_in_a_store_the_caller_supplies(tmp_path, monkeypatch):
    """The hosted web app lost every chat at each redeploy: sessions were files in its container (owner, 2026-09-24).
    CliApp now takes any store with _Store's load, save and append, and a database keeps the web app's."""
    import io

    from rich.console import Console

    class MemoryStore:
        def __init__(self):
            self.session_id, self.state, self.events = "kept-session", None, []

        def load(self):
            return self.state

        def save(self, payload):
            self.state = json.loads(json.dumps(payload))

        def append(self, role, content, **metadata):
            self.events.append((role, content))

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    store = MemoryStore()
    app = CliApp(store=store, console=Console(file=io.StringIO()))
    assert app.handle_command("/solvents all") is False
    assert store.state["session"]["solvent_scope"] == {"scope": "all"}
    assert app.store is store and store.events == []  # a slash command changes state; it is not a chat turn
    again = CliApp(store=store, console=Console(file=io.StringIO()))  # a new process, as after a redeploy
    assert again.session["solvent_scope"] == {"scope": "all"} and again.store.session_id == "kept-session"


def test_bare_solvents_non_tty_prints_status(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-status",
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
    )
    assert app.handle_command("/solvents") is False
    assert app.session["solvent_scope"] == {"scope": "common"}
    assert "solvent_scope=common" in buf.getvalue()


def test_bare_solvents_dumb_term_prints_status(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    def boom(*_a, **_k):
        raise AssertionError("TUI must not open on a dumb terminal")

    monkeypatch.setattr("dissolve.cli._run_arrow_picker", boom)
    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-dumb",
        store_root=tmp_path,
        console=Console(file=buf),
    )
    assert app.handle_command("/solvents") is False
    assert app.session["solvent_scope"] == {"scope": "common"}
    assert "solvent_scope=common" in buf.getvalue()


def test_bare_solvents_stdout_pipe_prints_status(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-stdout",
        store_root=tmp_path,
        console=Console(file=buf),
    )
    assert app.handle_command("/solvents") is False
    assert app.session["solvent_scope"] == {"scope": "common"}


def test_bare_solvents_picker_sets_common(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    from dissolve.cli import _solvents_picker_options

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    options, selected = _solvents_picker_options("all")
    assert selected == 1
    labels = " ".join(label for _value, label in options)
    assert "69" in labels
    assert "990" in labels
    assert "786" not in labels
    assert "(current)" in options[1][1]

    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-pick",
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
    )
    app._handle_solvents_command(
        [], picker_fn=lambda **_k: "common",
    )
    assert app.session.get("solvent_scope") == {"scope": "common"}
    app._handle_solvents_command([], picker_fn=lambda **_k: None)
    assert app.session.get("solvent_scope") == {"scope": "common"}


def test_bare_solvents_picker_rejects_unknown(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    app = CliApp(
        session_id="solvents-bad",
        store_root=tmp_path,
        console=Console(file=io.StringIO()),
    )
    app._handle_solvents_command([], picker_fn=lambda **_k: "nope")
    assert app.session["solvent_scope"] == {"scope": "common"}


def test_bare_solvents_quiet_never_prompts(tmp_path, monkeypatch):
    import io

    from rich.console import Console

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    buf = io.StringIO()
    app = CliApp(
        session_id="solvents-quiet",
        store_root=tmp_path,
        console=Console(file=buf),
        quiet=True,
    )
    app._handle_solvents_command([], picker_fn=None)
    assert app.session["solvent_scope"] == {"scope": "common"}


def test_query_overrides_session():
    session = new_session()
    session["solvent_scope"] = {"scope": "common"}
    with bind_tool_session(session):
        payload = _data_solvent_scope(solubility_query(
            polymers=["LDPE"], temperatures=[80.0], solvent_scope="all",
        ))
    assert payload["solvent_scope"] == "all"
    assert payload["solvent_scope_origin"] == "query"
    assert payload["selection"]["solvent_count"] == 990


def test_invalid_scope_token():
    payload = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], temperatures=[80.0], solvent_scope="nope",
    ))
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_solvent_scope"


def test_resolve_solvent_stays_on_the_990():
    with thermo.bind_query_solvent_scope("common"):
        assert thermo.resolve_solvent("n-methyl-2-pyrrolidinone") == (
            "n-methyl-2-pyrrolidinone"
        )
        assert thermo.resolve_solvent("h2o") == "h2o"
        assert "n-methyl-2-pyrrolidinone" not in thermo.get_available_solvents()


# --- from test_thermal_retirement.py: Accept tests for retiring the thermal estimator from the agent surface.
# Frozen 30-name registry at 7f3f7fb (thermal_retirement_BASELINE.json).
_PARENT_REGISTRY_NAMES = frozenset({
    "analyze_numeric_samples",
    "compare_contaminant_removal_modes",
    "compare_solvent_safety_at_conditions",
    "estimate_thermal_properties",
    "evaluate_process",
    "fetch_solvent_safety_by_cid",
    "get_solvent_safety_card",
    "ingest_literature_documents",
    "ingest_literature_graph",
    "inspect_literature_corpus",
    "list_thermal_evidence",
    "lookup_glass_transition",
    "lookup_hansen_parameters",
    "lookup_material_database_membership",
    "plan_multistage_separation",
    "rank_landscape",
    "resolve_polymer_data_scope",
    "screen_contaminant_leaching",
    "screen_contaminant_strap_removal",
    "screen_cool_then_reheat_getter",
    "screen_green_solvent_candidates",
    "screen_hansen_compatibility",
    "screen_pairwise_solubility_overlap",
    "screen_polymer_separation",
    "screen_precipitation_order",
    "screen_route_solvent_substitutions",
    "search_literature_corpus",
    "search_patent_literature",
    "search_scholarly_literature",
    "solubility_query",
})


# Spec omitted PSMILES; PET reconstruction is not unique.
_HDPE_PSMILES = "[*]CC[*]"


_PET_GROUP_MATCHED_PSMILES = "[*]C(C1=CC=C(C(OCCO[*])=O)C=C1)=O"


_PET_SECOND_ESTER_PSMILES = "[*]OCCOC(=O)c1ccc(C(=O)O)cc1[*]"


def _result(raw: str) -> dict:
    payload = parse_tool_result(raw)["data"]
    assert payload.get("success") is not False
    return payload["result"]


def test_a1_exactly_one_removal_by_identity_against_parent_30():
    now = frozenset(agent.BY_NAME)
    assert (_PARENT_REGISTRY_NAMES - now) == frozenset({"estimate_thermal_properties"})
    assert (now - _PARENT_REGISTRY_NAMES) == frozenset({
        "screen_contaminant_partitioning", "lookup_plastchem_contaminants"})  # added 2026-09-24 and 2026-09-25
    assert len(now) == 31
    assert now == EXPECTED_REGISTRY_NAMES
    assert len(agent.REGISTRY) == 31
    assert len(tool_schemas()) == 26
    assert "fetch_solvent_safety_by_cid" in now
    assert "estimate_thermal_properties" not in now
    names = {item["name"] for item in tool_schemas()}
    assert "estimate_thermal_properties" not in names
    assert "fetch_solvent_safety_by_cid" in names
    assert "result_read" in names


def test_a2_function_still_imports_and_computes_hdpe():
    from dissolve.analysis import estimate_thermal_properties

    assert callable(estimate_thermal_properties)
    tm_c = _result(estimate_thermal_properties(_HDPE_PSMILES))["tm_c"]
    assert abs(tm_c - 131) < 2


def test_a3_inventory_stops_advertising_and_keeps_tg_7736():
    rows = parse_tool_result(analysis.list_thermal_evidence())["data"]["rows"]
    assert len(rows) >= 4
    assert not any(
        "Van Krevelen" in str(row.get("capability")) and row.get("available") is True
        for row in rows
    )
    assert any(
        "Tg" in str(row.get("capability"))
        and row.get("available") is True
        and "7736" in str(row.get("basis"))
        for row in rows
    )
    vk = next(row for row in rows if "Van Krevelen" in str(row.get("capability")))
    assert vk["available"] is False
    assert "not offered to the agent" in vk["basis"]
    assert "present in the package" in vk["basis"]


def test_pet_group_matched_psmiles_pins_373_c_not_the_344_c_ester_form():
    matched = _result(analysis.estimate_thermal_properties(_PET_GROUP_MATCHED_PSMILES))
    other = _result(analysis.estimate_thermal_properties(_PET_SECOND_ESTER_PSMILES))
    assert abs(matched["tm_c"] - 373) < 1
    assert abs(other["tm_c"] - 344) < 1
    assert matched["groups"] == {
        "ester": 1, "phenylene": 1, "ether": 1, "methylene": 1,
    }


def test_a_shortlist_says_how_many_solvents_qualified():
    """Validation 2026-09-25: the PS-but-not-PP screen returned its default shortlist of three and the answer
    called them "the 3 screened solvents"; seven qualified. The screen now reports the total per target."""
    from dissolve import thermodynamics as thermo
    with bind_tool_session(new_session()):
        with thermo.bind_query_solvent_scope("common"):
            data = _data_solvent_scope(thermo.screen_polymer_separation(
                feed_polymers=["PS", "PP"], target_polymers=["PS"], require_atmospheric=True,
                temperature_min_c=25.0, temperature_max_c=25.0))
    assert data["shortlist_is_default"] is True and data["shortlist_per_target"] == 3
    assert data["qualifying_total_by_target"]["PS"] > len(data["ranked_candidates"])
    assert data["screened_directions"][0]["qualifying_total"] == data["qualifying_total_by_target"]["PS"]


def test_an_out_of_scope_refusal_names_its_remedy():
    """Validation 2026-09-25: "Does water dissolve LDPE at 80 °C?" was refused (water is outside the 69 common
    solvents) and the agent, told refusals are final, never asked again. The refusal now says how."""
    water = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["h2o"], temperatures=[80.0], solvent_scope="common"))
    assert water["error_code"] == "solvent_not_in_scope"
    assert "solvent_scope='all'" in water["remedy"]
    served = _data_solvent_scope(solubility_query(
        polymers=["LDPE"], solvents=["h2o"], temperatures=[80.0], solvent_scope="all"))
    assert served["success"] is True
    assert "repeat that call once with\nsolvent_scope='all'" in agent.SYSTEM_PROMPT


def test_saturation_ceilings_do_not_crowd_resolved_values_out_of_the_shortlist():
    """EVOH at 25 °C: five solvents on the 100 wt% saturation ceiling filled the default shortlist, so the answer had
    no resolved value to lead with (the prompt shows a capped option after resolved ones; review finding A03-R2).
    The ceilings keep their places (the planner and the TEA design points rely on them), and as many of the next
    resolved rows that meet the threshold follow them."""
    rows = parse_tool_result(thermo.screen_polymer_separation(
        feed_polymers=["EVOH"], temperature_min_c=25.0, temperature_max_c=25.0,
        ranking_mode="absolute_solubility", require_atmospheric=True))["data"]["ranked_candidates"]
    ceilings = [row for row in rows if row["is_clipped"]]
    resolved = [row for row in rows if not row["is_clipped"]]
    assert len(ceilings) == 5 and len({row["rank"] for row in ceilings}) == 1
    assert len(resolved) == 5 and all(row["meets_solubility_threshold"] for row in resolved)
    assert rows[:5] == ceilings


def test_keeping_a_polymer_intact_is_a_screen_limit_not_a_selectivity_cutoff():
    """"Dissolve PS but keep PP intact" listed 4 of the 7 solvents that meet both limits: a hidden 5-point selectivity
    cutoff dropped ethyl acetate (PS 5.39, PP 0.39 wt%), cyclohexanone and benzene, and the qualifying count still
    included THF, which dissolves PP at 1.99 wt%. With max_retained_pct the tool applies the user's two limits; without
    it, the count of intact solvents is published beside the selectivity count."""
    solvents = ["acetone", "DMF", "MEK", "cyclopentanone", "ethyl acetate", "cyclohexanone", "benzene", "THF",
                "tetrahydropyran", "toluene"]
    query = dict(feed_polymers=["PS", "PP"], target_polymers=["PS"], temperature_min_c=25.0, temperature_max_c=25.0,
                 require_atmospheric=True, solvents=solvents, top_k=10)
    plain = parse_tool_result(thermo.screen_polymer_separation(**query))["data"]
    assert plain["qualifying_total_by_target"] == {"PS": 6}
    assert plain["retained_intact_total_by_target"] == {"PS": 7}
    intact = parse_tool_result(thermo.screen_polymer_separation(**query, max_retained_pct=1.0))["data"]
    assert intact["qualifying_total_by_target"] == {"PS": 7}
    qualifying = [row["solvent"] for row in intact["ranked_candidates"] if row["meets_intact_limits"]]
    assert "Ethyl acetate" in qualifying and "Tetrahydrofuran (THF)" not in qualifying
    assert all(row["max_off_target_solubility_pct"] <= 1.0 for row in intact["ranked_candidates"][:7])
    assert intact["qualifying_rule"] == "target >= 5 wt% and every retained polymer <= 1 wt%"


def test_a_short_glass_transition_query_matches_whole_words_only():
    """"PE" returned the polyethylene row and also PEO, poly(pentadecalactone) and perfluoroalkoxy resin, whose names
    merely contain the letters (review finding A08-R1, live-checked). A partial match must be a whole word of at least
    three characters; "HDPE" still finds the row named "High-density polyethylene (HDPE)"."""
    names = lambda query: [(m["names"][0], m["match_type"]) for m in  # noqa: E731
                           parse_tool_result(analysis.lookup_glass_transition(query))["data"]["matches"]]
    assert names("PE") == [("PE", "exact_name_or_tag")]
    assert ("High-density polyethylene (HDPE)", "name_contains_word") in names("HDPE")
    assert all("PEO" != name for name, _ in names("PE"))


def test_a_precipitation_crossing_is_a_grid_node_with_its_bracket():
    """LDPE in toluene passes 1 wt% between the 60 and 65 °C nodes. The tool published 61.16 °C, an interpolation the
    grid cannot back, and the solubility states at that temperature then came back empty (review finding A04-R1).
    The crossing is now the first node below the threshold on cooling, with its bracket."""
    from dissolve import separation
    crossing = separation._threshold_crossing("LDPE", "toluene", 60.0, 100.0, 1.0)
    assert crossing == {"temperature_c": 60.0, "bracket_c": [60.0, 65.0], "status": "crossing_within_screen",
                        "method": thermo.GRID_EXACT}
    assert thermo.get_solubility_result("LDPE", "toluene", 60.0)["available"] is True


def test_a_screen_and_a_plan_say_which_temperature_each_row_stands_at():
    """With no temperature in the question each solvent sits at its own best grid point on 25-160 °C: 'which solvent
    separates PS from PVC?' came back as 145-160 °C conditions, and nothing told the answer to say so (review
    findings A03-R1, A04-R2)."""
    from dissolve import separation
    basis = lambda **kw: parse_tool_result(thermo.screen_polymer_separation(  # noqa: E731
        feed_polymers=["PS", "PVC"], **kw))["data"]["temperature_basis"]
    assert basis() == "not stated: each solvent at its own best grid temperature between 25 and 160 °C"
    assert basis(temperature_min_c=25.0, temperature_max_c=25.0) == "stated: 25 °C"
    assert basis(temperature_min_c=60.0, temperature_max_c=120.0).startswith("range: ")
    plan = parse_tool_result(separation.plan_multistage_separation(feed_polymers=["PS", "PP", "PET"]))["data"]
    assert plan["temperature_basis"] == "not stated: each step at its own best grid temperature between 25 and 160 °C"
