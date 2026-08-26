"""Sheet standing preview must pass the resolved plastics root.

A thin live_parameter_standing_payload call (row/solvent/config only)
skips package inspection. That is not the sheet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import tea, tea_polymer_parameters as params
from dissolve.cli import CliApp
from rich.console import Console
import io


def _pe_toluene_sheet(**overrides):
    committed = params.COMMITTED_PAIR_STEPS[("PE", "toluene")]
    buffer = {
        "target_polymer": "LDPE",
        "solvent": "Toluene",
        **params.process_config_from_assumptions(committed),
    }
    buffer.update(overrides)
    return buffer


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


def test_thin_payload_call_is_validated_sheet_without_root_is_not(
    monkeypatch,
):
    sheet = _pe_toluene_sheet()
    thin = params.live_parameter_standing_payload(
        params.POLYMERS["LDPE"],
        solvent="Toluene",
        config={
            "dissolution_temperature_c": sheet["dissolution_temperature_c"],
            "precipitation_temperature_c": sheet["precipitation_temperature_c"],
            "dissolution_capacity": sheet["dissolution_capacity"],
        },
    )
    assert thin["can_cite_as_validated_process"] is True
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    preview = tea.sheet_standing_preview(sheet)
    assert preview["can_cite_as_validated_process"] is False
    assert preview["badge"] == "unavailable"
    assert preview["reason"] == "missing_plastics_root"
    assert preview["passed_plastics_root"] is False


def test_matching_package_pe_toluene_95_35_3_is_validated(
    tmp_path, monkeypatch,
):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    preview = tea.sheet_standing_preview(_pe_toluene_sheet())
    assert preview["passed_plastics_root"] is True
    assert preview["plastics_root"] == str(tmp_path.resolve())
    assert preview["badge"] == "validated"
    assert preview["can_cite_as_validated_process"] is True


def test_package_disagreement_flips_the_same_config_to_provisional(
    tmp_path, monkeypatch,
):
    _write_matching_pe_package(tmp_path, pe_rho="1.0")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    preview = tea.sheet_standing_preview(_pe_toluene_sheet())
    assert preview["passed_plastics_root"] is True
    assert preview["badge"] == "provisional"
    assert preview["can_cite_as_validated_process"] is False
    assert "rho_kg_m3" in preview["provisional_parameters"]


def test_preview_passes_resolved_plastics_root_into_payload(
    tmp_path, monkeypatch,
):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    seen = {}
    original = params.live_parameter_standing_payload

    def spy(*args, **kwargs):
        seen["kwargs"] = kwargs
        seen["args"] = args
        return original(*args, **kwargs)

    monkeypatch.setattr(params, "live_parameter_standing_payload", spy)
    tea.sheet_standing_preview(_pe_toluene_sheet())
    assert seen["kwargs"]["plastics_root"] == tmp_path.resolve()
    assert seen["kwargs"]["package_disagreements"] is not None


def test_unrecognised_plastics_root_is_missing_package_not_validated(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    preview = tea.sheet_standing_preview(_pe_toluene_sheet())
    assert preview["badge"] == "unavailable"
    assert preview["reason"] == "missing_package"
    assert preview["can_cite_as_validated_process"] is False


def test_malformed_package_is_unavailable(tmp_path, monkeypatch):
    strap = tmp_path / "plastics" / "strap"
    strap.mkdir(parents=True)
    (strap / "property_package.py").write_text("def (\n", encoding="utf-8")
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    preview = tea.sheet_standing_preview(_pe_toluene_sheet())
    assert preview["badge"] == "unavailable"
    assert preview["reason"] == "property_package_unreadable"
    assert preview["can_cite_as_validated_process"] is False


def test_generic_first_run_setpoints_are_not_validated_pe_toluene(
    tmp_path, monkeypatch,
):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    seeded = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Toluene",
    })
    preview = tea.sheet_standing_preview(seeded)
    assert preview["can_cite_as_validated_process"] is False
    assert preview["badge"] == "provisional"
    assert "dissolution_temperature_c" in preview["provisional_parameters"]


def test_missing_polymer_or_solvent_is_incomplete(monkeypatch):
    monkeypatch.delenv("DISSOLVE_PLASTICS_PATH", raising=False)
    preview = tea.sheet_standing_preview({"target_polymer": "LDPE"})
    assert preview["badge"] == "incomplete"
    assert preview["missing"] == ["solvent"]
    assert preview["passed_plastics_root"] is False


def test_preview_does_not_call_live(tmp_path, monkeypatch):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))

    def boom(*args, **kwargs):
        raise AssertionError("standing preview must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", boom)
    tea.sheet_standing_preview(_pe_toluene_sheet())


def test_sheet_print_shows_standing_badge(tmp_path, monkeypatch):
    _write_matching_pe_package(tmp_path)
    monkeypatch.setenv("DISSOLVE_PLASTICS_PATH", str(tmp_path))
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    app = CliApp(
        session_id="test-session",
        store_root=tmp_path / "session",
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
    )
    app._print_process_sheet(_pe_toluene_sheet())
    shown = buf.getvalue()
    assert "VALIDATED-process" in shown
    assert "msp_usd_per_kg" not in shown
