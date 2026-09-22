"""v3 §8 commit 1: /contaminant CLI skin, not a solvents bind.

Does not embed washes. Does not call tea._config_key. No BioSTEAM.
"""
from __future__ import annotations

import inspect
import io
from pathlib import Path

from rich.console import Console


from dissolve import contaminants, separation
from dissolve.cli import (
    CliApp,
    _format_contaminant_default,
    _parse_contaminant_slash,
)
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


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


def test_compare_without_prior_screen_prints_usage(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant compare") is False
    assert "contaminant_mode" not in app.session
    assert "prior contaminant screen" in buf.getvalue()


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


def test_bad_token_does_not_write(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant solvents") is False
    assert "contaminant_mode" not in app.session
    assert "usage: /contaminant" in buf.getvalue()


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


def test_logp_invalid_smiles_refuses_without_writing_mode(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert app.handle_command("/contaminant logp --smiles not_a_smiles") is False
    assert "contaminant_mode" not in app.session
    assert "invalid_smiles" in buf.getvalue()


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
    import time
    import threading
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
    import time
    import threading
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

