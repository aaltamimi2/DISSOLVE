"""Commit F: table lookup + served identity. No query-time parser.

Regex is gone. Fragments stay unsupported because they are not rows.
identity_verified is true only for cas_verified. No embed. No BioSTEAM.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import contaminants, separation
from dissolve.contracts import parse_tool_result


_HELD = _ROOT / "src/dissolve/data/contaminant_cas_phthalates.local.v1.json"
_HELD_SHA256 = "eca9233329f8a33612996fd40669d94bdcd8036ed2641e51439bb3e18ee8654c"
_PIN = "0e6edf9debb0e7435c4382d7149fa2f12b620983ea94e5281ed770a872e42683"
_STRUCTURES = _ROOT / "src/dissolve/data/contaminant_structures.local.v1.json"
_STRUCTURES_SHA256 = "29838a0aa353bae765965bda444dad31ee288bcc27390d3bfa1d330669a72cd2"
_SHORTS = ("BBP", "DEHP", "DiDP", "DiNP", "DBP", "DnHP", "DnOP", "DEP")
_OWNER = {
    "butyl benzyl phthalate (bbp)": ("85-68-7", "2347"),
    "di-(2-ethylhexyl) phthalate (dehp)": ("117-81-7", "8343"),
    "di-isodecyl phthalate(didp)": ("26761-40-0", "33599"),
    "di-isononyl phthalate (dinp)": ("28553-12-0", "590836"),
    "di-n-butyl phthalate (dbp)": ("84-74-2", "3026"),
    "di-n-hexyl phthalate (dnhp)": ("84-75-3", "6786"),
    "di-n-octyl phthalate (dnop)": ("117-84-0", "8346"),
    "diethyl phthalate (dep)": ("84-66-2", "6781"),
}
_PERFLUORO_SOLVENT_CAS = {
    "355-25-9", "335-57-9", "355-42-0", "678-26-2",
    "355-02-2", "116-14-3", "76-05-1",
}
_BASIS = {"cas_verified", "held_snapshot", "catalog_declared"}


def _key(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _rows() -> list[tuple]:
    return contaminants._connection().execute(
        "SELECT alias, contaminant_key, canonical_name, family, "
        "cas_number, resolution_basis, pubchem_cid FROM contaminant_aliases"
    ).fetchall()


def _meta(key: str) -> str:
    return contaminants._connection().execute(
        "SELECT value FROM metadata WHERE key=?", [key]
    ).fetchone()[0]


def test_held_file_and_pin_are_the_verified_bytes():
    assert hashlib.sha256(_HELD.read_bytes()).hexdigest() == _HELD_SHA256
    assert json.loads(_HELD.read_text())["schema"] == (
        "dissolve.contaminant-cas.local.v1"
    )
    assert hashlib.sha256(_STRUCTURES.read_bytes()).hexdigest() == _STRUCTURES_SHA256
    assert json.loads(_STRUCTURES.read_text())["schema"] == (
        "dissolve.contaminant-structures.local.v1"
    )
    digest = hashlib.sha256(contaminants._ASSET.read_bytes()).hexdigest()
    assert digest == _PIN
    assert contaminants._ASSET_SHA256 == _PIN
    assert _meta("structure_local_count") == "8"
    assert _meta("structure_unavailable_count") == "26"
    assert _meta("structure_held_sha256") == _STRUCTURES_SHA256


def test_thirty_four_keys_and_workbook_counts_do_not_grow():
    con = contaminants._connection()
    assert con.execute("SELECT COUNT(*) FROM contaminants").fetchone()[0] == 34
    families = dict(con.execute(
        "SELECT family, COUNT(*) FROM contaminants GROUP BY family"
    ).fetchall())
    assert families == {"PFAS": 26, "Phthalates": 8}
    assert con.execute("SELECT COUNT(*) FROM logd").fetchone()[0] == 1088
    assert con.execute("SELECT COUNT(*) FROM miscibility").fetchone()[0] == 1344
    keys = {
        row[0] for row in con.execute(
            "SELECT DISTINCT contaminant_key FROM contaminant_aliases"
        ).fetchall()
    }
    catalog = {
        row[0] for row in con.execute(
            "SELECT contaminant_key FROM contaminants"
        ).fetchall()
    }
    assert keys == catalog
    assert len(keys) == 34


def test_every_row_has_a_three_token_resolution_basis():
    columns = {
        row[0]
        for row in contaminants._connection().execute(
            "DESCRIBE contaminant_aliases"
        ).fetchall()
    }
    assert columns == {
        "alias", "contaminant_key", "canonical_name", "family",
        "cas_number", "resolution_basis", "pubchem_cid",
        "smiles", "inchikey", "structure_basis",
    }
    assert "cas_status" not in columns
    keys_by_basis: dict[str, set[str]] = defaultdict(set)
    for alias, key, _name, family, cas, basis, cid in _rows():
        assert basis in _BASIS
        assert basis
        keys_by_basis[basis].add(key)
        if basis == "cas_verified":
            assert family == "Phthalates"
            assert cas and cid
            assert (cas, cid) == _OWNER[key]
        elif basis == "catalog_declared":
            assert family == "PFAS"
            assert cas is None
            assert cid is None
        else:
            raise AssertionError("held_snapshot must be empty today")
    assert keys_by_basis["cas_verified"] == set(_OWNER)
    assert len(keys_by_basis["catalog_declared"]) == 26
    assert "held_snapshot" not in keys_by_basis
    assert _meta("cas_absent_count") == "26"
    assert _meta("cas_local_count") == "8"
    assert _meta("held_snapshot_count") == "0"


def test_cas_discipline_on_rows_that_have_one():
    folded: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
    cas_keys: dict[str, set[str]] = defaultdict(set)
    for alias, key, name, _family, cas, basis, cid in _rows():
        folded[_key(alias)].add((key, cas))
        if cas:
            cas_keys[cas].add(key)
            assert basis in {"cas_verified", "held_snapshot"}
            if basis == "cas_verified":
                assert cid
        else:
            assert basis == "catalog_declared"
            assert cid is None
        assert _key(name) or name
    assert all(len(group) == 1 for group in folded.values())
    assert all(len(group) == 1 for group in cas_keys.values())


def test_eight_phthalate_shorts_are_cas_verified_rows():
    con = contaminants._connection()
    for short in _SHORTS:
        row = con.execute(
            "SELECT contaminant_key, family, cas_number, "
            "resolution_basis, pubchem_cid "
            "FROM contaminant_aliases WHERE lower(alias)=lower(?)",
            [short],
        ).fetchone()
        assert row is not None, short
        assert row[1] == "Phthalates"
        assert (row[2], row[4]) == _OWNER[row[0]]
        assert row[3] == "cas_verified"
    for cas, cid in _OWNER.values():
        rows = con.execute(
            "SELECT DISTINCT contaminant_key, pubchem_cid "
            "FROM contaminant_aliases WHERE cas_number=?",
            [cas],
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == cid


def test_traps_are_not_in_the_table():
    aliases = {_key(row[0]) for row in _rows()}
    assert "2-ethylhexyl" not in aliases
    assert "heptafluoropropoxy" not in aliases
    assert "dioctyl phthalate" not in aliases
    assert "genx" not in aliases
    assert "pfoa" not in aliases
    assert not any(row[4] in _PERFLUORO_SOLVENT_CAS for row in _rows())
    assert not any(row[4] == "131-11-3" for row in _rows())
    dehp = [
        row for row in _rows()
        if row[1] == "di-(2-ethylhexyl) phthalate (dehp)"
    ]
    assert {row[4] for row in dehp} == {"117-81-7"}
    assert {row[6] for row in dehp} == {"8343"}
    assert all("dioctyl" not in _key(row[0]) for row in dehp)


def test_parenthetical_pfas_are_catalog_declared_with_null_cas():
    con = contaminants._connection()
    rows = con.execute(
        """
        SELECT contaminant_key, cas_number, resolution_basis, pubchem_cid
        FROM contaminant_aliases
        WHERE contaminant_key LIKE '%heptafluoropropoxy%'
        """
    ).fetchall()
    keys = {row[0] for row in rows}
    assert keys == {
        "2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoic acid",
        "ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate",
    }
    assert all(
        cas is None and basis == "catalog_declared" and cid is None
        for _key, cas, basis, cid in rows
    )


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def test_lookup_is_the_table_and_the_parser_is_gone():
    source = Path(contaminants.__file__).read_text()
    assert "_TRAILING_SHORT_NAME" not in source
    assert "_name_aliases" not in source
    assert r"\(([^)]+)\)\s*$" not in source
    assert "import re" not in source
    assert "FROM contaminant_aliases" in source
    assert not hasattr(contaminants, "_TRAILING_SHORT_NAME")
    assert not hasattr(contaminants, "_name_aliases")
    supported, unsupported, families, _uncovered = contaminants._expand(
        ["DEHP", "117-81-7", "2-ethylhexyl", "heptafluoropropoxy", "PFOA"]
    )
    assert supported == ["di-(2-ethylhexyl) phthalate (DEHP)"]
    assert families == ["Phthalates"]
    assert unsupported == ["2-ethylhexyl", "heptafluoropropoxy", "PFOA"]
    refuse = _data(contaminants.screen_contaminant_leaching(
        "LDPE", ["2-ethylhexyl", "heptafluoropropoxy"],
    ))
    assert refuse["success"] is False
    assert refuse["error_code"] == "unsupported_contaminants"
    assert refuse["unsupported_contaminants"] == [
        "2-ethylhexyl", "heptafluoropropoxy",
    ]
    assert "contaminants" in inspect.signature(
        separation.plan_multistage_separation,
    ).parameters
    assert "contaminant_mode" in inspect.signature(
        separation.plan_multistage_separation,
    ).parameters
    planner = Path(separation.__file__).read_text()
    assert "def plan_multistage_separation_with_contaminants" not in planner


def test_eight_shorts_and_hfpo_catalog_rows_serve_the_basis():
    for short, (cas, cid) in zip(_SHORTS, [_OWNER[k] for k in (
        "butyl benzyl phthalate (bbp)",
        "di-(2-ethylhexyl) phthalate (dehp)",
        "di-isodecyl phthalate(didp)",
        "di-isononyl phthalate (dinp)",
        "di-n-butyl phthalate (dbp)",
        "di-n-hexyl phthalate (dnhp)",
        "di-n-octyl phthalate (dnop)",
        "diethyl phthalate (dep)",
    )]):
        supported, unsupported, _families, _uncovered = contaminants._expand([short])
        assert unsupported == []
        catalog = contaminants._contaminant_catalog(supported)
        assert len(catalog) == 1
        row = catalog[0]
        assert row["resolution_basis"] == "cas_verified"
        assert row["identity_verified"] is True
        assert row["cas_number"] == cas
        assert row["pubchem_cid"] == cid
    names = [
        "2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic acid",
        "2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoic acid",
        "Ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate",
        "ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate",
    ]
    supported, unsupported, _families, _uncovered = contaminants._expand(names)
    assert unsupported == []
    assert supported == [
        "2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic acid",
        "Ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate",
    ]
    catalog = contaminants._contaminant_catalog(supported)
    assert {row["contaminant_key"] for row in catalog} == {
        "2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoic acid",
        "ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate",
    }
    assert all(
        row["resolution_basis"] == "catalog_declared"
        and row["identity_verified"] is False
        and row["cas_number"] is None
        for row in catalog
    )


def test_served_catalog_marks_only_cas_verified_as_identity_verified():
    screens = (
        contaminants.screen_contaminant_leaching,
        contaminants.screen_contaminant_strap_removal,
        contaminants.compare_contaminant_removal_modes,
    )
    for screen in screens:
        pfas = _data(screen("LDPE", ["PFAS"]))
        catalog = pfas["contaminant_catalog"]
        assert len(catalog) == 26
        assert all(
            row["resolution_basis"] == "catalog_declared"
            and row["identity_verified"] is not True
            and row["cas_number"] is None
            for row in catalog
        )
        dehp = _data(screen("LDPE", ["DEHP"]))
        assert len(dehp["contaminant_catalog"]) == 1
        row = dehp["contaminant_catalog"][0]
        assert row["resolution_basis"] == "cas_verified"
        assert row["identity_verified"] is True
        assert row["cas_number"] == "117-81-7"
        assert row["pubchem_cid"] == "8343"
        assert "smiles" not in row
        assert "inchikey" not in row


def test_eight_phthalates_have_local_structure_and_unique_inchikeys():
    con = contaminants._connection()
    rows = con.execute(
        """
        SELECT DISTINCT contaminant_key, smiles, inchikey, structure_basis
        FROM contaminant_aliases WHERE family = 'Phthalates'
        """
    ).fetchall()
    assert len(rows) == 8
    keys = {row[0] for row in rows}
    inchikeys = {row[2] for row in rows}
    assert keys == set(_OWNER)
    assert len(inchikeys) == 8
    assert all(row[1] and row[2] and row[3] == "cosmobase_local" for row in rows)
    dehp = next(row for row in rows if "dehp" in row[0])
    dnop = next(row for row in rows if "dnop" in row[0])
    assert dehp[2] != dnop[2]
    assert dehp[2] == "BJQHLKABXJIVAM-PMACEKPBSA-N"
    assert dnop[2] == "MQIUGAXCHLFZKX-UHFFFAOYSA-N"
    dep = contaminants.resolve_structure("DEP")
    assert dep["success"] is True
    assert dep["inchikey"] == "FLKPEMZONWLCSK-UHFFFAOYSA-N"
    assert dep["structure_basis"] == "cosmobase_local"
    from dissolve import cosmo_logp as cl
    assert dep["inchikey"] == cl.DEP_INCHIKEY
    assert contaminants.resolve_structure("DBP")["inchikey"] == cl.DBP_INCHIKEY
    assert contaminants.resolve_structure("BBP")["inchikey"] == cl.BBP_INCHIKEY
    assert contaminants.resolve_structure("DEHP")["inchikey"] == cl.DEHP_INCHIKEY


def test_pfas_structure_is_unavailable_and_does_not_guess_a_smiles():
    pfoa = contaminants.resolve_structure("perfluorooctanoic acid")
    pfna = contaminants.resolve_structure("perfluorononanoic acid")
    assert pfoa["success"] is False
    assert pfoa["error_code"] == "structure_unavailable"
    assert pfoa["smiles"] is None
    assert pfoa["inchikey"] is None
    assert pfoa["structure_basis"] == "structure_unavailable"
    assert pfna["error_code"] == "structure_unavailable"
    con = contaminants._connection()
    n = con.execute(
        "SELECT COUNT(DISTINCT contaminant_key) FROM contaminant_aliases "
        "WHERE family='PFAS' AND smiles IS NULL AND inchikey IS NULL "
        "AND structure_basis='structure_unavailable'"
    ).fetchone()[0]
    assert n == 26
    assert con.execute(
        "SELECT COUNT(*) FROM contaminant_aliases WHERE family='PFAS' "
        "AND (smiles IS NOT NULL OR inchikey IS NOT NULL)"
    ).fetchone()[0] == 0


def test_unknown_name_refuses_without_fuzzy_match():
    typo = contaminants.resolve_structure("diethyl phtalate")
    isomer = contaminants.resolve_structure("diethyl terephthalate")
    dmp = contaminants.resolve_structure("dimethyl phthalate")
    pfoa_short = contaminants.resolve_structure("PFOA")
    missing = contaminants.resolve_structure("octanoic acid")
    for payload in (typo, isomer, dmp, pfoa_short, missing):
        assert payload["success"] is False
        assert payload["error_code"] == "unknown_contaminant"
        assert payload.get("smiles") is None
    banana = contaminants.resolve_structure("banana")
    assert banana["error_code"] == "unknown_contaminant"


def test_structure_resolver_is_not_wired_into_leaching_or_tea():
    src = Path(contaminants.__file__).read_text()
    tea = Path(_ROOT / "src/dissolve/tea.py").read_text()
    sep = Path(separation.__file__).read_text()
    assert "def resolve_structure" in src
    assert "cosmo_logp" not in src
    assert "cosmo_logp" not in tea
    assert "cosmo_logp" not in sep
    assert "resolve_structure" not in tea
    assert "resolve_structure" not in sep
    for fn in (
        contaminants.screen_contaminant_leaching,
        contaminants.screen_contaminant_strap_removal,
        contaminants.compare_contaminant_removal_modes,
        contaminants.evaluate_contaminant_at_feed_state,
    ):
        assert "resolve_structure" not in inspect.getsource(fn)

    dehp = "di-(2-ethylhexyl) phthalate (DEHP)"
    junk = _data(separation.plan_multistage_separation(
        ["LDPE", "PP"], top_k_routes=1, breadth=1, contaminant_mode="banana",
    ))
    assert junk["success"] is False
    assert junk["error_code"] == "invalid_contaminant_mode"
    fail = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", ["PET", "EVOH"], [dehp], solvents=["toluene"],
    )
    passed = contaminants.evaluate_contaminant_at_feed_state(
        "strap", "LDPE", ["EVOH"], [dehp], solvents=["toluene"],
    )
    fail_row = next(
        r for r in fail["candidate_solvents"] if r["solvent"].casefold() == "toluene"
    )
    pass_row = next(
        r for r in passed["candidate_solvents"] if r["solvent"].casefold() == "toluene"
    )
    assert fail["success"] is True
    assert fail_row.get("passes") is False
    assert passed["success"] is True
    assert pass_row.get("passes") is True

