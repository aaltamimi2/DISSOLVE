"""Search PlastChem contaminants by structure (owner, 2026-10-05): the elements a compound contains or lacks, named
functional groups, and a molecular-weight range. Features come from each compound's SMILES (RDKit), computed once per
release and kept in memory.

The release holds compounds built from the elements its campaign tiers computed: C, H, N and O, F, Cl, Br and I since
the halogen tier of 2026-10-06, and S, P and Si since the coverage campaign (A-13, promotion-v4, 2026-10-08); the
compound sets of Zhou et al., Green Chem. 2026 came in with promotion-v3. When an element is held only by those paper
sets, a search for it says so (the campaign computed no other compound with it). A search for an element no compound
holds (boron, selenium) is valid and finds nothing, and the result names the elements the release does hold. The
curated workbook's 26 PFAS, which store no structures, are also reached as the PFAS family."""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

from . import contaminants as screens
from .contracts import tool_error, tool_success

# name: (SMARTS, what it means). Matched with RDKit substructure search on the release's SMILES.
FUNCTIONAL_GROUPS: dict[str, tuple[str, str]] = {
    "carboxylic acid": ("[CX3](=O)[OX2H1]", "C(=O)OH"),
    "ester": ("[#6][#6X3](=O)[#8X2H0][#6;!$([#6X3]=O)]", "carboxylic ester, C(=O)O-C, not an anhydride"),
    "phthalate ester": ("[cR1](C(=O)O[#6])[cR1]C(=O)O[#6]", "benzene-1,2-dicarboxylate diester"),
    "lactone": ("[#6;R][#6X3;R](=O)[#8X2;R][#6;R;!$([#6X3]=O)]", "cyclic ester (coumarins too), not a cyclic anhydride"),
    "carbonate ester": ("[#6][OX2][CX3](=O)[OX2][#6]", "O-C(=O)-O"),
    "anhydride": ("[CX3](=O)[OX2][CX3](=O)", "C(=O)OC(=O)"),
    "acrylate": ("[CH2]=[CX3;H1,$([CH0]-[CH3])]-[CX3](=O)[OX2]", "acrylic or methacrylic ester or acid, "
                 "CH2=C(H or CH3)-C(=O)O; not maleates, crotonates or cinnamates"),
    "aldehyde": ("[CX3H1](=O)[#6]", "CH=O"),
    "ketone": ("[#6][CX3](=O)[#6]", "C-C(=O)-C"),
    "benzophenone": ("c-[CX3;!R](=O)-c", "two aryl rings on an open-chain C=O; not fluorenones or anthraquinones"),
    "quinone": ("[#6]1(=O)[#6]=,:[#6][#6](=O)[#6]=,:[#6]1", "para-quinone ring"),
    "alcohol": ("[OX2H][CX4]", "aliphatic OH"),
    "phenol": ("[OX2H]-c", "OH on an aromatic ring"),
    "hindered phenol": ("[OX2H]-c1:c(-[CX4H0]):c:c:c:c:1-[CX4H0]", "phenol with tertiary alkyl at both ortho positions"),
    "ether": ("[OD2]([#6;!$([CX3]=O)])[#6;!$([CX3]=O)]", "C-O-C, not an ester"),
    "epoxide": ("C1OC1", "three-membered C-O-C ring"),
    "amine": ("[NX3;H2,H1,H0;!$(N-[#6]=[#8,#7,#16]);!$(N-a);!$(N-[#7,#8])]", "aliphatic amine"),
    "aromatic amine": ("[NX3;H2,H1;!$(N-[#6]=[#8,#7,#16])]-c", "NH2 or NH on an aromatic ring"),
    "amide": ("[NX3][CX3](=O)[#6]", "C(=O)N"),
    "urea or carbamate": ("[NX3][CX3](=O)[NX3,OX2]", "N-C(=O)-N or N-C(=O)-O"),
    "imide": ("[CX3](=O)[NX3][CX3](=O)", "C(=O)NC(=O)"),
    "nitrile": ("[NX1]#[CX2]", "C#N"),
    "nitro": ("[$([NX3](=O)=O),$([NX3+](=O)[O-])]", "NO2"),
    "azo": ("[#6]-[NX2]=[NX2]-[#6]", "C-N=N-C"),
    "isocyanate": ("N=C=O", "N=C=O"),
    "benzotriazole": ("n1nc2ccccc2n1", "benzotriazole ring (UV absorbers)"),
    "aromatic ring": ("[a;r6]", "six-membered aromatic ring"),
    "fused aromatic rings": ("[a;$(a(:a)(:a):a)]", "two aromatic rings sharing a bond, as in naphthalene; not "
                             "indane or biphenyl"),
    "long alkyl chain": ("[CH2X4][CH2X4][CH2X4][CH2X4][CH2X4][CH2X4][CH2X4]", "seven or more CH2 in a row"),
}
HALOGENS = ("F", "Cl", "Br", "I")
_ALIASES = {"halogen": HALOGENS, "halogens": HALOGENS}
# Everyday names for a group, each naming exactly one entry above.
_GROUP_ALIASES = {"phthalate": "phthalate ester", "aromatic": "aromatic ring", "urea": "urea or carbamate",
                  "carbamate": "urea or carbamate", "urethane": "urea or carbamate", "lactam": "amide"}
_SPELLINGS = {"sulphur": "S", "sulfur": "S", "aluminium": "Al", "aluminum": "Al", "caesium": "Cs", "cesium": "Cs"}


def _asset_key() -> Optional[str]:
    """The promoted release's path and modification time: features are recomputed when the file changes."""
    if screens._plastchem() is None:
        return None
    path = getattr(screens._LOCAL, "plastchem", (None,))[0]
    return f"{path}@{Path(path).stat().st_mtime_ns}"


@lru_cache(maxsize=1)
def _patterns() -> dict[str, Any]:
    from rdkit import Chem, RDLogger  # RDKit is a dependency; imported here so a plain import costs nothing

    RDLogger.DisableLog("rdApp.*")
    return {name: Chem.MolFromSmarts(smarts) for name, (smarts, _what) in FUNCTIONAL_GROUPS.items()}


def structure_of(smiles: Optional[str]) -> Optional[tuple[frozenset[str], frozenset[str]]]:
    """(elements, functional groups) of one SMILES, hydrogen included when any atom carries one; None if it does
    not parse."""
    from rdkit import Chem

    patterns = _patterns()
    mol = Chem.MolFromSmiles(smiles) if smiles else None
    if mol is None:
        return None
    elements = {atom.GetSymbol() for atom in mol.GetAtoms()}
    if any(atom.GetTotalNumHs() for atom in mol.GetAtoms()):
        elements.add("H")
    return frozenset(elements), frozenset(name for name, pattern in patterns.items() if mol.HasSubstructMatch(pattern))


@lru_cache(maxsize=2)
def _features(asset: str) -> dict[int, tuple[frozenset[str], frozenset[str], Optional[float], bool]]:
    """id -> (elements, functional groups, molecular weight, computed), for every compound in the release."""
    out: dict[int, tuple[frozenset[str], frozenset[str], Optional[float], bool]] = {}
    for cid, smiles, weight, computed in screens._plastchem().execute(
        "SELECT id, smiles, molecular_weight, computed FROM contaminants"
    ).fetchall():
        elements, groups = structure_of(smiles) or (frozenset(), frozenset())
        out[int(cid)] = (elements, groups, float(weight) if weight is not None else None, bool(computed))
    return out


@lru_cache(maxsize=2)
def _tiers(asset: str) -> dict[int, str]:
    """id -> the campaign tier that computed it (main, tier2, halogen, publication, coverage)."""
    return {int(cid): str(tier) for cid, tier in screens._plastchem().execute("SELECT id, tier FROM contaminants").fetchall()}


_PAPER_SETS = "the PFAS, brominated flame retardants and phthalates of Zhou et al., Green Chem. 2026"


def paper_only_elements() -> list[str]:
    """Elements that only the paper's compound sets bring into the release (sulfur: its PFAS sulfonic acids); the
    campaign computed no other compound containing them."""
    asset = _asset_key()
    if not asset:
        return []
    tiers, paper, rest = _tiers(asset), set(), set()
    for cid, (elements, _groups, _weight, computed) in _features(asset).items():
        if computed:
            (paper if tiers.get(cid) == "publication" else rest).update(elements)
    return sorted(paper - rest, key=lambda e: (_ELEMENT_ORDER.index(e) if e in _ELEMENT_ORDER else 99, e))


@lru_cache(maxsize=1)
def _element_names() -> dict[str, str]:
    """'chlorine' -> 'Cl' for every element RDKit names, plus the other spelling of sulfur, aluminium and caesium."""
    from rdkit import Chem

    table = Chem.GetPeriodicTable()
    if not hasattr(table, "GetElementName"):  # RDKit before 2023.09: symbols only
        return dict(_SPELLINGS)
    names = {table.GetElementName(number).casefold(): table.GetElementSymbol(number) for number in range(1, 119)}
    return {**names, **_SPELLINGS}


def _symbol(text: str) -> Optional[str]:
    """An element symbol in any case ('cl', 'CL') or an element's name ('chlorine'); None for anything else."""
    from rdkit import Chem

    raw = str(text).strip()
    named = _element_names().get(raw.casefold())
    if named:
        return named
    candidate = raw[:1].upper() + raw[1:].lower()
    if not candidate.isalpha() or len(candidate) > 2:
        return None
    try:
        return candidate if Chem.GetPeriodicTable().GetAtomicNumber(candidate) > 0 else None
    except Exception:  # noqa: BLE001 - RDKit raises a bare RuntimeError for an unknown symbol
        return None


def _items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in (piece.strip() for piece in value.split(",")) if part]
    return [str(item).strip() for item in value if str(item).strip()]


def structure_filters(
    elements: Any = None, exclude_elements: Any = None, functional_groups: Any = None,
    mw_min_g_mol: Optional[float] = None, mw_max_g_mol: Optional[float] = None,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """(filters, None), (None, None) when no filter was given, or (None, refusal fields) for a bad one."""
    present, absent, groups = _items(elements), _items(exclude_elements), _items(functional_groups)
    if not (present or absent or groups) and mw_min_g_mol is None and mw_max_g_mol is None:
        return None, None
    resolved: dict[str, list[Any]] = {"all_of": [], "any_of": [], "none_of": []}
    for token in present:
        alias = _ALIASES.get(token.strip().casefold())
        if alias:
            resolved["any_of"].append(list(alias))
            continue
        symbol = _symbol(token)
        if symbol is None:
            return None, {"error_code": "unknown_element", "requested": token,
                          "detail": f"{token!r} is not an element symbol or name (Cl, N, O, chlorine ...); "
                                    "'halogen' means any of F, Cl, Br, I."}
        resolved["all_of"].append(symbol)
    for token in absent:
        alias = _ALIASES.get(token.strip().casefold())
        symbols = list(alias) if alias else [_symbol(token)]
        if None in symbols:
            return None, {"error_code": "unknown_element", "requested": token,
                          "detail": f"{token!r} is not an element symbol or name."}
        resolved["none_of"].extend(symbols)
    known = {name.casefold(): name for name in FUNCTIONAL_GROUPS}
    chosen_groups = []
    for token in groups:
        folded = " ".join(token.casefold().split())
        singular = folded[:-1] if folded.endswith("s") else folded
        name = (known.get(folded) or known.get(singular) or _GROUP_ALIASES.get(folded)
                or _GROUP_ALIASES.get(singular))
        if name is None:
            return None, {"error_code": "unknown_functional_group", "requested": token,
                          "functional_groups": sorted(FUNCTIONAL_GROUPS)}
        chosen_groups.append(name)
    try:
        low = None if mw_min_g_mol is None else float(mw_min_g_mol)
        high = None if mw_max_g_mol is None else float(mw_max_g_mol)
    except (TypeError, ValueError):
        low = high = math.nan
    if any(bound is not None and not math.isfinite(bound) for bound in (low, high)):
        return None, {"error_code": "invalid_molecular_weight", "detail": "molecular weights are numbers in g/mol"}
    if low is not None and high is not None and low > high:
        return None, {"error_code": "invalid_molecular_weight_range", "mw_min_g_mol": low, "mw_max_g_mol": high}
    return {"elements_all_of": resolved["all_of"], "elements_any_of": resolved["any_of"],
            "elements_none_of": sorted(set(resolved["none_of"])), "functional_groups": chosen_groups,
            "mw_min_g_mol": low, "mw_max_g_mol": high}, None


def select(filters: dict[str, Any], *, computed_only: bool = True) -> Optional[set[int]]:
    """Release ids meeting every filter (molecular-weight bounds inclusive); None when the release is not promoted."""
    asset = _asset_key()
    if asset is None:
        return None
    chosen = set()
    for cid, (elements, groups, weight, computed) in _features(asset).items():
        if computed_only and not computed:
            continue
        if any(symbol not in elements for symbol in filters["elements_all_of"]):
            continue
        if any(not elements.intersection(options) for options in filters["elements_any_of"]):
            continue
        if elements.intersection(filters["elements_none_of"]):
            continue
        if any(group not in groups for group in filters["functional_groups"]):
            continue
        if filters["mw_min_g_mol"] is not None and (weight is None or weight < filters["mw_min_g_mol"]):
            continue
        if filters["mw_max_g_mol"] is not None and (weight is None or weight > filters["mw_max_g_mol"]):
            continue
        chosen.add(cid)
    return chosen


def describe(filters: dict[str, Any]) -> str:
    parts = []
    if filters["elements_all_of"]:
        parts.append("containing " + " and ".join(filters["elements_all_of"]))
    for options in filters["elements_any_of"]:
        parts.append("containing any of " + ", ".join(options))
    if filters["elements_none_of"]:
        parts.append("without " + ", ".join(filters["elements_none_of"]))
    if filters["functional_groups"]:
        parts.append("with " + " and ".join(filters["functional_groups"]))
    if filters["mw_min_g_mol"] is not None:
        parts.append(f"at least {filters['mw_min_g_mol']:g} g/mol")
    if filters["mw_max_g_mol"] is not None:
        parts.append(f"at most {filters['mw_max_g_mol']:g} g/mol")
    return "; ".join(parts)


def _outside_release(filters: dict[str, Any]) -> list[str]:
    """Required elements no compound in the release contains, so an empty result can say why."""
    asset = _asset_key()
    present = set().union(*(item[0] for item in _features(asset).values())) if asset else set()
    missing = [symbol for symbol in filters["elements_all_of"] if symbol not in present]
    missing += [symbol for options in filters["elements_any_of"] if not present.intersection(options) for symbol in options]
    return missing


_ELEMENT_ORDER = ("C", "H", "N", "O", "F", "Cl", "Br", "I", "S", "P", "Si", "B")
# The curated workbook's 26 PFAS: every one has fluorine, the 11 sulfonates sulfur, and F-53B chlorine.
_WORKBOOK_PFAS_ELEMENTS = {"F", "S", "Cl"}


def _spoken(symbols: Sequence[str], joiner: str) -> str:
    items = list(symbols)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {joiner} " + items[-1]


def release_elements() -> list[str]:
    """The elements the release's compounds are built from, in the usual order (C, H, N, O first)."""
    asset = _asset_key()
    present = set().union(*(item[0] for item in _features(asset).values() if item[3])) if asset else set()
    return sorted(present, key=lambda e: (_ELEMENT_ORDER.index(e) if e in _ELEMENT_ORDER else 99, e))


def scope_note(filters: dict[str, Any]) -> Optional[str]:
    """Said when a search asks for an element that only the paper's compound sets bring into the release (sulfur), so a
    match is not read as the campaign covering compounds with that element."""
    asked = set(filters["elements_all_of"]) | {e for options in filters["elements_any_of"] for e in options}
    only_paper = [e for e in paper_only_elements() if e in asked]
    if not only_paper:
        return None
    return (f"In the release only compounds from {_PAPER_SETS} contain {_spoken(only_paper, 'or')}; the campaign "
            "computed no other PlastChem compound containing it.")


def empty_because(filters: dict[str, Any]) -> Optional[str]:
    """Why a search asking for an element the release lacks finds nothing; None when every element asked for is in
    it (an empty result then needs no excuse)."""
    missing = sorted(set(_outside_release(filters)))
    if not missing:
        return None
    text = (f"No compound in the PlastChem release contains {_spoken(missing, 'or')}: its compounds are built from "
            f"{_spoken(release_elements(), 'and')} only.")
    if _WORKBOOK_PFAS_ELEMENTS.intersection(missing):
        text += (" The curated workbook's 26 PFAS (all with fluorine, 11 with sulfur, one with chlorine) are reached as "
                 "the PFAS family.")
    return text


def find_plastchem_contaminants(
    elements: str | list[str] | None = None,
    exclude_elements: str | list[str] | None = None,
    functional_groups: str | list[str] | None = None,
    mw_min_g_mol: Optional[float] = None,
    mw_max_g_mol: Optional[float] = None,
    contaminants: str | list[str] | None = None,
) -> str:
    """Find PlastChem contaminants by structure: elements they contain (Cl, N, 'halogen'...) or lack, functional groups (ester, phenol, amine, phthalate ester...), and a molecular-weight range in g/mol, optionally within named contaminants or families."""
    tool = "find_plastchem_contaminants"
    con = screens._plastchem()
    if con is None:
        return tool_error(tool, "The PlastChem contaminant release has not been promoted into DISSOLVE yet.",
                          error_code="plastchem_data_unavailable")
    filters, refusal = structure_filters(elements, exclude_elements, functional_groups, mw_min_g_mol, mw_max_g_mol)
    if refusal is not None:
        return tool_error(tool, refusal.get("detail") or refusal["error_code"].replace("_", " "), **refusal)
    if filters is None:
        return tool_error(tool, "Give at least one element, functional group or molecular-weight bound.",
                          error_code="no_structure_filter", functional_groups=sorted(FUNCTIONAL_GROUPS))
    chosen = select(filters, computed_only=False)
    named = screens._requested(contaminants)
    unknown: list[str] = []
    if named:
        ids, _families, unknown, _ambiguous = screens._resolve_plastchem(con, named)
        chosen &= set(ids)
    features = _features(_asset_key())
    rows = []
    if chosen:
        for cid, name, cas, inchikey, smiles, weight in con.execute(
            "SELECT id, name, cas, inchikey, smiles, molecular_weight FROM contaminants WHERE id IN (SELECT unnest(?)) "
            "ORDER BY molecular_weight, lower(name)", [sorted(chosen)],
        ).fetchall():
            elements_of, groups_of, _w, computed = features[int(cid)]
            rows.append({"name": name, "cas": cas, "inchikey": inchikey, "smiles": smiles,
                         "molecular_weight_g_mol": weight, "elements": sorted(elements_of),
                         "functional_groups": sorted(groups_of), "partition_data": computed})
    reason = None if rows else empty_because(filters)
    note = scope_note(filters)
    campaign = [e for e in release_elements() if e not in paper_only_elements()]
    return tool_success(
        tool, analysis_type="contaminant_structure_search", filters=filters, described=describe(filters),
        matches=rows, total=len(rows), with_partition_data=sum(row["partition_data"] for row in rows),
        within=named or None, unsupported_contaminants=unknown,
        **({"empty_because": reason} if reason else {}),
        **({"scope_note": note} if note else {}),
        coverage=(f"PlastChem compounds built from {_spoken(campaign, 'and')}, and {_PAPER_SETS}; "
                  "features from each SMILES with RDKit"),
        functional_group_definitions={name: what for name, (_smarts, what) in FUNCTIONAL_GROUPS.items()
                                      if name in filters["functional_groups"]},
        method="Every filter must hold; molecular-weight bounds are inclusive. Functional groups are SMARTS "
               "substructure matches; partition_data says whether the release computed the compound's partitioning.",
    )


def search_categories() -> dict[str, Any]:
    """What a picker can offer: elements with how many compounds contain each, functional groups with counts, and the
    molecular-weight range, for compounds with partition data."""
    asset = _asset_key()
    if asset is None:
        return {"available": False}
    rows = [item for item in _features(asset).values() if item[3]]
    elements: dict[str, int] = {}
    groups: dict[str, int] = {}
    weights = sorted(item[2] for item in rows if item[2] is not None)
    for item in rows:
        for symbol in item[0]:
            elements[symbol] = elements.get(symbol, 0) + 1
        for name in item[1]:
            groups[name] = groups.get(name, 0) + 1
    return {
        "available": True, "compounds": len(rows),
        "elements": [{"symbol": symbol, "count": count} for symbol, count in sorted(elements.items(), key=lambda kv: -kv[1])],
        "functional_groups": [{"name": name, "meaning": FUNCTIONAL_GROUPS[name][1], "count": groups.get(name, 0)}
                              for name in FUNCTIONAL_GROUPS],
        "molecular_weight_g_mol": {"min": weights[0], "median": weights[len(weights) // 2], "max": weights[-1]}
        if weights else None,
    }
