"""Contaminants found by structure (owner, 2026-10-05): by element ("Cl, N, etc"), by functional group, and by
molecular weight below or above a mass. The oracles do not use the RDKit features the search is built on: the
release's own SMILES text for elements, its stored molecular weights read with SQL, and named compounds whose groups
are textbook chemistry."""
from __future__ import annotations

import json

import pytest

from dissolve import agent, contaminants
from dissolve import contaminant_search as search
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session

pytest.importorskip("rdkit")


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _keys(data: dict) -> set[str]:
    return {row["inchikey"] for row in data["matches"]}


def _smiles() -> dict[str, tuple[str, bool]]:
    """inchikey -> (SMILES, computed). The release writes SMILES in Kekulé form with C, H, N and O only, so an
    element's capital letter in the text is the element."""
    rows = contaminants._plastchem().execute("SELECT inchikey, smiles, computed FROM contaminants").fetchall()
    return {key: (smiles, bool(computed)) for key, smiles, computed in rows}


def test_an_element_search_finds_what_the_smiles_text_holds_and_names_by_symbol_or_word():
    release = _smiles()
    assert {letter for smiles, _ in release.values() for letter in smiles if letter.isalpha()} == set("CHNO")
    with_n = {key for key, (smiles, _) in release.items() if "N" in smiles}
    found = _data(search.find_plastchem_contaminants(elements="N"))
    assert _keys(found) == with_n and found["total"] == len(with_n) > 1000
    assert _keys(_data(search.find_plastchem_contaminants(elements=["nitrogen"]))) == with_n
    without_n = _data(search.find_plastchem_contaminants(exclude_elements="n"))
    assert _keys(without_n) == set(release) - with_n
    both = _data(search.find_plastchem_contaminants(elements=["N", "O"]))
    assert _keys(both) == {key for key in with_n if "O" in release[key][0]}
    assert both["described"] == "containing N and O"


def test_a_chlorine_search_is_valid_finds_nothing_and_says_why():
    """The release is C, H, N and O only (the test above proves it from the SMILES). "Cl" and "chlorine" are real
    elements, so the answer is an explained empty result, not a refusal."""
    for asked in ("Cl", "chlorine", ["CL"]):
        data = _data(search.find_plastchem_contaminants(elements=asked))
        assert data["success"] is True and data["total"] == 0 and data["matches"] == []
        assert "contains Cl" in data["empty_because"] and "carbon, hydrogen, nitrogen and oxygen" in data["empty_because"]
        assert "PFAS" in data["empty_because"]
    halogen = _data(search.find_plastchem_contaminants(elements="halogen"))
    assert halogen["total"] == 0 and "contains Br, Cl, F or I:" in halogen["empty_because"]
    assert _data(search.find_plastchem_contaminants(exclude_elements="halogens"))["total"] == len(_smiles())
    assert "empty_because" not in _data(search.find_plastchem_contaminants(elements="N", mw_max_g_mol=1.0))


@pytest.mark.parametrize(("name", "has", "lacks"), [
    ("DEHP", {"ester", "phthalate ester", "aromatic ring"}, {"phenol", "carboxylic acid", "amide"}),
    ("BHT", {"phenol", "hindered phenol", "aromatic ring"}, {"ester", "ketone"}),
    ("Bisphenol A", {"phenol", "aromatic ring"}, {"hindered phenol", "ester"}),
    ("Acrylamide", {"amide"}, {"acrylate", "amine", "aromatic ring"}),
    ("Caprolactam", {"amide"}, {"lactone", "ester", "amine"}),
    ("Benzophenone", {"benzophenone", "ketone", "aromatic ring"}, {"ester", "quinone"}),
    ("Drometrizole", {"benzotriazole", "phenol"}, {"hindered phenol", "amide"}),
    ("Methane", set(), set(search.FUNCTIONAL_GROUPS)),
])
def test_named_compounds_carry_their_textbook_functional_groups(name, has, lacks):
    data = _data(search.find_plastchem_contaminants(elements="C", contaminants=[name]))
    (row,) = data["matches"]
    assert has <= set(row["functional_groups"]) and not lacks & set(row["functional_groups"])
    for group in has:  # and a group search finds the compound
        assert row["inchikey"] in _keys(_data(search.find_plastchem_contaminants(functional_groups=group)))


@pytest.mark.parametrize(("smiles", "has", "lacks"), [
    ("C=C(C)C(=O)OC", {"acrylate", "ester"}, set()),  # methyl methacrylate
    ("COC(=O)/C=C\\C(=O)OC", {"ester"}, {"acrylate"}),  # dimethyl maleate: unsaturated, not acrylic
    ("COC(=O)/C=C/c1ccccc1", {"ester"}, {"acrylate"}),  # methyl cinnamate
    ("C=CC(=O)N", {"amide"}, {"acrylate", "ester"}),  # acrylamide
    ("O=C1OC(=O)c2ccccc12", {"anhydride"}, {"ester", "lactone", "phthalate ester"}),  # phthalic anhydride
    ("O=C1CCCO1", {"lactone", "ester"}, set()),  # gamma-butyrolactone
    ("O=c1ccc2ccccc2o1", {"lactone", "ester", "fused aromatic rings"}, set()),  # coumarin
    ("O=C1c2ccccc2-c2ccccc12", {"ketone"}, {"benzophenone"}),  # fluorenone: the C=O is in a ring
    ("O=C1c2ccccc2C(=O)c2ccccc12", {"quinone", "ketone"}, {"benzophenone", "fused aromatic rings"}),  # anthraquinone
    ("c1ccc2ccccc2c1", {"fused aromatic rings"}, set()),  # naphthalene
    ("c1ccc2c(c1)CCC2", {"aromatic ring"}, {"fused aromatic rings"}),  # indane: one aromatic ring
    ("c1ccc(-c2ccccc2)cc1", {"aromatic ring"}, {"fused aromatic rings"}),  # biphenyl: joined, not fused
    ("Nc1ccccc1", {"aromatic amine"}, {"amine", "amide"}),  # aniline
    ("CCN(CC)CC", {"amine"}, {"aromatic amine", "amide"}),  # triethylamine
    ("CC(=O)Nc1ccccc1", {"amide"}, {"aromatic amine", "amine"}),  # acetanilide
    ("COC(=O)OC", {"carbonate ester"}, {"ester", "ether"}),  # dimethyl carbonate
])
def test_group_patterns_mean_what_their_names_say(smiles, has, lacks):
    """Each pattern against molecules a chemist would sort without a computer, the near misses included."""
    _elements, groups = search.structure_of(smiles)
    assert has <= groups and not lacks & groups, sorted(groups)


def test_group_names_are_forgiving_but_never_guessed():
    for asked, meant in [("phthalates", "phthalate ester"), ("Phthalate Esters", "phthalate ester"),
                         ("hindered  phenols", "hindered phenol"), ("lactams", "amide"), ("urethane", "urea or carbamate")]:
        filters, refusal = search.structure_filters(functional_groups=asked)
        assert refusal is None and filters["functional_groups"] == [meant], asked
    refused = _data(search.find_plastchem_contaminants(functional_groups="sulfonamide"))
    assert refused["success"] is False and refused["error_code"] == "unknown_functional_group"
    assert refused["functional_groups"] == sorted(search.FUNCTIONAL_GROUPS)


def test_molecular_weight_bounds_match_sql_and_are_inclusive():
    con = contaminants._plastchem()
    (at_most,) = con.execute("SELECT count(*) FROM contaminants WHERE molecular_weight <= 200").fetchone()
    (at_least,) = con.execute("SELECT count(*) FROM contaminants WHERE molecular_weight >= 200").fetchone()
    below = _data(search.find_plastchem_contaminants(mw_max_g_mol=200))
    above = _data(search.find_plastchem_contaminants(mw_min_g_mol=200))
    assert (below["total"], above["total"]) == (at_most, at_least)
    weights = [row["molecular_weight_g_mol"] for row in below["matches"]]
    assert weights == sorted(weights) and weights[-1] <= 200  # lightest first
    assert min(row["molecular_weight_g_mol"] for row in above["matches"]) >= 200
    exact = _data(search.find_plastchem_contaminants(mw_min_g_mol=390.6, mw_max_g_mol=390.6, contaminants="DEHP"))
    assert [row["name"] for row in exact["matches"]] == ["Bis(2-ethylhexyl) phthalate"]  # 390.6 g/mol, both bounds
    assert _data(search.find_plastchem_contaminants(mw_max_g_mol=390.59, contaminants="DEHP"))["total"] == 0
    assert _data(search.find_plastchem_contaminants(mw_min_g_mol=390.61, contaminants="DEHP"))["total"] == 0


def test_every_filter_must_hold_at_once():
    nitrogen = _keys(_data(search.find_plastchem_contaminants(elements="N")))
    amides = _keys(_data(search.find_plastchem_contaminants(functional_groups="amide")))
    light = _keys(_data(search.find_plastchem_contaminants(mw_max_g_mol=300)))
    combined = _data(search.find_plastchem_contaminants(elements="N", functional_groups="amide", mw_max_g_mol=300))
    assert _keys(combined) == nitrogen & amides & light and combined["total"] > 0
    assert combined["described"] == "containing N; with amide; at most 300 g/mol"


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"elements": "banana"}, "unknown_element"),
    ({"exclude_elements": ["Xx"]}, "unknown_element"),
    ({"mw_min_g_mol": 300, "mw_max_g_mol": 200}, "invalid_molecular_weight_range"),
    ({"mw_min_g_mol": float("nan")}, "invalid_molecular_weight"),
    ({"mw_max_g_mol": "heavy"}, "invalid_molecular_weight"),
    ({}, "no_structure_filter"),
    ({"contaminants": "DEHP"}, "no_structure_filter"),
])
def test_a_bad_or_missing_filter_is_refused_by_name(kwargs, code):
    data = _data(search.find_plastchem_contaminants(**kwargs))
    assert data["success"] is False and data["error_code"] == code


def test_the_partitioning_screen_takes_the_same_filters_and_changes_no_row():
    """One solvent: the class's rows are exactly the unfiltered screen's rows for those compounds, so the filter
    selects and never alters a value."""
    esters = {row["inchikey"] for row in _data(search.find_plastchem_contaminants(
        functional_groups="phthalate ester", mw_max_g_mol=400))["matches"] if row["partition_data"]}
    filtered = _data(contaminants.screen_contaminant_partitioning(
        "PVC", solvent="ethanol", functional_groups="phthalate esters", mw_max_g_mol=400))
    every = _data(contaminants.screen_contaminant_partitioning("PVC", solvent="ethanol"))
    assert filtered["evaluated"] == filtered["structure_selected"] == len(esters) > 0
    assert filtered["structure_filter"] == "with phthalate ester; at most 400 g/mol"
    expected = sorted((row for row in every["rows"] if row["inchikey"] in esters), key=lambda row: row["inchikey"])
    assert sorted(filtered["rows"], key=lambda row: row["inchikey"]) == expected
    assert "structure_filter" not in every and "empty_because" not in filtered


def test_a_filter_narrows_named_families_and_drops_their_coverage_table():
    family = _data(contaminants.screen_contaminant_partitioning("PVC", solvent="ethanol", contaminants="phthalates"))
    light = _data(contaminants.screen_contaminant_partitioning(
        "PVC", solvent="ethanol", contaminants="phthalates", mw_max_g_mol=300))
    weights = {row["inchikey"]: row["molecular_weight_g_mol"]
               for row in _data(search.find_plastchem_contaminants(mw_max_g_mol=300))["matches"]}
    assert 0 < light["evaluated"] < family["evaluated"]
    assert {row["inchikey"] for row in light["rows"]} == {row["inchikey"] for row in family["rows"]
                                                          if row["inchikey"] in weights}
    assert "family_coverage" in family and "family_coverage" not in light


def test_a_class_ranks_every_panel_solvent_in_one_call_within_the_limit():
    names = sorted(row["name"] for row in _data(search.find_plastchem_contaminants(
        functional_groups="phthalate ester", mw_min_g_mol=380, mw_max_g_mol=400))["matches"] if row["partition_data"])
    ranked = _data(contaminants.screen_contaminant_partitioning(
        "PVC", functional_groups="phthalate ester", mw_min_g_mol=380, mw_max_g_mol=400))
    assert ranked["mode"] == "solvent_ranking" and ranked["solvents_screened"] == 32
    assert ranked["contaminants_screened"] == names and len(names) == ranked["structure_selected"] > 1
    too_many = _data(contaminants.screen_contaminant_partitioning("PVC", elements="N"))
    assert too_many["error_code"] == "too_many_contaminants_to_rank_solvents"


@pytest.mark.parametrize("solvent", ["ethanol", None])
def test_a_screen_for_chlorine_compounds_is_empty_and_explained(solvent):
    data = _data(contaminants.screen_contaminant_partitioning("PVC", solvent=solvent, elements="Cl"))
    assert data["success"] is True and data["evaluated"] == 0 and data["structure_selected"] == 0
    assert "contains Cl:" in data["empty_because"] and "carbon, hydrogen, nitrogen and oxygen" in data["empty_because"]


def test_a_bad_filter_on_the_screen_is_refused_by_the_screen():
    data = _data(contaminants.screen_contaminant_partitioning("PVC", solvent="ethanol", functional_groups="sulfonamide"))
    assert (data["success"], data["error_code"], data["tool_name"]) == (
        False, "unknown_functional_group", "screen_contaminant_partitioning")


def test_the_agent_can_call_the_search_and_page_a_large_class():
    (schema,) = [entry for entry in agent.tool_schemas() if entry["name"] == "find_plastchem_contaminants"]
    properties = schema["parameters"]["properties"]
    assert {"elements", "exclude_elements", "functional_groups", "mw_min_g_mol", "mw_max_g_mol"} <= set(properties)
    assert {"type": "array", "items": {"type": "string"}} in properties["elements"]["anyOf"]  # Gemini needs item types
    assert "find_plastchem_contaminants" in agent.SYSTEM_PROMPT and "empty_because" in agent.SYSTEM_PROMPT
    with bind_tool_session(new_session()):
        paged = agent.dispatch("find_plastchem_contaminants", elements=["N"])
        empty = agent.dispatch("find_plastchem_contaminants", elements=["Cl"])
    assert paged["available"] is True and paged["total"] > paged["shown"] > 0 and paged["handle"]
    assert paged["source_basis"] == "plastchem_identity"
    assert "contains Cl" in json.dumps(empty)


def test_the_categories_a_picker_would_offer_count_compounds_with_partition_data():
    release = _smiles()
    computed = [smiles for smiles, done in release.values() if done]
    categories = search.search_categories()
    assert categories["available"] is True and categories["compounds"] == len(computed)
    counts = {item["symbol"]: item["count"] for item in categories["elements"]}
    assert counts == {symbol: sum(symbol in smiles for smiles in computed) for symbol in "CNO"} | {"H": counts["H"]}
    assert [item["name"] for item in categories["functional_groups"]] == list(search.FUNCTIONAL_GROUPS)
    weights = categories["molecular_weight_g_mol"]
    assert weights["min"] <= weights["median"] <= weights["max"]
