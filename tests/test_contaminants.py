"""Contaminants tests."""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import math
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import pytest
from rich.console import Console

from dissolve import contaminants, separation, tea, tea_ranking
from dissolve import contaminants as C
from dissolve import cosmo_logp as cl
from dissolve import polymer_cosmo as pc
from dissolve.cli import (
    CliApp,
    _format_contaminant_default,
    _parse_contaminant_slash,
)
from dissolve.contracts import parse_tool_result
from dissolve.cosmo_logp import COSMOBASE_PARAMETERISATION, Atom
from dissolve.session import bind_tool_session, new_session


@pytest.fixture(autouse=True)
def _live_tea_works(monkeypatch, tmp_path):
    """TEA answers only when live TEA works; these tests stand in a working engine (tests of the check itself
    override this) and never see this checkout's own live environment (.venv-tea, vendor/plastics)."""
    monkeypatch.setattr(tea, "_live_tea_blocker", lambda: None)
    monkeypatch.setattr(tea, "_REPO_TEA_PYTHON", tmp_path / "no-venv-tea" / "python")
    monkeypatch.setattr(tea.tea_polymer_parameters, "VENDORED_PLASTICS", tmp_path / "no-vendored-plastics")

# --- from test_contaminant_aliases.py: Commit F: table lookup + served identity. No query-time parser.
_ROOT = Path(__file__).resolve().parents[1]


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


# --- from test_contaminant_cli.py: v3 §8 commit 1: /contaminant CLI skin, not a solvents bind.
def _app(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    app = CliApp(
        session_id="contaminant-cli",
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
        **kwargs,
    )
    return app, buf


def test_parse_and_format_skin():
    assert _parse_contaminant_slash([]) is None
    assert _parse_contaminant_slash(["off"]) == {"mode": "off"}
    assert _parse_contaminant_slash(["leaching"]) == {"mode": "leaching"}
    assert _parse_contaminant_slash(["strap"]) == {"mode": "strap"}
    assert _parse_contaminant_slash(["swing"]) == {"mode": "strap"}
    assert _parse_contaminant_slash(["compare"]) == {"compare": True}
    try:
        _parse_contaminant_slash(["bind"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "usage: /contaminant" in str(error)
    assert _format_contaminant_default(None, origin="built-in") == (
        "contaminant_mode=off  (built-in)"
    )
    assert _format_contaminant_default(
        {"mode": "strap"}, origin="session",
    ) == "contaminant_mode=strap  (session)"


def test_session_set_and_clear(tmp_path, monkeypatch):
    app, _buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant leaching") is False
    assert app.session.get("contaminant_mode") == {"mode": "leaching"}
    assert app.handle_command("/contaminant swing") is False
    assert app.session.get("contaminant_mode") == {"mode": "strap"}
    assert app.handle_command("/clear") is False
    assert "contaminant_mode" not in app.session
    assert "handle_command" not in inspect.getsource(app.ask)


def test_bare_non_tty_prints_status_and_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant") is False
    assert "contaminant_mode" not in app.session
    assert "contaminant_mode=off" in buf.getvalue()


def test_bare_picker_sets_strap(tmp_path, monkeypatch):
    from dissolve.cli import _contaminant_picker_options

    options, selected = _contaminant_picker_options("off")
    assert selected == 0
    assert [key for key, _label in options] == ["off", "leaching", "strap"]
    app, _buf = _app(tmp_path, monkeypatch)
    app._handle_contaminant_command([], picker_fn=lambda **_k: "strap")
    assert app.session.get("contaminant_mode") == {"mode": "strap"}
    app._handle_contaminant_command([], picker_fn=lambda **_k: None)
    assert app.session.get("contaminant_mode") == {"mode": "strap"}


@pytest.mark.parametrize(
    ('value', 'value_2'),
    [
        pytest.param('prior contaminant screen', '/contaminant compare', id='compare_without_prior_screen_prints_usage'),
        pytest.param('usage: /contaminant', '/contaminant solvents', id='bad_token_does_not_write'),
        pytest.param('invalid_smiles', '/contaminant logp --smiles not_a_smiles', id='logp_invalid_smiles_refuses_without_writing_mode'),
    ],
)
def test_compare_without_prior_screen_prints_usage_cases(tmp_path, monkeypatch, value, value_2):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command(value_2) is False
    assert "contaminant_mode" not in app.session
    assert value in buf.getvalue()


def test_compare_does_not_persist_a_mode(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.session["last_contaminant"] = {
        "target_polymer": "LDPE",
        "contaminants": ["di-(2-ethylhexyl) phthalate (DEHP)"],
        "other_polymers": ["EVOH"],
        "solvents": ["toluene"],
    }
    assert app.handle_command("/contaminant compare") is False
    assert "contaminant_mode" not in app.session
    assert "recommended_mode" in buf.getvalue()


def test_mode_key_does_not_change_planner_or_screens():
    def run(mode: str | None) -> tuple[str, str, str, str]:
        record = new_session()
        if mode is not None:
            record["contaminant_mode"] = {"mode": mode}
        with bind_tool_session(record):
            plan = separation.plan_multistage_separation(["LDPE", "PP"])
            leach = contaminants.screen_contaminant_leaching(
                "LDPE", ["di-(2-ethylhexyl) phthalate (DEHP)"],
                solvents=["toluene"],
            )
            strap = contaminants.screen_contaminant_strap_removal(
                "LDPE", ["di-(2-ethylhexyl) phthalate (DEHP)"],
                other_polymers=["EVOH"], solvents=["toluene"],
            )
            compare = contaminants.compare_contaminant_removal_modes(
                "LDPE", ["di-(2-ethylhexyl) phthalate (DEHP)"],
                other_polymers=["EVOH"], solvents=["toluene"],
            )
        return plan, leach, strap, compare

    unset = run(None)
    assert unset == run("off") == run("leaching") == run("strap")
    assert _data(unset[1])["success"] is True
    assert "contaminants" in inspect.signature(
        separation.plan_multistage_separation,
    ).parameters
    assert "bind_query_solvent_scope" not in inspect.getsource(
        CliApp._handle_contaminant_command,
    )
    assert "bind_query_solvent_scope" not in inspect.getsource(
        CliApp._run_contaminant_compare,
    )


def test_logp_one_shot_parses_smiles_and_does_not_persist_a_mode(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.session["contaminant_mode"] = {"mode": "leaching"}
    assert app.handle_command(
        "/contaminant logp --smiles CCOC(=O)c1ccccc1C(=O)OCC"
    ) is False
    assert app.session.get("contaminant_mode") == {"mode": "leaching"}
    text = buf.getvalue()
    assert "FLKPEMZONWLCSK-UHFFFAOYSA-N" in text
    assert "dft=not_run" in text
    assert "n_rotatable_bonds=" in text
    assert "invalid_smiles" not in text
    assert "table=33" in text
    assert "orca=5/33" in text


def test_logp_solvents_report_routes_and_refuse_a_cousin_substitute(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command(
        "/contaminant logp --smiles CCOC(=O)c1ccccc1C(=O)OCC "
        "--solvents toluene,xylene,water"
    ) is False
    assert "contaminant_mode" not in app.session
    text = buf.getvalue()
    assert "FLKPEMZONWLCSK-UHFFFAOYSA-N" in text
    assert "dft=not_run" in text
    assert "solvent_not_available" in text
    assert "solvent=xylene" in text
    assert "water" in text
    assert "24a" in text
    for line in text.splitlines():
        if "xylene" in line and "delta_logd=" in line:
            raise AssertionError(line)


def test_logp_is_not_a_persistent_mode_token():
    try:
        _parse_contaminant_slash(["logp"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "--smiles" in str(error)
        assert "--file" in str(error)
    try:
        _parse_contaminant_slash(["banana"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "usage: /contaminant" in str(error)
        assert "--smiles" not in str(error)
        assert "--file" not in str(error)
        assert "--job" not in str(error)
        assert "--solvent-dft" not in str(error)
        assert "--literature-logp" not in str(error)


def test_logp_absolute_is_refused_and_does_not_persist(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    parsed = _parse_contaminant_slash(["logp", "--smiles", "CC", "--absolute"])
    assert parsed["absolute"] is True
    assert parsed["logp"] is True
    assert app.handle_command(
        "/contaminant logp --smiles CCOC(=O)c1ccccc1C(=O)OCC --absolute"
    ) is False
    assert "contaminant_mode" not in app.session
    text = buf.getvalue()
    assert "absolute_logp_refused" in text
    assert "delta_logd=" not in text.lower()
    assert "dft=not_run" in buf.getvalue() or "dft=not_run" in text or "absolute_logp_refused" in text


def test_logp_prints_delta_against_named_water_when_gamma_is_mocked(
    tmp_path, monkeypatch,
):
    from dissolve import cosmo_logp as cl

    monkeypatch.setattr(
        cl, "ln_gamma_infinite_dilution",
        lambda *a, **k: 1.0,
    )
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command(
        "/contaminant logp --smiles CCOC(=O)c1ccccc1C(=O)OCC "
        "--solvents dichloromethane,toluene,xylene"
    ) is False
    assert "contaminant_mode" not in app.session
    text = buf.getvalue()
    assert "FLKPEMZONWLCSK-UHFFFAOYSA-N" in text or cl.DEP_INCHIKEY in text
    assert "reference=water" in text
    assert "delta_logd=" in text
    assert "param=24a" in text
    assert "param=2002" in text
    assert "solvent_not_available" in text
    assert "solvent=xylene" in text
    assert "validated_ok=yes" not in text
    assert "dft=not_run" in text
    assert "solute_sha256=" in text
    assert "ln_gamma_solvent=" in text
    for line in text.splitlines():
        if "xylene" in line and "delta_logd=" in line:
            raise AssertionError(line)


def test_logp_unknown_smiles_is_no_validation_basis(tmp_path, monkeypatch):
    monkeypatch.setenv("DISSOLVE_COSMO_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("DISSOLVE_COSMO_ARTIFACTS_DIR", str(tmp_path / "orca"))
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command(
        "/contaminant logp --smiles CCO --solvents toluene"
    ) is False
    text = buf.getvalue()
    assert "no_validation_basis" in text
    assert "validated_ok=yes" not in text
    assert "solvent_orca_unavailable" in text
    assert "handle=" in text
    assert "estimated_wall_min=" in text
    assert "dft=not_run" in text
    assert "contaminant_mode" not in app.session


def test_logp_file_is_not_a_persistent_mode_and_continues_after_a_refusal(tmp_path, monkeypatch):
    from dissolve import cosmo_logp as cl

    monkeypatch.setattr(cl, "ln_gamma_infinite_dilution", lambda *a, **k: 1.0)
    smiles_file = tmp_path / "batch.smi"
    smiles_file.write_text("\n".join([cl.DEP_SMILES, "not_a_smiles", "CCO"]) + "\n")
    parsed = _parse_contaminant_slash(
        ["logp", "--file", str(smiles_file), "--solvents", "toluene,xylene"]
    )
    assert parsed["logp"] is True
    assert parsed["file"] == str(smiles_file)
    assert parsed["smiles"] is None
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command(
        f"/contaminant logp --file {smiles_file} --solvents toluene,xylene"
    ) is False
    assert "contaminant_mode" not in app.session
    text = buf.getvalue()
    assert "logp batch" in text
    assert "n_ok=" in text
    assert "n_refused=" in text
    assert "invalid_smiles" in text
    assert "line=2" in text
    assert "delta_logd=" in text
    assert "reference=water" in text
    assert "solvent_not_available" in text
    assert "solvent=xylene" in text
    assert "no_validation_basis" in text
    assert "validated_ok=yes" not in text
    assert "dft=not_run" in text
    assert "max_concurrent_dft=4" in text


def test_logp_file_xor_smiles_and_missing_file_are_named(tmp_path, monkeypatch):
    try:
        _parse_contaminant_slash(["logp", "--smiles", "CC", "--file", "x.smi"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "logp" in str(error)
    app, buf = _app(tmp_path, monkeypatch)
    missing = tmp_path / "missing.smi"
    assert app.handle_command(f"/contaminant logp --file {missing} --solvents toluene") is False
    assert "contaminant_mode" not in app.session
    assert "batch_file_unavailable" in buf.getvalue()
    assert "banana" not in buf.getvalue()


def test_logp_job_xor_and_status_query(tmp_path, monkeypatch):
    from dissolve import cosmo_logp as cl

    monkeypatch.setenv("DISSOLVE_COSMO_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("DISSOLVE_COSMO_ARTIFACTS_DIR", str(tmp_path / "orca"))
    monkeypatch.setattr(
        cl, "_SOLUTE_DFT_RUNNER",
        lambda ctx: {"orcacosmo": __import__("pathlib").Path(ctx["artifacts_dir"]) / "skip"},
    )
    try:
        _parse_contaminant_slash(["logp", "--smiles", "CC", "--job", "abc"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "--smiles" in str(error) or "logp" in str(error)
    try:
        _parse_contaminant_slash(["logp", "--smiles", "CC", "--solvent-dft", "1-octanol"])
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "logp" in str(error)
    parsed = _parse_contaminant_slash(["logp", "--job", "missing-handle"])
    assert parsed["job"] == "missing-handle"
    assert parsed["smiles"] is None
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant logp --job missing-handle") is False
    assert "job_not_found" in buf.getvalue()
    assert "banana" not in buf.getvalue()


def test_logp_new_smiles_returns_handle_and_estimate_without_blocking(
    tmp_path, monkeypatch,
):
    import threading
    import time

    from dissolve import cosmo_logp as cl

    monkeypatch.setenv("DISSOLVE_COSMO_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("DISSOLVE_COSMO_ARTIFACTS_DIR", str(tmp_path / "orca"))
    release = threading.Event()

    def runner(ctx):
        release.wait(timeout=2)
        dest = Path(ctx["artifacts_dir"]) / f"{ctx['inchikey']}_cosmo.solute.orcacosmo"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("dummy-surface\n")
        return {"orcacosmo": dest}

    monkeypatch.setattr(cl, "_SOLUTE_DFT_RUNNER", runner)
    app, buf = _app(tmp_path, monkeypatch)
    started = time.monotonic()
    assert app.handle_command("/contaminant logp --smiles CCO") is False
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 0.3
    text = buf.getvalue()
    assert "handle=" in text
    assert "estimated_wall_min=" in text
    assert "n_atoms=" in text
    assert "n_rotatable_bonds=" in text
    assert "dft=not_run" in text
    assert "contaminant_mode" not in app.session
    assert "%pal" not in text
    handle = None
    for token in text.split():
        if token.startswith("handle="):
            handle = token.split("=", 1)[1]
    assert handle
    app2, buf2 = _app(tmp_path, monkeypatch)
    assert app2.handle_command(f"/contaminant logp --job {handle}") is False
    status = buf2.getvalue()
    assert handle in status
    assert "job_not_found" not in status


def test_logp_solvent_dft_returns_handle_without_blocking(tmp_path, monkeypatch):
    import threading
    import time

    from dissolve import cosmo_logp as cl

    monkeypatch.setenv("DISSOLVE_COSMO_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("DISSOLVE_COSMO_ARTIFACTS_DIR", str(tmp_path / "orca"))
    release = threading.Event()

    def runner(ctx):
        release.wait(timeout=2)
        dest = Path(ctx["artifacts_dir"]) / "octanol_cosmo.solute.orcacosmo"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("dummy-octanol-surface\n")
        return {"orcacosmo": dest}

    monkeypatch.setattr(cl, "_SOLVENT_DFT_RUNNER", runner)
    app, buf = _app(tmp_path, monkeypatch)
    started = time.monotonic()
    assert app.handle_command("/contaminant logp --solvent-dft 1-octanol") is False
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 0.3
    text = buf.getvalue()
    assert "handle=" in text
    assert "estimated_wall_min=" in text
    assert "dft=not_run" in text
    assert "contaminant_mode" not in app.session
    assert "%pal" not in text
    parsed = _parse_contaminant_slash(["logp", "--solvent-dft", "octanol"])
    assert parsed["solvent_dft"] == "octanol"
    assert parsed["smiles"] is None


def test_logp_literature_is_a_sanity_check_never_a_gate(tmp_path, monkeypatch):
    from dissolve import cosmo_logp as cl

    parsed = _parse_contaminant_slash(
        ["logp", "--smiles", "CCO", "--literature-logp", "-0.31"],
    )
    assert parsed["smiles"] == "CCO"
    assert parsed["literature_logp"] == -0.31
    try:
        _parse_contaminant_slash(
            ["logp", "--solvent-dft", "octanol", "--literature-logp", "1.2"],
        )
        raise AssertionError("expected ValueError")
    except ValueError as error:
        assert "usage: /contaminant logp" in str(error)

    monkeypatch.setenv("DISSOLVE_COSMO_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("DISSOLVE_COSMO_ARTIFACTS_DIR", str(tmp_path / "orca"))
    monkeypatch.setattr(
        cl, "submit_solute_dft_job",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sanity must not start DFT")),
    )
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command(
        "/contaminant logp --smiles CCO --literature-logp -0.31"
    ) is False
    text = buf.getvalue()
    assert "octanol_orca_unavailable" in text
    assert "role=sanity_check" in text
    assert "gate=no" in text
    assert "validated_ok=no" in text
    assert "handle=" not in text
    assert "contaminant_mode" not in app.session
    assert "dft=not_run" in text

    artifacts = tmp_path / "orca"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "water_cosmo.solute.orcacosmo").write_text("dummy-water\n")
    (artifacts / "dep_cosmo.solute.orcacosmo").write_text("dummy-dep\n")
    (artifacts / "octanol_cosmo.solute.orcacosmo").write_text(
        "route=orca\nparameterisation=24a\n"
    )
    solvents = tmp_path / "cosmo"
    solvents.mkdir(parents=True, exist_ok=True)
    (solvents / "1-octanol_c0.cosmo").write_text("dummy-octanol-cb\n")
    (solvents / "h2o_c0.cosmo").write_text("dummy-water-cb\n")
    monkeypatch.setattr(cl, "DEFAULT_COSMOBASE_SOLVENTS_DIR", solvents)
    monkeypatch.setattr(cl, "ln_gamma_infinite_dilution", lambda *a, **k: 0.0)
    app2, buf2 = _app(tmp_path, monkeypatch)
    assert app2.handle_command(
        f"/contaminant logp --smiles {cl.DEP_SMILES} --literature-logp 0.0"
    ) is False
    ok = buf2.getvalue()
    assert "role=sanity_check" in ok
    assert "gate=no" in ok
    assert "validated_ok=no" in ok
    assert "no_validation_basis" in ok
    assert "handle=" not in ok
    assert "contaminant_mode" not in app2.session
    assert "dft=not_run" in ok


# --- from test_contaminant_leftovers_cl1.py: CL-1: validate contaminant_mode before the empty-contaminants short-circuit.
_DEHP = "di-(2-ethylhexyl) phthalate (DEHP)"


def _plan(**kwargs) -> dict:
    return _data(separation.plan_multistage_separation(
        ["LDPE", "PP"], top_k_routes=1, breadth=1, **kwargs,
    ))


def _candidate(payload: dict, solvent: str) -> dict:
    wanted = solvent.casefold()
    for row in payload.get("candidate_solvents") or []:
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


def test_argument_junk_refuses_even_without_contaminants():
    payload = _plan(contaminant_mode="banana")
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_contaminant_mode"
    assert payload["error_code"] != "unsupported_contaminants"


def test_argument_strap_or_leaching_without_contaminants_is_a_new_code():
    strap = _plan(contaminant_mode="strap")
    leach = _plan(contaminant_mode="leaching")
    swing = _plan(contaminant_mode="swing")
    for payload in (strap, leach, swing):
        assert payload["success"] is False
        assert payload["error_code"] == "contaminant_mode_without_contaminants"
        assert payload["error_code"] != "unsupported_contaminants"
        assert payload["error_code"] != "invalid_contaminant_mode"
        assert payload.get("contaminant_mode_origin") == "argument"


def test_argument_off_without_contaminants_is_ident_to_unset():
    unset = _plan()
    off = _plan(contaminant_mode="off")
    assert unset["success"] is True
    assert off == unset
    assert "wash" not in (off.get("best_sequence") or [])


def test_session_mode_without_contaminants_stays_a_silent_noop():
    def run(mode: str | None) -> dict:
        record = new_session()
        if mode is not None:
            record["contaminant_mode"] = {"mode": mode}
        with bind_tool_session(record):
            return _plan()

    unset = run(None)
    assert unset["success"] is True
    assert unset == run("off") == run("leaching") == run("strap") == run("banana")


def test_helper_distinguishes_argument_from_session():
    mode, requested, origin = separation._planner_contaminant_mode(None, "strap")
    assert origin == "argument"
    assert mode == "without_contaminants"
    assert requested == []
    junk, _requested, junk_origin = separation._planner_contaminant_mode(
        None, "banana",
    )
    assert junk == "invalid"
    assert junk_origin == "argument"
    record = new_session()
    record["contaminant_mode"] = {"mode": "strap"}
    with bind_tool_session(record):
        session_mode, session_requested, session_origin = (
            separation._planner_contaminant_mode(None, None)
        )
    assert session_origin == "session"
    assert session_mode is None
    assert session_requested == []


def test_argument_junk_with_dehp_still_invalid():
    payload = _plan(contaminants=_DEHP, contaminant_mode="banana")
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_contaminant_mode"


def test_accept_test_2_unchanged():
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


# --- from test_contaminant_leftovers_cl2.py: CL-2: omit a wash that cannot run; publish positions_considered with reasons.
_TRIPLE = ["LDPE", "PET", "EVOH"]


_ROUTE = {
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
}


def _empty_screen(*_args, **_kwargs) -> dict:
    return {
        "success": True,
        "recommended_solvents": [],
        "candidate_solvents": [{
            "solvent": "toluene",
            "passes": False,
        }],
    }


def test_empty_recommended_omits_wash_and_publishes_reasons():
    original = contaminants.evaluate_contaminant_at_feed_state
    contaminants.evaluate_contaminant_at_feed_state = _empty_screen
    try:
        payload = separation._embed_leaching_route(
            _ROUTE,
            names=["LDPE", "PP"],
            supported=[_DEHP],
            solvents=["toluene"],
            temperature_max_c=None,
            strict_maximum=False,
            step_c=5.0,
        )
    finally:
        contaminants.evaluate_contaminant_at_feed_state = original
    assert isinstance(payload, dict)
    steps = list(payload.get("steps") or [])
    considered = list(payload.get("positions_considered") or [])
    assert all(item.get("step_kind") != "wash" for item in steps)
    assert "wash" not in (payload.get("sequence") or [])
    assert all(not str(item).startswith("wash") for item in (payload.get("sequence") or []))
    assert len(considered) == 2
    assert all(item.get("reason") for item in considered)
    assert all(item.get("passing_count") == 0 for item in considered)
    assert payload.get("chosen_wash_position") is None
    assert not any(item.get("caveat") for item in steps)


def test_pass_case_still_inserts_wash_at_chosen_position():
    payload = _data(separation.plan_multistage_separation(
        _TRIPLE, contaminants=_DEHP, contaminant_mode="leaching",
        top_k_routes=1, breadth=1,
    ))
    assert payload["success"] is True
    steps = list(payload.get("steps") or [])
    dissolutions = [item for item in steps if item.get("step_kind") == "dissolution"]
    washes = [item for item in steps if item.get("step_kind") == "wash"]
    considered = list(payload.get("positions_considered") or [])
    assert len(washes) == 1
    assert washes[0].get("passes") is True
    assert washes[0].get("path") == "leaching"
    assert len(considered) == len(dissolutions) + 1
    assert payload.get("chosen_wash_position") in {item["index"] for item in considered}
    assert all(item.get("reason") for item in considered)
    assert "wash" in (payload.get("best_sequence") or [])


# --- from test_contaminant_leftovers_cl3.py: CL-3: unspecified_not_a_strap_basis stays; name whether leaching exists.
_PFOA = "Perfluorooctanoic Acid"


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


# --- from test_contaminant_planner_embed.py: v3 §8.3–§8.4: leaching embed plus STRAP stamp on remaining-polymer others.
_PAIR = ["LDPE", "EVOH"]


def _plan_contaminant_planner_embed(feed, **kwargs) -> dict:
    return _data(separation.plan_multistage_separation(
        feed, top_k_routes=1, breadth=1, **kwargs,
    ))


def _candidate_contaminant_planner_embed(payload: dict, solvent: str | None = None) -> dict:
    rows = payload.get("candidate_solvents") or []
    if solvent is None:
        return rows[0] if rows else {}
    wanted = solvent.casefold()
    for row in rows:
        if str(row.get("solvent") or "").casefold() == wanted:
            return row
    return {}


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
            return _plan_contaminant_planner_embed(["LDPE", "PP"])

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
    payload = _plan_contaminant_planner_embed(
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
    fail_row = _candidate_contaminant_planner_embed(fail, "toluene")
    pass_row = _candidate_contaminant_planner_embed(passed, "toluene")
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
    payload = _plan_contaminant_planner_embed(
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
    fail_row = _candidate_contaminant_planner_embed(fail, "toluene")
    pass_row = _candidate_contaminant_planner_embed(passed, "toluene")
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
    assert _candidate_contaminant_planner_embed(derived_screen, "toluene").get("passes") is True
    assert _candidate_contaminant_planner_embed(frozen_screen, "toluene").get("passes") is False
    assert _regimes(_candidate_contaminant_planner_embed(derived_screen, "toluene")) == ["rt", "rt"]


def test_leaching_enumerates_positions_and_names_the_objective():
    payload = _plan_contaminant_planner_embed(
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
    payload = _plan_contaminant_planner_embed(
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
        payload = _plan_contaminant_planner_embed(["LDPE", "PP"], contaminants=_DEHP)
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


# --- from test_contaminant_refusals.py: v1 hold-to 3 / v3 §6: distinct absence codes, including unspecified STRAP.
def test_bfr_class_uses_family_code_not_junk_code():
    bfr = _data(C.screen_contaminant_leaching("LDPE", ["BFR"]))
    junk = _data(C.screen_contaminant_leaching("LDPE", ["xyzzy-not-a-contaminant"]))
    assert bfr["success"] is False
    assert bfr["error_code"] == "unsupported_contaminant_family"
    assert bfr["unsupported_families"] == ["BFR"]
    assert bfr["supported_families"] == ["PFAS", "Phthalates"]
    assert junk["error_code"] == "unsupported_contaminants"
    assert junk["error_code"] != bfr["error_code"]


def test_brominated_flame_retardants_alias_is_the_family_code():
    payload = _data(C.screen_contaminant_leaching(
        "LDPE", ["brominated flame retardants"],
    ))
    assert payload["error_code"] == "unsupported_contaminant_family"


def test_all_unknown_solvents_refuse_distinct_code():
    payload = _data(C.screen_contaminant_leaching(
        "LDPE", ["Perfluorooctanoic Acid"],
        solvents=["not-a-real-solvent-xyz"],
    ))
    assert payload["success"] is False
    assert payload["error_code"] == "unknown_contaminant_solvent"
    assert payload["unsupported_solvents"] == ["not-a-real-solvent-xyz"]


def test_mixed_known_and_unknown_solvent_continues():
    payload = _data(C.screen_contaminant_leaching(
        "LDPE", ["Perfluorooctanoic Acid"],
        solvents=["Toluene", "not-a-real-solvent-xyz"],
    ))
    assert payload["success"] is True
    assert payload["unsupported_solvents"] == ["not-a-real-solvent-xyz"]
    assert any(row.get("solvent") == "toluene" for row in payload["candidate_solvents"])


def test_unspecified_only_does_not_pass_strap():
    row = C._miscibility("toluene", "Perfluorooctanoic Acid", "rt")
    assert row is not None
    assert row["temperature_regime"] == "unspecified"
    strap = _data(C.screen_contaminant_strap_removal(
        "LDPE", ["Perfluorooctanoic Acid"],
        other_polymers=["PET"], solvents=["cyclohexanol"],
    ))
    assert strap["success"] is False
    assert strap["error_code"] == "unspecified_not_a_strap_basis"
    leach = _data(C.screen_contaminant_leaching(
        "LDPE", ["Perfluorooctanoic Acid"],
        other_polymers=["PET"], solvents=["toluene"],
    ))
    assert leach["success"] is True


def test_specified_rt_fallback_still_passes_strap():
    dehp = _data(C.screen_contaminant_strap_removal(
        "LDPE", ["di-(2-ethylhexyl) phthalate (DEHP)"],
        other_polymers=["EVOH"], solvents=["toluene"],
    ))
    assert dehp["success"] is True
    row = dehp["candidate_solvents"][0]
    assert row["passes"] is True
    assert row["unspecified_not_a_strap_basis"] is False
    assert [item["miscibility_regime"] for item in row["contaminants"]] == ["rt"]


# --- from test_cosmo_logp.py: ORCA_LOGP_SCOPE.v1 accept tests.
COSMOBASE = (Path.home() / "COSMO-POLYMER-ML/results/oligomers/all-cosmotherm-solvents")


ARTIFACTS = (Path.home() / "cosmo-artifacts")


HAS_COSMOBASE = (COSMOBASE / "diethylphthalate_c0.cosmo").is_file()


HAS_OBABEL = shutil.which("obabel") is not None


# ---------------------------------------------------------------- constants

def test_rt_ln10_is_1_364_kcal_at_298():
    """1 log unit = 1.364 kcal/mol. A sign or unit slip here produces a
    plausible-looking number rather than an error, so it is pinned."""
    assert cl.RT_LN10_KCAL == pytest.approx(1.364, abs=0.002)


def test_level_of_theory_is_the_parameterised_one():
    """openCOSMORS24a is fit to BP86/def2-TZVP(-f)//def2-TZVPD. A different
    level yields a valid sigma-profile the parameterisation never saw, and
    nothing raises -- so the constants are asserted, not merely documented."""
    assert cl.DFT_FUNCTIONAL == "BP86"
    assert cl.DFT_BASIS_OPT == "def2-TZVP(-f)"
    assert cl.DFT_BASIS_SP == "def2-TZVPD"
    assert cl.ORCA_MIN_VERSION >= (6, 0)      # 5.x has no COSMORS keyword


def test_tolerance_is_derived_from_published_accuracy_not_chosen():
    """AAD 0.76 implies sigma ~0.95. At tolerance 1.0 a CORRECT pipeline fails
    the 3-of-4 test about a third of the time; at 1.5 about 7%."""
    sigma = cl.PUBLISHED_LOGP_AAD / math.sqrt(2 / math.pi)
    phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
    p_at_1_0 = 2 * phi(1.0 / sigma) - 1
    p_at_tol = 2 * phi(cl.ACCEPT_TOLERANCE_LOG_UNITS / sigma) - 1
    three_of_four = lambda p: p**4 + 4 * p**3 * (1 - p)
    assert three_of_four(p_at_1_0) < 0.70          # 1.0 would be too tight
    assert three_of_four(p_at_tol) > 0.90          # 1.5 is not
    assert cl.ACCEPT_TOLERANCE_LOG_UNITS == 1.5


def test_anchor_set_contains_a_water_free_pair():
    """Water is in three of four anchors and its sigma-profile is the most
    idiosyncratic in COSMO-RS. Without a water-free pair, a water error
    propagates into every pair and reads as agreement."""
    assert any((a, b) == cl.WATER_FREE_PAIR for a, b, _ in cl.ANCHOR_PAIRS)
    a, b = cl.WATER_FREE_PAIR
    assert "water" not in (a, b)


# ------------------------------------------------------------ pure geometry

def _atoms(*triples):
    return [cl.Atom(e, x, y, z) for e, x, y, z in triples]


def test_detect_length_unit_distinguishes_bohr_from_angstrom():
    """A C-C bond is ~1.5 A and ~2.9 bohr. Reading bohr as angstrom scales
    every coordinate by 1.89 and silently changes the molecule."""
    assert cl.detect_length_unit(_atoms(("C", 0, 0, 0), ("C", 1.54, 0, 0))) == "angstrom"
    assert cl.detect_length_unit(_atoms(("C", 0, 0, 0), ("C", 2.91, 0, 0))) == "bohr"


def test_detect_length_unit_defaults_to_angstrom_without_two_carbons():
    assert cl.detect_length_unit(_atoms(("O", 0, 0, 0), ("H", 0.96, 0, 0))) == "angstrom"


# ------------------------------------------------------------- ORCA text i/o

def test_orca_opt_input_names_the_parameterised_level():
    text = cl.orca_opt_input(_atoms(("H", 0, 0, 0), ("H", 0, 0, 0.74)))
    assert "! OPT BP86 def2-TZVP(-f) TightSCF" in text
    assert text.count("\n*\n") == 1 or text.rstrip().endswith("*")


def test_orca_cosmors_input_uses_the_keyword_that_emits_orcacosmo():
    text = cl.orca_cosmors_input(_atoms(("O", 0, 0, 0)), solvent="Water")
    assert text.startswith("! COSMORS(Water)")


def test_parse_orca_final_energy_takes_the_LAST_value():
    """An optimisation prints one energy per cycle; only the last is converged."""
    out = "FINAL SINGLE POINT ENERGY  -766.900000000000\nFINAL SINGLE POINT ENERGY  -766.923452708450\n"
    assert cl.parse_orca_final_energy(out) == pytest.approx(-766.923452708450)


def test_parse_orca_final_energy_refuses_output_without_one():
    with pytest.raises(cl.CosmoError):
        cl.parse_orca_final_energy("SCF failed to converge\n")


def test_parse_orca_optimised_geometry_takes_the_last_block():
    out = (
        "CARTESIAN COORDINATES (ANGSTROEM)\n----\n"
        "  O 0.0 0.0 0.0\n  H 0.9 0.0 0.0\n\n"
        "CARTESIAN COORDINATES (ANGSTROEM)\n----\n"
        "  O 0.0 0.0 0.4\n  H 0.8 0.0 -0.2\n\n"
    )
    atoms = cl.parse_orca_optimised_geometry(out)
    assert [a.element for a in atoms] == ["O", "H"]
    assert atoms[0].z == pytest.approx(0.4)


def test_orca_converged_reads_the_banner():
    assert cl.orca_converged("THE OPTIMIZATION HAS CONVERGED")
    assert not cl.orca_converged("OPTIMIZATION RUN DONE")


# ------------------------------------------------------ thermodynamic algebra

def test_boltzmann_weights_sum_to_one_and_favour_the_lowest():
    w = cl.boltzmann_weights([0.0, 0.55, 0.73])
    assert sum(w) == pytest.approx(1.0)
    assert w[0] > w[1] > w[2]


def test_boltzmann_weights_reproduce_the_measured_dep_ensemble():
    """DFT relative energies of the six DEP conformers, and the weights
    recorded in DEP_LOGD.stage2.v1.json."""
    w = cl.boltzmann_weights([0.0, 0.55, 0.46, 0.73, 0.55, 0.73])
    assert w[0] == pytest.approx(0.353, abs=0.01)
    assert w[2] == pytest.approx(0.162, abs=0.01)


def test_ensemble_ln_gamma_is_below_every_single_conformer():
    """More accessible states means a lower ensemble gamma. An implementation
    that averaged instead of summing exponentials would land in between."""
    per_conf = [1.0, 1.2, 1.4]
    ens = cl.boltzmann_combine(per_conf, [0.0, 0.3, 0.6])
    assert ens < min(per_conf)


def test_boltzmann_combine_of_one_conformer_is_that_conformer():
    assert cl.boltzmann_combine([2.5], [0.0]) == pytest.approx(2.5)


def test_boltzmann_combine_refuses_mismatched_lengths():
    with pytest.raises(cl.CosmoError):
        cl.boltzmann_combine([1.0, 2.0], [0.0])


@pytest.mark.parametrize(
    ('value_2', 'value_3', 'value_4'),
    [
        pytest.param(11.453, 2.043, 5.31, id='published'),
        pytest.param(25.989, 3.155, 12.11, id='measured_dehp'),
        pytest.param(17.316, 2.512, 8.06, id='measured_bbp'),
        pytest.param(16.039, 2.737, 7.61, id='measured_dbp'),
    ],
)
def test_delta_log_d_reproduces_the_dcm_water_value(value_2, value_3, value_4):
    value = cl.delta_log_d(
        -value_3, value_2,
        volume_a=cl.MOLAR_VOLUMES_CM3["dichloromethane"],
        volume_b=cl.MOLAR_VOLUMES_CM3["water"],
    )
    assert value == pytest.approx(value_4, abs=0.01)


def test_delta_log_d_is_antisymmetric():
    fwd = cl.delta_log_d(-2.0, 11.0, volume_a=64.0, volume_b=18.07)
    rev = cl.delta_log_d(11.0, -2.0, volume_a=18.07, volume_b=64.0)
    assert fwd == pytest.approx(-rev)


def test_a_solvent_independent_shift_cancels_in_a_difference():
    """The measured conformer finding, as arithmetic: the DEP ensemble moved
    ln gamma by -0.97 in EVERY solvent, so delta logD barely moved. This is why
    the single-conformer caveat is immaterial for a partition coefficient."""
    before = cl.delta_log_d(-2.043, 11.453)
    after = cl.delta_log_d(-2.043 - 0.97, 11.453 - 0.97)
    assert after == pytest.approx(before, abs=1e-9)


# ------------------------------------------------------------------ scoring

def test_linear_fit_recovers_an_exact_line():
    fit = cl.linear_fit([0.0, 1.0, 2.0, 3.0], [1.0, 3.0, 5.0, 7.0])
    assert fit.slope == pytest.approx(2.0)
    assert fit.intercept == pytest.approx(1.0)
    assert fit.r_squared == pytest.approx(1.0)


def test_linear_fit_refuses_too_few_points():
    with pytest.raises(cl.CosmoError):
        cl.linear_fit([1.0, 2.0], [1.0, 2.0])


def test_unit_slope_test_separates_the_two_recorded_fits():
    """The recorded result: over all 31 solvents the slope is 0.774 and
    EXCLUDES 1; dropping the one solvent COSMO-RS is independently known to get
    wrong gives 0.928, which INCLUDES 1. A constant reference phase predicts
    slope 1, so this is the test of whether the two are the same quantity."""
    tight = cl.LinearFit(0.928, 0.230, 0.909, 0.257, 30, 0.109 / 1.96)
    loose = cl.LinearFit(0.774, 0.140, 0.848, 0.342, 31, 0.119 / 1.96)
    assert tight.contains_unit_slope()
    assert not loose.contains_unit_slope()


def test_evaluate_anchor_pairs_accepts_the_published_stage1_numbers():
    result = cl.evaluate_anchor_pairs({
        "dichloromethane-water": 5.31, "cyclohexanol-water": 3.79,
        "hexane-water": 3.36, "dichloromethane-methanol": 1.45,
    })
    assert result["accept"]
    assert result["n_passing"] == 4
    assert result["water_free_passes"]


def test_evaluate_anchor_pairs_refuses_when_only_water_pairs_pass():
    """Three of four passing is enough ONLY if the water-free pair is among
    them. This case would pass a naive 3-of-4 count and must not pass here."""
    result = cl.evaluate_anchor_pairs({
        "dichloromethane-water": 5.31, "cyclohexanol-water": 3.79,
        "hexane-water": 3.36, "dichloromethane-methanol": 9.99,
    })
    assert result["n_passing"] == 3
    assert not result["water_free_passes"]
    assert not result["accept"]


def test_evaluate_anchor_pairs_refuses_a_missing_pair():
    result = cl.evaluate_anchor_pairs({"dichloromethane-water": 5.31})
    assert not result["accept"]


def test_dep_stage1_numbers_do_not_pass_the_dbp_accept():
    """Replaying the closed DEP measurement must not satisfy L1."""
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 5.31, "cyclohexanol-water": 3.79,
            "hexane-water": 3.36, "dichloromethane-methanol": 1.45,
        },
        pairs=cl.DBP_ANCHOR_PAIRS,
    )
    assert not result["accept"]


@pytest.mark.parametrize(
    "dcm_water, cyclohexanol_water, hexane_water, dcm_methanol, pairs",
    [
        pytest.param(7.05, 5.17, 4.98, 2.18, "DBP_ANCHOR_PAIRS", id="dbp_numbers_on_dbp_pairs"),
        pytest.param(7.61, 5.88, 5.64, 1.92, "DBP_ANCHOR_PAIRS", id="the_measured_dbp_stage1_numbers"),
        pytest.param(7.18, 5.04, 4.61, 2.14, "BBP_ANCHOR_PAIRS", id="bbp_table_deltas_on_bbp_pairs"),
        pytest.param(8.06, 6.3, 5.94, 1.92, "BBP_ANCHOR_PAIRS", id="the_measured_bbp_stage1_numbers"),
        pytest.param(9.98, 8.13, 8.33, 2.6, "DEHP_ANCHOR_PAIRS", id="dehp_table_deltas_on_dehp_pairs"),
    ],
)
def test_evaluate_anchor_pairs_accepts(dcm_water, cyclohexanol_water, hexane_water, dcm_methanol, pairs):
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": dcm_water,
            "cyclohexanol-water": cyclohexanol_water,
            "hexane-water": hexane_water,
            "dichloromethane-methanol": dcm_methanol,
        },
        pairs=getattr(cl, pairs),
    )
    assert result["accept"]
    assert result["n_passing"] == 4
    assert result["water_free_passes"]


def test_measured_dehp_stage1_numbers_do_not_accept():
    """L3 stage-1: water-free passes; the three water pairs miss by ~2.1.
    This is the recorded fail. Do not weaken 1.5 to make it pass."""
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 12.11, "cyclohexanol-water": 10.20,
            "hexane-water": 10.34, "dichloromethane-methanol": 2.73,
        },
        pairs=cl.DEHP_ANCHOR_PAIRS,
    )
    assert result["water_free_passes"]
    assert result["n_passing"] == 1
    assert result["tolerance"] == 1.5
    assert not result["accept"]


def test_measured_bbp_stage1_numbers_do_not_pass_the_dehp_accept():
    """L2's recorded Δ are not L3's accept surface."""
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 8.06, "cyclohexanol-water": 6.30,
            "hexane-water": 5.94, "dichloromethane-methanol": 1.92,
        },
        pairs=cl.DEHP_ANCHOR_PAIRS,
    )
    assert not result["accept"]


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'value_4'),
    [
        pytest.param(9.98, 8.13, 8.33, 2.6, id='dehp'),
        pytest.param(7.18, 5.04, 4.61, 2.14, id='bbp'),
    ],
)
def test_table_deltas_do_not_pass_the_dep_accept(value, value_2, value_3, value_4):
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": value, "cyclohexanol-water": value_2,
            "hexane-water": value_3, "dichloromethane-methanol": value_4,
        },
    )
    assert not result["accept"]


def test_chloroform_exclusion_is_recorded_with_a_mechanism():
    """The exclusion must be justified by a stated mechanism, not by which
    solvent the current fit happens to dislike."""
    assert "chloroform" in cl.KNOWN_METHOD_FAILURES
    assert "hydrogen-bond" in cl.KNOWN_METHOD_FAILURES["chloroform"]
    assert len(cl.KNOWN_METHOD_FAILURES) == 1


def test_solvent_file_stem_maps_corpus_names_to_cosmobase():
    assert cl.solvent_file_stem("dichloromethane") == "ch2cl2"
    assert cl.solvent_file_stem("Water") == "h2o"
    assert cl.solvent_file_stem("toluene") == "toluene"      # identity fallback


# --------------------------------------------- real data, skipped if absent

@pytest.mark.skipif(not HAS_COSMOBASE, reason="COSMObase .cosmo library not present")
def test_real_cosmo_geometry_comes_out_in_angstrom():
    atoms = cl.parse_cosmo_geometry(COSMOBASE / "diethylphthalate_c0.cosmo")
    carbons = [a for a in atoms if a.element == "C"]
    shortest = min(
        math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
        for i, a in enumerate(carbons) for b in carbons[i + 1:]
    )
    assert 1.3 < shortest < 1.7, "a C-C bond in angstrom"


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_identity_of_the_real_dep_file_binds_to_the_expected_inchikey(tmp_path):
    found = cl.verify_identity(
        COSMOBASE / "diethylphthalate_c0.cosmo", cl.DEP_INCHIKEY, scratch=tmp_path
    )
    assert found == cl.DEP_INCHIKEY


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_identity_check_rejects_a_molecule_that_is_not_the_target(tmp_path):
    """The must-refuse side. A check with no failing input has not been tested."""
    with pytest.raises(cl.CosmoIdentityError):
        cl.verify_identity(
            COSMOBASE / "diethylphthalate_c0.cosmo",
            cl.DEP_ISOMER_DECOYS["diethyl terephthalate"][1],
            scratch=tmp_path,
        )


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_identity_of_the_real_dbp_file_binds_to_the_expected_inchikey(tmp_path):
    found = cl.verify_identity(
        COSMOBASE / "dibutylphthalate_c0.cosmo", cl.DBP_INCHIKEY, scratch=tmp_path
    )
    assert found == cl.DBP_INCHIKEY


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_dbp_identity_refuses_the_terephthalate_decoy(tmp_path):
    """Must-refuse: formula-colliding 1,4-isomer key is not this structure."""
    with pytest.raises(cl.CosmoIdentityError):
        cl.verify_identity(
            COSMOBASE / "dibutylphthalate_c0.cosmo",
            cl.DBP_ISOMER_DECOYS["di-n-butyl terephthalate"][1],
            scratch=tmp_path,
        )


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_dbp_file_is_not_dep(tmp_path):
    with pytest.raises(cl.CosmoIdentityError):
        cl.verify_identity(
            COSMOBASE / "dibutylphthalate_c0.cosmo", cl.DEP_INCHIKEY, scratch=tmp_path
        )


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_identity_of_the_real_bbp_file_binds_to_the_expected_inchikey(tmp_path):
    found = cl.verify_identity(
        COSMOBASE / "butylbenzylphthalate_c0.cosmo", cl.BBP_INCHIKEY, scratch=tmp_path
    )
    assert found == cl.BBP_INCHIKEY


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_bbp_identity_refuses_the_terephthalate_decoy(tmp_path):
    with pytest.raises(cl.CosmoIdentityError):
        cl.verify_identity(
            COSMOBASE / "butylbenzylphthalate_c0.cosmo",
            cl.BBP_ISOMER_DECOYS["butyl benzyl terephthalate"][1],
            scratch=tmp_path,
        )


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_bbp_file_is_not_dbp_or_dep(tmp_path):
    with pytest.raises(cl.CosmoIdentityError):
        cl.verify_identity(
            COSMOBASE / "butylbenzylphthalate_c0.cosmo", cl.DBP_INCHIKEY, scratch=tmp_path
        )
    with pytest.raises(cl.CosmoIdentityError):
        cl.verify_identity(
            COSMOBASE / "butylbenzylphthalate_c0.cosmo", cl.DEP_INCHIKEY, scratch=tmp_path
        )


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_identity_of_the_real_dehp_file_binds_to_the_perceived_stereo_key(tmp_path):
    found = cl.verify_identity(
        COSMOBASE / "di-2-ethylhexylphthalate_c0.cosmo", cl.DEHP_INCHIKEY, scratch=tmp_path
    )
    assert found == cl.DEHP_INCHIKEY


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_dehp_file_refuses_the_nostereo_catalog_key(tmp_path):
    """The local file is a specified stereoisomer, not the racemic catalog key."""
    with pytest.raises(cl.CosmoIdentityError):
        cl.verify_identity(
            COSMOBASE / "di-2-ethylhexylphthalate_c0.cosmo",
            cl.DEHP_INCHIKEY_NOSTEREO,
            scratch=tmp_path,
        )


@pytest.mark.skipif(not (HAS_COSMOBASE and HAS_OBABEL), reason="needs COSMObase and obabel")
def test_dehp_identity_refuses_the_terephthalate_decoy(tmp_path):
    with pytest.raises(cl.CosmoIdentityError):
        cl.verify_identity(
            COSMOBASE / "di-2-ethylhexylphthalate_c0.cosmo",
            cl.DEHP_ISOMER_DECOYS["di-(2-ethylhexyl) terephthalate"][1],
            scratch=tmp_path,
        )


@pytest.mark.skipif(not ARTIFACTS.is_dir(), reason="rescued ORCA artifacts not present")
def test_rescued_orcacosmo_files_are_all_present():
    """The stage-1 and stage-2 records cite these exact files."""
    found = sorted(p.name for p in ARTIFACTS.rglob("*.orcacosmo"))
    assert len(found) >= 12
    assert any("dep_cosmo.solute" in n for n in found)


def test_conformer_generation_produces_distinct_geometries_of_one_molecule():
    pytest.importorskip("rdkit")
    confs = cl.generate_conformers(cl.DEP_SMILES, n_embed=40, seed=12345)
    assert len(confs) >= 2
    assert all(len(atoms) == 30 for atoms, _ in confs)
    energies = [e for _, e in confs]
    assert energies == sorted(energies), "returned lowest-energy first"
    assert energies[0] == pytest.approx(0.0)


def test_dbp_conformer_generation_is_one_molecule_of_42_atoms():
    pytest.importorskip("rdkit")
    confs = cl.generate_conformers(cl.DBP_SMILES, n_embed=20, seed=12345)
    assert len(confs) >= 1
    assert all(len(atoms) == 42 for atoms, _ in confs)
    assert confs[0][1] == pytest.approx(0.0)


def test_bbp_conformer_generation_is_one_molecule_of_43_atoms():
    pytest.importorskip("rdkit")
    confs = cl.generate_conformers(cl.BBP_SMILES, n_embed=20, seed=12345)
    assert len(confs) >= 1
    assert all(len(atoms) == 43 for atoms, _ in confs)
    assert confs[0][1] == pytest.approx(0.0)


def test_dehp_conformer_generation_is_one_molecule_of_66_atoms():
    pytest.importorskip("rdkit")
    confs = cl.generate_conformers(cl.DEHP_SMILES, n_embed=20, seed=12345)
    assert len(confs) >= 1
    assert all(len(atoms) == 66 for atoms, _ in confs)
    assert confs[0][1] == pytest.approx(0.0)


def test_tea_separation_contaminants_do_not_import_cosmo_logp():
    """Hold-to 4: no planner/TEA bind of a computed logD."""
    src = Path(__file__).resolve().parents[1] / "src" / "dissolve"
    for name in ("tea.py", "separation.py", "contaminants.py"):
        text = (src / name).read_text()
        assert "cosmo_logp" not in text


def test_leftover_cl1_banana_still_invalid_and_accept_test_2_unchanged():
    """Ladder SHAs must not reopen leftovers. Same refuse / same accept-test-2 bits."""
    from dissolve import contaminants, separation
    from dissolve.contracts import parse_tool_result

    dehp = "di-(2-ethylhexyl) phthalate (DEHP)"
    junk = parse_tool_result(separation.plan_multistage_separation(
        ["LDPE", "PP"], top_k_routes=1, breadth=1, contaminant_mode="banana",
    ))["data"]
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


def test_ingest_smiles_binds_inchikey_and_does_not_run_dft():
    pytest.importorskip("rdkit")
    dep = cl.ingest_smiles(cl.DEP_SMILES)
    assert dep["success"] is True
    assert dep["inchikey"] == cl.DEP_INCHIKEY
    assert dep["dft_ran"] is False
    assert dep["n_atoms"] == 30
    assert dep["n_rotatable_bonds"] >= 4
    decoy_smiles, decoy_key = cl.DEP_ISOMER_DECOYS["diethyl terephthalate"]
    decoy = cl.ingest_smiles(decoy_smiles)
    assert decoy["success"] is True
    assert decoy["inchikey"] == decoy_key
    assert decoy["inchikey"] != dep["inchikey"]
    refused = cl.ingest_smiles("not_a_smiles")
    assert refused["success"] is False
    assert refused["error_code"] == "invalid_smiles"
    assert refused["dft_ran"] is False
    empty = cl.ingest_smiles("   ")
    assert empty["error_code"] == "invalid_smiles"
    src = Path(cl.__file__).read_text()
    body = src.split("def ingest_smiles", 1)[1].split("def generate_conformers", 1)[0]
    assert "generate_conformers(" not in body
    assert "orca_opt_input" not in body
    assert "subprocess" not in body


def test_table_solvent_set_is_thirty_three_and_orca_route_is_five():
    assert len(cl.TABLE_SOLVENT_KEYS) == 33
    assert len(cl.ORCA_ROUTE_SOLVENTS) == 5
    assert cl.ORCA_ROUTE_SOLVENTS <= cl.TABLE_SOLVENT_KEYS
    assert "xylene" in cl.TABLE_SOLVENT_KEYS
    assert "o-xylene" in cl.TABLE_SOLVENT_KEYS
    assert "xylene" not in cl.ORCA_ROUTE_SOLVENTS
    coverage = cl.solvent_route_coverage(solvents_dir=Path("/tmp/dissolve-missing-cosmobase"))
    assert coverage["n_table"] == 33
    assert coverage["n_orca"] == 5
    assert coverage["n_orca"] != 9
    assert coverage["dft_ran"] is False
    assert coverage["orca_parameterisation"] == "24a"
    assert coverage["cosmobase_parameterisation"] == "2002"


def test_xylene_does_not_silently_become_o_xylene(tmp_path):
    (tmp_path / "1,2-dimethylbenzene_c0.cosmo").write_text("pin\n")
    (tmp_path / "toluene_c0.cosmo").write_text("pin\n")
    xylene = cl.resolve_solvent("xylene", solvents_dir=tmp_path)
    isomer = cl.resolve_solvent("o-xylene", solvents_dir=tmp_path)
    toluene = cl.resolve_solvent("toluene", solvents_dir=tmp_path)
    typo = cl.resolve_solvent("toluenee", solvents_dir=tmp_path)
    assert xylene["success"] is False
    assert xylene["error_code"] == "solvent_not_available"
    assert xylene["dft_ran"] is False
    assert isomer["success"] is True
    assert isomer["solvent_key"] == "o-xylene"
    assert isomer["cosmobase"]["parameterisation"] == "2002"
    assert isomer["cosmobase"]["engine"] == "turbomole"
    assert isomer["orca"]["available"] is False
    assert toluene["success"] is True
    assert toluene["orca"]["available"] is False
    assert typo["error_code"] == "solvent_not_available"
    water = cl.resolve_solvent("water", solvents_dir=tmp_path)
    assert water["orca"]["available"] is True
    assert water["orca"]["parameterisation"] == "24a"
    assert water["cosmobase"]["available"] is False


@pytest.mark.skipif(not HAS_COSMOBASE, reason="COSMObase .cosmo library not present")
def test_real_cosmobase_covers_thirty_two_table_solvents_and_not_xylene():
    coverage = cl.solvent_route_coverage(solvents_dir=COSMOBASE)
    assert coverage["n_table"] == 33
    assert coverage["n_orca"] == 5
    assert coverage["n_cosmobase"] == 32
    assert coverage["unavailable"] == ["xylene"]
    dcm = cl.resolve_solvent("DCM", solvents_dir=COSMOBASE)
    assert dcm["success"] is True
    assert dcm["solvent_key"] == "dichloromethane"
    assert dcm["orca"]["available"] is True
    assert dcm["cosmobase"]["available"] is True
    assert dcm["cosmobase"]["parameterisation"] == "2002"
    assert dcm["cosmobase"]["parameterisation"] != "24a"


def test_no_dft_override_knobs_on_the_parameterised_level():
    """Hold-to 2: a different level still yields a σ-profile and nothing raises."""
    text = Path(cl.__file__).read_text()
    assert "dftfunc" not in text.lower()
    assert "dftbas" not in text.lower()
    assert cl.DFT_FUNCTIONAL == "BP86"
    assert cl.DFT_BASIS_OPT == "def2-TZVP(-f)"
    assert cl.DFT_BASIS_SP == "def2-TZVPD"


def test_dependency_errors_name_what_is_missing_and_how_to_get_it(monkeypatch):
    """A missing 17 GB licensed program should say so, not fail obscurely."""
    monkeypatch.setenv(cl.COSMO_PYTHON_ENV, "/no/such/dissolve-cosmo-python")
    try:
        import opencosmorspy  # noqa: F401
    except ImportError:
        with pytest.raises(cl.CosmoDependencyError) as exc:
            cl.ln_gamma_infinite_dilution("a.orcacosmo", "b.orcacosmo")
        assert "opencosmorspy" in str(exc.value)
        assert "github" in str(exc.value).lower()


# ------------------------------------------------------------- P-3 Δ vs named reference

def _touch(path: Path, body: str = "dummy-surface\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _dummy_p3_dirs(root: Path) -> tuple[Path, Path]:
    """Isolated ORCA + COSMObase trees. No live DFT."""
    artifacts = root / "orca"
    solvents = root / "cosmo"
    for stem in (
        "dep", "dbp", "bbp", "dehp",
        "water", "dcm", "methanol", "hexane", "cyclohexanol",
    ):
        _touch(artifacts / f"{stem}_cosmo.solute.orcacosmo")
    for name in cl.SOLUTE_COSMOBASE_FILE_BY_INCHIKEY.values():
        _touch(solvents / name)
    for name in (
        "h2o_c0.cosmo", "ch2cl2_c0.cosmo", "methanol_c0.cosmo",
        "hexane_c0.cosmo", "cyclohexanol_c0.cosmo", "toluene_c0.cosmo",
        "1-octanol_c0.cosmo",
    ):
        _touch(solvents / name)
    return artifacts, solvents


def _solvent_key_from_path(path) -> str:
    name = Path(path).name.lower()
    mapping = (
        ("cyclohexanol", "cyclohexanol"),
        ("dichloromethane", "dichloromethane"),
        ("ch2cl2", "dichloromethane"),
        ("dcm", "dichloromethane"),
        ("methanol", "methanol"),
        ("hexane", "hexane"),
        ("water", "water"),
        ("h2o", "water"),
        ("toluene", "toluene"),
        ("1-octanol", "1-octanol"),
        ("octanol", "1-octanol"),
    )
    for needle, key in mapping:
        if needle in name:
            return key
    return name.split("_")[0]


def _ln_map_from_pairs(pairs) -> dict[str, float]:
    ln = {"water": 0.0}
    remaining = [(a, b, ours) for a, b, ours in pairs]
    for _ in range(len(remaining) + 2):
        nxt = []
        for a, b, ours in remaining:
            va = cl.MOLAR_VOLUMES_CM3.get(a)
            vb = cl.MOLAR_VOLUMES_CM3.get(b)
            vol_term = math.log10(vb / va) if va is not None and vb is not None else 0.0
            mole = ours - vol_term
            delta_ln = mole * math.log(10)
            if b in ln and a not in ln:
                ln[a] = ln[b] - delta_ln
            elif a in ln and b not in ln:
                ln[b] = ln[a] + delta_ln
            elif a not in ln or b not in ln:
                nxt.append((a, b, ours))
        remaining = nxt
        if not remaining:
            break
    assert not remaining, remaining
    return ln


def _ln_gamma_from_map(ln_map, default: float = 0.0):
    def ln_gamma(solute, solvent, **kwargs):
        return ln_map.get(_solvent_key_from_path(solvent), default)
    return ln_gamma


def test_absolute_logp_is_refused_and_does_not_run_dft(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    payload = cl.compute_delta_logd(
        cl.DEP_SMILES, ["toluene"], absolute=True,
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    assert payload["success"] is False
    assert payload["error_code"] == "absolute_logp_refused"
    assert payload["dft_ran"] is False
    none = cl.compute_delta_logd(
        cl.DEP_SMILES, ["toluene"], reference="none",
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    assert none["error_code"] == "absolute_logp_refused"
    assert none["dft_ran"] is False


def test_single_solvent_vs_default_water_is_not_an_absolute(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    payload = cl.compute_delta_logd(
        cl.DEP_SMILES, ["toluene"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 1.0,
    )
    assert payload["success"] is True
    assert payload["reference"] == "water"
    assert payload["error_code"] != "absolute_logp_refused"
    row = payload["results"][0]
    assert row["success"] is True
    assert row["solvent_key"] == "toluene"
    assert row["reference"] == "water"
    assert row["route"] == cl.COSMOBASE_ROUTE
    assert row["parameterisation"] == "2002"
    assert row["parameterisation"] != "24a"
    assert row["engine"] == "turbomole"
    assert row["dft_ran"] is False
    assert "delta_logd" in row
    assert row["n_conformers"] == 1
    assert row["temperature"] == cl.STANDARD_T
    assert len(row["solute_sha256"]) == 64
    assert "ln_gamma_solvent" in row
    assert "volume_correction" in row


def test_dichloromethane_water_is_24a_and_toluene_is_not_labelled_24a(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    payload = cl.compute_delta_logd(
        cl.DEP_SMILES, ["dichloromethane", "toluene"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    by_key = {row["solvent_key"]: row for row in payload["results"]}
    dcm = by_key["dichloromethane"]
    toluene = by_key["toluene"]
    assert dcm["success"] is True
    assert dcm["route"] == cl.ORCA_ROUTE
    assert dcm["parameterisation"] == "24a"
    assert dcm["engine"] is None
    assert cl.DFT_FUNCTIONAL in dcm["level_of_theory"]
    assert toluene["success"] is True
    assert toluene["route"] == cl.COSMOBASE_ROUTE
    assert toluene["parameterisation"] == "2002"
    assert toluene["engine"] == "turbomole"
    assert "24a" not in toluene["level_of_theory"]
    assert dcm["dft_ran"] is False
    assert toluene["dft_ran"] is False


def test_xylene_refuses_without_becoming_o_xylene(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    payload = cl.compute_delta_logd(
        cl.DEP_SMILES, ["xylene"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    row = payload["results"][0]
    assert row["success"] is False
    assert row["error_code"] == "solvent_not_available"
    assert row["query"] == "xylene"
    assert row["solvent_key"] == "xylene"
    assert row["dft_ran"] is False


def test_unknown_smiles_is_no_validation_basis_and_does_not_start_dft(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    payload = cl.compute_delta_logd(
        "CCO", ["toluene"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    assert payload["validation_status"] == cl.VALIDATION_NO_BASIS
    assert payload["validated_ok"] is False
    row = payload["results"][0]
    assert row["error_code"] == "solute_cosmo_unavailable"
    assert row["dft_ran"] is False
    assert payload["dft_ran"] is False


def test_dehp_is_computed_unvalidated_and_tolerance_stays_1_5(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    payload = cl.compute_delta_logd(
        cl.DEHP_SMILES, ["dichloromethane"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    assert payload["inchikey"] == cl.DEHP_INCHIKEY
    assert payload["validation_status"] == cl.VALIDATION_COMPUTED_UNVALIDATED
    assert payload["validated_ok"] is False
    assert cl.ACCEPT_TOLERANCE_LOG_UNITS == 1.5
    row = payload["results"][0]
    assert row["success"] is True
    assert row["dft_ran"] is False


def test_dep_matching_anchors_is_validated_and_visually_distinct(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    ln_gamma = _ln_gamma_from_map(_ln_map_from_pairs(cl.ANCHOR_PAIRS))
    payload = cl.compute_delta_logd(
        cl.DEP_SMILES, ["dichloromethane"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=ln_gamma,
    )
    assert payload["validation_status"] == cl.VALIDATION_VALIDATED
    assert payload["validated_ok"] is True
    assert payload["validation_status"] != cl.VALIDATION_COMPUTED_UNVALIDATED
    assert payload["validation_status"] != cl.VALIDATION_NO_BASIS


def test_one_octanol_is_labelled_2002_and_does_not_start_dft(tmp_path, monkeypatch):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)

    def boom(*_a, **_k):
        raise AssertionError("DFT must not start in P-3")

    monkeypatch.setattr(cl.subprocess, "run", boom)
    payload = cl.compute_delta_logd(
        cl.DEP_SMILES, ["1-octanol"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.5,
    )
    row = payload["results"][0]
    assert row["success"] is True
    assert row["route"] == cl.COSMOBASE_ROUTE
    assert row["parameterisation"] == "2002"
    assert row["engine"] == "turbomole"
    assert row["dft_ran"] is False
    body = Path(cl.__file__).read_text().split("def compute_delta_logd", 1)[1]
    assert "subprocess" not in body
    assert "generate_conformers(" not in body


def test_compute_delta_logd_does_not_write_the_logd_table(tmp_path):
    pytest.importorskip("rdkit")
    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    cl.compute_delta_logd(
        cl.DEP_SMILES, ["toluene", "xylene"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    assert before.startswith("866d769b")


def test_batch_one_failure_does_not_abort_and_names_the_skip(tmp_path, monkeypatch):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    smiles_file = tmp_path / "batch.smi"
    smiles_file.write_text(
        "\n".join([cl.DEP_SMILES, "", "not_a_smiles", "CCO", cl.DEHP_SMILES]) + "\n"
    )

    def boom(*_a, **_k):
        raise AssertionError("DFT must not start in P-4")

    monkeypatch.setattr(cl.subprocess, "run", boom)
    payload = cl.compute_delta_logd_batch(
        smiles_file,
        ["toluene", "xylene"],
        solvents_dir=solvents,
        artifacts_dir=artifacts,
        ln_gamma=lambda *a, **k: 1.0,
    )
    assert payload["success"] is True
    assert payload["error_code"] is None
    assert payload["n_lines"] == 5
    assert payload["n_skipped"] == 1
    assert payload["n_refused"] == 1
    assert payload["n_ok"] == 3
    assert payload["dft_ran"] is False
    assert payload["max_concurrent_dft"] == 4
    assert cl.MAX_CONCURRENT_DFT == 4
    skipped = payload["skipped"][0]
    assert skipped["line"] == 2
    assert skipped["reason"] == "empty_line"
    refused = payload["refused"][0]
    assert refused["line"] == 3
    assert refused["error_code"] == "invalid_smiles"
    by_line = {row["line"]: row for row in payload["results"]}
    assert by_line[1]["success"] is True
    assert by_line[1]["inchikey"] == cl.DEP_INCHIKEY
    toluene = next(r for r in by_line[1]["results"] if r.get("solvent_key") == "toluene")
    assert toluene["success"] is True
    assert toluene["parameterisation"] == "2002"
    assert toluene["parameterisation"] != "24a"
    xylene = next(r for r in by_line[1]["results"] if r.get("query") == "xylene")
    assert xylene["error_code"] == "solvent_not_available"
    assert by_line[4]["success"] is True
    assert by_line[4]["validation_status"] == cl.VALIDATION_NO_BASIS
    assert by_line[4]["validated_ok"] is False
    assert by_line[5]["success"] is True
    assert by_line[5]["validation_status"] == cl.VALIDATION_COMPUTED_UNVALIDATED
    assert by_line[5]["validated_ok"] is False
    assert cl.ACCEPT_TOLERANCE_LOG_UNITS == 1.5
    body = Path(cl.__file__).read_text().split("def compute_delta_logd_batch", 1)[1]
    assert "subprocess" not in body
    assert "generate_conformers(" not in body
    assert "field_origin" not in payload


def test_batch_exception_on_one_line_does_not_abort_later_lines(tmp_path, monkeypatch):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    smiles_file = tmp_path / "batch.smi"
    smiles_file.write_text(cl.DEP_SMILES + "\nCCO\n")
    seen = []
    real = cl.compute_delta_logd

    def wrapped(smiles, *args, **kwargs):
        seen.append(smiles)
        if len(seen) == 1:
            raise RuntimeError("injected failure")
        return real(smiles, *args, **kwargs)

    monkeypatch.setattr(cl, "compute_delta_logd", wrapped)
    payload = cl.compute_delta_logd_batch(
        smiles_file,
        ["toluene"],
        solvents_dir=solvents,
        artifacts_dir=artifacts,
        ln_gamma=lambda *a, **k: 0.0,
    )
    assert payload["success"] is True
    assert payload["n_refused"] == 1
    assert payload["n_ok"] == 1
    assert payload["refused"][0]["error_code"] == "batch_line_failed"
    assert payload["refused"][0]["line"] == 1
    assert payload["results"][1]["success"] is True
    assert payload["dft_ran"] is False
    assert seen == [cl.DEP_SMILES, "CCO"]


def test_batch_missing_file_is_named_and_does_not_run_dft(tmp_path, monkeypatch):
    monkeypatch.setattr(cl.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("DFT")))
    payload = cl.compute_delta_logd_batch(tmp_path / "missing.smi", ["toluene"])
    assert payload["success"] is False
    assert payload["error_code"] == "batch_file_unavailable"
    assert payload["dft_ran"] is False
    assert payload["results"] == []
    assert payload["n_ok"] == 0


def test_batch_absolute_refuses_each_line_without_aborting(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    smiles_file = tmp_path / "batch.smi"
    smiles_file.write_text(cl.DEP_SMILES + "\nCCO\n")
    payload = cl.compute_delta_logd_batch(
        smiles_file,
        ["toluene"],
        absolute=True,
        solvents_dir=solvents,
        artifacts_dir=artifacts,
        ln_gamma=lambda *a, **k: 0.0,
    )
    assert payload["success"] is True
    assert payload["n_refused"] == 2
    assert payload["n_ok"] == 0
    assert {row["error_code"] for row in payload["refused"]} == {"absolute_logp_refused"}
    assert payload["dft_ran"] is False


def test_batch_does_not_write_the_logd_table(tmp_path):
    pytest.importorskip("rdkit")
    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    smiles_file = tmp_path / "batch.smi"
    smiles_file.write_text(cl.DEP_SMILES + "\n")
    cl.compute_delta_logd_batch(
        smiles_file, ["toluene"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    assert before.startswith("866d769b")


def test_leaching_tabulated_logd_carries_field_origin_and_does_not_write_logd():
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result

    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    payload = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", ["diethyl phthalate (DEP)"], solvents=["toluene"],
    ))["data"]
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    row = next(item for item in payload["candidate_solvents"] if item["solvent"] == "toluene")
    contaminant = row["contaminants"][0]
    assert contaminant["logd"] is not None
    assert contaminant["field_origin"] == cl.FIELD_ORIGIN_TABULATED
    assert contaminant["field_origin"] != cl.FIELD_ORIGIN_COMPUTED
    assert contaminant["tabulated_logd"] == contaminant["logd"]
    assert contaminant["computed_delta_logd"] is None
    src = Path(__file__).resolve().parents[1] / "src" / "dissolve"
    assert "cosmo_logp" not in (src / "tea.py").read_text()
    assert "cosmo_logp" not in (src / "separation.py").read_text()
    assert "cosmo_logp" not in (src / "contaminants.py").read_text()
    assert "INSERT" not in (src / "contaminants.py").read_text()


def test_leaching_computed_overlay_is_labelled_computed_and_cannot_wear_tabulated():
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result

    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    overlay = [{
        "solvent_key": "toluene",
        "delta_logd": 9.99,
        "reference": "water",
        "field_origin": cl.FIELD_ORIGIN_TABULATED,
        "inchikey": cl.DEP_INCHIKEY,
    }]
    payload = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", ["diethyl phthalate (DEP)"], solvents=["toluene"],
        computed_deltas=overlay,
    ))["data"]
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    row = next(item for item in payload["candidate_solvents"] if item["solvent"] == "toluene")
    contaminant = row["contaminants"][0]
    assert contaminant["field_origin"] == cl.FIELD_ORIGIN_TABULATED
    assert contaminant["logd"] == contaminant["tabulated_logd"]
    assert contaminant["logd"] != pytest.approx(9.99)
    assert contaminant["computed_delta_logd"] == pytest.approx(9.99)
    assert contaminant["computed_field_origin"] == cl.FIELD_ORIGIN_COMPUTED
    assert contaminant["computed_field_origin"] != cl.FIELD_ORIGIN_TABULATED
    assert contaminant["computed_reference"] == "water"


def test_leaching_uses_computed_delta_when_table_logd_is_missing():
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result

    payload = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", ["diethyl phthalate (DEP)"], solvents=["xylene"],
        computed_deltas=[{
            "solvent_key": "xylene",
            "query": "xylene",
            "delta_logd": 1.25,
            "reference": "water",
            "field_origin": cl.FIELD_ORIGIN_COMPUTED,
            "inchikey": cl.DEP_INCHIKEY,
        }],
    ))["data"]
    row = next(item for item in payload["candidate_solvents"] if item["solvent"] == "xylene")
    contaminant = row["contaminants"][0]
    assert contaminant["tabulated_logd"] is None
    assert contaminant["logd"] == pytest.approx(1.25)
    assert contaminant["field_origin"] == cl.FIELD_ORIGIN_COMPUTED
    assert contaminant["computed_delta_logd"] == pytest.approx(1.25)
    assert contaminant["computed_field_origin"] == cl.FIELD_ORIGIN_COMPUTED
    assert row["contaminant_logd_pass"] is True
    assert any("field_origin=computed" in warning for warning in payload["warnings"])
    assert "o-xylene" not in str(payload).split("xylene")[0]


def test_leaching_overlay_does_not_inform_a_different_contaminant():
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result

    payload = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE",
        ["diethyl phthalate (DEP)", "di-n-butyl phthalate (DBP)"],
        solvents=["xylene"],
        computed_deltas=[{
            "solvent_key": "xylene",
            "delta_logd": 1.25,
            "reference": "water",
            "field_origin": cl.FIELD_ORIGIN_COMPUTED,
            "inchikey": cl.DEP_INCHIKEY,
        }],
    ))["data"]
    row = next(item for item in payload["candidate_solvents"] if item["solvent"] == "xylene")
    by_name = {item["contaminant"]: item for item in row["contaminants"]}
    dep = by_name["diethyl phthalate (DEP)"]
    dbp = by_name["di-n-butyl phthalate (DBP)"]
    assert dep["tabulated_logd"] is None
    assert dep["logd"] == pytest.approx(1.25)
    assert dep["field_origin"] == cl.FIELD_ORIGIN_COMPUTED
    assert dbp["tabulated_logd"] is None
    assert dbp["logd"] is None
    assert dbp["field_origin"] is None
    assert dbp["computed_delta_logd"] is None
    assert "o-xylene" not in str(payload).split("xylene")[0]


def test_leaching_family_expansion_does_not_share_one_overlay():
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result

    payload = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", "phthalates", solvents=["xylene"],
        computed_deltas=[{
            "solvent_key": "xylene",
            "delta_logd": 1.25,
            "field_origin": cl.FIELD_ORIGIN_COMPUTED,
            "inchikey": cl.DEP_INCHIKEY,
        }],
    ))["data"]
    row = next(item for item in payload["candidate_solvents"] if item["solvent"] == "xylene")
    by_name = {item["contaminant"]: item for item in row["contaminants"]}
    assert len(by_name) > 1
    assert by_name["diethyl phthalate (DEP)"]["logd"] == pytest.approx(1.25)
    assert by_name["diethyl phthalate (DEP)"]["field_origin"] == cl.FIELD_ORIGIN_COMPUTED
    others = [
        item for name, item in by_name.items() if name != "diethyl phthalate (DEP)"
    ]
    assert others
    assert all(item["logd"] is None for item in others)
    assert all(item.get("computed_delta_logd") is None for item in others)


def test_leaching_unstamped_overlay_and_bare_map_do_not_inform():
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result

    unstamped = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", ["diethyl phthalate (DEP)"], solvents=["xylene"],
        computed_deltas=[{
            "solvent_key": "xylene",
            "delta_logd": 1.25,
            "inchikey": cl.DEP_INCHIKEY,
        }],
    ))["data"]
    row = next(item for item in unstamped["candidate_solvents"] if item["solvent"] == "xylene")
    contaminant = row["contaminants"][0]
    assert contaminant["logd"] is None
    assert contaminant["field_origin"] is None

    bare = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", ["diethyl phthalate (DEP)"], solvents=["xylene"],
        computed_deltas={"xylene": 1.25},
    ))["data"]
    row = next(item for item in bare["candidate_solvents"] if item["solvent"] == "xylene")
    contaminant = row["contaminants"][0]
    assert contaminant["logd"] is None
    assert contaminant["field_origin"] is None


def test_leaching_tabulated_stamp_does_not_fill_a_missing_table_cell():
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result

    payload = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", ["diethyl phthalate (DEP)"], solvents=["xylene"],
        computed_deltas=[{
            "solvent_key": "xylene",
            "delta_logd": 1.25,
            "field_origin": cl.FIELD_ORIGIN_TABULATED,
            "inchikey": cl.DEP_INCHIKEY,
        }],
    ))["data"]
    row = next(item for item in payload["candidate_solvents"] if item["solvent"] == "xylene")
    contaminant = row["contaminants"][0]
    assert contaminant["tabulated_logd"] is None
    assert contaminant["logd"] is None
    assert contaminant["field_origin"] is None
    assert contaminant["computed_delta_logd"] == pytest.approx(1.25)
    assert contaminant["computed_field_origin"] == cl.FIELD_ORIGIN_COMPUTED


def test_leaching_overlay_without_identity_does_not_inform():
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result

    payload = parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", ["diethyl phthalate (DEP)"], solvents=["xylene"],
        computed_deltas=[{
            "solvent_key": "xylene",
            "delta_logd": 1.25,
            "field_origin": cl.FIELD_ORIGIN_COMPUTED,
        }],
    ))["data"]
    row = next(item for item in payload["candidate_solvents"] if item["solvent"] == "xylene")
    contaminant = row["contaminants"][0]
    assert contaminant["logd"] is None
    assert contaminant["field_origin"] is None
    assert contaminant["computed_delta_logd"] is None


# ------------------------------------------------------------- P-4b engine bridge

def _p4b_dummy_compute(tmp_path, monkeypatch, **env):
    pytest.importorskip("rdkit")
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    return cl.compute_delta_logd(
        cl.DEP_SMILES, ["dichloromethane"],
        solvents_dir=solvents, artifacts_dir=artifacts,
    )


def test_p4b_missing_interpreter_refuses_cosmo_rs_and_does_not_serve_2002(tmp_path, monkeypatch):
    payload = _p4b_dummy_compute(
        tmp_path, monkeypatch, **{cl.COSMO_PYTHON_ENV: "/no/such/dissolve-cosmo-python"},
    )
    row = payload["results"][0]
    assert row["success"] is False
    assert row["error_code"] == "cosmo_rs_unavailable"
    assert "opencosmorspy" in row["error"]
    assert row.get("delta_logd") is None
    assert "delta_logd" not in row or row["delta_logd"] is None
    assert row.get("parameterisation") != "2002"
    assert row.get("route") != cl.COSMOBASE_ROUTE
    assert payload["dft_ran"] is False
    assert row["dft_ran"] is False


def test_p4b_interpreter_without_opencosmorspy_refuses_and_does_not_recurse(tmp_path, monkeypatch):
    payload = _p4b_dummy_compute(
        tmp_path, monkeypatch, **{cl.COSMO_PYTHON_ENV: sys.executable},
    )
    row = payload["results"][0]
    assert row["success"] is False
    assert row["error_code"] == "cosmo_rs_unavailable"
    assert "opencosmorspy" in row["error"]
    assert row.get("delta_logd") is None
    assert row.get("parameterisation") != "2002"
    assert payload["dft_ran"] is False


def test_p4b_unparseable_stdout_and_timeout_are_named_refuses(tmp_path, monkeypatch):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)

    def fake_run(*_a, **_k):
        return type("R", (), {
            "returncode": 0, "stdout": "not-json {", "stderr": "",
        })()

    monkeypatch.setattr(cl.subprocess, "run", fake_run)
    payload = cl.compute_delta_logd(
        cl.DEP_SMILES, ["dichloromethane"],
        solvents_dir=solvents, artifacts_dir=artifacts,
    )
    row = payload["results"][0]
    assert row["error_code"] == "cosmo_rs_unavailable"
    assert row.get("delta_logd") is None
    assert row.get("parameterisation") != "2002"

    def boom(*_a, **_k):
        raise cl.subprocess.TimeoutExpired(cmd=["python"], timeout=0.01)

    monkeypatch.setattr(cl.subprocess, "run", boom)
    timed = cl.compute_delta_logd(
        cl.DEP_SMILES, ["dichloromethane"],
        solvents_dir=solvents, artifacts_dir=artifacts,
    )
    trow = timed["results"][0]
    assert trow["error_code"] == "cosmo_rs_unavailable"
    assert "timed out" in trow["error"] or "timeout" in trow["error"].lower()
    assert trow.get("delta_logd") is None


def test_p4b_bridge_uses_argv_list_and_does_not_use_a_shell():
    src = Path(cl.__file__).read_text()
    worker = Path(cl.LN_GAMMA_WORKER).read_text()
    assert "shell=True" not in src
    assert "shell=True" not in worker
    assert "shell=False" in src
    assert "subprocess.run(" in src
    # SMILES is JSON payload, not interpolated into a shell string.
    assert "shell=True" not in Path(cl.LN_GAMMA_WORKER).read_text()
    assert "/tmp/" not in worker
    assert "world-writable" not in worker


def test_p4b_main_env_matches_direct_venv_dep_counterfactual():
    """Same molecule, same files, main env after the bridge = direct venv."""
    pytest.importorskip("rdkit")
    if not cl.DEFAULT_COSMO_PYTHON.is_file():
        pytest.skip("isolated COSMO interpreter is not present")
    if cl.orca_solute_cosmo_path(cl.DEP_INCHIKEY) is None:
        pytest.skip("stage-1 DEP .orcacosmo is not present")
    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    payload = cl.compute_delta_logd(
        cl.DEP_SMILES, ["dichloromethane", "hexane"], reference="water",
    )
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    assert before.startswith("866d769b")
    assert payload["success"] is True
    assert payload["validation_status"] == cl.VALIDATION_VALIDATED
    assert payload["validated_ok"] is True
    assert payload["dft_ran"] is False
    by_key = {row["solvent_key"]: row for row in payload["results"]}
    dcm = by_key["dichloromethane"]
    hexane = by_key["hexane"]
    assert dcm["success"] is True
    assert hexane["success"] is True
    assert dcm["delta_logd"] == pytest.approx(5.311936866031463, abs=1e-12)
    assert hexane["delta_logd"] == pytest.approx(3.3643418255389044, abs=1e-12)
    assert round(dcm["delta_logd"], 2) == 5.31
    assert round(hexane["delta_logd"], 2) == 3.36
    for row in (dcm, hexane):
        assert row["route"] == cl.ORCA_ROUTE
        assert row["parameterisation"] == "24a"
        assert row["parameterisation"] != "2002"
        assert row["validation_status"] == cl.VALIDATION_VALIDATED
        assert row["dft_ran"] is False
        assert row["n_conformers"] == 1
        assert row["temperature"] == cl.STANDARD_T
        assert payload.get("inchikey") == cl.DEP_INCHIKEY
        assert len(row["solute_sha256"]) == 64
        assert len(row["solvent_sha256"]) == 64
        assert len(row["reference_sha256"]) == 64
        assert "ln_gamma_solvent" in row
        assert "ln_gamma_reference" in row
        assert "volume_correction" in row
        assert cl.DFT_FUNCTIONAL in row["level_of_theory"]


def _p4c_fake_runner(artifacts: Path):
    def runner(ctx):
        dest = Path(ctx["artifacts_dir"]) / f"{ctx['inchikey']}_cosmo.solute.orcacosmo"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("dummy-surface\n")
        assert "%pal" not in str(ctx)
        assert "1-octanol" not in str(ctx.get("solvents") or [])
        return {"orcacosmo": dest}
    return runner


def test_p4c_estimate_uses_atom_and_rotor_counts():
    estimate = cl.estimate_dft_cost(30, 4)
    assert estimate["n_atoms"] == 30
    assert estimate["n_rotatable_bonds"] == 4
    assert estimate["estimated_wall_min"] == 7.0
    assert estimate["maxcore_mb"] == 1500
    assert estimate["max_concurrent_dft"] == 4
    assert estimate["pal"] is False
    assert cl.DFT_FUNCTIONAL in estimate["level_of_theory"]
    assert "%pal" not in cl.orca_opt_input([cl.Atom("C", 0, 0, 0)])
    assert "%maxcore 1500" in cl.orca_opt_input([cl.Atom("C", 0, 0, 0)])
    assert cl.DFT_FUNCTIONAL in cl.orca_opt_input([cl.Atom("C", 0, 0, 0)])
    assert "%pal" not in cl.orca_cosmors_input([cl.Atom("C", 0, 0, 0)])
    assert "%maxcore 1500" in cl.orca_cosmors_input([cl.Atom("C", 0, 0, 0)])


def test_p4c_new_smiles_job_does_not_block_and_reaches_done_with_held_orca(
    tmp_path, monkeypatch,
):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    jobs = tmp_path / "jobs"
    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    submitted = cl.submit_solute_dft_job(
        "CCO",
        ["dichloromethane"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        ln_gamma=lambda *a, **k: 1.0,
        runner=_p4c_fake_runner(artifacts),
        background=True,
    )
    assert submitted.get("handle")
    assert submitted["status"] in {"queued", "running", "done"}
    assert submitted["dft_ran"] is False
    assert submitted["reused"] is False
    assert submitted["estimate"]["n_atoms"] >= 3
    assert "n_rotatable_bonds" in submitted["estimate"]
    deadline = time.time() + 5
    record = submitted
    while time.time() < deadline:
        record = cl.solute_dft_job_status(submitted["handle"], jobs_dir=jobs)
        if record.get("status") in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert record["status"] == "done"
    assert record["dft_ran"] is True
    assert record["reused"] is False
    assert record["error_code"] is None
    assert record["validation_status"] == cl.VALIDATION_NO_BASIS
    assert record["validated_ok"] is False
    computed = record["result"]
    assert computed["validation_status"] == cl.VALIDATION_NO_BASIS
    row = next(r for r in computed["results"] if r.get("solvent_key") == "dichloromethane")
    assert row["success"] is True
    assert row["route"] == cl.ORCA_ROUTE
    assert row["parameterisation"] == "24a"
    assert row["parameterisation"] != "2002"
    assert row["delta_logd"] is not None
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    assert before.startswith("866d769b")
    reused = cl.submit_solute_dft_job(
        "CCO",
        ["dichloromethane"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        ln_gamma=lambda *a, **k: 1.0,
        runner=lambda ctx: (_ for _ in ()).throw(RuntimeError("reuse must not DFT")),
        background=False,
    )
    assert reused["reused"] is True
    assert reused["dft_ran"] is False
    assert reused["status"] == "done"


def test_p4c_dep_reuse_serves_held_orca_delta_without_new_dft(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    jobs = tmp_path / "jobs"
    record = cl.submit_solute_dft_job(
        cl.DEP_SMILES,
        ["dichloromethane", "hexane"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        ln_gamma=lambda *a, **k: 1.0,
        runner=lambda ctx: (_ for _ in ()).throw(RuntimeError("DEP surface exists")),
        background=False,
    )
    assert record["reused"] is True
    assert record["dft_ran"] is False
    assert record["status"] == "done"
    by_key = {row["solvent_key"]: row for row in record["result"]["results"]}
    assert by_key["dichloromethane"]["success"] is True
    assert by_key["hexane"]["success"] is True
    assert by_key["dichloromethane"]["route"] == cl.ORCA_ROUTE
    assert by_key["dichloromethane"]["parameterisation"] == "24a"


def test_p4c_identity_changed_refuses_delta(tmp_path):
    pytest.importorskip("rdkit")
    if shutil.which("obabel") is None:
        pytest.skip("obabel is required to perceive identity from xyz")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    jobs = tmp_path / "jobs"

    def runner(ctx):
        work = Path(ctx["work_dir"])
        work.mkdir(parents=True, exist_ok=True)
        xyz = work / "decoy.xyz"
        xyz.write_text(
            "6\nmethanol decoy\n"
            "C    0.000000    0.000000    0.000000\n"
            "O    1.400000    0.000000    0.000000\n"
            "H   -0.500000    0.900000    0.000000\n"
            "H   -0.500000   -0.900000    0.000000\n"
            "H    0.500000    0.000000    0.900000\n"
            "H    1.700000    0.400000    0.800000\n"
        )
        dest = Path(ctx["artifacts_dir"]) / f"{ctx['inchikey']}_cosmo.solute.orcacosmo"
        dest.write_text("dummy-surface\n")
        return {"orcacosmo": dest, "opt_xyz": xyz}

    record = cl.submit_solute_dft_job(
        "CCO",
        ["dichloromethane"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        ln_gamma=lambda *a, **k: 1.0,
        runner=runner,
        background=False,
    )
    assert record["status"] == "failed"
    assert record["error_code"] == "identity_changed"
    assert record.get("result") in (None, {})
    dest = artifacts / f"{record['inchikey']}_cosmo.solute.orcacosmo"
    assert not dest.is_file()
    assert cl.orca_solute_cosmo_path(
        record["inchikey"], artifacts_dir=artifacts,
    ) is None

    calls: list[object] = []

    def refuse_reuse(ctx):
        calls.append(ctx)
        raise RuntimeError("failed-identity leftover must not bind the artifact store")

    second = cl.submit_solute_dft_job(
        "CCO",
        ["dichloromethane"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        ln_gamma=lambda *a, **k: 1.0,
        runner=refuse_reuse,
        background=False,
    )
    assert calls, "second submit must not reuse a leftover surface"
    assert second.get("reused") is not True
    assert second.get("result") in (None, {})
    if second.get("result"):
        for row in second["result"].get("results") or []:
            assert row.get("delta_logd") is None


def test_p4c_fifth_orca_slot_blocks_on_flock(tmp_path):
    slots = tmp_path / "slots"
    held = []
    for _ in range(4):
        cm = cl.acquire_dft_slot(blocking=False, slots_dir=slots)
        held.append(cm)
        cm.__enter__()
    try:
        try:
            with cl.acquire_dft_slot(blocking=False, slots_dir=slots):
                raise AssertionError("fifth slot must not start")
        except BlockingIOError:
            pass
        blocked = []

        def waiter():
            with cl.acquire_dft_slot(blocking=True, slots_dir=slots):
                blocked.append("acquired")

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        time.sleep(0.1)
        assert blocked == []
        assert thread.is_alive()
        held[0].__exit__(None, None, None)
        held.pop(0)
        thread.join(timeout=2)
        assert blocked == ["acquired"]
    finally:
        for cm in held:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass


def test_p4c_missing_solvent_orca_is_named_and_does_not_fake_24a(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    jobs = tmp_path / "jobs"
    record = cl.submit_solute_dft_job(
        "CCO",
        ["toluene"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        runner=lambda ctx: (_ for _ in ()).throw(RuntimeError("no solvent DFT")),
        background=False,
    )
    assert record["error_code"] == "solvent_orca_unavailable"
    assert record["dft_ran"] is False
    assert record["validation_status"] == cl.VALIDATION_NO_BASIS
    xylene = cl.submit_solute_dft_job(
        "CCO",
        ["xylene"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        runner=lambda ctx: (_ for _ in ()).throw(RuntimeError("no xylene DFT")),
        background=False,
    )
    assert xylene["error_code"] == "solvent_not_available"
    assert "o-xylene" not in str(xylene).lower()
    assert xylene["dft_ran"] is False


def _p4d_fake_runner(artifacts: Path):
    def runner(ctx):
        dest = Path(ctx["artifacts_dir"]) / "octanol_cosmo.solute.orcacosmo"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("dummy-octanol-surface\n")
        assert "%pal" not in str(ctx)
        return {"orcacosmo": dest}
    return runner


def test_p4d_wave0_octanol_job_does_not_block_and_stamps_24a(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    jobs = tmp_path / "jobs"
    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    submitted = cl.submit_solvent_dft_job(
        "octanol",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=_p4d_fake_runner(artifacts),
        background=True,
    )
    assert submitted.get("handle")
    assert submitted["status"] in {"queued", "running", "done"}
    assert submitted["dft_ran"] is False
    assert submitted["reused"] is False
    assert submitted["estimate"]["n_atoms"] >= 9
    assert submitted["estimate"]["pal"] is False
    assert submitted["estimate"]["maxcore_mb"] == 1500
    deadline = time.time() + 5
    record = submitted
    while time.time() < deadline:
        record = cl.solvent_dft_job_status(submitted["handle"], jobs_dir=jobs)
        if record.get("status") in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert record["status"] == "done"
    assert record["dft_ran"] is True
    assert record["reused"] is False
    assert record["error_code"] is None
    assert record["route"] == cl.ORCA_ROUTE
    assert record["parameterisation"] == "24a"
    assert record["parameterisation"] != "2002"
    surface = artifacts / "octanol_cosmo.solute.orcacosmo"
    assert surface.is_file()
    stamped = surface.read_text()
    assert "route=orca" in stamped
    assert "parameterisation=24a" in stamped
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    assert before.startswith("866d769b")
    reused = cl.submit_solvent_dft_job(
        "1-octanol",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=lambda ctx: (_ for _ in ()).throw(RuntimeError("reuse must not DFT")),
        background=False,
    )
    assert reused["reused"] is True
    assert reused["dft_ran"] is False
    assert reused["status"] == "done"
    coverage = cl.solvent_route_coverage(solvents_dir=solvents)
    assert coverage["n_orca"] == 5
    computed = cl.compute_delta_logd(
        cl.DEP_SMILES, ["1-octanol"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.5,
    )
    row = computed["results"][0]
    assert row["success"] is True
    assert row["route"] == cl.ORCA_ROUTE
    assert row["parameterisation"] == "24a"
    assert row["parameterisation"] != "2002"


def test_p4d_identity_changed_refuses_and_does_not_serve_delta(tmp_path):
    pytest.importorskip("rdkit")
    if shutil.which("obabel") is None:
        pytest.skip("obabel is required to perceive identity from xyz")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    jobs = tmp_path / "jobs"

    def runner(ctx):
        work = Path(ctx["work_dir"])
        work.mkdir(parents=True, exist_ok=True)
        xyz = work / "decoy.xyz"
        xyz.write_text(
            "6\nmethanol decoy\n"
            "C    0.000000    0.000000    0.000000\n"
            "O    1.400000    0.000000    0.000000\n"
            "H   -0.500000    0.900000    0.000000\n"
            "H   -0.500000   -0.900000    0.000000\n"
            "H    0.500000    0.000000    0.900000\n"
            "H    1.700000    0.400000    0.800000\n"
        )
        dest = Path(ctx["artifacts_dir"]) / "octanol_cosmo.solute.orcacosmo"
        dest.write_text("dummy-surface\n")
        (Path(ctx["artifacts_dir"]) / "octanol_cosmo.solvent.orcacosmo").write_text(
            "dummy-solvent-surface\n"
        )
        return {"orcacosmo": dest, "opt_xyz": xyz}

    record = cl.submit_solvent_dft_job(
        "1-octanol",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=runner,
        background=False,
    )
    assert record["status"] == "failed"
    assert record["error_code"] == "identity_changed"
    assert record.get("result") in (None, {})
    leftover = artifacts / "octanol_cosmo.solute.orcacosmo"
    assert not leftover.is_file()
    assert not (artifacts / "octanol_cosmo.solvent.orcacosmo").is_file()
    assert cl.orca_solvent_cosmo_path(
        "1-octanol", artifacts_dir=artifacts,
    ) is None

    calls: list[object] = []

    def refuse_reuse(ctx):
        calls.append(ctx)
        raise RuntimeError("failed-identity leftover must not bind the solvent library")

    second = cl.submit_solvent_dft_job(
        "1-octanol",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=refuse_reuse,
        background=False,
    )
    assert calls, "second submit must not reuse a leftover 1-octanol surface"
    assert second.get("reused") is not True
    assert second.get("status") != "done"
    assert second.get("error_code") == "solvent_dft_failed"
    still_2002 = cl.compute_delta_logd(
        cl.DEP_SMILES, ["1-octanol"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.5,
    )
    row = still_2002["results"][0]
    assert row["route"] == cl.COSMOBASE_ROUTE
    assert row["parameterisation"] == "2002"
    assert row["parameterisation"] != "24a"
    sanity = cl.octanol_water_sanity(
        cl.DEP_SMILES, 0.0,
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.5,
    )
    assert sanity["success"] is False
    assert sanity["error_code"] == "octanol_orca_unavailable"
    assert sanity.get("parameterisation") != "24a"
    assert sanity.get("parameterisation") != cl.ORCA_PARAMETERISATION


def test_identity_changed_discards_solvent_lookup_alias_leftover(tmp_path):
    """Lookup-alias leftover after identity_changed must not bind as ORCA 24a."""
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    jobs = tmp_path / "jobs"
    alias = artifacts / "octanol_cosmo.solvent.orcacosmo"

    def plant_alias_then_refuse(ctx):
        Path(ctx["artifacts_dir"]).mkdir(parents=True, exist_ok=True)
        alias.write_text("dummy-lookup-alias\n")
        raise cl.CosmoIdentityError("identity_changed")

    record = cl.submit_solvent_dft_job(
        "1-octanol",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=plant_alias_then_refuse,
        background=False,
    )
    assert record["status"] == "failed"
    assert record["error_code"] == "identity_changed"
    assert not alias.is_file()
    assert not (artifacts / "octanol_cosmo.solute.orcacosmo").is_file()
    assert cl.orca_solvent_cosmo_path("1-octanol", artifacts_dir=artifacts) is None

    calls: list[object] = []

    def never_runner(ctx):
        calls.append(ctx)
        raise RuntimeError("lookup-alias leftover must not bind as 24a reuse")

    second = cl.submit_solvent_dft_job(
        "1-octanol",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=never_runner,
        background=False,
    )
    assert calls, "second submit must not reuse a lookup-alias leftover"
    assert second.get("reused") is not True
    assert second.get("status") != "done"
    assert second.get("error_code") == "solvent_dft_failed"
    still_2002 = cl.compute_delta_logd(
        cl.DEP_SMILES, ["1-octanol"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.5,
    )
    row = still_2002["results"][0]
    assert row["route"] == cl.COSMOBASE_ROUTE
    assert row["parameterisation"] == "2002"
    assert row["parameterisation"] != "24a"
    sanity = cl.octanol_water_sanity(
        cl.DEP_SMILES, 0.0,
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.5,
    )
    assert sanity["success"] is False
    assert sanity["error_code"] == "octanol_orca_unavailable"
    assert sanity.get("parameterisation") != "24a"
    assert sanity.get("route") != cl.ORCA_ROUTE


def test_identity_changed_discards_solute_stem_alias_leftover(tmp_path):
    """Stage-1 stem alias leftover after identity_changed must not bind as reuse."""
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    (artifacts / "dep_cosmo.solute.orcacosmo").unlink()
    jobs = tmp_path / "jobs"
    stem_alias = artifacts / "dep_cosmo.solute.orcacosmo"

    def plant_stem_then_refuse(ctx):
        Path(ctx["artifacts_dir"]).mkdir(parents=True, exist_ok=True)
        stem_alias.write_text("dummy-stem-alias\n")
        raise cl.CosmoIdentityError("identity_changed")

    record = cl.submit_solute_dft_job(
        cl.DEP_SMILES,
        ["dichloromethane"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        ln_gamma=lambda *a, **k: 1.0,
        runner=plant_stem_then_refuse,
        background=False,
    )
    assert record["status"] == "failed"
    assert record["error_code"] == "identity_changed"
    assert not stem_alias.is_file()
    assert not (
        artifacts / f"{cl.DEP_INCHIKEY}_cosmo.solute.orcacosmo"
    ).is_file()
    assert cl.orca_solute_cosmo_path(
        cl.DEP_INCHIKEY, artifacts_dir=artifacts,
    ) is None

    calls: list[object] = []

    def never_runner(ctx):
        calls.append(ctx)
        raise RuntimeError("solute stem-alias leftover must not bind as 24a reuse")

    second = cl.submit_solute_dft_job(
        cl.DEP_SMILES,
        ["dichloromethane"],
        reference="water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        solvents_dir=solvents,
        ln_gamma=lambda *a, **k: 1.0,
        runner=never_runner,
        background=False,
    )
    assert calls, "second submit must not reuse a solute stem-alias leftover"
    assert second.get("reused") is not True
    assert second.get("status") != "done"
    still_2002 = cl.compute_delta_logd(
        cl.DEP_SMILES, ["dichloromethane"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 1.0,
    )
    row = still_2002["results"][0]
    assert row["route"] == cl.COSMOBASE_ROUTE
    assert row["parameterisation"] == "2002"
    assert row["parameterisation"] != "24a"


def test_p4d_wave1_and_xylene_do_not_start_dft(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    jobs = tmp_path / "jobs"
    toluene = cl.submit_solvent_dft_job(
        "toluene",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=lambda ctx: (_ for _ in ()).throw(RuntimeError("wave 1 must not DFT")),
        background=False,
    )
    assert toluene["error_code"] == "solvent_library_wave_not_started"
    assert toluene["dft_ran"] is False
    assert toluene["handle"] is None
    xylene = cl.submit_solvent_dft_job(
        "xylene",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=lambda ctx: (_ for _ in ()).throw(RuntimeError("xylene must not DFT")),
        background=False,
    )
    assert xylene["error_code"] == "solvent_not_available"
    assert "o-xylene" not in str(xylene).lower()
    assert xylene["dft_ran"] is False
    still_2002 = cl.compute_delta_logd(
        cl.DEP_SMILES, ["1-octanol"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.5,
    )
    row = still_2002["results"][0]
    assert row["route"] == cl.COSMOBASE_ROUTE
    assert row["parameterisation"] == "2002"
    assert row["parameterisation"] != "24a"
    body = Path(cl.__file__).read_text().split("def compute_delta_logd", 1)[1]
    assert "subprocess" not in body
    assert "generate_conformers(" not in body
    coverage = cl.solvent_route_coverage(solvents_dir=solvents)
    assert coverage["n_orca"] == 5
    water = cl.submit_solvent_dft_job(
        "water",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=lambda ctx: (_ for _ in ()).throw(RuntimeError("held water must reuse")),
        background=False,
    )
    assert water["reused"] is True
    assert water["dft_ran"] is False


def test_p4e_octanol_water_sanity_is_not_a_gate_and_never_validated(tmp_path, monkeypatch):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    def boom(*_a, **_k):
        raise AssertionError("P-4e must not start DFT")

    monkeypatch.setattr(cl.subprocess, "run", boom)
    missing = cl.octanol_water_sanity(
        cl.DEP_SMILES, 0.0,
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    assert missing["success"] is False
    assert missing["error_code"] == "octanol_orca_unavailable"
    assert missing["parameterisation"] != "24a"
    assert missing["parameterisation"] != cl.ORCA_PARAMETERISATION
    assert missing["is_gate"] is False
    assert missing["role"] == "sanity_check"
    assert missing["validated_ok"] is False
    assert missing["validation_status"] == cl.VALIDATION_NO_BASIS
    assert missing["dft_ran"] is False

    _touch(
        artifacts / "octanol_cosmo.solute.orcacosmo",
        "route=orca\nparameterisation=24a\n",
    )
    zero = cl.octanol_water_sanity(
        cl.DEP_SMILES, 0.0,
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    assert zero["success"] is True
    assert zero["computed_logp"] == 0.0
    assert zero["residual"] == 0.0
    assert zero["route"] == cl.ORCA_ROUTE
    assert zero["parameterisation"] == cl.ORCA_PARAMETERISATION
    assert zero["is_gate"] is False
    assert zero["role"] == "sanity_check"
    assert zero["validated_ok"] is False
    assert zero["validation_status"] == cl.VALIDATION_NO_BASIS
    assert zero["dft_ran"] is False

    far = cl.octanol_water_sanity(
        cl.DEP_SMILES, 12.0,
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
    )
    assert far["success"] is True
    assert far["residual"] == far["computed_logp"] - 12.0
    assert far["validated_ok"] is False
    assert far["is_gate"] is False
    assert far["validation_status"] == cl.VALIDATION_NO_BASIS

    for bad in (float("nan"), float("inf"), "not-a-number"):
        refused = cl.octanol_water_sanity(
            cl.DEP_SMILES, bad,
            solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.0,
        )
        assert refused["success"] is False
        assert refused["error_code"] == "invalid_literature_logp"
        assert refused["validated_ok"] is False
        assert refused["is_gate"] is False

    xylene = cl.resolve_solvent("xylene", solvents_dir=solvents)
    assert xylene["success"] is False
    assert xylene["error_code"] == "solvent_not_available"
    assert "o-xylene" not in str(xylene).lower()

    coverage = cl.solvent_route_coverage(solvents_dir=solvents)
    assert coverage["n_orca"] == 5
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    assert before.startswith("866d769b")
    body = Path(cl.__file__).read_text().split("def compute_delta_logd", 1)[1]
    assert "subprocess" not in body
    assert "generate_conformers(" not in body


# --- from test_polymer_cosmo.py: R-1 polymer COSMO ingest. Never launches ORCA. Not the intercept test.
_SRC = Path(pc.__file__).read_text()


_PE = pc.PE_MCOS


_PVC = pc.PVC_MCOS


_PET = pc.PET_MCOS


def test_duckdb_pin_unmoved():
    digest = hashlib.sha256(contaminants._ASSET.read_bytes()).hexdigest()
    assert digest == contaminants._ASSET_SHA256
    assert digest.startswith("866d769b")


def test_does_not_import_broken_converter_or_orca_runner():
    assert "gaussian_to_turbomole_converter" not in _SRC
    assert "spec_from_file_location" in _SRC
    assert "cosmo_logp.py" in _SRC
    assert "import run_orca_stage" not in _SRC
    assert "submit_solute_dft_job" not in _SRC
    assert "submit_solvent_dft_job" not in _SRC
    assert "gaussian_to_turbomole_converter" not in _SRC
    assert "compare_pe_dodecane_routes" not in _SRC
    assert "write_dft_handoff" not in _SRC
    assert "_discard_unverified" not in _SRC


def test_split_pe_is_one_conformer_not_two():
    recs = pc.split_mcos(_PE)
    assert len(recs) == 1
    rec = recs[0]
    text = _PE.read_text()
    assert text.count(";start;") == 1
    assert text.count(";end;") == 1
    assert rec.n_atoms == 38
    assert rec.nps == 2233
    assert len(rec.w_bits) == 38
    assert rec.atom_mask == rec.w_vector
    w_vals = [pc.split_mcos(p)[0].w_vector for p in sorted(_PE.parent.glob("*.mcos"))]
    assert all(len(w) == 38 for w in w_vals)
    assert all(set(w) <= {"0", "1"} for w in w_vals)
    assert sum(int(ch) for ch in rec.w_vector) != 1


def test_split_pvc_config_9400():
    recs = pc.split_mcos(_PVC)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.n_atoms == 44
    assert rec.nps == 3057
    segs = pc.parse_segment_information(rec.gaussian_body)
    assert len(segs) == 3057
    atoms = pc.parse_coord_rad_gaussian(rec.gaussian_body)
    assert len(atoms) == 44
    assert atoms[0]["znum"] == 6
    assert atoms[0]["symbol"] == "c"


def test_pvc_w_is_atom_mask_not_boltzmann():
    masks = [pc.split_mcos(p)[0].w_vector for p in sorted(_PVC.parent.glob("*.mcos"))]
    assert len(masks) == 27
    assert len(set(masks)) == 1
    assert len(masks[0]) == 44


def test_convert_pvc_keeps_full_surface(tmp_path):
    converted = pc.convert_gaussian_cosmo(_PVC, tmp_path / "pvc.cosmo")
    assert converted.n_atoms == 44
    assert converted.n_segments == 3057
    assert converted.nps == 3057
    assert converted.n_segments != converted.n_atoms
    assert converted.engine == pc.ENGINE_GAUSSIAN_CONVERTED
    assert converted.parameterisation == COSMOBASE_PARAMETERISATION
    assert converted.parameterisation != "24a"
    assert converted.qc_origin == "gaussian_cosmo"
    text = converted.path.read_text()
    coord_rows = []
    in_coord = False
    for ln in text.splitlines():
        if ln.strip() == "$coord_rad":
            in_coord = True
            continue
        if in_coord:
            if ln.strip().startswith("$"):
                break
            parts = ln.split()
            if parts and parts[0].isdigit():
                coord_rows.append(parts)
    assert len(coord_rows) == 44
    assert all(len(r) == 6 for r in coord_rows)
    assert coord_rows[0][4].isalpha()
    assert pc.area_matches_header(converted.header_area, converted.segment_area_sum)
    assert text.index("$coord_rad") < text.index("$coord_car") < text.index(
        "$segment_information"
    )


def test_convert_pet_91_not_91_segments(tmp_path):
    converted = pc.convert_gaussian_cosmo(_PET, tmp_path / "pet.cosmo")
    assert converted.n_atoms == 91
    assert converted.nps == 6155
    assert converted.n_segments == 6155
    assert converted.n_segments != converted.n_atoms


def test_coord_rad_only_file_is_refused():
    rec = pc.split_mcos(_PVC)[0]
    fake = [{"area": 1.0} for _ in range(rec.n_atoms)]
    with pytest.raises(pc.PolymerCosmoError) as exc:
        pc._assert_identity(rec.n_atoms, rec.nps, fake, rec.area)
    assert exc.value.error_code == pc.SURFACE_DISCARDED


def test_measured_oligomer_sizes_are_identity_not_a_job_launch():
    assert pc.DFT_JOB_ORDER[0] == ("pe", 38)
    assert pc.DFT_JOB_ORDER[-1] == ("ps", 104)
    assert [n for _, n in pc.DFT_JOB_ORDER] == [38, 44, 44, 48, 62, 84, 91, 104]


def test_pe_converted_identity_is_n_dodecane_surface(tmp_path):
    converted = pc.convert_gaussian_cosmo(_PE, tmp_path / "pe.cosmo")
    rec = pc.split_mcos(_PE)[0]
    assert converted.n_atoms == 38
    assert rec.n_atoms == 38
    assert converted.n_segments == converted.nps
    assert converted.n_segments != 38
    assert pc.area_matches_header(converted.header_area, converted.segment_area_sum)
    assert pc.PE_DODECANE_INCHIKEY == "SNRUBQQJIBEYMU-UHFFFAOYSA-N"
    assert converted.parameterisation == COSMOBASE_PARAMETERISATION
    assert converted.parameterisation != "24a"
    assert converted.engine == pc.ENGINE_GAUSSIAN_CONVERTED
    text = converted.path.read_text()
    assert "Gaussian COSMO output" not in text
    first_atom = next(
        ln.split()
        for ln in text.splitlines()
        if ln.split() and ln.split()[0] == "1" and len(ln.split()) >= 5
        and ln.split()[4].isalpha()
    )
    assert len(first_atom) == 6


def test_coord_car_not_coord_rad_for_geometry():
    rec = pc.split_mcos(_PE)[0]
    atoms = pc.parse_coord_car_atoms(rec.gaussian_body)
    assert len(atoms) == 38
    assert all(isinstance(a, Atom) for a in atoms)
    symbols = [a.element.upper() for a in atoms]
    assert symbols.count("C") == 12
    assert symbols.count("H") == 26


def test_converted_file_has_coord_car_then_nine_field_segments(tmp_path):
    converted = pc.convert_gaussian_cosmo(_PE, tmp_path / "pe.cosmo")
    text = converted.path.read_text()
    assert text.index("$coord_car") < text.index("$segment_information")
    in_seg = False
    rows = []
    for ln in text.splitlines():
        if ln.strip() == "$segment_information":
            in_seg = True
            continue
        if in_seg:
            if ln.strip().startswith("$"):
                break
            parts = ln.split()
            if parts and parts[0].isdigit():
                rows.append(parts)
    assert len(rows) == converted.nps == 2233
    assert all(len(r) == 9 for r in rows)
    assert converted.n_segments != converted.n_atoms


def test_pvdf_is_2883_segments_not_44():
    src = pc.first_source_file("pvdf")
    rec = pc.split_mcos(src)[0]
    assert rec.n_atoms == 44
    assert rec.nps == 2883
    segs = pc.parse_segment_information(rec.gaussian_body)
    assert len(segs) == 2883
    assert rec.nps != rec.n_atoms


def test_xylene_is_not_rewritten_here():
    assert "xylene" not in _SRC
    assert "o-xylene" not in _SRC
    assert "subprocess" not in _SRC
    assert "generate_conformers(" not in _SRC
    assert "INSERT" not in _SRC
    assert "UPDATE" not in _SRC


# --- from test_polymer_partition.py: R-2 polymer-as-solvent partition and intercept test.
_POLYMER_SRC = Path(pc.__file__).read_text()


def test_contaminants_asset_matches_pin():
    assert hashlib.sha256(contaminants._ASSET.read_bytes()).hexdigest() == contaminants._ASSET_SHA256


def test_wrong_phase_role_is_named_refuse(tmp_path):
    dummy = tmp_path / "x.cosmo"
    dummy.write_text("x")
    with pytest.raises(pc.PolymerCosmoError) as exc:
        pc.compute_log10_p_solvent_over_polymer(
            dummy, dummy, dummy,
            polymer_name="pvc",
            solvent_key="hexane",
            polymer_role="solute",
            ln_gamma=lambda *a, **k: 0.0,
        )
    assert exc.value.error_code == pc.WRONG_PHASE_ROLE


def test_24a_parameterization_is_refused(tmp_path):
    dummy = tmp_path / "x.cosmo"
    dummy.write_text("x")
    with pytest.raises(pc.PolymerCosmoError) as exc:
        pc.compute_log10_p_solvent_over_polymer(
            dummy, dummy, dummy,
            polymer_name="pvc",
            solvent_key="hexane",
            parameterization="openCOSMORS24a",
            ln_gamma=lambda *a, **k: 0.0,
        )
    assert exc.value.error_code == pc.PARAMETERISATION_24A_REFUSED
    assert "24a" not in str(pc.COSMOBASE_PARAMETERISATION)


def test_r1_identity_still_refuses_n_segments_eq_n_atoms():
    rec = pc.split_mcos(pc.PVC_MCOS)[0]
    fake = [{"area": 1.0} for _ in range(rec.n_atoms)]
    with pytest.raises(pc.PolymerCosmoError) as exc:
        pc._assert_identity(rec.n_atoms, rec.nps, fake, rec.area)
    assert exc.value.error_code == pc.SURFACE_DISCARDED


def test_r2_does_not_mix_leftover_helpers():
    assert "submit_solute_dft_job" not in _POLYMER_SRC
    assert "submit_solvent_dft_job" not in _POLYMER_SRC
    assert "import run_orca_stage" not in _POLYMER_SRC
    assert "_discard_unverified" not in _POLYMER_SRC


def test_polymer_cosmo_loads_by_path_in_isolated_interpreter():
    iso = Path(pc.DEFAULT_COSMO_PYTHON)
    if not iso.is_file():
        pytest.skip("isolated COSMO interpreter is not present")
    src = Path(pc.__file__).resolve()
    script = (
        "import importlib.util, sys\n"
        f"p = {str(src)!r}\n"
        "spec = importlib.util.spec_from_file_location('dissolve.polymer_cosmo', p)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['dissolve.polymer_cosmo'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "assert hasattr(mod, 'compute_log10_p_solvent_over_polymer')\n"
        "assert hasattr(mod, 'ln_gamma_infinite_dilution')\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [str(iso), "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "ok" in proc.stdout
    assert "langchain" not in (proc.stderr or "").casefold()


def test_default_compute_converts_mcos_before_ln_gamma(monkeypatch):
    seen: list[Path] = []

    def spy(solute, solvent, **kwargs):
        seen.append(Path(solvent))
        assert "24a" not in str(kwargs.get("parameterization", "")).casefold()
        return 0.0

    monkeypatch.setattr(pc, "_default_ln_gamma", spy)
    solute = Path(pc.DEFAULT_DEP_SOLUTE_COSMO)
    solvent = pc.DEFAULT_SOLVENTS_DIR / "hexane_c0.cosmo"
    if not solute.is_file() or not solvent.is_file():
        pytest.skip("COSMOtherm DEP/hexane files are not present")
    src = pc.first_source_file("pvc")
    assert src.suffix.lower() == ".mcos"
    pc.compute_log10_p_solvent_over_polymer(
        solute, solvent, src,
        polymer_name="pvc",
        solvent_key="hexane",
    )
    assert seen, "default ln_gamma was not called"
    polymer_seen = seen[-1]
    assert polymer_seen.suffix.lower() != ".mcos"
    text = polymer_seen.read_text(errors="replace")
    assert not pc.is_gaussian_cosmo_text(text)


def test_bridged_dep_toluene_ln_gamma_matches_isolated_2002():
    iso = Path(pc.DEFAULT_COSMO_PYTHON)
    dep = pc.DEFAULT_DEP_SOLUTE_COSMO
    tol = pc.DEFAULT_SOLVENTS_DIR / "toluene_c0.cosmo"
    if not iso.is_file() or not dep.is_file() or not tol.is_file():
        pytest.skip("isolated interpreter or DEP/toluene COSMO files are not present")
    token = pc.TURBOMOLE_2002_PARAMETERIZATION
    bridged = pc.ln_gamma_infinite_dilution(
        dep, tol, parameterization=token,
    )
    script = (
        "import importlib.util, sys\n"
        f"p = {str(Path(pc._cl.__file__).resolve())!r}\n"
        "spec = importlib.util.spec_from_file_location('dissolve.cosmo_logp', p)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['dissolve.cosmo_logp'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        f"v = mod.ln_gamma_infinite_dilution({str(dep)!r}, {str(tol)!r}, "
        f"parameterization={token!r})\n"
        "print(repr(v))\n"
    )
    proc = subprocess.run(
        [str(iso), "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    direct = float(proc.stdout.strip().splitlines()[-1])
    assert bridged == pytest.approx(direct, abs=1e-12)
    assert bridged == pytest.approx(0.039, abs=5e-4)
    assert direct == pytest.approx(0.039, abs=5e-4)


# --- from test_campaign_basis.py: campaign_basis.v1 projection: held switches join without a campaign rerun.
_PAIR_CAMPAIGN = Path(
    f"{Path.home()}/dissolve-v12-campaign/"
    "polymer-solvent-tea-lca-20260818/run_definition.json"
)


_SECONDS_PER_PAIR = 6991.363639038995 / 462


def _minimal_v2(**overrides):
    definition = {
        "schema": tea_ranking.CAMPAIGN_DEFINITION_SCHEMA_V2,
        "fixed_fields": {
            "target_plastic_percent": 55.0,
            "processing_capacity": 20_000.0,
            "energy_case": "C1",
            "precipitation_temperature_c": 35.0,
            "solvent_loss_pct": 0.01,
            "feedstock_distance_km": 0.0,
            "dissolution_capacity": 3.0,
            "labor_cost": 120_000.0,
        },
        "setpoint_rule": {
            "dissolution_temperature_c": (
                "lowest stored grid node from 25 through 160 C"
            ),
        },
        "pair_definitions": [
            {"config_sent": {"target_plastic": "LDPE", "solvent": "toluene"}},
            {"config_sent": {"target_plastic": "HDPE", "solvent": "dodecane"}},
        ],
    }
    definition.update(overrides)
    return definition


def test_pair_campaign_projects_and_is_not_incomplete():
    run = json.loads(_PAIR_CAMPAIGN.read_text(encoding="utf-8"))
    projected = tea_ranking.project_campaign_basis_v1(run)
    basis = projected["campaign_basis"]
    assert projected["campaign_basis_projection"] == "campaign_basis.v1"
    assert basis["complete"] is True
    assert basis["n_pairs"] == 462
    roles = basis["field_role"]
    assert roles["target_mass_percent"] == {
        "role": "held", "value": 55.0, "projection": "fixed_fields",
    }
    assert roles["processing_capacity_mt_per_yr"]["value"] == pytest.approx(20_000)
    assert roles["energy_case"]["value"] == "C1"
    assert roles["target_polymer"]["role"] == "varied"
    assert roles["solvent"]["role"] == "varied"
    assert roles["dissolution_temperature_c"]["role"] == "derived"
    assert roles["solvent_price_usd_per_kg"]["role"] == "derived"
    assert roles["burn_leftover_plastic"] == {
        "role": "held",
        "value": False,
        "projection": "worker_production",
    }
    assert "burn_leftover_plastic" not in (run.get("fixed_fields") or {})
    twelve = {public for _internal, public in tea._DESIGN_POINT_PUBLIC_FIELDS}
    assert twelve <= set(roles)


def test_sixty_fifteen_c2_is_exactly_three_held_mismatches():
    run = json.loads(_PAIR_CAMPAIGN.read_text(encoding="utf-8"))
    projected = tea_ranking.project_campaign_basis_v1(run)
    result = tea_ranking.held_field_mismatches(
        projected,
        {
            "target_mass_percent": 60.0,
            "processing_capacity_mt_per_yr": 15_000.0,
            "energy_case": "C2",
        },
        seconds_per_pair=_SECONDS_PER_PAIR,
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in result["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]
    by_field = {row["field"]: row for row in result["mismatches"]}
    assert by_field["target_mass_percent"]["campaign_value"] == pytest.approx(55)
    assert by_field["target_mass_percent"]["requested_value"] == pytest.approx(60)
    assert by_field["processing_capacity_mt_per_yr"]["campaign_value"] == pytest.approx(
        20_000
    )
    assert by_field["energy_case"]["campaign_value"] == "C1"
    assert by_field["energy_case"]["requested_value"] == "C2"
    assert "burn_leftover_plastic" not in fields
    quote = result["live_rerun_quote"]
    assert quote["n_pairs"] == 462
    assert quote["estimated_wall_seconds"] == pytest.approx(6991.363639038995)


def test_burn_true_is_switch_mismatch_not_a_ranking():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {"burn_leftover_plastic": True},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    assert result["mismatches"] == [
        {
            "field": "burn_leftover_plastic",
            "campaign_value": False,
            "requested_value": True,
            "delta": {"campaign": False, "requested": True},
        }
    ]


@pytest.mark.parametrize(
    ('value', 'key', 'value_2', 'value_3', 'value_4'),
    [
        pytest.param('irr', 'irr', 0.15, 0.1, 0.15, id='irr'),
        pytest.param('income_tax', 'income_tax', 0.3, 0.21, 0.3, id='income_tax'),
        pytest.param('operating_days', 'operating_days', 300.0, 350.4, 300.0, id='operating_days'),
        pytest.param('labor_burden', 'labor_burden', 0.5, 0.9, 0.5, id='labor_burden'),
        pytest.param('finance_interest', 'finance_interest', 0.12, 0.08, 0.12, id='finance_interest'),
        pytest.param('finance_years', 'finance_years', 15, 10, 15, id='finance_years'),
        pytest.param('finance_fraction', 'finance_fraction', 0.4, 0.0, 0.4, id='finance_fraction'),
        pytest.param('startup_months', 'startup_months', 6, 3, 6, id='startup_months'),
        pytest.param('startup_FOCfrac', 'startup_FOCfrac', 0.5, 1, 0.5, id='startup_FOCfrac'),
        pytest.param('startup_VOCfrac', 'startup_VOCfrac', 0.5, 0.75, 0.5, id='startup_VOCfrac'),
        pytest.param('startup_salesfrac', 'startup_salesfrac', 0.8, 0.5, 0.8, id='startup_salesfrac'),
        pytest.param('WC_over_FCI', 'WC_over_FCI', 0.1, 0.05, 0.1, id='wc_over_fci'),
        pytest.param('warehouse', 'warehouse', 0.08, 0.04, 0.08, id='warehouse'),
        pytest.param('site_development', 'site_development', 0.18, 0.09, 0.18, id='site_development'),
        pytest.param('additional_piping', 'additional_piping', 0.09, 0.045, 0.09, id='additional_piping'),
        pytest.param('proratable_costs', 'proratable_costs', 0.2, 0.1, 0.2, id='proratable_costs'),
        pytest.param('field_expenses', 'field_expenses', 0.2, 0.1, 0.2, id='field_expenses'),
        pytest.param('construction', 'construction', 0.4, 0.2, 0.4, id='construction'),
        pytest.param('contingency', 'contingency', 0.8, 0.4, 0.8, id='contingency'),
        pytest.param('other_indirect_costs', 'other_indirect_costs', 0.2, 0.1, 0.2, id='other_indirect_costs'),
        pytest.param('property_insurance', 'property_insurance', 0.014, 0.007, 0.014, id='property_insurance'),
        pytest.param('maintenance', 'maintenance', 0.06, 0.03, 0.06, id='maintenance'),
    ],
)
def test_override_is_coefficient_mismatch(value, key, value_2, value_3, value_4):
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {key: value_2},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == value
    assert row["campaign_value"] == pytest.approx(value_3)
    assert row["requested_value"] == pytest.approx(value_4)


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'key', 'value_4'),
    [
        pytest.param('depreciation', 'MACRS7', 'MACRS5', 'depreciation', 'MACRS5', id='depreciation_override_is_coefficient_mismatch'),
        pytest.param('steam_power_depreciation', 'MACRS20', 'MACRS7', 'steam_power_depreciation', 'MACRS7', id='steam_power'),
    ],
)
def test_depreciation_override_is_coefficient_mismatch_2(value, value_2, value_3, key, value_4):
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {key: value_4},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == value
    assert row["campaign_value"] == value_2
    assert row["requested_value"] == value_3


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        pytest.param('depreciation', 'MACRS7', id='default_depreciation'),
        pytest.param('steam_power_depreciation', 'MACRS20', id='default_steam_power_depreciation'),
        pytest.param('lang_factor', None, id='none_lang_factor'),
    ],
)
def test_matching_is_not_a_held_mismatch(key, value):
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {key: value},
    )
    assert result["mismatches"] == []
    assert "error_code" not in result


def test_duration_override_is_coefficient_mismatch():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {"duration": [2025, 2045]},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "duration"
    assert tuple(row["campaign_value"]) == (2025, 2055)
    assert tuple(row["requested_value"]) == (2025, 2045)


def test_matching_default_duration_is_not_a_held_mismatch():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {"duration": [2025, 2055]},
    )
    assert result["mismatches"] == []
    assert "error_code" not in result


def test_construction_schedule_override_is_coefficient_mismatch():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {"construction_schedule": [0.5, 0.5]},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "construction_schedule"
    assert tuple(row["campaign_value"]) == (0.08, 0.60, 0.32)
    assert tuple(row["requested_value"]) == (0.5, 0.5)


def test_matching_default_construction_schedule_is_not_a_held_mismatch():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {"construction_schedule": [0.08, 0.60, 0.32]},
    )
    assert result["mismatches"] == []
    assert "error_code" not in result


def test_lang_factor_override_is_coefficient_mismatch():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {"lang_factor": 3.0},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    assert [row["field"] for row in result["mismatches"]] == ["lang_factor"]
    row = result["mismatches"][0]
    assert row["campaign_value"] is None
    assert row["requested_value"] == pytest.approx(3.0)


def test_polymer_only_is_not_a_held_mismatch():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected, {"target_polymer": "LDPE", "solvent": "Toluene"},
    )
    assert result["mismatches"] == []
    assert "error_code" not in result


def test_matching_held_values_are_not_mismatch():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected,
        {
            "target_mass_percent": 55.0,
            "processing_capacity_mt_per_yr": 20_000.0,
            "energy_case": "C1",
            "burn_leftover_plastic": False,
            "irr": 0.10,
        },
    )
    assert result["mismatches"] == []


def test_held_values_come_from_the_run_definition_not_a_constant():
    definition = _minimal_v2()
    definition["fixed_fields"]["target_plastic_percent"] = 50.0
    projected = tea_ranking.project_campaign_basis_v1(definition)
    result = tea_ranking.held_field_mismatches(
        projected, {"target_mass_percent": 55.0},
    )
    assert result["mismatches"][0]["campaign_value"] == pytest.approx(50)
    assert result["mismatches"][0]["requested_value"] == pytest.approx(55)


def test_missing_v2_pieces_are_incomplete_not_mismatch():
    with pytest.raises(tea_ranking.CampaignBasisIncomplete) as caught:
        tea_ranking.project_campaign_basis_v1({"schema": "other"})
    assert caught.value.error_code == "campaign_basis_incomplete"
    assert set(caught.value.details["missing"]) == {
        "schema", "fixed_fields", "setpoint_rule", "pair_definitions",
    }


def test_wrong_schema_is_incomplete_even_with_the_rest():
    definition = _minimal_v2(
        schema="dissolve.ldpe_feed_quality_campaign_definition.v1",
    )
    with pytest.raises(tea_ranking.CampaignBasisIncomplete) as caught:
        tea_ranking.project_campaign_basis_v1(definition)
    assert "schema" in caught.value.details["missing"]


def test_absence_of_switches_in_fixed_fields_is_not_incomplete():
    definition = _minimal_v2()
    assert "burn_leftover_plastic" not in definition["fixed_fields"]
    projected = tea_ranking.project_campaign_basis_v1(definition)
    assert projected["campaign_basis"]["complete"] is True
    assert projected["campaign_basis"]["field_role"]["sell_leftover_plastic"][
        "value"
    ] is False


def test_two_switch_request_names_both_fields():
    projected = tea_ranking.project_campaign_basis_v1(_minimal_v2())
    result = tea_ranking.held_field_mismatches(
        projected,
        {
            "sell_leftover_plastic": True,
            "precipitation_configuration": "solvent mixing",
        },
    )
    fields = [row["field"] for row in result["mismatches"]]
    assert fields == [
        "sell_leftover_plastic",
        "precipitation_configuration",
    ]
    assert "burn_leftover_plastic" not in fields


def test_declared_switch_in_fixed_fields_is_held_not_stamped():
    definition = _minimal_v2()
    definition["fixed_fields"]["burn_leftover_plastic"] = True
    projected = tea_ranking.project_campaign_basis_v1(definition)
    role = projected["campaign_basis"]["field_role"]["burn_leftover_plastic"]
    assert role == {
        "role": "held",
        "value": True,
        "projection": "fixed_fields",
    }
    result = tea_ranking.held_field_mismatches(
        projected, {"burn_leftover_plastic": False},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    assert result["mismatches"][0]["campaign_value"] is True
    assert result["mismatches"][0]["requested_value"] is False


# --- from test_campaign_consume.py: Bind a registered campaign to lookup without ingesting JSONL into cache.
_SEALED = Path(
    f"{Path.home()}/dissolve-v12-campaign/"
    "polymer-solvent-tea-lca-20260818"
)


_CANONICAL = (
    "ef62efb3a708d782ce58cac3295cc9de71b0a7e8d076f6ca79f1ba5830a29636"
)


_APPEND_LOG = (
    "8b11ee68ce9948418872d892076bf275cb8b0dc1f95e72b0af80bbd2ac1eee3a"
)


_OTHER = "ab" * 32


def _data_campaign_consume(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _write_registry(tmp_path: Path, entries: dict) -> Path:
    path = tmp_path / "campaign_registry.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _sealed_entry() -> dict:
    manifest = _SEALED / "manifest.json"
    return {
        "manifest_path": str(manifest),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "append_log_aliases": [_APPEND_LOG],
    }


def _lookup(monkeypatch, registry_path: Path, **kwargs):
    monkeypatch.setenv(
        tea_ranking.REGISTRY_ENV, str(registry_path),
    )
    return _data_campaign_consume(tea.lookup_admitted_process_records(**kwargs))


def _minimal_definition(**overrides):
    definition = {
        "schema": tea_ranking.CAMPAIGN_DEFINITION_SCHEMA_V2,
        "fixed_fields": {
            "target_plastic_percent": 55.0,
            "processing_capacity": 20_000.0,
            "energy_case": "C1",
            "precipitation_temperature_c": 35.0,
            "solvent_loss_pct": 0.01,
            "feedstock_distance_km": 0.0,
            "dissolution_capacity": 3.0,
            "labor_cost": 120_000.0,
        },
        "setpoint_rule": {
            "dissolution_temperature_c": (
                "lowest stored grid node from 25 through 160 C"
            ),
        },
        "pair_definitions": [
            {
                "config_sent": {
                    "target_plastic": "LDPE",
                    "solvent": "toluene",
                    "dissolution_temperature_c": 95.0,
                },
            },
        ],
        "runtime_engine_versions": {"python": "3.12.0"},
    }
    definition.update(overrides)
    return definition


def _mini_campaign(
    tmp_path: Path,
    *,
    definition=None,
    rows=None,
    complete: bool = True,
    include_results: bool = True,
):
    root = tmp_path / "mini_campaign"
    root.mkdir()
    run_definition = definition if definition is not None else _minimal_definition()
    canonical = tea_ranking.canonical_json_digest(run_definition)
    run_path = root / "run_definition.json"
    run_path.write_text(json.dumps(run_definition), encoding="utf-8")
    if rows is None:
        rows = [
            {
                "campaign_fingerprint": canonical,
                "polymer": "LDPE",
                "solvent_public_identity": "2,4-Pentanedione",
                "pair_id": "p1",
                "config_sent": {"solvent": "toluene"},
            },
        ]
    rows_path = root / "process_rows.jsonl"
    rows_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    if include_results:
        (root / "results.jsonl").write_text(
            json.dumps({
                "campaign_fingerprint": "ff" * 32,
                "should_not": "be_consumed",
            }) + "\n",
            encoding="utf-8",
        )
    manifest = {
        "campaign_fingerprint": canonical,
        "append_log_fingerprint": "cc" * 32,
        "complete": complete,
        "counts": {"attempted": len(rows)},
        "census": {"pair_count": len(run_definition["pair_definitions"])},
        "aggregate_pair_wall_seconds": 10.0 * max(len(rows), 1),
        "artifact_sha256": {
            "run_definition_json": hashlib.sha256(
                run_path.read_bytes()
            ).hexdigest(),
            "process_rows_jsonl": hashlib.sha256(
                rows_path.read_bytes()
            ).hexdigest(),
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    entry = {
        "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "append_log_aliases": [],
    }
    return {
        "root": root,
        "canonical": canonical,
        "entry": entry,
        "run_path": run_path,
        "rows_path": rows_path,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "definition": run_definition,
    }


def test_empty_registry_is_unregistered(monkeypatch, tmp_path):
    monkeypatch.delenv(tea_ranking.REGISTRY_ENV, raising=False)
    data = _data_campaign_consume(tea.lookup_admitted_process_records(
        source="campaign",
        campaign_fingerprint=_OTHER,
    ))
    assert data["success"] is False
    assert data["error_code"] == "campaign_not_registered"
    assert data["n_registered"] == 0
    assert data["supplied"] == _OTHER


def test_missing_fingerprint_is_first(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch, registry, source="campaign", target_polymer="LDPE",
    )
    assert data["success"] is False
    assert data["error_code"] == "missing_campaign_fingerprint"
    assert "n_registered" not in data


def test_append_log_alias_is_mismatch_not_unregistered(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_APPEND_LOG,
    )
    assert data["success"] is False
    assert data["error_code"] == "campaign_fingerprint_mismatch"
    assert data["supplied"] == _APPEND_LOG
    assert data["canonical"] == _CANONICAL
    assert data["note"] == "append-log"


def test_unknown_digest_is_unregistered_when_another_is_registered(
    monkeypatch, tmp_path,
):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch, registry, source="campaign", campaign_fingerprint=_OTHER,
    )
    assert data["error_code"] == "campaign_not_registered"
    assert data["n_registered"] == 1
    assert data["supplied"] == _OTHER


def test_legal_sealed_bind_does_not_ingest_cache(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    before = len(tea._records())
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
    )
    assert data["success"] is True
    assert data["source"] == "campaign"
    assert data["campaign_fingerprint"] == _CANONICAL
    assert data["append_log_fingerprint"] == _APPEND_LOG
    assert data["campaign_basis_projection"] == "campaign_basis.v1"
    assert data["campaign_basis"]["complete"] is True
    assert data["campaign_basis"]["n_pairs"] == 462
    assert data["n_rows_consumed"] == 462
    assert data["ingested_into_admitted_cache"] is False
    assert "records" not in data
    assert len(data["comparison_rows"]) == 462
    assert "engine_envelope" not in data["comparison_rows"][0]
    roles = data["campaign_basis"]["field_role"]
    assert roles["target_mass_percent"]["value"] == pytest.approx(55)
    assert roles["burn_leftover_plastic"]["value"] is False
    assert len(tea._records()) == before == 24
    assert "matching_row_count" not in data


def test_polymer_filter_dumps_matching_rows(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
        target_polymer="LDPE",
    )
    assert data["success"] is True
    assert data["matching_row_count"] == 60
    assert len(data["comparison_rows"]) == 60
    assert {row["polymer"] for row in data["comparison_rows"]} == {"LDPE"}
    assert "records" not in data
    assert len(tea._records()) == 24


def test_sixty_fifteen_c2_is_held_mismatch_not_a_ranking(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
        process_config={
            "target_mass_percent": 60.0,
            "processing_capacity_mt_per_yr": 15_000.0,
        },
        energy_cases=["C2"],
    )
    assert data["success"] is False
    assert data["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in data["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]
    assert data["n_held_mismatches"] == 3
    quote = data["live_rerun_quote"]
    assert quote["n_pairs"] == 462
    assert quote["estimated_wall_seconds"] == pytest.approx(6991.363639038995)


def test_worker_names_bind_on_consume(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
        process_config={
            "target_plastic_percent": 60.0,
            "processing_capacity": 15_000.0,
            "energy_case": "C2",
        },
    )
    assert data["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in data["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]


def test_burn_true_is_switch_mismatch_on_lookup(monkeypatch, tmp_path):
    registry = _write_registry(tmp_path, {_CANONICAL: _sealed_entry()})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=_CANONICAL,
        process_config={"burn_leftover_plastic": True},
    )
    assert data["error_code"] == "campaign_basis_mismatch"
    assert data["mismatches"][0]["field"] == "burn_leftover_plastic"
    assert data["mismatches"][0]["campaign_value"] is False
    assert data["mismatches"][0]["requested_value"] is True


def test_flipped_jsonl_byte_is_artifact_integrity_mismatch(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path)
    payload = mini["rows_path"].read_bytes()
    mini["rows_path"].write_bytes(payload[:-1] + bytes([payload[-1] ^ 0x01]))
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_artifact_integrity_mismatch"
    assert data["artifact"] == "process_rows.jsonl"


def test_run_definition_field_change_is_artifact_integrity_mismatch(
    monkeypatch, tmp_path,
):
    mini = _mini_campaign(tmp_path)
    definition = json.loads(mini["run_path"].read_text(encoding="utf-8"))
    definition["runtime_engine_versions"]["python"] = "0.0.0"
    mini["run_path"].write_text(json.dumps(definition), encoding="utf-8")
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_artifact_integrity_mismatch"
    assert data["artifact"] == "run_definition.json"


def test_lockstep_artifact_and_manifest_without_registry_is_manifest_mismatch(
    monkeypatch, tmp_path,
):
    mini = _mini_campaign(tmp_path)
    payload = mini["rows_path"].read_bytes()
    mini["rows_path"].write_bytes(payload + b"\n")
    manifest = json.loads(mini["manifest_path"].read_text(encoding="utf-8"))
    manifest["artifact_sha256"]["process_rows_jsonl"] = hashlib.sha256(
        mini["rows_path"].read_bytes()
    ).hexdigest()
    mini["manifest_path"].write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8",
    )
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_manifest_integrity_mismatch"


def test_stale_canonical_after_python_change_is_fingerprint_mismatch(
    monkeypatch, tmp_path,
):
    mini = _mini_campaign(tmp_path)
    stale = mini["canonical"]
    definition = json.loads(mini["run_path"].read_text(encoding="utf-8"))
    definition["runtime_engine_versions"]["python"] = "3.11.0"
    mini["run_path"].write_text(json.dumps(definition), encoding="utf-8")
    new_digest = tea_ranking.canonical_json_digest(definition)
    assert new_digest != stale
    manifest = json.loads(mini["manifest_path"].read_text(encoding="utf-8"))
    manifest["artifact_sha256"]["run_definition_json"] = hashlib.sha256(
        mini["run_path"].read_bytes()
    ).hexdigest()
    manifest["campaign_fingerprint"] = new_digest
    mini["manifest_path"].write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8",
    )
    entry = dict(mini["entry"])
    entry["manifest_sha256"] = hashlib.sha256(
        mini["manifest_path"].read_bytes()
    ).hexdigest()
    registry = _write_registry(tmp_path, {stale: entry})
    data = _lookup(
        monkeypatch, registry, source="campaign", campaign_fingerprint=stale,
    )
    assert data["error_code"] == "campaign_fingerprint_mismatch"
    assert data["supplied"] == stale
    assert data["computed"] == new_digest
    assert data["manifest"] == new_digest


def test_duplicate_registry_key_is_ambiguous(monkeypatch, tmp_path):
    digest = "aa" * 32
    path = tmp_path / "dup.json"
    path.write_text(
        "{"
        f'"{digest}": {{"manifest_path": "/x", "manifest_sha256": "{"0" * 64}"}}, '
        f'"{digest}": {{"manifest_path": "/y", "manifest_sha256": "{"1" * 64}"}}'
        "}",
        encoding="utf-8",
    )
    data = _lookup(
        monkeypatch, path, source="campaign", campaign_fingerprint=digest,
    )
    assert data["error_code"] == "ambiguous_campaign_registry"


def test_alias_claimed_by_two_canonicals_is_ambiguous(monkeypatch, tmp_path):
    alias = "dd" * 32
    entries = {
        "aa" * 32: {
            "manifest_path": "/a",
            "manifest_sha256": "0" * 64,
            "append_log_aliases": [alias],
        },
        "bb" * 32: {
            "manifest_path": "/b",
            "manifest_sha256": "1" * 64,
            "append_log_aliases": [alias],
        },
    }
    registry = _write_registry(tmp_path, entries)
    data = _lookup(
        monkeypatch, registry, source="campaign", campaign_fingerprint="aa" * 32,
    )
    assert data["error_code"] == "ambiguous_campaign_registry"


def test_default_lookup_stays_on_the_admitted_cache(monkeypatch, tmp_path):
    monkeypatch.delenv(tea_ranking.REGISTRY_ENV, raising=False)
    data = _data_campaign_consume(tea.lookup_admitted_process_records(target_polymer="LDPE"))
    assert data["success"] is True
    assert data["engine_mode"] == "cache"
    assert data["record_count"] >= 1
    assert len(tea._records()) == 24


def test_public_solvent_identity_matches_aliases(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path)
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
        solvent="acetylacetone",
    )
    assert data["success"] is True
    assert data["matching_row_count"] == 1
    lowercase_only = (
        "acetylacetone".casefold() == "2,4-Pentanedione".casefold()
    )
    assert lowercase_only is False


def test_results_jsonl_is_not_consumed(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path, include_results=True)
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["success"] is True
    assert data["n_rows_consumed"] == 1
    assert len(data["comparison_rows"]) == 1


def test_mixed_row_fingerprint_is_mismatch(monkeypatch, tmp_path):
    definition = _minimal_definition()
    canonical = tea_ranking.canonical_json_digest(definition)
    mini = _mini_campaign(
        tmp_path,
        definition=definition,
        rows=[
            {
                "campaign_fingerprint": canonical,
                "polymer": "LDPE",
                "solvent_public_identity": "Toluene",
                "pair_id": "p1",
            },
            {
                "campaign_fingerprint": "ee" * 32,
                "polymer": "HDPE",
                "solvent_public_identity": "Toluene",
                "pair_id": "p2",
            },
        ],
    )
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_fingerprint_mismatch"
    assert data["pair_id"] == "p2"


def test_incomplete_campaign_refuses_unless_named(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path, complete=False)
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
    )
    assert data["error_code"] == "campaign_incomplete"
    allowed = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
        allow_partial_campaign=True,
    )
    assert allowed["success"] is True


def test_derived_temperature_not_produced_is_mismatch(monkeypatch, tmp_path):
    mini = _mini_campaign(tmp_path)
    registry = _write_registry(tmp_path, {mini["canonical"]: mini["entry"]})
    data = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
        dissolution_temperature_c=1.0,
    )
    assert data["error_code"] == "campaign_basis_mismatch"
    assert data["mismatches"][-1]["field"] == "dissolution_temperature_c"
    matching = _lookup(
        monkeypatch,
        registry,
        source="campaign",
        campaign_fingerprint=mini["canonical"],
        dissolution_temperature_c=95.0,
    )
    assert matching["success"] is True
