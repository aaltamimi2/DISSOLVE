"""ORCA_LOGP_SCOPE.v1 accept tests.

These run WITHOUT ORCA (a ~17 GB licensed download) and WITHOUT openCOSMO-RS
(a source build). Anything needing them is skipped by capability, never by
name, so a machine that HAS them runs strictly more tests rather than a
different set.

The known answers are the measured results recorded in
dissolve-v12-audit/cosmo/DEP_LOGD.stage{0,1,2}.v1.json. Where a number here
disagrees with that record, one of the two is wrong and this suite says so.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest

from dissolve import cosmo_logp as cl

COSMOBASE = Path("/home/aaltamimi2/COSMO-POLYMER-ML/results/oligomers/all-cosmotherm-solvents")
ARTIFACTS = Path("/home/aaltamimi2/cosmo-artifacts")
HAS_COSMOBASE = (COSMOBASE / "diethylphthalate_c0.cosmo").is_file()
HAS_OBABEL = shutil.which("obabel") is not None
HAS_RDKIT = pytest.importorskip is not None  # resolved lazily inside the test


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


def test_molecular_formula_is_hill_ordered():
    dep = _atoms(*([("C", 0, 0, 0)] * 12 + [("H", 0, 0, 0)] * 14 + [("O", 0, 0, 0)] * 4))
    assert cl.molecular_formula(dep) == "C12H14O4"


def test_formula_cannot_distinguish_the_isomers_this_pipeline_must_reject():
    """The premise of the InChIKey check: all three diethyl benzene-
    dicarboxylates are C12H14O4 at 222.24 g/mol. If formula were sufficient,
    verify_identity would be unnecessary."""
    same = _atoms(*([("C", 0, 0, 0)] * 12 + [("H", 0, 0, 0)] * 14 + [("O", 0, 0, 0)] * 4))
    assert cl.molecular_formula(same) == "C12H14O4"
    assert len(cl.DEP_ISOMER_DECOYS) == 2
    keys = {cl.DEP_INCHIKEY} | {k for _, k in cl.DEP_ISOMER_DECOYS.values()}
    assert len(keys) == 3, "the decoys must have distinct InChIKeys or they test nothing"


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


def test_delta_log_d_reproduces_the_published_dcm_water_value():
    """Stage 1: ln gamma of DEP is -2.043 in dichloromethane and 11.453 in
    water; the recorded delta logD is 5.31."""
    value = cl.delta_log_d(
        -2.043, 11.453,
        volume_a=cl.MOLAR_VOLUMES_CM3["dichloromethane"],
        volume_b=cl.MOLAR_VOLUMES_CM3["water"],
    )
    assert value == pytest.approx(5.31, abs=0.01)


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
def test_parses_the_real_dep_cosmo_file_to_the_right_formula():
    atoms = cl.parse_cosmo_geometry(COSMOBASE / "diethylphthalate_c0.cosmo")
    assert len(atoms) == 30
    assert cl.molecular_formula(atoms) == "C12H14O4"


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


def test_dependency_errors_name_what_is_missing_and_how_to_get_it():
    """A missing 17 GB licensed program should say so, not fail obscurely."""
    try:
        import opencosmorspy  # noqa: F401
    except ImportError:
        with pytest.raises(cl.CosmoDependencyError) as exc:
            cl.ln_gamma_infinite_dilution("a.orcacosmo", "b.orcacosmo")
        assert "opencosmorspy" in str(exc.value)
        assert "github" in str(exc.value).lower()
