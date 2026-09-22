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


def _write_dummy_cosmo(path: Path, volume: float = 100.0) -> Path:
    path.write_text(f"$cosmo_data\n  volume=      {volume}\n")
    return path


def _lng_from_catalog(catalog: dict[str, float], polymer_paths):
    if isinstance(polymer_paths, (str, Path)):
        polymers = {Path(polymer_paths).resolve()}
    else:
        polymers = {Path(item).resolve() for item in polymer_paths}

    def lng(solute, solvent, **kwargs):
        path = Path(solvent)
        if path.resolve() in polymers:
            return 0.0
        return -float(catalog[path.stem]) * math.log(10)

    return lng


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


def test_served_row_is_2002_polymer_cosmo_not_24a(tmp_path):
    solute = _write_dummy_cosmo(tmp_path / "dep.cosmo")
    solvent = _write_dummy_cosmo(tmp_path / "acetone.cosmo")
    polymer = _write_dummy_cosmo(tmp_path / "pvc.cosmo")

    def lng(solute_path, solvent_path, **kwargs):
        assert kwargs["parameterization"] == pc.TURBOMOLE_2002_PARAMETERIZATION
        return 0.0 if Path(solvent_path) == polymer else math.log(10)

    row = pc.compute_log10_p_solvent_over_polymer(
        solute, solvent, polymer,
        polymer_name="pvc",
        solvent_key="acetone",
        ln_gamma=lng,
    )
    assert row["parameterisation"] == COSMOBASE_PARAMETERISATION == "2002"
    assert row["route"] == pc.ROUTE_POLYMER_COSMO
    assert row["engine"] == pc.ENGINE_GAUSSIAN_CONVERTED
    assert row["qc_origin"] == pc.QC_ORIGIN_GAUSSIAN_COSMO
    assert row["dft_ran"] is False
    assert row["reference_phase"] == "pvc"
    assert row["volume_term_applied"] is True
    assert row["volume_source_solvent"] == pc.VOLUME_SOURCE_COSMO_CAVITY
    assert row["volume_source_polymer"] == pc.VOLUME_SOURCE_COSMO_CAVITY
    assert row["basis"] != "mole_fraction"
    assert row["log10_p_solvent_over_polymer"] == pytest.approx(-1.0)
    text = pc.format_served_row(row)
    assert "polymer=pvc" in text
    assert "reference_phase=pvc" in text
    assert "24a" not in text


def test_intercept_drops_chloroform_only_and_does_not_apply_threshold(tmp_path):
    polymer = _write_dummy_cosmo(tmp_path / "polymer.cosmo")
    solute = _write_dummy_cosmo(tmp_path / "dep.cosmo")
    catalog = {
        "acetone": -0.92,
        "heptane": -3.33,
        "ethyl_acetate": -1.10,
        "chloroform": 1.45,
        "toluene": 0.24,
    }

    def solvent_file_for(key: str):
        return _write_dummy_cosmo(tmp_path / f"{key}.cosmo")

    report = pc.intercept_test_dep(
        "pvc",
        polymer,
        solute_cosmo=solute,
        ln_gamma=_lng_from_catalog(catalog, polymer),
        catalog_rows=list(catalog.items()),
        solvent_file_for=solvent_file_for,
    )
    assert [row["solvent_key"] for row in report["dropped"]] == ["chloroform"]
    assert report["chloroform_dropped"] is True
    assert report["second_solvent_dropped"] is False
    assert report["n"] == 4
    assert report["n_conformers"] == 1
    assert report["provisional"] is True
    assert report["volume_term_applied"] is True
    assert report["reference_phase"] == "pvc"
    assert report["intercept_ci95"][0] <= 0.0 <= report["intercept_ci95"][1]
    assert report["slope"] == pytest.approx(1.0)
    assert report["intercept"] == pytest.approx(0.0)
    assert report["intercept_consistent_with_zero"] is True
    assert report["removability_threshold_applied"] is False
    assert report["catalog_column_written"] is False
    assert report["parameterisation"] == "2002"
    assert report["dft_ran"] is False
    assert report["water_referenced_control"]["n"] == 30
    assert report["water_referenced_control"]["slope"] == 0.928
    assert "improved" not in str(report).casefold()
    assert "improved" not in pc.format_intercept_report(report).casefold()


def test_missing_cosmobase_solvent_is_named_not_rewritten(tmp_path):
    polymer = _write_dummy_cosmo(tmp_path / "polymer.cosmo")
    solute = _write_dummy_cosmo(tmp_path / "dep.cosmo")
    catalog = [
        ("acetone", -0.92),
        ("heptane", -3.33),
        ("ethyl_acetate", -1.10),
        ("xylene", 0.11),
    ]

    def solvent_file_for(key: str):
        if key == "xylene":
            return None
        return _write_dummy_cosmo(tmp_path / f"{key}.cosmo")

    catalog_map = {k: v for k, v in catalog if k != "xylene"}
    report = pc.intercept_test_dep(
        "pvc",
        polymer,
        solute_cosmo=solute,
        ln_gamma=_lng_from_catalog(catalog_map, polymer),
        catalog_rows=catalog,
        solvent_file_for=solvent_file_for,
    )
    missing = [row for row in report["rows"] if row["solvent_key"] == "xylene"]
    assert missing and missing[0]["error_code"] == pc.SOLVENT_NOT_AVAILABLE
    assert missing[0]["in_fit"] is False
    assert missing[0]["log10_p_solvent_over_polymer"] is None
    assert report["n"] == 3
    assert "o-xylene" not in _POLYMER_SRC


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
        "assert hasattr(mod, 'intercept_test_dep')\n"
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
