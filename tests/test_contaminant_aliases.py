"""Commit D5: resolution_basis alias table. Regex still the lookup.

Eight phthalates are cas_verified (CAS + CID). Twenty-six PFAS are
catalog_declared with NULL CAS and NULL CID. No BioSTEAM. No registry.
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


_HELD = _ROOT / "src/dissolve/data/contaminant_cas_phthalates.local.v1.json"
_HELD_SHA256 = "eca9233329f8a33612996fd40669d94bdcd8036ed2641e51439bb3e18ee8654c"
_PIN = "866d769b6a140bf289c5036fd5c0d7d2b6f424cb7994e71c76e16a1a1d9a4c5f"
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
    digest = hashlib.sha256(contaminants._ASSET.read_bytes()).hexdigest()
    assert digest == _PIN
    assert contaminants._ASSET_SHA256 == _PIN


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


def test_commit_d5_does_not_delete_the_regex_or_bind_the_planner():
    source = Path(contaminants.__file__).read_text()
    assert "_TRAILING_SHORT_NAME" in source
    assert "def _name_aliases" in source
    assert "FROM contaminant_aliases" not in source
    assert contaminants._TRAILING_SHORT_NAME.pattern == r"\(([^)]+)\)\s*$"
    expanded = contaminants._expand(
        ["DEHP", "2-ethylhexyl", "heptafluoropropoxy", "117-81-7", "PFOA"]
    )
    assert expanded[0] == ["di-(2-ethylhexyl) phthalate (DEHP)"]
    assert expanded[1] == [
        "2-ethylhexyl", "heptafluoropropoxy", "117-81-7", "PFOA",
    ]
    assert "contaminants" not in inspect.signature(
        separation.plan_multistage_separation,
    ).parameters
    assert "contaminant_mode" not in inspect.signature(
        separation.plan_multistage_separation,
    ).parameters
