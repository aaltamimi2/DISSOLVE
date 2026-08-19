"""Live TEA admission, parameter surface, and standing — no BioSTEAM."""
from __future__ import annotations

import ast
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dissolve import tea, tea_polymer_parameters as params, tea_worker
from dissolve.cli import doctor_report

_REAL_PLASTICS_PARENT = Path(
    "/home/aaltamimi2/langchain-STRAP-v8/reference-scripts/plastics-master-3"
)
_FIXTURE_OUTLINE = '''
STRAP_chemicals_outline = bst.ChemicalsOutline([
    "Toluene",
    bst.ChemicalDraft("PE", aliases=set(["Polyethylene"]), formula="C2H4"),
    bst.ChemicalDraft("PEoligomer", search_ID="1-Hexene"),
    bst.ChemicalDraft("PES", formula="C12H8O3S"),
    bst.ChemicalDraft("PESoligomer", search_ID="Diphenyl sulfone"),
    bst.ChemicalDraft("PET", formula="C10H8O4"),
    bst.ChemicalDraft("Nylon6", formula="C6H11NO"),
    bst.ChemicalDraft("Nylon6oligomer", search_ID="Caprolactam"),
    "Water",
])
'''


def _committed_pe_toluene_config(**overrides):
    committed = params.COMMITTED_PAIR_STEPS[("PE", "toluene")]
    config = {
        "solvent": "toluene",
        "target_plastic": "LDPE",
        "target_plastic_percent": 60.0,
        "processing_capacity": 20_000.0,
        "energy_case": "C1",
        **params.process_config_from_assumptions(committed),
        "solvent_price": 1.312,
        "solvent_loss_pct": 0.01,
        "feedstock_distance_km": 0.0,
        "labor_cost": 120_000.0,
    }
    config.update(overrides)
    return config


# Owner-authorized live BioSTEAM endpoints against merged v12. Injected
# standing-shape fixtures only — not produced by the worker in these tests.
LDPE_TOLUENE_LIVE = {
    "msp_usd_per_kg": 1.13492,
    "tci_usd": 7.85216e7,
    "aoc_usd_per_yr": 2.08159e6,
    "gwp_kg_co2e_per_kg": 0.443567,
}
LDPE_DODECANE_C1 = {
    "solvent": "Dodecane",
    "target_plastic": "LDPE",
    "target_plastic_percent": 55.0,
    "processing_capacity": 20_000.0,
    "energy_case": "C1",
    "dissolution_temperature_c": 145.0,
    "precipitation_temperature_c": 25.0,
    "solvent_price": 4.08,
    "solvent_loss_pct": 0.01,
    "feedstock_distance_km": 0.0,
    "dissolution_capacity": 3.0,
    "labor_cost": 120_000.0,
}
LDPE_DODECANE_LIVE_MSP = 1.2635722365577455


def _write_matching_pe_package(
    root: Path, *, pe_rho: str | None = "0.5 * (880 + 960)",
) -> Path:
    strap = root / "plastics" / "strap"
    strap.mkdir(parents=True)
    rho_line = f"        rho={pe_rho},\n" if pe_rho is not None else ""
    (strap / "property_package.py").write_text(
        f'''
STRAP_chemicals_outline = bst.ChemicalsOutline([
    bst.ChemicalDraft(
        "PE",
        formula="C2H4",
{rho_line}        Cp=0.5 * (1.330 + 2.400),
        Tm=0.5 * (115 + 135) + 273.15,
    ),
    bst.ChemicalDraft("PEoligomer", search_ID="1-Hexene"),
])
''',
        encoding="utf-8",
    )
    (strap / "dissolution_steps.py").write_text(
        """
def PE_Toluene_dissolution():
    return DissolutionStep(
        'PE', 'PEoligomer', 'Toluene', None, 0.03,
        0.5, 368.15, 0.5
    )
""",
        encoding="utf-8",
    )
    (strap / "precipitation_steps.py").write_text(
        """
def PE_Toluene_precipitation():
    return PrecipitationStep(
        'Toluene', 'PE', 'PEoligomer',
        0, 0.8, 0.4, 308.15, 0.5,
        None,
    )
""",
        encoding="utf-8",
    )
    return root


def test_expert_surface_lists_every_grid_polymer_including_refusals():
    assert set(params.POLYMERS) >= {
        "LDPE", "HDPE", "EVOH", "PC", "PET", "PP", "PS", "PVC",
        "NYLON6", "NYLON66", "PES", "PU",
    }
    assert params.POLYMERS["PU"].admission == params.ADMISSION_REFUSE
    assert params.POLYMERS["PES"].admission == params.ADMISSION_LIVE
    assert params.POLYMERS["PVC"].chemist_signoff_required is True
    assert "dissolution" in params.POLYMERS["PVC"].chemist_signoff_note.lower()


def test_nylon_maps_to_package_id_not_raw_grid_name():
    assert params.POLYMERS["NYLON6"].identity_in_model == "Nylon6"
    assert params.POLYMERS["NYLON66"].identity_in_model == "Nylon66"
    admitted = params.admit_live_target("NYLON6")
    assert admitted.admitted is True
    assert admitted.identity_in_model == "Nylon6"


def test_pet_oligomer_is_provisional_and_injectable():
    row = params.POLYMERS["PET"]
    assert row.oligomer is not None
    assert row.oligomer.in_package_outline is False
    assert row.oligomer.inject_if_missing is True
    assert row.oligomer.standing == params.PROVISIONAL


def test_admission_is_not_a_raw_name_fallthrough():
    unknown = params.admit_live_target("ABS")
    assert unknown.admitted is False
    assert unknown.error_type == "unsupported_live_target"
    assert "PET-clone" in unknown.error or "parameter surface" in unknown.error

    pu = params.admit_live_target("PU")
    assert pu.admitted is False
    assert "PET-clone" in pu.error

    pes = params.admit_live_target("PES")
    assert pes.admitted is True
    assert pes.identity_in_model == "PES"


def test_package_predicate_refuses_pet_clone_when_id_missing():
    ids = frozenset({"PE", "PEoligomer", "EVOH", "EVOHoligomer"})
    missing = params.admit_live_target("PES", package_chemical_ids=ids)
    assert missing.admitted is False
    assert missing.package_chemical_present is False
    assert "PET-clone" in missing.error

    nylon_raw = params.admit_live_target(
        "NYLON6", package_chemical_ids=frozenset({"NYLON6"}),
    )
    # Outline has the raw grid name only: that is NOT Nylon6, so refuse.
    assert nylon_raw.admitted is False

    nylon_ok = params.admit_live_target(
        "NYLON6",
        package_chemical_ids=frozenset({"Nylon6", "Nylon6oligomer"}),
    )
    assert nylon_ok.admitted is True
    assert nylon_ok.identity_in_model == "Nylon6"


def test_pet_missing_oligomer_requires_injection_not_clone():
    ids = frozenset({"PET"})  # chemical yes, oligomer no
    admission = params.admit_live_target("PET", package_chemical_ids=ids)
    assert admission.admitted is True
    assert admission.oligomer_injection_required is True
    assert admission.package_oligomer_present is False


def test_parser_reads_outline_ids_not_aliases_or_search_ids(tmp_path):
    source = tmp_path / "property_package.py"
    source.write_text(_FIXTURE_OUTLINE, encoding="utf-8")
    ids = params.package_chemical_ids_from_source(source)
    assert "PE" in ids
    assert "PEoligomer" in ids
    assert "PES" in ids
    assert "PET" in ids
    assert "Toluene" in ids
    assert "Polyethylene" not in ids
    assert "1-Hexene" not in ids
    assert "Diphenyl sulfone" not in ids
    assert "PU" not in ids
    assert "PEToligomer" not in ids


@pytest.mark.skipif(
    not (_REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py").is_file(),
    reason="unpublished plastics 0.1.4 is not on this machine",
)
def test_real_package_outline_has_pes_nylon_not_pu_not_petoligomer():
    ids = params.package_chemical_ids(_REAL_PLASTICS_PARENT)
    assert ids is not None
    assert "PES" in ids and "PESoligomer" in ids
    assert "Nylon6" in ids and "Nylon6oligomer" in ids
    assert "PET" in ids
    assert "PEToligomer" not in ids
    assert "PU" not in ids
    pes = params.admit_live_target("PES", package_chemical_ids=ids)
    assert pes.admitted is True
    pu = params.admit_live_target("PU", package_chemical_ids=ids)
    assert pu.admitted is False
    pet = params.admit_live_target("PET", package_chemical_ids=ids)
    assert pet.admitted is True
    assert pet.oligomer_injection_required is True


def test_provisional_payload_labels_every_costed_metric():
    row = params.POLYMERS["PES"]
    payload = params.live_parameter_standing_payload(
        row, solvent="toluene", present_metrics=("msp_usd_per_kg", "tci_usd"),
    )
    assert payload["can_cite_as_validated_process"] is False
    assert payload["process_parameter_status"]["msp_usd_per_kg"] == (
        params.PROCESS_PARAMETER_STATUS_CODE
    )
    assert payload["process_parameter_status"]["tci_usd"] == (
        params.PROCESS_PARAMETER_STATUS_CODE
    )
    definition = payload["process_parameter_status_definitions"][
        params.PROCESS_PARAMETER_STATUS_CODE
    ]
    assert definition["can_cite_as_validated_process"] is False
    assert definition["current_scientific_use"] == (
        "not_admitted_as_validated_process"
    )
    assert payload["live_parameter_standing"]["chemist_signoff_required"] is False


def test_pe_toluene_committed_pair_is_validated_other_solvents_are_not():
    row = params.POLYMERS["LDPE"]
    exact = _committed_pe_toluene_config()
    toluene = params.live_parameter_standing_payload(
        row, solvent="Toluene", config=exact, package_disagreements=(),
    )
    assert toluene["can_cite_as_validated_process"] is True
    assert "process_parameter_status" not in toluene
    unnamed = params.live_parameter_standing_payload(
        row, solvent="Toluene", package_disagreements=(),
    )
    assert unnamed["can_cite_as_validated_process"] is False
    hexane = params.live_parameter_standing_payload(
        row, solvent="hexane", config=exact, package_disagreements=(),
    )
    assert hexane["can_cite_as_validated_process"] is False
    assert "msp_usd_per_kg" in hexane["process_parameter_status"]


def test_pvc_is_provisional_because_chemist_has_not_signed_off():
    row = params.POLYMERS["PVC"]
    payload = params.live_parameter_standing_payload(row, solvent="thf")
    assert payload["can_cite_as_validated_process"] is False
    assert payload["live_parameter_standing"]["chemist_signoff_required"] is True
    assert "chemist_signoff_required" in payload["live_parameter_standing"][
        "provisional_parameters"
    ]


def test_worker_refuses_pu_and_unknown_before_python_version(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    pu = tea_worker.run({"target_plastic": "PU"})
    assert pu["success"] is False
    assert pu["error_type"] == "unsupported_live_target"
    assert "PET-clone" in pu["error"]
    unknown = tea_worker.run({"target_plastic": "ABS"})
    assert unknown["success"] is False
    assert unknown["error_type"] == "unsupported_live_target"
    assert tea_worker._TARGET.get("PES") == "PES"
    assert "PU" not in tea_worker._TARGET
    assert tea_worker._TARGET.get("NYLON6") == "Nylon6"
    # No raw-name default: missing keys stay missing.
    assert tea_worker._TARGET.get("ABS") is None


def test_worker_no_longer_has_a_denylist_guarding_a_default():
    source = Path(tea_worker.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    text = ast.dump(tree)
    assert "_UNSUPPORTED_LIVE_TARGETS" not in source
    assert "original_target, original_target" not in source
    # Admission must call the predicate, not a remembered six-name set.
    assert "admit_live_target" in source
    assert "PET-clone" in source or "PET-clone" in text


def test_refuse_reason_is_populated_for_every_refused_polymer():
    refused = [
        row for row in params.POLYMERS.values()
        if row.admission == params.ADMISSION_REFUSE
    ]
    assert refused, "the surface must list refused polymers, not omit them"
    for row in refused:
        assert row.refuse_reason.strip(), (
            f"{row.grid_name} is refused with an empty refuse_reason"
        )


def test_unlabelled_live_msp_is_a_standing_defect():
    bare = {
        "success": True,
        "tea": {"msp_usd_per_kg": 1.25},
    }
    defects = params.live_number_standing_defects(bare)
    assert defects
    assert any("can_cite_as_validated_process is absent" in item for item in defects)


def test_run_binds_standing_on_injected_live_success(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    unlabelled = {
        "success": True,
        "solvent": "toluene",
        "target_plastic": "PES",
        "tea": {
            "msp_usd_per_kg": 1.25,
            "tci_usd": 1.0,
            "aoc_usd_per_yr": 1.0,
        },
        "lca": {"gwp_kg_co2e_per_kg": 0.8},
        "operations": {},
    }
    monkeypatch.setattr(tea, "_live", lambda config, timeout: dict(unlabelled))
    config = {
        "solvent": "toluene",
        "target_plastic": "PES",
        "target_plastic_percent": 60.0,
        "processing_capacity": 20_000.0,
        "energy_case": "C1",
        "dissolution_temperature_c": 130.0,
        "precipitation_temperature_c": 25.0,
        "solvent_price": 2.17,
        "solvent_loss_pct": 0.01,
        "feedstock_distance_km": 0.0,
        "dissolution_capacity": 3.0,
        "labor_cost": 120_000.0,
    }
    pes = tea._run(dict(config), "live", 1)
    assert pes["success"] is True
    assert not params.live_number_standing_defects(pes)
    assert pes["tea"]["can_cite_as_validated_process"] is False
    assert pes["tea"]["process_parameter_status"]["msp_usd_per_kg"]
    row = tea._comparison_row("pes", pes)
    assert row["process_parameter_status"]["msp_usd_per_kg"]
    assert row["can_cite_as_validated_process"] is False

    config["target_plastic"] = "LDPE"
    config["dissolution_temperature_c"] = 95.0
    unlabelled["target_plastic"] = "LDPE"
    ldpe_wrong_precip = tea._run(dict(config), "live", 1)
    assert ldpe_wrong_precip["success"] is True
    assert not params.live_number_standing_defects(ldpe_wrong_precip)
    assert ldpe_wrong_precip["tea"]["can_cite_as_validated_process"] is False
    assert "precipitation_temperature_c" in ldpe_wrong_precip[
        "live_parameter_standing"
    ]["committed_setpoint_mismatches"]

    config["precipitation_temperature_c"] = 35.0
    ldpe = tea._run(dict(config), "live", 1)
    assert ldpe["success"] is True
    assert not params.live_number_standing_defects(ldpe)
    assert ldpe["tea"]["can_cite_as_validated_process"] is True
    assert params.live_standing_signature(pes) != params.live_standing_signature(ldpe)
    assert params.live_standing_signature(ldpe_wrong_precip) != (
        params.live_standing_signature(ldpe)
    )


def test_standing_shape_keeps_validated_apart_from_correct(monkeypatch):
    """Standing-shape unit test: injected metrics, not a live oracle.

    Patched ``_live`` proves validated vs provisional follows executed
    setpoints and committed-pair membership. It does not bind MSP/TCI/AOC/GWP
    to BioSTEAM. The production-bound child handshake is
    ``test_runpy_child_returns_named_json_for_refused_target``.
    """
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)

    def fake_live(config, timeout):
        solvent = str(config.get("solvent") or "").casefold()
        plastic = str(config.get("target_plastic") or "").upper()
        if plastic == "LDPE" and solvent == "toluene":
            return {
                "success": True,
                "solvent": "toluene",
                "target_plastic": "LDPE",
                "tea": {
                    "msp_usd_per_kg": LDPE_TOLUENE_LIVE["msp_usd_per_kg"],
                    "tci_usd": LDPE_TOLUENE_LIVE["tci_usd"],
                    "aoc_usd_per_yr": LDPE_TOLUENE_LIVE["aoc_usd_per_yr"],
                },
                "lca": {"gwp_kg_co2e_per_kg": LDPE_TOLUENE_LIVE["gwp_kg_co2e_per_kg"]},
                "operations": {},
            }
        if plastic == "LDPE" and solvent == "dodecane":
            return {
                "success": True,
                "solvent": "Dodecane",
                "target_plastic": "LDPE",
                "tea": {
                    "msp_usd_per_kg": LDPE_DODECANE_LIVE_MSP,
                    "tci_usd": 87102535.73687321,
                    "aoc_usd_per_yr": 2356462.05524089,
                },
                "lca": {"gwp_kg_co2e_per_kg": 0.6207154872703069},
                "operations": {},
            }
        raise AssertionError(f"unexpected live config {config!r}")

    monkeypatch.setattr(tea, "_live", fake_live)
    committed = tea._run(_committed_pe_toluene_config(), "live", 1)
    moved = tea._run(
        _committed_pe_toluene_config(precipitation_temperature_c=25.0),
        "live",
        1,
    )
    dodecane = tea._run(dict(LDPE_DODECANE_C1), "live", 1)

    assert committed["tea"]["msp_usd_per_kg"] == LDPE_TOLUENE_LIVE["msp_usd_per_kg"]
    assert committed["can_cite_as_validated_process"] is True
    assert not params.live_number_standing_defects(committed)

    assert moved["tea"]["msp_usd_per_kg"] == LDPE_TOLUENE_LIVE["msp_usd_per_kg"]
    assert moved["can_cite_as_validated_process"] is False
    assert "precipitation_temperature_c" in moved["live_parameter_standing"][
        "committed_setpoint_mismatches"
    ]
    assert moved["process_parameter_status"]["msp_usd_per_kg"]

    assert dodecane["tea"]["msp_usd_per_kg"] == LDPE_DODECANE_LIVE_MSP
    assert dodecane["can_cite_as_validated_process"] is False
    assert dodecane["live_parameter_standing"]["committed_pair"] is None
    assert dodecane["process_parameter_status"]["msp_usd_per_kg"]
    assert params.live_standing_signature(committed) != (
        params.live_standing_signature(dodecane)
    )
    assert params.live_standing_signature(committed) != (
        params.live_standing_signature(moved)
    )


def test_parent_refuses_unlabelled_provisional_live_result():
    unlabelled = {
        "success": True,
        "target_plastic": "PES",
        "solvent": "toluene",
        "tea": {"msp_usd_per_kg": 1.23, "tci_usd": 4.0, "aoc_usd_per_yr": 5.0},
        "lca": {"gwp_kg_co2e_per_kg": 0.9},
        "operations": {},
    }
    guarded = tea._require_live_parameter_standing(
        dict(unlabelled), {"target_plastic": "PES", "solvent": "toluene"},
    )
    assert guarded["success"] is True
    assert guarded["can_cite_as_validated_process"] is False
    assert guarded["tea"]["can_cite_as_validated_process"] is False
    assert guarded["process_parameter_status"]["msp_usd_per_kg"] == (
        params.PROCESS_PARAMETER_STATUS_CODE
    )
    assert guarded["tea"]["process_parameter_status"]["msp_usd_per_kg"] == (
        params.PROCESS_PARAMETER_STATUS_CODE
    )
    stray = tea._require_live_parameter_standing(
        {
            "success": True,
            "target_plastic": "ABS",
            "tea": {"msp_usd_per_kg": 9.9},
        },
        {"target_plastic": "ABS"},
    )
    assert stray["success"] is False
    assert stray["error_type"] == "live_parameter_standing_missing"
    assert "tea" not in stray


def test_classify_live_feed_uses_parameter_surface_not_old_three_names():
    unrecognised, unmodelled = tea._classify_live_feed_polymers(
        ["LDPE", "PES", "PET", "PU", "not-a-polymer"],
    )
    assert "not-a-polymer" in unrecognised
    # "PU" resolves to the POLYURETHANES family; its one grid member is
    # refused on the parameter surface, so the family is unmodelled.
    assert "POLYURETHANES" in unmodelled
    assert "PU" not in params.live_identity_map()
    assert "PES" not in unmodelled
    assert "PET" not in unmodelled
    assert "LDPE" not in unmodelled


def test_doctor_reports_live_tea_path_without_requiring_it(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in report["checks"] if c["name"] == "Live TEA")
    assert check["status"] in {"pass", "warn", "fail"}
    assert check["parameter_surface"].endswith("tea_polymer_parameters.py")
    assert "PES" in check["admitted_live_targets"]
    assert "PU" in check["refused_grid_targets"]
    assert "unavailable because" in check["detail"]
    assert "DISSOLVE_PLASTICS_PATH is unset" in check["detail"]
    assert "3.12" in check["detail"]
    assert "subprocess" in check["detail"]
    assert "duckdb" in check["detail"]
    assert "tea_worker" in check["detail"]
    assert check.get("execution_path")
    assert "ModuleNotFoundError: duckdb" in check["execution_path"]
    assert check.get("why_unavailable")
    # Unconfigured live TEA must not flip doctor to fail by itself.
    if check["status"] == "warn":
        live_is_only_fail = not any(
            item["status"] == "fail" and item["name"] == "Live TEA"
            for item in report["checks"]
        )
        assert live_is_only_fail


def test_public_scenario_overrides_cannot_cite_committed_pair_names(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    captured = {}
    unlabelled = {
        "success": True,
        "solvent": "toluene",
        "target_plastic": "LDPE",
        "tea": {
            "msp_usd_per_kg": 7.25,
            "tci_usd": 1.0,
            "aoc_usd_per_yr": 1.0,
        },
        "lca": {"gwp_kg_co2e_per_kg": 0.8},
        "operations": {},
    }

    def fake_live(config, timeout):
        captured.update(config)
        return dict(unlabelled)

    monkeypatch.setattr(tea, "_live", fake_live)
    raw = tea.evaluate_tea_lca_scenarios(
        scenarios=[{
            "target_polymer": "LDPE",
            "solvent": "toluene",
            "dissolution_temp_c": 200.0,
            "precipitation_temp_c": 10.0,
            "dissolution_capacity": 9.0,
            "solvent_price": 2.17,
        }],
        engine_mode="live",
    )
    payload = json.loads(raw)
    assert captured["dissolution_temperature_c"] == 200.0
    assert captured["precipitation_temperature_c"] == 10.0
    assert captured["dissolution_capacity"] == 9.0
    rows = payload["data"]["comparison_rows"]
    assert rows[0]["success"] is True
    assert rows[0]["msp_usd_per_kg"] == 7.25
    assert rows[0]["can_cite_as_validated_process"] is False
    assert rows[0]["process_parameter_status"]["msp_usd_per_kg"]
    mismatches = rows[0]["live_parameter_standing"]["committed_setpoint_mismatches"]
    assert "dissolution_temperature_c" in mismatches
    assert "precipitation_temperature_c" in mismatches
    assert "dissolution_capacity" in mismatches


def test_mutating_committed_precip_makes_validation_fail(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    original = params.COMMITTED_PAIR_STEPS[("PE", "toluene")]
    exact = _committed_pe_toluene_config()
    before = params.live_parameter_standing_payload(
        params.POLYMERS["LDPE"],
        solvent="toluene",
        config=exact,
        package_disagreements=(),
    )
    assert before["can_cite_as_validated_process"] is True
    mutated = replace(original, precipitation_temperature_c=40.0)
    monkeypatch.setitem(params.COMMITTED_PAIR_STEPS, ("PE", "toluene"), mutated)
    after = params.live_parameter_standing_payload(
        params.POLYMERS["LDPE"],
        solvent="toluene",
        config=exact,
        package_disagreements=(),
    )
    assert after["can_cite_as_validated_process"] is False
    assert "precipitation_temperature_c" in after["live_parameter_standing"][
        "committed_setpoint_mismatches"
    ]
    # The table does not configure the plant. Mutating the claim cannot
    # change the request config that would be sent to the worker.
    cfg = tea._scenario_config({
        "target_polymer": "LDPE",
        "solvent": "toluene",
        "solvent_price": 2.17,
        "dissolution_temperature_c": 95.0,
    })
    assert cfg["precipitation_temperature_c"] != 40.0


def test_package_chemical_mutation_makes_validation_fail(tmp_path):
    _write_matching_pe_package(tmp_path)
    row = params.POLYMERS["LDPE"]
    exact = _committed_pe_toluene_config()
    matching = params.surface_package_disagreements(row, "toluene", tmp_path)
    assert matching == ()
    validated = params.live_parameter_standing_payload(
        row, solvent="toluene", config=exact, plastics_root=tmp_path,
    )
    assert validated["can_cite_as_validated_process"] is True
    mutated_row = replace(row, chemical=replace(row.chemical, rho_kg_m3=1.0))
    disagreements = params.surface_package_disagreements(
        mutated_row, "toluene", tmp_path,
    )
    assert "rho_kg_m3" in disagreements
    payload = params.live_parameter_standing_payload(
        mutated_row,
        solvent="toluene",
        config=exact,
        plastics_root=tmp_path,
    )
    assert payload["can_cite_as_validated_process"] is False


def test_doctor_malformed_property_package_is_named_fail(tmp_path, monkeypatch):
    root = tmp_path / "plastics-root"
    strap = root / "plastics" / "strap"
    strap.mkdir(parents=True)
    (strap / "property_package.py").write_text("def (\n", encoding="utf-8")
    (strap / "process_model.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = tea.live_environment_report()
    assert report["reason"] == "property_package_unreadable"
    assert report["check_status"] == "fail"
    assert str(strap / "property_package.py") in (report["why_unavailable"] or "")
    assert "Traceback" not in (report["why_unavailable"] or "")
    doctor = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in doctor["checks"] if c["name"] == "Live TEA")
    assert check["status"] == "fail"
    assert check["reason"] == "property_package_unreadable"
    assert str(strap / "property_package.py") in (check["detail"] or "")


def test_worker_malformed_package_is_named_refusal(tmp_path, monkeypatch):
    root = tmp_path / "plastics-root"
    strap = root / "plastics" / "strap"
    strap.mkdir(parents=True)
    (strap / "property_package.py").write_text("def (\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    result = tea_worker.run({"target_plastic": "LDPE"})
    assert result["success"] is False
    assert result["error_type"] == "property_package_unreadable"
    assert str(strap / "property_package.py") in result["error"]


def test_cited_package_hashes_seal_the_files_the_table_cites(tmp_path):
    _write_matching_pe_package(tmp_path)
    first = params.cited_strap_source_provenance(tmp_path)
    assert first["missing"] == ()
    assert set(first["sources"]) == set(params.CITED_STRAP_SOURCE_NAMES)
    for name, row in first["sources"].items():
        assert row["sha256"]
        assert Path(row["path"]).is_file()
    precip = Path(first["sources"]["precipitation_steps"]["path"])
    precip.write_text(precip.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    drifted = params.cited_strap_source_provenance(tmp_path)
    assert drifted["sources"]["precipitation_steps"]["sha256"] != (
        first["sources"]["precipitation_steps"]["sha256"]
    )
    mismatches = params.cited_package_hash_mismatches(
        params.cited_package_sha256_map(first), drifted,
    )
    assert "precipitation_steps" in mismatches
    assert "property_package" not in mismatches


def test_runpy_child_returns_named_json_for_refused_target(monkeypatch):
    """Production child handshake: exact runpy argv, cheap refused target.

    On sealed 5c0f4be this child wrote no JSON (invalid_worker_output).
    The producer is the documented worker, not an expected-value fixture.
    When the isolated 3.12 interpreter and unpublished package are on
    this machine, use them — that is the path that was dead.
    """
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    live_python = Path(
        "/home/aaltamimi2/anaconda3/envs/dissolve-tea-312/bin/python"
    )
    if live_python.is_file():
        monkeypatch.setenv("DISSOLVE_TEA_PYTHON", str(live_python))
    else:
        monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    if (_REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py").is_file():
        monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(_REAL_PLASTICS_PARENT))
    else:
        monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    argv = tea.live_worker_runpy_argv({"target_plastic": "PU"})
    assert argv[1] == "-c"
    assert argv[2] == tea.LIVE_WORKER_RUNPY_BOOTSTRAP
    assert "runpy.run_path" in argv[2]
    result = tea._launch_live_worker(
        {"target_plastic": "PU"},
        timeout_seconds=30,
        environment=tea._tea_worker_environment(),
        provenance=tea._tea_worker_source_provenance(),
    )
    assert result.get("error_type") != "invalid_worker_output"
    assert result["success"] is False
    assert result["error_type"] == "unsupported_live_target"
    assert "PET-clone" in (result.get("error") or "")
    assert result.get("target_plastic") == "PU"


def test_live_engine_status_exercises_runpy_child(monkeypatch):
    """Readiness must start the documented child, not only hash and probe."""
    live_python = Path(
        "/home/aaltamimi2/anaconda3/envs/dissolve-tea-312/bin/python"
    )
    plastics_ok = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py"
    ).is_file()
    if not live_python.is_file() or not plastics_ok:
        pytest.skip("isolated live interpreter or unpublished plastics missing")
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", str(live_python))
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(_REAL_PLASTICS_PARENT))
    status = tea.live_engine_status()
    assert status["available"] is True
    provenance = status["live_provenance"]
    assert provenance["child_handshake_ok"] is True
    assert provenance["child_handshake_error_type"] == "unsupported_live_target"
    assert provenance["child_handshake_target"] == "PU"
    assert "child handshake" in (status.get("detail") or "")


def test_readiness_fails_closed_when_child_writes_no_json(monkeypatch):
    live_python = Path(
        "/home/aaltamimi2/anaconda3/envs/dissolve-tea-312/bin/python"
    )
    plastics_ok = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "property_package.py"
    ).is_file()
    if not live_python.is_file() or not plastics_ok:
        pytest.skip("isolated live interpreter or unpublished plastics missing")
    tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", str(live_python))
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(_REAL_PLASTICS_PARENT))

    def dead_child(*_args, **_kwargs):
        return {
            "success": False,
            "error": "BioSTEAM worker returned invalid JSON",
            "error_type": "invalid_worker_output",
        }

    monkeypatch.setattr(tea, "_launch_live_worker", dead_child)
    try:
        status = tea.live_engine_status()
        assert status["available"] is False
        assert status["reason"] == "invalid_worker_output"
        assert status["live_provenance"]["child_handshake_ok"] is False
    finally:
        tea._LIVE_CHILD_HANDSHAKE_CACHE.clear()


def test_uninspectable_chemical_rho_forces_provisional(tmp_path):
    _write_matching_pe_package(tmp_path, pe_rho="external_rho()")
    row = params.POLYMERS["LDPE"]
    exact = _committed_pe_toluene_config()
    disagreements = params.surface_package_disagreements(row, "toluene", tmp_path)
    assert "rho_kg_m3_uninspectable" in disagreements
    payload = params.live_parameter_standing_payload(
        row, solvent="toluene", config=exact, plastics_root=tmp_path,
    )
    assert payload["can_cite_as_validated_process"] is False
    assert payload["live_parameter_standing"]["package_disagreements"] == (
        list(disagreements)
    )


def test_absent_chemical_rho_forces_provisional(tmp_path):
    _write_matching_pe_package(tmp_path, pe_rho=None)
    row = params.POLYMERS["LDPE"]
    disagreements = params.surface_package_disagreements(
        row, "toluene", tmp_path,
    )
    assert "rho_kg_m3_uninspectable" in disagreements
    payload = params.live_parameter_standing_payload(
        row,
        solvent="toluene",
        config=_committed_pe_toluene_config(),
        plastics_root=tmp_path,
    )
    assert payload["can_cite_as_validated_process"] is False


def test_missing_oligomer_draft_is_named_disagreement(tmp_path):
    _write_matching_pe_package(tmp_path)
    strap = tmp_path / "plastics" / "strap"
    (strap / "property_package.py").write_text(
        '''
STRAP_chemicals_outline = bst.ChemicalsOutline([
    bst.ChemicalDraft(
        "PE",
        formula="C2H4",
        rho=0.5 * (880 + 960),
        Cp=0.5 * (1.330 + 2.400),
        Tm=0.5 * (115 + 135) + 273.15,
    ),
])
''',
        encoding="utf-8",
    )
    row = params.POLYMERS["LDPE"]
    disagreements = params.surface_package_disagreements(row, "toluene", tmp_path)
    assert "oligomer_not_in_package_source" in disagreements
    payload = params.live_parameter_standing_payload(
        row,
        solvent="toluene",
        config=_committed_pe_toluene_config(),
        plastics_root=tmp_path,
    )
    assert payload["can_cite_as_validated_process"] is False


@pytest.mark.parametrize(
    "filename",
    ("dissolution_steps.py", "precipitation_steps.py"),
)
def test_doctor_malformed_cited_step_is_named_fail(
    tmp_path, monkeypatch, filename,
):
    root = tmp_path / "plastics-root"
    _write_matching_pe_package(root)
    strap = root / "plastics" / "strap"
    (strap / filename).write_text("def (\n", encoding="utf-8")
    (strap / "process_model.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    monkeypatch.delenv("DISSOLVE_TEA_PYTHON", raising=False)
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = tea.live_environment_report()
    assert report["reason"] == "cited_package_unreadable"
    assert report["check_status"] == "fail"
    assert str(strap / filename) in (report["why_unavailable"] or "")
    assert "Traceback" not in (report["why_unavailable"] or "")
    doctor = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in doctor["checks"] if c["name"] == "Live TEA")
    assert check["status"] == "fail"
    assert check["reason"] == "cited_package_unreadable"
    assert str(strap / filename) in (check["detail"] or "")


@pytest.mark.parametrize(
    "filename",
    ("dissolution_steps.py", "precipitation_steps.py"),
)
def test_worker_malformed_cited_step_is_named_refusal(
    tmp_path, monkeypatch, filename,
):
    root = tmp_path / "plastics-root"
    _write_matching_pe_package(root)
    strap = root / "plastics" / "strap"
    (strap / filename).write_text("def (\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(root))
    result = tea_worker.run({"target_plastic": "LDPE"})
    assert result["success"] is False
    assert result["error_type"] == "cited_package_unreadable"
    assert str(strap / filename) in result["error"]


def test_live_engine_status_parses_cited_steps_before_ready(tmp_path, monkeypatch):
    real_model = (
        _REAL_PLASTICS_PARENT / "plastics" / "strap" / "process_model.py"
    )
    if not real_model.is_file():
        pytest.skip("unpublished process_model.py is not on this machine")
    _write_matching_pe_package(tmp_path)
    strap = tmp_path / "plastics" / "strap"
    shutil.copy2(real_model, strap / "process_model.py")
    (strap / "dissolution_steps.py").write_text("def (\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    monkeypatch.setenv("DISSOLVE_TEA_PYTHON", sys.executable)
    status = tea.live_engine_status()
    assert status["available"] is False
    assert status["reason"] == "cited_package_unreadable"
    assert "dissolution_steps.py" in (status.get("detail") or "")
