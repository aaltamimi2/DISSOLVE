"""Commit D: CAS-anchored contaminant_aliases. Regex still present.

Phthalate CAS from Solvent_Data.csv via the normalized / cosmobase name.
26 PFAS CAS are explicit NULL. No BioSTEAM. No registry call.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import contaminants


_HELD = _ROOT / "src/dissolve/data/contaminant_cas_phthalates.local.v1.json"
_HELD_SHA256 = "eca9233329f8a33612996fd40669d94bdcd8036ed2641e51439bb3e18ee8654c"
_PIN = "9f85a554809a547a60aa980879e8b24f9c141ff1959d9114a4d81077ff7d4716"
_SHORTS = ("BBP", "DEHP", "DiDP", "DiNP", "DBP", "DnHP", "DnOP", "DEP")
_OWNER_CAS = {
    "butyl benzyl phthalate (bbp)": "85-68-7",
    "di-(2-ethylhexyl) phthalate (dehp)": "117-81-7",
    "di-isodecyl phthalate(didp)": "26761-40-0",
    "di-isononyl phthalate (dinp)": "28553-12-0",
    "di-n-butyl phthalate (dbp)": "84-74-2",
    "di-n-hexyl phthalate (dnhp)": "84-75-3",
    "di-n-octyl phthalate (dnop)": "117-84-0",
    "diethyl phthalate (dep)": "84-66-2",
}
_PERFLUORO_SOLVENT_CAS = {
    "355-25-9", "335-57-9", "355-42-0", "678-26-2",
    "355-02-2", "116-14-3", "76-05-1",
}


def _key(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _rows() -> list[tuple]:
    return contaminants._connection().execute(
        "SELECT alias, contaminant_key, canonical_name, family, "
        "cas_number, cas_status FROM contaminant_aliases"
    ).fetchall()


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


def test_phthalate_cas_are_local_and_pfas_cas_are_explicit_null():
    phthalate = {}
    pfas_status = set()
    for alias, key, _name, family, cas, status in _rows():
        if family == "Phthalates":
            assert status == "local"
            assert cas
            phthalate[key] = cas
        else:
            assert family == "PFAS"
            assert status == "absent"
            assert cas is None
            pfas_status.add(key)
    assert phthalate == _OWNER_CAS
    assert len(pfas_status) == 26


def test_alias_identity_invariants():
    folded: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
    cas_keys: dict[str, set[str]] = defaultdict(set)
    for alias, key, name, _family, cas, status in _rows():
        folded[_key(alias)].add((key, cas))
        if cas:
            cas_keys[cas].add(key)
            assert status == "local"
        else:
            assert status == "absent"
        assert _key(name) or name
    assert all(len(group) == 1 for group in folded.values())
    assert all(len(group) == 1 for group in cas_keys.values())


def test_eight_phthalate_shorts_and_cas_are_aliases():
    con = contaminants._connection()
    for short in _SHORTS:
        row = con.execute(
            "SELECT contaminant_key, family, cas_number, cas_status "
            "FROM contaminant_aliases WHERE lower(alias)=lower(?)",
            [short],
        ).fetchone()
        assert row is not None, short
        assert row[1] == "Phthalates"
        assert row[2] == _OWNER_CAS[row[0]]
        assert row[3] == "local"
    for cas in _OWNER_CAS.values():
        row = con.execute(
            "SELECT DISTINCT contaminant_key FROM contaminant_aliases "
            "WHERE cas_number=?",
            [cas],
        ).fetchall()
        assert len(row) == 1


def test_traps_are_not_in_the_table():
    aliases = {_key(row[0]) for row in _rows()}
    assert "2-ethylhexyl" not in aliases
    assert "heptafluoropropoxy" not in aliases
    assert "dioctyl phthalate" not in aliases
    assert "genx" not in aliases
    assert not any(row[4] in _PERFLUORO_SOLVENT_CAS for row in _rows())
    dehp = [
        row for row in _rows()
        if row[1] == "di-(2-ethylhexyl) phthalate (dehp)"
    ]
    assert {row[4] for row in dehp} == {"117-81-7"}
    assert all("dioctyl" not in _key(row[0]) for row in dehp)


def test_parenthetical_pfas_are_catalog_rows_with_absent_cas():
    con = contaminants._connection()
    rows = con.execute(
        """
        SELECT contaminant_key, cas_number, cas_status, COUNT(*)
        FROM contaminant_aliases
        WHERE contaminant_key LIKE '%heptafluoropropoxy%'
        GROUP BY 1, 2, 3
        """
    ).fetchall()
    keys = {row[0] for row in rows}
    assert keys == {
        "2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoic acid",
        "ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate",
    }
    assert all(cas is None and status == "absent" for _key, cas, status, _n in rows)


def test_commit_d_does_not_delete_the_regex():
    source = Path(contaminants.__file__).read_text()
    assert "_TRAILING_SHORT_NAME" in source
    assert "def _name_aliases" in source
    assert "FROM contaminant_aliases" not in source
    assert contaminants._TRAILING_SHORT_NAME.pattern == r"\(([^)]+)\)\s*$"
    expanded = contaminants._expand(["DEHP", "2-ethylhexyl", "heptafluoropropoxy"])
    assert expanded[0] == ["di-(2-ethylhexyl) phthalate (DEHP)"]
    assert expanded[1] == ["2-ethylhexyl", "heptafluoropropoxy"]
