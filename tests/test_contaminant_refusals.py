"""v1 hold-to 3 / v3 §6: distinct absence codes, including unspecified STRAP."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import contaminants as C
from dissolve.contracts import parse_tool_result


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def test_bfr_class_uses_family_code_not_junk_code():
    bfr = _data(C.screen_contaminant_leaching("LDPE", ["BFR"]))
    junk = _data(C.screen_contaminant_leaching("LDPE", ["xyzzy-not-a-contaminant"]))
    assert bfr["success"] is False
    assert bfr["error_code"] == "unsupported_contaminant_family"
    assert bfr["unsupported_families"] == ["BFR"]
    assert bfr["supported_families"] == ["PFAS", "Phthalates"]
    assert junk["error_code"] == "unsupported_contaminants"
    assert junk["error_code"] != bfr["error_code"]


def test_brominated_flame_retardants_alias_is_the_family_code():
    payload = _data(C.screen_contaminant_leaching(
        "LDPE", ["brominated flame retardants"],
    ))
    assert payload["error_code"] == "unsupported_contaminant_family"


def test_all_unknown_solvents_refuse_distinct_code():
    payload = _data(C.screen_contaminant_leaching(
        "LDPE", ["Perfluorooctanoic Acid"],
        solvents=["not-a-real-solvent-xyz"],
    ))
    assert payload["success"] is False
    assert payload["error_code"] == "unknown_contaminant_solvent"
    assert payload["unsupported_solvents"] == ["not-a-real-solvent-xyz"]


def test_mixed_known_and_unknown_solvent_continues():
    payload = _data(C.screen_contaminant_leaching(
        "LDPE", ["Perfluorooctanoic Acid"],
        solvents=["Toluene", "not-a-real-solvent-xyz"],
    ))
    assert payload["success"] is True
    assert payload["unsupported_solvents"] == ["not-a-real-solvent-xyz"]
    assert any(row.get("solvent") == "toluene" for row in payload["candidate_solvents"])


def test_unspecified_only_does_not_pass_strap():
    row = C._miscibility("toluene", "Perfluorooctanoic Acid", "rt")
    assert row is not None
    assert row["temperature_regime"] == "unspecified"
    strap = _data(C.screen_contaminant_strap_removal(
        "LDPE", ["Perfluorooctanoic Acid"],
        other_polymers=["PET"], solvents=["cyclohexanol"],
    ))
    assert strap["success"] is False
    assert strap["error_code"] == "unspecified_not_a_strap_basis"
    leach = _data(C.screen_contaminant_leaching(
        "LDPE", ["Perfluorooctanoic Acid"],
        other_polymers=["PET"], solvents=["toluene"],
    ))
    assert leach["success"] is True


def test_specified_rt_fallback_still_passes_strap():
    dehp = _data(C.screen_contaminant_strap_removal(
        "LDPE", ["di-(2-ethylhexyl) phthalate (DEHP)"],
        other_polymers=["EVOH"], solvents=["toluene"],
    ))
    assert dehp["success"] is True
    row = dehp["candidate_solvents"][0]
    assert row["passes"] is True
    assert row["unspecified_not_a_strap_basis"] is False
    assert [item["miscibility_regime"] for item in row["contaminants"]] == ["rt"]
