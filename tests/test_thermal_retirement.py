"""Accept tests for retiring the thermal estimator from the agent surface.

A-1 diffs registered names by identity against the frozen 30-name parent
set at 7f3f7fb. A count of 29 is not enough: one drop plus one add would
also be 29. A-2 keeps the function in the package. A-3 stops advertising
Van Krevelen as agent-available while the Tg 7736 row stays.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import tool_schemas
from dissolve import analysis, registry
from dissolve.cli import EXPECTED_REGISTRY_NAMES
from dissolve.contracts import parse_tool_result

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
    now = frozenset(registry.BY_NAME)
    assert (_PARENT_REGISTRY_NAMES - now) == frozenset({"estimate_thermal_properties"})
    assert (now - _PARENT_REGISTRY_NAMES) == frozenset()
    assert len(now) == 29
    assert now == EXPECTED_REGISTRY_NAMES
    assert len(registry.REGISTRY) == 29
    assert len(tool_schemas()) == 30
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
