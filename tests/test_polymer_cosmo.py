"""R-1 polymer COSMO ingest. Never launches ORCA. Not the intercept test."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from dissolve import contaminants
from dissolve import polymer_cosmo as pc
from dissolve.cosmo_logp import COSMOBASE_PARAMETERISATION, Atom

_SRC = Path(pc.__file__).read_text()
_PE = pc.PE_MCOS
_PVC = pc.PVC_MCOS
_PET = pc.PET_MCOS


def test_duckdb_pin_unmoved():
    digest = hashlib.sha256(contaminants._ASSET.read_bytes()).hexdigest()
    assert digest == contaminants._ASSET_SHA256
    assert digest.startswith("866d769b")


def test_does_not_import_broken_converter_or_orca_runner():
    assert "spec_from_file_location" not in _SRC
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


def test_unconverted_gaussian_is_named_refuse(tmp_path):
    raw = tmp_path / "raw.cosmo"
    raw.write_text(pc.split_mcos(_PE)[0].gaussian_body)
    with pytest.raises(pc.PolymerCosmoError) as exc:
        pc.refuse_unconverted(raw)
    assert exc.value.error_code == pc.GAUSSIAN_UNCONVERTED
    with pytest.raises(pc.PolymerCosmoError) as exc2:
        pc.refuse_unconverted(_PE)
    assert exc2.value.error_code == pc.MCOS_NOT_SPLIT


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
