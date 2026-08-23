"""COSMO-RS partition coefficients for contaminants. ORCA_LOGP_SCOPE.v1.

Reproduces the per-solvent logD our contaminant corpus reports, from first
principles: SMILES -> conformers -> ORCA DFT + COSMO surface -> openCOSMO-RS
chemical potentials -> partition coefficient.

WHAT IS COMPUTED, AND WHY IT IS A DIFFERENCE
Our `logd` table stores log10(C_solvent / C_reference) against a reference
phase the source sheet never names (the polymer). Differencing two solvents
for the same contaminant cancels that unknown exactly:

    logD(A) - logD(B) = log10(C_A/C_ref) - log10(C_B/C_ref) = log10(C_A/C_B)

which is a solvent/solvent partition coefficient computable from solvent
sigma-profiles alone -- no polymer phase, no polymer thermodynamics. The
cancellation needs only that the reference is THE SAME across rows, not that
it is identified.

LEVEL OF THEORY IS NOT A FREE CHOICE
openCOSMO-RS 24a is parameterised at BP86/def2-TZVP(-f) for geometry and
BP86/def2-TZVPD for single points. ORCA 6.0+ sets these itself under the
COSMORS keyword and the documentation calls changing them "strongly
discouraged". A different level yields a perfectly valid sigma-profile the
parameterisation was never fit to, and NOTHING RAISES -- the number is just
wrong by an unbounded amount. Do not override DFT_FUNCTIONAL / DFT_BASIS.

IDENTITY IS BOUND ON InChIKey, NEVER ON FORMULA
Diethyl phthalate, diethyl isophthalate and diethyl terephthalate all have
formula C12H14O4 and molecular weight 222.24, and differ only in connectivity.
A pipeline that checks formula or mass will accept the para isomer as the
ortho target and every downstream number is then confidently wrong. See
`verify_identity`.

RUNTIME DEPENDENCIES ARE OPTIONAL BY DESIGN
ORCA is a ~17 GB licensed download and openCOSMO-RS is a source build. Neither
is importable in a normal checkout, so every function that needs one raises
CosmoDependencyError naming what is missing. The pure functions -- geometry
parsing, unit detection, the Boltzmann combination, the partition relation,
the regression -- have no such dependency and are what the test suite covers.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# --- physical constants -------------------------------------------------
BOHR_TO_ANGSTROM = 0.529177210903
HARTREE_TO_KCAL = 627.5094740631
GAS_CONSTANT_KCAL = 0.0019872041          # kcal/(mol K)
STANDARD_T = 298.15                        # K

#: 1 log unit expressed as a free energy at 298.15 K. Worth stating: a sign or
#: unit error in the partition relation produces a plausible-looking number.
RT_LN10_KCAL = GAS_CONSTANT_KCAL * STANDARD_T * math.log(10)   # 1.364 kcal/mol

# --- level of theory: fixed by the openCOSMO-RS 24a parameterisation ----
DFT_FUNCTIONAL = "BP86"
DFT_BASIS_OPT = "def2-TZVP(-f)"
DFT_BASIS_SP = "def2-TZVPD"
ORCA_MIN_VERSION = (6, 0)                  # 5.x has no COSMORS keyword

#: Published accuracy of openCOSMO-RS 24a for partition coefficients:
#: AAD 0.76 over 296 points, R^2 0.92. The accept tolerance is derived from
#: this, not chosen -- see ACCEPT_TOLERANCE_LOG_UNITS.
PUBLISHED_LOGP_AAD = 0.76

#: |predicted - ours| <= this, on at least MIN_PAIRS_PASSING of the anchor
#: pairs, with the water-free pair among them. 1.0 would fail a CORRECT
#: pipeline about a third of the time given AAD 0.76; 1.5 leaves ~7%.
ACCEPT_TOLERANCE_LOG_UNITS = 1.5
MIN_PAIRS_PASSING = 3

#: The study target. DEP is the smallest phthalate that is also in our corpus
#: (4 rotatable bonds). DEHP has 14, and its literature logP spans 7.5-8.4 --
#: a reference spread wider than the method error cannot falsify anything.
DEP_SMILES = "CCOC(=O)c1ccccc1C(=O)OCC"
DEP_CAS = "84-66-2"
DEP_INCHIKEY = "FLKPEMZONWLCSK-UHFFFAOYSA-N"

#: The isomers that formula and mass CANNOT distinguish from the target.
#: Present so the must-refuse side of the identity check has real inputs.
DEP_ISOMER_DECOYS = {
    "diethyl isophthalate": ("CCOC(=O)c1cccc(C(=O)OCC)c1", "JLVWYWVLMFVCDI-UHFFFAOYSA-N"),
    "diethyl terephthalate": ("CCOC(=O)c1ccc(C(=O)OCC)cc1", "ONIHPYYWNBVMID-UHFFFAOYSA-N"),
}

#: Molar volumes at 298 K, cm3/mol. Used only for the concentration-basis
#: variant of the partition relation; the mole-fraction basis ignores them.
MOLAR_VOLUMES_CM3 = {
    "dichloromethane": 64.0, "water": 18.07, "methanol": 40.7,
    "hexane": 131.6, "cyclohexanol": 106.0,
}

#: Anchor pairs and the values OUR table gives for DEP. At least one pair must
#: be WATER-FREE: water's sigma-profile is both the best-validated and the most
#: idiosyncratic in COSMO-RS, so if every pair contains it, a water error
#: propagates into all of them and reads as agreement.
ANCHOR_PAIRS: tuple[tuple[str, str, float], ...] = (
    ("dichloromethane", "water", 4.81),
    ("cyclohexanol", "water", 2.92),
    ("hexane", "water", 2.41),
    ("dichloromethane", "methanol", 1.79),      # water-free
)
WATER_FREE_PAIR = ("dichloromethane", "methanol")

#: Our solvent names -> COSMObase file stems. Needed because the corpus and the
#: .cosmo library disagree on naming for a third of the solvents.
SOLVENT_FILE_ALIASES = {
    "dichloromethane": "ch2cl2", "water": "h2o", "chloroform": "chcl3",
    "acetone": "propanone", "tetrahydrofuran": "thf", "2-butanone": "butanone",
    "o-xylene": "1,2-dimethylbenzene", "1-propanol": "propanol",
    "1,2-propanediol": "propyleneglycol", "ethylene glycol": "glycol",
    "heptane": "n-heptane", "n,n-dimethylformamide": "dimethylformamide",
    "dimethyl sulfoxide": "dimethylsulfoxide", "isopropanol": "2-propanol",
}

#: Chloroform is EXCLUDED from the headline regression, and the exclusion is
#: principled rather than post-hoc: it was independently flagged as a COSMO-RS
#: failure before that regression existed. Its C-H is a weak hydrogen-bond
#: donor that COSMO-RS parameterisations handle badly. Never widen this set to
#: whatever the current fit dislikes -- that is fitting to the answer.
KNOWN_METHOD_FAILURES = {
    "chloroform": "weak C-H hydrogen-bond donor; documented COSMO-RS deviation",
}


class CosmoError(RuntimeError):
    """A failure in the COSMO logP pipeline."""


class CosmoDependencyError(CosmoError):
    """A required external program or package is not installed."""


class CosmoIdentityError(CosmoError):
    """A structure is not the molecule it was asked to be."""


# ======================================================================
# .cosmo / .orcacosmo geometry -- pure, no external dependency
# ======================================================================

@dataclass(frozen=True)
class Atom:
    element: str
    x: float
    y: float
    z: float


def parse_cosmo_geometry(path: str | Path) -> list[Atom]:
    """Read the ``$coord_rad`` block of a COSMObase ``.cosmo`` file.

    Coordinates are returned in ANGSTROM. The file stores bohr, and the unit is
    not declared anywhere in it -- see `detect_length_unit` for how that is
    established rather than assumed.
    """
    lines = Path(path).read_text(errors="ignore").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().startswith("$coord_rad"))
    except StopIteration:
        raise CosmoError(f"no $coord_rad block in {path}") from None
    raw: list[Atom] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("$"):
            break
        if not stripped or stripped.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 6:
            raw.append(Atom(parts[4].capitalize(), float(parts[1]), float(parts[2]), float(parts[3])))
    if not raw:
        raise CosmoError(f"empty $coord_rad block in {path}")
    scale = BOHR_TO_ANGSTROM if detect_length_unit(raw) == "bohr" else 1.0
    return [Atom(a.element, a.x * scale, a.y * scale, a.z * scale) for a in raw]


def detect_length_unit(atoms: Sequence[Atom]) -> str:
    """Return ``"bohr"`` or ``"angstrom"`` from the shortest carbon-carbon distance.

    A C-C single bond is ~1.5 A and ~2.9 bohr. The gap is large enough that the
    shortest C-C distance identifies the unit outright. Guessing wrong scales
    every coordinate by 1.89 and silently changes the molecule.
    """
    carbons = [a for a in atoms if a.element == "C"]
    if len(carbons) < 2:
        return "angstrom"
    shortest = min(
        math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
        for i, a in enumerate(carbons) for b in carbons[i + 1:]
    )
    return "bohr" if shortest > 2.2 else "angstrom"


def molecular_formula(atoms: Sequence[Atom]) -> str:
    """Hill-order formula. Sufficient to catch a truncated file, NOT sufficient
    to bind identity -- the DEP isomers share a formula. Use `verify_identity`."""
    counts: dict[str, int] = {}
    for a in atoms:
        counts[a.element] = counts.get(a.element, 0) + 1
    ordered = [e for e in ("C", "H") if e in counts] + sorted(e for e in counts if e not in ("C", "H"))
    return "".join(f"{e}{counts[e]}" if counts[e] > 1 else e for e in ordered)


def write_xyz(atoms: Sequence[Atom], path: str | Path, comment: str = "") -> Path:
    out = Path(path)
    body = "".join(f"{a.element:<3}{a.x:14.6f}{a.y:14.6f}{a.z:14.6f}\n" for a in atoms)
    out.write_text(f"{len(atoms)}\n{comment}\n{body}")
    return out


def inchikey_from_xyz(path: str | Path) -> str:
    """Perceive connectivity from 3D coordinates and return the InChIKey.

    Requires Open Babel. Bond perception from coordinates is what makes this a
    real identity check: it reads the structure, not a label we attached.
    """
    obabel = shutil.which("obabel")
    if not obabel:
        raise CosmoDependencyError("obabel not found; install openbabel to verify identity")
    result = subprocess.run([obabel, str(path), "-oinchikey"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        token = line.strip()
        if re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z]", token):
            return token
    raise CosmoError(f"obabel produced no InChIKey for {path}: {result.stderr.strip()[:200]}")


def verify_identity(structure: str | Path, expected_inchikey: str, *, scratch: str | Path | None = None) -> str:
    """Raise unless ``structure`` really is the molecule ``expected_inchikey`` names.

    Accepts a ``.cosmo``/``.orcacosmo`` file or an ``.xyz``. This is the check
    that formula and molecular weight cannot perform: the three diethyl
    benzenedicarboxylate isomers are C12H14O4 at 222.24 g/mol and differ only
    here. Run it again AFTER geometry optimisation -- an optimiser that closes a
    ring or transfers a proton has changed the molecule without saying so.
    """
    path = Path(structure)
    if path.suffix == ".xyz":
        xyz = path
    else:
        atoms = parse_cosmo_geometry(path)
        base = Path(scratch) if scratch else path.parent
        xyz = write_xyz(atoms, base / f"{path.stem}.identity.xyz", comment=f"identity check {path.name}")
    found = inchikey_from_xyz(xyz)
    if found != expected_inchikey:
        raise CosmoIdentityError(
            f"{path.name} is not the expected molecule: got {found}, expected {expected_inchikey}. "
            "Formula and molecular weight cannot distinguish these; connectivity can."
        )
    return found


# ======================================================================
# Conformers -- RDKit
# ======================================================================

def generate_conformers(
    smiles: str, *, n_embed: int = 300, seed: int = 12345,
    prune_rms: float = 0.5, max_iters: int = 2000,
) -> list[tuple[list[Atom], float]]:
    """ETKDGv3 embedding + MMFF optimisation, RMSD-pruned.

    Returns ``(atoms, mmff_energy_kcal)`` sorted by energy, lowest first. The
    MMFF energies ORDER the conformers; they are NOT the Boltzmann weights --
    those come from the DFT energies, see `boltzmann_weights`.
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as exc:
        raise CosmoDependencyError(f"rdkit is required to generate conformers: {exc}") from exc
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if mol is None:
        raise CosmoError(f"RDKit could not parse SMILES: {smiles}")
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.pruneRmsThresh = prune_rms
    ids = list(AllChem.EmbedMultipleConfs(mol, numConfs=n_embed, params=params))
    results = AllChem.MMFFOptimizeMoleculeConfs(mol, maxIters=max_iters)
    converged = sorted(
        ((cid, energy) for cid, (status, energy) in zip(ids, results) if status == 0),
        key=lambda pair: pair[1],
    )
    if not converged:
        raise CosmoError(f"no conformer of {smiles} converged under MMFF")
    lowest = converged[0][1]
    out: list[tuple[list[Atom], float]] = []
    for cid, energy in converged:
        conf = mol.GetConformer(cid)
        atoms = [
            Atom(atom.GetSymbol(), *(lambda p: (p.x, p.y, p.z))(conf.GetAtomPosition(atom.GetIdx())))
            for atom in mol.GetAtoms()
        ]
        out.append((atoms, energy - lowest))
    return out


# ======================================================================
# ORCA inputs -- pure text generation
# ======================================================================

def orca_opt_input(atoms: Sequence[Atom], *, maxcore_mb: int = 1500) -> str:
    """Geometry optimisation at the parameterised level.

    ``%maxcore`` is PER PROCESS. With 4 processes ``1500`` is ~6 GB, not 1.5 --
    a misreading that costs the machine, not the calculation.
    """
    body = "".join(f"{a.element:<3}{a.x:14.6f}{a.y:14.6f}{a.z:14.6f}\n" for a in atoms)
    return (
        f"! OPT {DFT_FUNCTIONAL} {DFT_BASIS_OPT} TightSCF\n"
        f"%maxcore {maxcore_mb}\n* xyz 0 1\n{body}*\n"
    )


def orca_cosmors_input(atoms: Sequence[Atom], *, solvent: str = "Water", maxcore_mb: int = 1500) -> str:
    """COSMO surface via the COSMORS keyword, which emits ``.orcacosmo``.

    The named solvent selects ORCA's reference calculation; the SOLUTE surface
    it writes is what openCOSMO-RS consumes, and that surface does not depend on
    which solvent was named. One call per molecule is enough.
    """
    body = "".join(f"{a.element:<3}{a.x:14.6f}{a.y:14.6f}{a.z:14.6f}\n" for a in atoms)
    return f"! COSMORS({solvent})\n%maxcore {maxcore_mb}\n* xyz 0 1\n{body}*\n"


def parse_orca_final_energy(output: str) -> float:
    """Last ``FINAL SINGLE POINT ENERGY`` in hartree."""
    hits = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", output)
    if not hits:
        raise CosmoError("no FINAL SINGLE POINT ENERGY in ORCA output")
    return float(hits[-1])


def parse_orca_optimised_geometry(output: str) -> list[Atom]:
    """Last Cartesian block of an ORCA optimisation, in angstrom."""
    blocks = re.findall(r"CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\n", output, re.S)
    if not blocks:
        raise CosmoError("no CARTESIAN COORDINATES block in ORCA output")
    atoms = []
    for line in blocks[-1].splitlines():
        parts = line.split()
        if len(parts) == 4:
            atoms.append(Atom(parts[0].capitalize(), float(parts[1]), float(parts[2]), float(parts[3])))
    if not atoms:
        raise CosmoError("empty CARTESIAN COORDINATES block")
    return atoms


def orca_converged(output: str) -> bool:
    return "THE OPTIMIZATION HAS CONVERGED" in output


# ======================================================================
# openCOSMO-RS
# ======================================================================

def ln_gamma_infinite_dilution(
    solute_orcacosmo: str | Path, solvent_orcacosmo: str | Path, *,
    temperature: float = STANDARD_T, solute_fraction: float = 1e-5,
    parameterization: str = "openCOSMORS24a",
) -> float:
    """ln of the infinite-dilution activity coefficient of solute in solvent.

    ``solute_fraction`` 1e-5 is converged: 1e-5 -> 1e-6 moves a resulting
    delta logD by 0.001.

    NOTE `COSMORS.add_molecule` accepts a list but raises
    ``NotImplementedError: More than one conformer not supported``. The
    ensemble is therefore combined explicitly -- see `boltzmann_combine`.
    """
    try:
        import numpy as np
        from opencosmorspy import COSMORS, Parameterization
        from opencosmorspy.parameterization import openCOSMORS24a
    except ImportError as exc:
        raise CosmoDependencyError(
            f"opencosmorspy is required: {exc}. It has no PyPI package; install with "
            "pip install git+https://github.com/TUHH-TVT/opencosmorspy.git"
        ) from exc
    par = openCOSMORS24a() if parameterization == "openCOSMORS24a" else Parameterization(parameterization)
    engine = COSMORS(par)
    engine.add_molecule([str(solute_orcacosmo)])
    engine.add_molecule([str(solvent_orcacosmo)])
    engine.add_job(
        x=np.array([solute_fraction, 1.0 - solute_fraction]),
        T=temperature, refst="pure_component",
    )
    return float(engine.calculate()["tot"]["lng"][0][0])


def boltzmann_weights(relative_energies_kcal: Sequence[float], *, temperature: float = STANDARD_T) -> list[float]:
    """Normalised Boltzmann weights from energies relative to the lowest state."""
    rt = GAS_CONSTANT_KCAL * temperature
    raw = [math.exp(-e / rt) for e in relative_energies_kcal]
    total = sum(raw)
    if total <= 0:
        raise CosmoError("degenerate Boltzmann weights")
    return [w / total for w in raw]


def boltzmann_combine(
    per_conformer_ln_gamma: Sequence[float], relative_energies_kcal: Sequence[float],
    *, temperature: float = STANDARD_T,
) -> float:
    """Ensemble ln gamma over conformers.

        exp(-mu_ens/RT) = SUM_k exp(-(mu_k + dE_k)/RT)
        ln gamma_ens    = -ln SUM_k exp(-ln gamma_k - dE_k/RT)

    More accessible states lower the ensemble gamma below any single conformer.

    MEASURED, AND WORTH KNOWING BEFORE SPENDING DFT TIME ON THIS: for DEP the
    six-conformer ensemble shifted ln gamma by -0.97 with standard deviation
    0.08 across five chemically diverse solvents. A solvent-INDEPENDENT shift
    cancels in a difference, so delta logD moved by 0.01-0.10. Conformer
    averaging matters for an absolute solvation free energy; for a partition
    coefficient it is close to free.
    """
    if len(per_conformer_ln_gamma) != len(relative_energies_kcal):
        raise CosmoError("conformer count mismatch between ln gamma and energies")
    if not per_conformer_ln_gamma:
        raise CosmoError("empty conformer ensemble")
    rt = GAS_CONSTANT_KCAL * temperature
    total = sum(
        math.exp(-lng - dE / rt)
        for lng, dE in zip(per_conformer_ln_gamma, relative_energies_kcal)
    )
    return -math.log(total)


def delta_log_d(
    ln_gamma_a: float, ln_gamma_b: float, *,
    volume_a: float | None = None, volume_b: float | None = None,
) -> float:
    """log10 of the A/B partition coefficient of the solute.

        mole-fraction basis : log10(gamma_B / gamma_A)
        concentration basis : the same, plus log10(V_B / V_A)

    Supplying both volumes selects the concentration basis. For our table the
    two differ little -- the fitted slope moves 0.774 -> 0.813 -- so the basis
    is not what explains a slope away from 1.
    """
    value = (ln_gamma_b - ln_gamma_a) / math.log(10)
    if volume_a is not None and volume_b is not None:
        value += math.log10(volume_b / volume_a)
    return value


# ======================================================================
# Scoring
# ======================================================================

@dataclass(frozen=True)
class LinearFit:
    slope: float
    intercept: float
    r_squared: float
    residual_sd: float
    n: int
    slope_stderr: float

    @property
    def slope_ci95(self) -> tuple[float, float]:
        half = 1.96 * self.slope_stderr
        return (self.slope - half, self.slope + half)

    def contains_unit_slope(self) -> bool:
        """True when a slope of exactly 1 is inside the 95% interval.

        Slope 1 is what a single constant reference phase predicts. This is the
        test of whether our logD and the COSMO-RS prediction are the same
        physical quantity offset by a constant, or different quantities.
        """
        low, high = self.slope_ci95
        return low <= 1.0 <= high


def linear_fit(x: Sequence[float], y: Sequence[float]) -> LinearFit:
    """Ordinary least squares of y on x, with the standard error of the slope."""
    n = len(x)
    if n != len(y):
        raise CosmoError("x and y differ in length")
    if n < 3:
        raise CosmoError("need at least 3 points to estimate a slope and its error")
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    sxx = sum((xi - mean_x) ** 2 for xi in x)
    if sxx == 0:
        raise CosmoError("x has no variance; slope is undefined")
    slope = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / sxx
    intercept = mean_y - slope * mean_x
    residuals = [yi - (slope * xi + intercept) for xi, yi in zip(x, y)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    resid_sd = math.sqrt(ss_res / n)
    slope_stderr = math.sqrt(ss_res / (n - 2) / sxx) if n > 2 else float("nan")
    return LinearFit(slope, intercept, r_squared, resid_sd, n, slope_stderr)


def evaluate_anchor_pairs(predicted: Mapping[str, float]) -> dict[str, Any]:
    """Score predicted delta logD against ANCHOR_PAIRS.

    The water-free pair MUST be among those passing. Water appears in three of
    the four anchors, and its sigma-profile is the best-validated and most
    idiosyncratic in COSMO-RS; passing only the water pairs is consistent with a
    correct water profile masking a broken solute, which is not evidence the
    pipeline works.
    """
    rows = []
    for solvent_a, solvent_b, ours in ANCHOR_PAIRS:
        key = f"{solvent_a}-{solvent_b}"
        value = predicted.get(key)
        if value is None:
            rows.append({"pair": key, "ours": ours, "predicted": None, "residual": None, "passes": False})
            continue
        residual = value - ours
        rows.append({
            "pair": key, "ours": ours, "predicted": value, "residual": residual,
            "passes": abs(residual) <= ACCEPT_TOLERANCE_LOG_UNITS,
            "water_free": (solvent_a, solvent_b) == WATER_FREE_PAIR,
        })
    passing = [r for r in rows if r["passes"]]
    water_free_passes = any(r["passes"] and r.get("water_free") for r in rows)
    residuals = [r["residual"] for r in rows if r["residual"] is not None]
    return {
        "rows": rows,
        "n_passing": len(passing),
        "water_free_passes": water_free_passes,
        "tolerance": ACCEPT_TOLERANCE_LOG_UNITS,
        "mean_residual": (sum(residuals) / len(residuals)) if residuals else None,
        "accept": len(passing) >= MIN_PAIRS_PASSING and water_free_passes,
    }


def solvent_file_stem(name: str) -> str:
    """Map a corpus solvent name to its COSMObase file stem."""
    return SOLVENT_FILE_ALIASES.get(name.strip().lower(), name.strip().lower())
