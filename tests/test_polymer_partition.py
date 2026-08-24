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
_LOGP_SRC = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "cosmo_logp.py"


def _lng_from_catalog(catalog: dict[str, float], polymer_path: Path):
    def lng(solute, solvent, **kwargs):
        path = Path(solvent)
        if path.resolve() == Path(polymer_path).resolve():
            return 0.0
        return -float(catalog[path.stem]) * math.log(10)

    return lng


def test_leftover_store_ident_on_c2cffc9():
    committed = subprocess.check_output(
        ["git", "show", "c2cffc9:src/dissolve/cosmo_logp.py"],
        cwd=Path(__file__).resolve().parents[1],
    )
    assert hashlib.sha256(_LOGP_SRC.read_bytes()).hexdigest() == hashlib.sha256(
        committed
    ).hexdigest()
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
    solute = tmp_path / "dep.cosmo"
    solvent = tmp_path / "hexane.cosmo"
    polymer = tmp_path / "pvc.cosmo"
    for path in (solute, solvent, polymer):
        path.write_text("dummy")

    def lng(solute_path, solvent_path, **kwargs):
        assert kwargs["parameterization"] == pc.TURBOMOLE_2002_PARAMETERIZATION
        return 0.0 if Path(solvent_path) == polymer else math.log(10)

    row = pc.compute_log10_p_solvent_over_polymer(
        solute, solvent, polymer,
        polymer_name="pvc",
        solvent_key="hexane",
        ln_gamma=lng,
    )
    assert row["parameterisation"] == COSMOBASE_PARAMETERISATION == "2002"
    assert row["route"] == pc.ROUTE_POLYMER_COSMO
    assert row["engine"] == pc.ENGINE_GAUSSIAN_CONVERTED
    assert row["qc_origin"] == pc.QC_ORIGIN_GAUSSIAN_COSMO
    assert row["dft_ran"] is False
    assert row["log10_p_solvent_over_polymer"] == pytest.approx(-1.0)
    text = pc.format_served_row(row)
    assert "polymer=pvc" in text
    assert "reference_phase=pvc" in text
    assert "24a" not in text


def test_intercept_drops_chloroform_only_and_does_not_apply_threshold(tmp_path):
    polymer = tmp_path / "polymer.cosmo"
    solute = tmp_path / "dep.cosmo"
    polymer.write_text("p")
    solute.write_text("s")
    catalog = {
        "hexane": -0.92,
        "water": -3.33,
        "methanol": -1.10,
        "chloroform": 1.45,
        "toluene": 0.24,
    }
    files = {}

    def solvent_file_for(key: str):
        path = tmp_path / f"{key}.cosmo"
        path.write_text(key)
        files[key] = path
        return path

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
    assert any(row["solvent_key"] == "water" and row["in_fit"] for row in report["rows"])
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


def test_missing_cosmobase_solvent_is_named_not_rewritten(tmp_path):
    polymer = tmp_path / "polymer.cosmo"
    solute = tmp_path / "dep.cosmo"
    polymer.write_text("p")
    solute.write_text("s")
    catalog = [
        ("hexane", -0.92),
        ("water", -3.33),
        ("methanol", -1.10),
        ("xylene", 0.11),
    ]

    def solvent_file_for(key: str):
        if key == "xylene":
            return None
        path = tmp_path / f"{key}.cosmo"
        path.write_text(key)
        return path

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
