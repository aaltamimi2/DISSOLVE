"""Identity join + §7 facts that do not depend on contaminant-mode test 2.

Does not implement unspecified-as-strap-refuse. No BioSTEAM.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_MEASURE = None


def _measure():
    global _MEASURE
    if _MEASURE is None:
        path = _ROOT / "audit" / "measure_contaminant_identity.py"
        spec = importlib.util.spec_from_file_location(
            "measure_contaminant_identity", path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_dehp_and_other_parentheticals_expand():
    aliases = _measure().identity()["parenthetical_aliases"]
    assert aliases["DEHP"]["supported"] == [
        "di-(2-ethylhexyl) phthalate (DEHP)",
    ]
    assert aliases["DEHP"]["families"] == ["Phthalates"]
    assert aliases["BBP"]["supported"] == ["butyl benzyl phthalate (BBP)"]
    assert aliases["DiNP"]["supported"] == ["di-isononyl phthalate (DiNP)"]
    assert aliases["DEP"]["unsupported"] == []


def test_inner_parentheticals_do_not_expand():
    aliases = _measure().identity()["parenthetical_aliases"]
    assert aliases["2-ethylhexyl"]["supported"] == []
    assert aliases["2-ethylhexyl"]["unsupported"] == ["2-ethylhexyl"]
    assert aliases["heptafluoropropoxy"]["supported"] == []
    assert aliases["heptafluoropropoxy"]["unsupported"] == ["heptafluoropropoxy"]


def test_resolved_thermo_name_hits_workbook_row():
    hits = _measure().identity()["constructed_hits"]
    assert hits["butanone_equals_2_butanone"] is True
    assert hits["aceticacid_equals_acetic_acid"] is True


def test_o_xylene_does_not_become_catalog_xylene():
    item = _measure().identity()["o_xylene_is_not_catalog_xylene"]
    assert item["different_identities"] is True
    assert item["dehp_o_xylene_hits"] is True


def test_key_not_equal_resolved_is_nineteen_of_thirty_three():
    ident = _measure().identity()
    assert ident["logd_distinct_solvents"] == 33
    assert ident["key_not_equal_resolved_count"] == 19
    assert ident["solvent_raw_is_queried"] is True


def test_leaching_and_strap_invert_the_polymer_requirement():
    item = _measure().screens()
    assert item["leaching"]["success"] is True
    assert item["strap"]["success"] is True
    assert item["leaching"]["mode"] == "leaching"
    assert item["strap"]["mode"] == "strap_contaminant_removal"
    assert item["inverted_polymer_requirement"]["pass_sets_differ"] is True
    assert "remains below" in " ".join(item["leaching"]["decision_basis"] or [])
    assert any(
        "precipitation" in str(line).lower()
        or "dissolution" in str(line).lower()
        for line in (item["strap"]["decision_basis"] or [])
    )


def test_threshold_split_is_recorded_not_relabelled():
    item = _measure().thresholds()
    assert item["defaults"]["precipitation_threshold_wt_pct"] == 1.0
    assert item["split"]["precipitation_threshold_wt_pct"]["status"] == (
        "paper_sourced"
    )
    assert item["split"]["dissolution_min_wt_pct"]["status"] == "unsourced"
    assert item["served_still_labels_block_unsourced"] is True
    assert item["no_citation_invented"] is True


def test_absence_cases_are_constructed():
    item = _measure().absence()
    assert item["all_unknown"]["refuses"] is True
    assert item["all_unknown"]["error_code"] == "unsupported_contaminants"
    assert item["uncovered_class_BFR"]["empty_is_not_clean"] is True
    assert item["uncovered_class_BFR"]["error_code"] == (
        "unsupported_contaminant_family"
    )
    assert item["uncovered_class_BFR"]["distinct_family_code"] is True
    assert item["uncovered_class_BFR"]["supported_families"] == [
        "PFAS", "Phthalates",
    ]
    assert item["mixed_known_unknown_continues"]["success"] is True
    assert item["mixed_known_unknown_continues"]["unsupported_contaminants"] == [
        "HBCD",
    ]
    assert item["unknown_solvent"]["refuses"] is True
    assert item["unknown_solvent"]["error_code"] == "unknown_contaminant_solvent"
    assert item["unknown_solvent"]["today_is_success_with_failing_candidate"] is False
    unspecified = item["unspecified_fallback"]
    assert unspecified["all_unspecified_are_pfas"] is True
    assert unspecified["rt_and_t_higher_are_phthalates"] is True
    assert unspecified["unspecified_not_a_strap_basis_not_implemented"] is True
    assert unspecified["toluene_pfoa_asked_rt"]["temperature_regime"] == (
        "unspecified"
    )
