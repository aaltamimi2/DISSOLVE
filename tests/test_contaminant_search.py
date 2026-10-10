"""Contaminants found by structure (owner, 2026-10-05): by element ("Cl, N, etc"), by functional group, and by
molecular weight below or above a mass. The oracles do not use the RDKit features the search is built on: the
release's own SMILES text for elements, its stored molecular weights read with SQL, and named compounds whose groups
are textbook chemistry."""
from __future__ import annotations

import json
import re

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
    """inchikey -> (SMILES, computed)."""
    rows = contaminants._plastchem().execute("SELECT inchikey, smiles, computed FROM contaminants").fetchall()
    return {key: (smiles, bool(computed)) for key, smiles, computed in rows}


_TOKEN = re.compile(r"\[(?:\d*)([A-Z][a-z]?|[bcnops])[^\]]*\]|(Cl|Br|[BCNOPSFI]|[bcnops])")


def _elements_in(smiles: str) -> set[str]:
    """Element symbols read from the SMILES text alone, no RDKit: bracket atoms and the organic subset (two-letter
    Cl and Br before one-letter C and B). Hydrogen counts only where written."""
    found = set()
    for bracket, organic in _TOKEN.findall(smiles):
        symbol = bracket or organic
        found.add(symbol.capitalize() if symbol in "bcnops" else symbol)
    return found


def test_an_element_search_finds_what_the_smiles_text_holds_and_names_by_symbol_or_word():
    release = _smiles()
    elements = {key: _elements_in(smiles) for key, (smiles, _) in release.items()}
    computed = {key for key, (_, done) in release.items() if done}
    held = set().union(*(elements[key] for key in computed)) | {"H"}
    # phosphorus and silicon came in with the coverage campaign (A-13, promotion-v4); no compound holds boron
    assert set(search.release_elements()) == held and {"S", "P", "Si"} <= held and "B" not in held
    # the campaign computes sulfur compounds itself since promotion-v4 (in promotion-v3 sulfur came only from the
    # paper's PFAS, and this was ["S"])
    assert search.paper_only_elements() == []
    for symbol in sorted(held - {"C", "H"}):
        expected = {key for key, found in elements.items() if symbol in found}
        found = _data(search.find_plastchem_contaminants(elements=symbol))
        assert _keys(found) == expected and found["total"] == len(expected) > 0, symbol
    with_n = {key for key, found in elements.items() if "N" in found}
    assert len(with_n) > 1000
    assert _keys(_data(search.find_plastchem_contaminants(elements=["nitrogen"]))) == with_n
    without_n = _data(search.find_plastchem_contaminants(exclude_elements="n"))
    assert _keys(without_n) == set(release) - with_n
    both = _data(search.find_plastchem_contaminants(elements=["N", "O"]))
    assert _keys(both) == {key for key in with_n if "O" in elements[key]}
    assert both["described"] == "containing N and O"


def test_a_search_for_an_element_the_release_lacks_is_valid_finds_nothing_and_says_why():
    """Boron is a real element no structure in the release contains (openCOSMO-RS 24a has no boron parameters, so the
    coverage campaign leaves it out), so the answer is an explained empty result, not a refusal, and it names what the
    release holds. Phosphorus was this example until the coverage campaign computed phosphorus compounds (A-13,
    promotion-v4); sulfur, which promotion-v3 held only through the paper's 11 PFAS, is the campaign's own too, so a
    sulfur search finds both and needs no scope note."""
    held = search.release_elements()
    assert {"C", "H", "N", "O", "S", "P", "Si"} <= set(held) and "B" not in held
    for asked in ("B", "boron", ["b"]):
        data = _data(search.find_plastchem_contaminants(elements=asked))
        assert data["success"] is True and data["total"] == 0 and data["matches"] == []
        assert "contains B:" in data["empty_because"] and ", ".join(held[:-1]) + " and " + held[-1] in data["empty_because"]
        assert "PFAS" not in data["empty_because"]  # no PFAS holds boron
    release = _smiles()
    with_s = {key for key, (smiles, _done) in release.items() if "S" in _elements_in(smiles)}
    computed_s = {key for key in with_s if release[key][1]}
    sulfur = _data(search.find_plastchem_contaminants(elements="sulfur"))
    assert (sulfur["total"], sulfur["with_partition_data"]) == (len(with_s), len(computed_s)) and len(computed_s) > 11
    assert "empty_because" not in sulfur and "scope_note" not in sulfur
    assert _data(search.find_plastchem_contaminants(exclude_elements="B"))["total"] == len(release)
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
def test_a_screen_for_boron_compounds_is_empty_and_explained(solvent):
    """Boron took phosphorus's place here when the coverage campaign computed phosphorus compounds (promotion-v4)."""
    data = _data(contaminants.screen_contaminant_partitioning("PVC", solvent=solvent, elements="B"))
    assert data["success"] is True and data["evaluated"] == 0 and data["structure_selected"] == 0
    assert "contains B:" in data["empty_because"] and "built from C, H, N" in data["empty_because"]


@pytest.mark.parametrize("element", ["S", "P", "Si"])
def test_a_screen_for_sulfur_phosphorus_or_silicon_reaches_the_campaigns_compounds(element):
    """The coverage campaign (A-13, promotion-v4) computes sulfur, phosphorus and silicon compounds: a screen by any of
    them selects every computed compound holding it, with no scope note (in promotion-v3 a sulfur screen reached only
    the paper's 11 PFAS and said so, and phosphorus and silicon found nothing)."""
    computed = {key for key, (smiles, done) in _smiles().items() if done and element in _elements_in(smiles)}
    data = _data(contaminants.screen_contaminant_partitioning("PVC", solvent="ethanol", elements=element))
    assert data["success"] is True and data["structure_selected"] == len(computed) > 0 and "empty_because" not in data
    assert "scope_note" not in data


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
        empty = agent.dispatch("find_plastchem_contaminants", elements=["B"])
    assert paged["available"] is True and paged["total"] > paged["shown"] > 0 and paged["handle"]
    assert paged["source_basis"] == "plastchem_identity"
    assert "contains B:" in json.dumps(empty)


def test_the_categories_a_picker_would_offer_count_compounds_with_partition_data():
    release = _smiles()
    computed = [smiles for smiles, done in release.values() if done]
    categories = search.search_categories()
    assert categories["available"] is True and categories["compounds"] == len(computed)
    counts = {item["symbol"]: item["count"] for item in categories["elements"]}
    oracle = [_elements_in(smiles) for smiles in computed]
    assert {k: v for k, v in counts.items() if k != "H"} == {
        symbol: sum(symbol in found for found in oracle) for symbol in set().union(*oracle) - {"H"}}
    assert [item["name"] for item in categories["functional_groups"]] == list(search.FUNCTIONAL_GROUPS)
    weights = categories["molecular_weight_g_mol"]
    assert weights["min"] <= weights["median"] <= weights["max"]
