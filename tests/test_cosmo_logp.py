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

import hashlib
import math
import os
import shutil
import sys
import threading
import time
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


def test_formula_cannot_distinguish_the_dbp_isomers_either():
    """L1: di-n-butyl phthalate / iso / tere are all C16H22O4. The decoys
    exist so the InChIKey refuse has a real colliding-formula input."""
    same = _atoms(*([("C", 0, 0, 0)] * 16 + [("H", 0, 0, 0)] * 22 + [("O", 0, 0, 0)] * 4))
    assert cl.molecular_formula(same) == "C16H22O4"
    assert cl.DBP_CAS == "84-74-2"
    assert cl.DBP_INCHIKEY == "DOIRQSBPFJWKBE-UHFFFAOYSA-N"
    assert cl.DBP_INCHIKEY != cl.DEP_INCHIKEY
    assert len(cl.DBP_ISOMER_DECOYS) == 2
    keys = {cl.DBP_INCHIKEY} | {k for _, k in cl.DBP_ISOMER_DECOYS.values()}
    assert len(keys) == 3, "the DBP decoys must have distinct InChIKeys or they test nothing"
    assert cl.DEP_INCHIKEY not in keys


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


def test_dbp_anchors_are_the_pin_table_deltas_not_deps():
    """Scoring DBP against DEP's 4.81/2.92/2.41/1.79 would be a silent
    wrong-molecule accept. The numbers are pin 866d769b solvent_key Δ."""
    assert cl.anchor_pairs_for("dbp") == cl.DBP_ANCHOR_PAIRS
    assert cl.anchor_pairs_for("dep") == cl.ANCHOR_PAIRS
    assert cl.DBP_ANCHOR_PAIRS != cl.ANCHOR_PAIRS
    assert cl.DBP_ANCHOR_PAIRS == (
        ("dichloromethane", "water", 7.05),
        ("cyclohexanol", "water", 5.17),
        ("hexane", "water", 4.98),
        ("dichloromethane", "methanol", 2.18),
    )
    assert any((a, b) == cl.WATER_FREE_PAIR for a, b, _ in cl.DBP_ANCHOR_PAIRS)
    with pytest.raises(cl.CosmoError):
        cl.anchor_pairs_for("dmp")


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


def test_evaluate_anchor_pairs_accepts_dbp_numbers_on_dbp_pairs():
    """A correct pipeline landing on the table Δ themselves must accept."""
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 7.05, "cyclohexanol-water": 5.17,
            "hexane-water": 4.98, "dichloromethane-methanol": 2.18,
        },
        pairs=cl.DBP_ANCHOR_PAIRS,
    )
    assert result["accept"]
    assert result["n_passing"] == 4
    assert result["water_free_passes"]


def test_evaluate_anchor_pairs_accepts_the_measured_dbp_stage1_numbers():
    """Recorded L1 stage-1 Δ, same role as the DEP 5.31/3.79/3.36/1.45 pin."""
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 7.61, "cyclohexanol-water": 5.88,
            "hexane-water": 5.64, "dichloromethane-methanol": 1.92,
        },
        pairs=cl.DBP_ANCHOR_PAIRS,
    )
    assert result["accept"]
    assert result["n_passing"] == 4
    assert result["water_free_passes"]


def test_bbp_anchors_are_the_pin_table_deltas_not_dbp_or_dep():
    """L2 numbers are pin 866d769b solvent_key Δ for BBP, not L1's column."""
    assert cl.anchor_pairs_for("bbp") == cl.BBP_ANCHOR_PAIRS
    assert cl.BBP_ANCHOR_PAIRS != cl.DBP_ANCHOR_PAIRS
    assert cl.BBP_ANCHOR_PAIRS != cl.ANCHOR_PAIRS
    assert cl.BBP_ANCHOR_PAIRS == (
        ("dichloromethane", "water", 7.18),
        ("cyclohexanol", "water", 5.04),
        ("hexane", "water", 4.61),
        ("dichloromethane", "methanol", 2.14),
    )
    assert any((a, b) == cl.WATER_FREE_PAIR for a, b, _ in cl.BBP_ANCHOR_PAIRS)
    with pytest.raises(cl.CosmoError):
        cl.anchor_pairs_for("dmp")


def test_evaluate_anchor_pairs_accepts_bbp_table_deltas_on_bbp_pairs():
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 7.18, "cyclohexanol-water": 5.04,
            "hexane-water": 4.61, "dichloromethane-methanol": 2.14,
        },
        pairs=cl.BBP_ANCHOR_PAIRS,
    )
    assert result["accept"]
    assert result["n_passing"] == 4
    assert result["water_free_passes"]


def test_dehp_anchors_are_the_pin_table_deltas_not_literature_logp():
    """L3 accept is table Δ 9.98/8.13/8.33/2.60, not logP 7.5–8.4."""
    assert cl.anchor_pairs_for("dehp") == cl.DEHP_ANCHOR_PAIRS
    assert cl.DEHP_ANCHOR_PAIRS != cl.BBP_ANCHOR_PAIRS
    assert cl.DEHP_ANCHOR_PAIRS != cl.DBP_ANCHOR_PAIRS
    assert cl.DEHP_ANCHOR_PAIRS != cl.ANCHOR_PAIRS
    assert cl.DEHP_ANCHOR_PAIRS == (
        ("dichloromethane", "water", 9.98),
        ("cyclohexanol", "water", 8.13),
        ("hexane", "water", 8.33),
        ("dichloromethane", "methanol", 2.60),
    )
    assert 7.5 not in {v for *_, v in cl.DEHP_ANCHOR_PAIRS}
    assert 8.4 not in {v for *_, v in cl.DEHP_ANCHOR_PAIRS}
    assert any((a, b) == cl.WATER_FREE_PAIR for a, b, _ in cl.DEHP_ANCHOR_PAIRS)
    with pytest.raises(cl.CosmoError):
        cl.anchor_pairs_for("dmp")


def test_evaluate_anchor_pairs_accepts_dehp_table_deltas_on_dehp_pairs():
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 9.98, "cyclohexanol-water": 8.13,
            "hexane-water": 8.33, "dichloromethane-methanol": 2.60,
        },
        pairs=cl.DEHP_ANCHOR_PAIRS,
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


def test_delta_log_d_reproduces_the_measured_dehp_dcm_water_value():
    """Stage 1: ln γ of DEHP is -3.155 in dichloromethane and 25.989 in water."""
    value = cl.delta_log_d(
        -3.155, 25.989,
        volume_a=cl.MOLAR_VOLUMES_CM3["dichloromethane"],
        volume_b=cl.MOLAR_VOLUMES_CM3["water"],
    )
    assert value == pytest.approx(12.11, abs=0.01)


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


def test_dehp_table_deltas_do_not_pass_the_dep_accept():
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 9.98, "cyclohexanol-water": 8.13,
            "hexane-water": 8.33, "dichloromethane-methanol": 2.60,
        },
    )
    assert not result["accept"]


def test_formula_cannot_distinguish_the_dehp_isomers_either():
    same = _atoms(*([("C", 0, 0, 0)] * 24 + [("H", 0, 0, 0)] * 38 + [("O", 0, 0, 0)] * 4))
    assert cl.molecular_formula(same) == "C24H38O4"
    assert cl.DEHP_CAS == "117-81-7"
    assert cl.DEHP_INCHIKEY == "BJQHLKABXJIVAM-PMACEKPBSA-N"
    assert cl.DEHP_INCHIKEY != cl.DEHP_INCHIKEY_NOSTEREO
    assert cl.DEHP_INCHIKEY.split("-")[0] == cl.DEHP_INCHIKEY_NOSTEREO.split("-")[0]
    keys = {cl.DEHP_INCHIKEY} | {k for _, k in cl.DEHP_ISOMER_DECOYS.values()}
    assert len(keys) == 3
    assert cl.BBP_INCHIKEY not in keys


def test_bbp_table_deltas_do_not_pass_the_dep_accept():
    """Scoring BBP's column on DEP anchors must not silently accept."""
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 7.18, "cyclohexanol-water": 5.04,
            "hexane-water": 4.61, "dichloromethane-methanol": 2.14,
        },
    )
    assert not result["accept"]


def test_evaluate_anchor_pairs_accepts_the_measured_bbp_stage1_numbers():
    """Recorded L2 stage-1 Δ. Water-free is the tightest residual."""
    result = cl.evaluate_anchor_pairs(
        {
            "dichloromethane-water": 8.06, "cyclohexanol-water": 6.30,
            "hexane-water": 5.94, "dichloromethane-methanol": 1.92,
        },
        pairs=cl.BBP_ANCHOR_PAIRS,
    )
    assert result["accept"]
    assert result["n_passing"] == 4
    assert result["water_free_passes"]


def test_delta_log_d_reproduces_the_measured_bbp_dcm_water_value():
    """Stage 1: ln γ of BBP is -2.512 in dichloromethane and 17.316 in water."""
    value = cl.delta_log_d(
        -2.512, 17.316,
        volume_a=cl.MOLAR_VOLUMES_CM3["dichloromethane"],
        volume_b=cl.MOLAR_VOLUMES_CM3["water"],
    )
    assert value == pytest.approx(8.06, abs=0.01)


def test_formula_cannot_distinguish_the_bbp_isomers_either():
    same = _atoms(*([("C", 0, 0, 0)] * 19 + [("H", 0, 0, 0)] * 20 + [("O", 0, 0, 0)] * 4))
    assert cl.molecular_formula(same) == "C19H20O4"
    assert cl.BBP_CAS == "85-68-7"
    assert cl.BBP_INCHIKEY == "IRIAEXORFWYRCZ-UHFFFAOYSA-N"
    assert cl.BBP_INCHIKEY != cl.DBP_INCHIKEY
    assert cl.BBP_INCHIKEY != cl.DEP_INCHIKEY
    keys = {cl.BBP_INCHIKEY} | {k for _, k in cl.BBP_ISOMER_DECOYS.values()}
    assert len(keys) == 3
    assert cl.DBP_INCHIKEY not in keys


def test_delta_log_d_reproduces_the_measured_dbp_dcm_water_value():
    """Stage 1: ln γ of DBP is -2.737 in dichloromethane and 16.039 in water."""
    value = cl.delta_log_d(
        -2.737, 16.039,
        volume_a=cl.MOLAR_VOLUMES_CM3["dichloromethane"],
        volume_b=cl.MOLAR_VOLUMES_CM3["water"],
    )
    assert value == pytest.approx(7.61, abs=0.01)


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


@pytest.mark.skipif(not HAS_COSMOBASE, reason="COSMObase .cosmo library not present")
def test_parses_the_real_dbp_cosmo_file_to_the_right_formula():
    atoms = cl.parse_cosmo_geometry(COSMOBASE / "dibutylphthalate_c0.cosmo")
    assert len(atoms) == 42
    assert cl.molecular_formula(atoms) == "C16H22O4"


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


@pytest.mark.skipif(not HAS_COSMOBASE, reason="COSMObase .cosmo library not present")
def test_parses_the_real_bbp_cosmo_file_to_the_right_formula():
    atoms = cl.parse_cosmo_geometry(COSMOBASE / "butylbenzylphthalate_c0.cosmo")
    assert len(atoms) == 43
    assert cl.molecular_formula(atoms) == "C19H20O4"


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


@pytest.mark.skipif(not HAS_COSMOBASE, reason="COSMObase .cosmo library not present")
def test_parses_the_real_dehp_cosmo_file_to_the_right_formula():
    atoms = cl.parse_cosmo_geometry(COSMOBASE / "di-2-ethylhexylphthalate_c0.cosmo")
    assert len(atoms) == 66
    assert cl.molecular_formula(atoms) == "C24H38O4"


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


def test_computed_deltas_for_screen_stamps_computed_origin(tmp_path):
    pytest.importorskip("rdkit")
    artifacts, solvents = _dummy_p3_dirs(tmp_path)
    computed = cl.compute_delta_logd(
        cl.DEP_SMILES, ["toluene"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 1.0,
    )
    overlay = cl.computed_deltas_for_screen(computed)
    assert overlay
    assert all(row["field_origin"] == cl.FIELD_ORIGIN_COMPUTED for row in overlay)
    assert all(row["field_origin"] != cl.FIELD_ORIGIN_TABULATED for row in overlay)
    toluene = next(row for row in overlay if row["solvent_key"] == "toluene")
    assert toluene["delta_logd"] is not None
    from dissolve import contaminants
    from dissolve.contracts import parse_tool_result
    db = Path(__file__).resolve().parents[1] / "src" / "dissolve" / "data" / "contaminants.duckdb"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    parse_tool_result(contaminants.screen_contaminant_leaching(
        "LDPE", ["diethyl phthalate (DEP)"], solvents=["toluene"],
        computed_deltas=overlay,
    ))
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    assert before.startswith("866d769b")


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
        raise RuntimeError("failed-identity leftover must not bind the artifact store")

    second = cl.submit_solvent_dft_job(
        "1-octanol",
        jobs_dir=jobs,
        artifacts_dir=artifacts,
        runner=refuse_reuse,
        background=False,
    )
    assert calls, "second submit must not reuse a leftover surface"
    assert second.get("reused") is not True
    computed = cl.compute_delta_logd(
        cl.DEP_SMILES, ["1-octanol"],
        solvents_dir=solvents, artifacts_dir=artifacts, ln_gamma=lambda *a, **k: 0.5,
    )
    row = computed["results"][0]
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

