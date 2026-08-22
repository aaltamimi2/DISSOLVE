"""v3 §8.3–§8.4: leaching embed plus STRAP stamp on remaining-polymer others.

Accept test 2 is DEHP / toluene / {PET,EVOH} fail vs {EVOH} pass, both rows rt.
Do not weaken it. No BioSTEAM.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import contaminants, separation
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session


_DEHP = "di-(2-ethylhexyl) phthalate (DEHP)"
_TRIPLE = ["LDPE", "PET", "EVOH"]
_PAIR = ["LDPE", "EVOH"]


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _plan(feed, **kwargs) -> dict:
    return _data(separation.plan_multistage_separation(
        feed, top_k_routes=1, breadth=1, **kwargs,
    ))


def _candidate(payload: dict, solvent: str | None = None) -> dict:
    rows = payload.get("candidate_solvents") or []
    if solvent is None:
        return rows[0] if rows else {}
    wanted = solvent.casefold()
    for row in rows:
        if str(row.get("solvent") or "").casefold() == wanted:
            return row
    return {}


def _regimes(row: dict) -> list[str]:
    hot = [item.get("miscibility_regime") for item in (row.get("contaminants") or [])]
    cold = [
        item.get("miscibility_regime")
        for item in (row.get("precipitation_regime_contaminants") or [])
    ]
    return [str(item) for item in hot + cold if item is not None]


def _published_routes(payload: dict) -> list[dict]:
    routes = list(payload.get("top_k_sequences") or [])
    if payload.get("steps") and payload not in routes:
        routes = [payload, *routes]
    return routes


def _ldpe_step(payload: dict, *, others: set[str] | None = None) -> dict:
    for route in _published_routes(payload):
        for item in route.get("steps") or []:
            if item.get("dissolved_polymer") != "LDPE":
                continue
            if others is None or set(item.get("other_polymers") or []) == others:
                return item
    raise AssertionError(
        f"planner emitted no LDPE dissolution with others={others}"
    )


def test_no_parallel_planner():
    source = Path(separation.__file__).read_text()
    assert "def plan_multistage_separation_with_contaminants" not in source
    assert "def plan_multistage_separation(" in source
    assert "_embed_leaching_route" in inspect.getsource(
        separation.plan_multistage_separation,
    )
    assert "_stamp_strap_route" in inspect.getsource(
        separation.plan_multistage_separation,
    )
    assert "others_from_feed_state" in inspect.getsource(
        separation._stamp_strap_route,
    )
    assert "others_from_feed_state" in inspect.getsource(
        separation._embed_leaching_route,
    )


def test_omitted_contaminants_are_inert_across_session_modes():
    def run(mode: str | None) -> dict:
        record = new_session()
        if mode is not None:
            record["contaminant_mode"] = {"mode": mode}
        with bind_tool_session(record):
            return _plan(["LDPE", "PP"])

    unset = run(None)
    assert unset["success"] is True
    assert unset == run("off") == run("leaching") == run("strap")
    assert all(not str(item).startswith("wash") for item in unset["best_sequence"])
    assert "wash" not in (unset.get("solvent_mapping") or {})
    assert "positions_considered" not in unset
    for item in unset.get("steps") or []:
        assert item.get("path") is None
        assert item.get("feed_state_at_step") is None


def test_off_plus_contaminants_embeds_nothing_and_does_not_refuse():
    payload = _plan(
        ["LDPE", "PP"],
        contaminants=["not-a-real-contaminant", _DEHP],
        contaminant_mode="off",
    )
    assert payload["success"] is True
    assert payload["contaminant_mode"] == "off"
    assert all(not str(item).startswith("wash") for item in payload["best_sequence"])
    assert "wash" not in (payload.get("solvent_mapping") or {})
    assert "positions_considered" not in payload
    assert "strap_evaluations" not in payload
    for item in payload.get("steps") or []:
        assert item.get("path") is None
        assert item.get("feed_state_at_step") is None


def test_accept_test_2_through_planner_derived_others():
    three = _data(separation.plan_multistage_separation(
        _TRIPLE, contaminants=_DEHP, contaminant_mode="strap",
        top_k_routes=5, breadth=1,
    ))
    two = _data(separation.plan_multistage_separation(
        _PAIR, contaminants=_DEHP, contaminant_mode="strap",
        top_k_routes=5, breadth=1,
    ))
    assert three["success"] is True
    assert two["success"] is True
    assert three["contaminant_mode"] == "strap"
    assert "wash" not in (three.get("best_sequence") or [])
    assert "wash" not in (two.get("best_sequence") or [])
    assert all(
        item.get("step_kind") != "wash"
        for route in _published_routes(three)
        for item in (route.get("steps") or [])
    )
    first_state = (three.get("steps") or [{}])[0].get("feed_state_at_step") or {}
    assert first_state.get("inventory_model") == "none"
    assert set(first_state.get("polymers") or []) == set(_TRIPLE)
    ldpe_fail = _ldpe_step(three, others={"PET", "EVOH"})
    ldpe_pass = _ldpe_step(two, others={"EVOH"})
    assert ldpe_fail.get("path") == "strap"
    assert ldpe_pass.get("path") == "strap"
    assert ldpe_fail["feed_state_at_step"]["inventory_model"] == "none"
    assert ldpe_pass["feed_state_at_step"]["inventory_model"] == "none"
    fail_others = contaminants.others_from_feed_state(
        ldpe_fail["feed_state_at_step"], "LDPE",
    )
    pass_others = contaminants.others_from_feed_state(
        ldpe_pass["feed_state_at_step"], "LDPE",
    )
    assert set(fail_others) == {"PET", "EVOH"}
    assert pass_others == ["EVOH"]
    fail = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", fail_others, [_DEHP], solvents=["toluene"],
    )
    passed = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", pass_others, [_DEHP], solvents=["toluene"],
    )
    fail_row = _candidate(fail, "toluene")
    pass_row = _candidate(passed, "toluene")
    assert fail_row.get("passes") is False
    assert pass_row.get("passes") is True
    assert _regimes(pass_row) == ["rt", "rt"]
    if str(ldpe_pass.get("solvent") or "").casefold() == "toluene":
        assert ldpe_pass.get("passes") is True
        assert _regimes(ldpe_pass) == ["rt", "rt"]


def test_stamp_strap_route_flips_on_toluene_when_others_change():
    fail_route = separation._stamp_strap_route(
        {
            "complete": True,
            "final_residue": "EVOH",
            "unresolved_polymers": [],
            "steps": [{
                "step_kind": "dissolution",
                "dissolved_polymer": "LDPE",
                "solvent": "toluene",
                "temperature_c": 105.0,
                "selectivity_pct": 80.0,
                "target_solubility_pct": 20.0,
                "off_target_solubilities_pct": {"PET": 0.5, "EVOH": 0.5},
            }],
        },
        names=_TRIPLE,
        supported=[_DEHP],
        temperature_max_c=None,
        strict_maximum=False,
    )
    pass_route = separation._stamp_strap_route(
        {
            "complete": True,
            "final_residue": "EVOH",
            "unresolved_polymers": [],
            "steps": [{
                "step_kind": "dissolution",
                "dissolved_polymer": "LDPE",
                "solvent": "toluene",
                "temperature_c": 105.0,
                "selectivity_pct": 80.0,
                "target_solubility_pct": 20.0,
                "off_target_solubilities_pct": {"EVOH": 0.5},
            }],
        },
        names=_PAIR,
        supported=[_DEHP],
        temperature_max_c=None,
        strict_maximum=False,
    )
    fail_step = fail_route["steps"][0]
    pass_step = pass_route["steps"][0]
    assert fail_step["other_polymers"] == ["PET", "EVOH"]
    assert pass_step["other_polymers"] == ["EVOH"]
    assert fail_step["passes"] is False
    assert pass_step["passes"] is True
    assert _regimes(pass_step) == ["rt", "rt"]
    assert "wash" not in fail_route["sequence"]
    assert "wash" not in pass_route["sequence"]
    assert all(
        row.get("resolution_basis") == "cas_verified"
        and row.get("identity_verified") is True
        for row in (pass_step.get("contaminants") or [])
    )


def test_owner_example_strap_stamps_each_dissolution_without_a_wash():
    payload = _plan(
        _TRIPLE, contaminants="PFAS", contaminant_mode="strap",
    )
    assert payload["success"] is True
    assert payload["contaminant_mode"] == "strap"
    assert "wash" not in (payload.get("best_sequence") or [])
    assert "positions_considered" not in payload
    evaluations = payload.get("strap_evaluations") or []
    dissolutions = [
        item for item in (payload.get("steps") or [])
        if item.get("dissolved_polymer")
    ]
    assert evaluations
    assert len(evaluations) == len(dissolutions)
    for item in dissolutions:
        assert item.get("path") == "strap"
        assert item["feed_state_at_step"]["inventory_model"] == "none"
        others = contaminants.others_from_feed_state(
            item["feed_state_at_step"], item["dissolved_polymer"],
        )
        assert item.get("other_polymers") == others
    catalog = payload.get("contaminant_catalog") or []
    assert len(catalog) == 26
    assert all(
        row["resolution_basis"] == "catalog_declared"
        and row["identity_verified"] is not True
        for row in catalog
    )


def test_refusals_stay_constructed():
    junk = _data(separation.plan_multistage_separation(
        ["LDPE", "PP"], contaminants="not-a-real-contaminant",
        contaminant_mode="leaching",
    ))
    assert junk["success"] is False
    assert junk["error_code"] == "unsupported_contaminants"
    family = _data(separation.plan_multistage_separation(
        ["LDPE", "PP"], contaminants="BFR", contaminant_mode="leaching",
    ))
    assert family["success"] is False
    assert family["error_code"] == "unsupported_contaminant_family"
    invalid = _data(separation.plan_multistage_separation(
        ["LDPE", "PP"], contaminants=_DEHP, contaminant_mode="banana",
    ))
    assert invalid["success"] is False
    assert invalid["error_code"] == "invalid_contaminant_mode"


def test_accept_test_2_on_evaluator_only():
    fail = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", ["PET", "EVOH"], [_DEHP], solvents=["toluene"],
    )
    passed = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", ["EVOH"], [_DEHP], solvents=["toluene"],
    )
    fail_row = _candidate(fail, "toluene")
    pass_row = _candidate(passed, "toluene")
    assert fail["success"] is True
    assert fail_row.get("passes") is False
    assert passed["success"] is True
    assert pass_row.get("passes") is True
    assert _regimes(pass_row) == ["rt", "rt"]
    assert pass_row.get("unspecified_not_a_strap_basis") is False


def test_frozen_initial_feed_others_changes_the_toluene_evoh_result():
    after_pet = contaminants.feed_state_at_step(
        feed_order=_TRIPLE, remaining=_PAIR, contaminants=[_DEHP],
    )
    derived = contaminants.others_from_feed_state(after_pet, "LDPE")
    frozen = ["PET", "EVOH"]
    assert derived == ["EVOH"]
    derived_screen = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", derived, [_DEHP], solvents=["toluene"],
    )
    frozen_screen = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", frozen, [_DEHP], solvents=["toluene"],
    )
    assert _candidate(derived_screen, "toluene").get("passes") is True
    assert _candidate(frozen_screen, "toluene").get("passes") is False
    assert _regimes(_candidate(derived_screen, "toluene")) == ["rt", "rt"]


def test_leaching_enumerates_positions_and_names_the_objective():
    payload = _plan(
        _TRIPLE, contaminants=_DEHP, contaminant_mode="leaching",
    )
    assert payload["success"] is True
    assert payload["contaminant_mode"] == "leaching"
    considered = payload.get("positions_considered") or []
    dissolutions = [
        item for item in (payload.get("steps") or [])
        if item.get("step_kind") == "dissolution"
    ]
    assert len(considered) == len(dissolutions) + 1
    assert len(considered) >= 2
    assert payload.get("chosen_wash_position") in {item["index"] for item in considered}
    winner = next(
        item for item in considered
        if item["index"] == payload["chosen_wash_position"]
    )
    assert set(winner) >= {"passing_count", "contaminant_logd_min", "index"}
    washes = [
        item for item in (payload.get("steps") or [])
        if item.get("step_kind") == "wash"
    ]
    assert len(washes) == 1
    wash = washes[0]
    assert wash["path"] == "leaching"
    assert wash["feed_state_at_step"]["inventory_model"] == "none"
    assert "dissolved_polymer" not in wash
    assert "wash" in (payload.get("best_sequence") or [])
    assert "wash" not in (payload.get("solvent_mapping") or {})
    catalog = payload.get("contaminant_catalog") or []
    assert catalog
    assert all(row["resolution_basis"] == "cas_verified" for row in catalog)
    assert all(row["identity_verified"] is True for row in catalog)
    for item in considered:
        assert item["feed_state_at_step"]["inventory_model"] == "none"
        assert "passing_count" in item
        assert "index" in item


def test_owner_example_leaching_pfas_enumerates_positions():
    payload = _plan(
        _TRIPLE, contaminants="PFAS", contaminant_mode="leaching",
    )
    assert payload["success"] is True
    considered = payload.get("positions_considered") or []
    assert len(considered) >= 2
    for item in considered:
        assert item["feed_state_at_step"]["inventory_model"] == "none"
    winner = next(
        item for item in considered
        if item["index"] == payload["chosen_wash_position"]
    )
    assert set(winner) >= {"passing_count", "contaminant_logd_min", "index"}
    assert any(item.get("step_kind") == "wash" for item in payload["steps"])
    catalog = payload.get("contaminant_catalog") or []
    assert len(catalog) == 26
    assert all(
        row["resolution_basis"] == "catalog_declared"
        and row["identity_verified"] is not True
        for row in catalog
    )


def test_session_leaching_binds_only_when_contaminants_supplied():
    record = new_session()
    record["contaminant_mode"] = {"mode": "leaching"}
    with bind_tool_session(record):
        payload = _plan(["LDPE", "PP"], contaminants=_DEHP)
    assert payload["success"] is True
    assert payload["contaminant_mode"] == "leaching"
    assert payload.get("contaminant_mode_origin") == "session"
    assert any(item.get("step_kind") == "wash" for item in payload["steps"])


def test_wash_temperature_conflict_refuses_without_reordering():
    adjacent = [
        {
            "step_kind": "wash",
            "solvent": "toluene",
            "temperature_c": 25.0,
        },
        {
            "step_kind": "dissolution",
            "dissolved_polymer": "LDPE",
            "solvent": "toluene",
            "temperature_c": 105.0,
        },
    ]
    assert separation._wash_temperature_conflict(adjacent, 5.0) is True
    assert separation._wash_temperature_conflict(
        [
            {**adjacent[0], "solvent": "acetone"},
            adjacent[1],
        ],
        5.0,
    ) is False

    def fake_evaluate(*_args, **_kwargs):
        return {
            "success": True,
            "recommended_solvents": ["toluene"],
            "candidate_solvents": [{
                "solvent": "toluene",
                "passes": True,
                "operating_temperature_c": 25.0,
                "contaminant_logd_min": 0.81,
            }],
            "threshold_citation_status": "paper_sourced",
        }

    original = contaminants.evaluate_contaminant_at_feed_state
    contaminants.evaluate_contaminant_at_feed_state = fake_evaluate
    try:
        result = separation._embed_leaching_route(
            {
                "complete": True,
                "final_residue": "PP",
                "unresolved_polymers": [],
                "steps": [{
                    "step_kind": "dissolution",
                    "dissolved_polymer": "LDPE",
                    "solvent": "toluene",
                    "temperature_c": 105.0,
                    "selectivity_pct": 80.0,
                    "target_solubility_pct": 20.0,
                    "off_target_solubilities_pct": {"PP": 0.5},
                }],
            },
            names=["LDPE", "PP"],
            supported=[_DEHP],
            solvents=["toluene"],
            temperature_max_c=None,
            strict_maximum=False,
            step_c=5.0,
        )
    finally:
        contaminants.evaluate_contaminant_at_feed_state = original
    assert isinstance(result, str)
    payload = _data(result)
    assert payload["success"] is False
    assert payload["error_code"] == "incompatible_wash_temperature"
