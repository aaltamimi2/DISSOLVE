"""CL-3: unspecified_not_a_strap_basis stays; name whether leaching exists.

Payload addition only. No strap→leaching fallback. No BioSTEAM.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import contaminants
from dissolve.contracts import parse_tool_result


_PFOA = "Perfluorooctanoic Acid"


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _pfoa_unspecified_strap() -> dict:
    return _data(contaminants.screen_contaminant_strap_removal(
        "LDPE", [_PFOA], other_polymers=["PET"], solvents=["cyclohexanol"],
    ))


def test_pfoa_cyclohexanol_pet_still_refuses_and_names_leaching():
    payload = _pfoa_unspecified_strap()
    assert payload["success"] is False
    assert payload["error_code"] == "unspecified_not_a_strap_basis"
    assert "leaching_basis_available" in payload
    assert "leaching_basis_solvents" in payload
    assert payload["leaching_basis_available"] is True
    named = list(payload["leaching_basis_solvents"])
    assert named
    for solvent in named:
        logd = contaminants._logd(solvent, _PFOA)
        assert contaminants.CONTAMINANT_LOGD_CRITERION.passes(logd)
    assert "steps" not in payload


def test_constructed_empty_leaching_basis_still_has_both_fields():
    original = contaminants._logd
    contaminants._logd = lambda *_args, **_kwargs: None
    try:
        payload = _pfoa_unspecified_strap()
    finally:
        contaminants._logd = original
    assert payload["success"] is False
    assert payload["error_code"] == "unspecified_not_a_strap_basis"
    assert payload["leaching_basis_available"] is False
    assert payload["leaching_basis_solvents"] == []
    assert "steps" not in payload


def test_evaluator_path_carries_the_same_fields():
    payload = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", ["PET"], [_PFOA], solvents=["cyclohexanol"],
    )
    assert payload["success"] is False
    assert payload["error_code"] == "unspecified_not_a_strap_basis"
    assert payload["leaching_basis_available"] is True
    assert payload["leaching_basis_solvents"]
