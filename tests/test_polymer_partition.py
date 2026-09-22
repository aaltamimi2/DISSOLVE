"""R-2 polymer-as-solvent partition and intercept test.

Never launches ORCA. Injected ln_gamma so COSMO-RS is not required.
Does not write the catalog partition column.
"""
from __future__ import annotations

import hashlib
import math
import subprocess
from pathlib import Path

import pytest

from dissolve import contaminants
from dissolve import polymer_cosmo as pc
from dissolve.cosmo_logp import COSMOBASE_PARAMETERISATION

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
