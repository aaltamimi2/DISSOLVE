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

import hashlib
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

# --- physical constants -------------------------------------------------
BOHR_TO_ANGSTROM = 0.529177210903
HARTREE_TO_KCAL = 627.5094740631
GAS_CONSTANT_KCAL = 0.0019872041          # kcal/(mol K)
STANDARD_T = 298.15                        # K
MAX_CONCURRENT_DFT = 4  # measured safe vs ~6 GiB; P-4 serialises and does not start DFT

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

#: L1 target (CONTAMINANT_COSMO_LADDER_SPEC.v1, ADMIT 0c07e6fc).
#: InChIKey perceived from the local COSMObase Turbomole file
#: ``dibutylphthalate_c0.cosmo`` (same library as DEP) via
#: ``parse_cosmo_geometry`` + obabel -- not a registry fetch.
#: CAS from ``contaminant_cas_phthalates.local.v1.json``.
#: SMILES is the Kekulé form of that perceived connectivity.
DBP_SMILES = "CCCCOC(=O)c1ccccc1C(=O)OCCCC"
DBP_CAS = "84-74-2"
DBP_INCHIKEY = "DOIRQSBPFJWKBE-UHFFFAOYSA-N"

#: 1,3- and 1,4-dibutyl benzenedicarboxylates. Same formula C16H22O4 as
#: the ortho target; InChIKeys computed locally from the constructed
#: SMILES (obabel), not fetched.
DBP_ISOMER_DECOYS = {
    "di-n-butyl isophthalate": ("CCCCOC(=O)c1cccc(C(=O)OCCCC)c1", "GOPWOUQJIMLDDM-UHFFFAOYSA-N"),
    "di-n-butyl terephthalate": ("CCCCOC(=O)c1ccc(C(=O)OCCCC)cc1", "LQLQDKBJAIILIQ-UHFFFAOYSA-N"),
}

#: L2 target. InChIKey perceived from the local COSMObase Turbomole file
#: ``butylbenzylphthalate_c0.cosmo`` via ``parse_cosmo_geometry`` + obabel.
#: CAS from ``contaminant_cas_phthalates.local.v1.json``. Not a registry fetch.
BBP_SMILES = "CCCCOC(=O)c1ccccc1C(=O)OCc1ccccc1"
BBP_CAS = "85-68-7"
BBP_INCHIKEY = "IRIAEXORFWYRCZ-UHFFFAOYSA-N"

#: 1,3- and 1,4-butyl benzyl benzenedicarboxylates. Same formula C19H20O4
#: as the ortho target; InChIKeys computed locally from the constructed
#: SMILES (obabel), not fetched.
BBP_ISOMER_DECOYS = {
    "butyl benzyl isophthalate": ("CCCCOC(=O)c1cccc(C(=O)OCc2ccccc2)c1", "QDKAUHONDCKMOU-UHFFFAOYSA-N"),
    "butyl benzyl terephthalate": ("CCCCOC(=O)c1ccc(C(=O)OCc2ccccc2)cc1", "IEICWPCBTOBBDK-UHFFFAOYSA-N"),
}

#: L3 target. InChIKey perceived from the local COSMObase Turbomole file
#: ``di-2-ethylhexylphthalate_c0.cosmo``. That geometry specifies both
#: 2-ethylhexyl stereocenters (``…-PMACEKPBSA-N``). The no-stereo catalog
#: key ``BJQHLKABXJIVAM-UHFFFAOYSA-N`` is the same connectivity and is
#: NOT this file. CAS from ``contaminant_cas_phthalates.local.v1.json``.
#: Literature logP 7.5–8.4 is not the accept test — the table Δ are.
DEHP_SMILES = "c1cc(C(=O)OC[C@@H](CC)CCCC)c(C(=O)OC[C@@H](CC)CCCC)cc1"
DEHP_CAS = "117-81-7"
DEHP_INCHIKEY = "BJQHLKABXJIVAM-PMACEKPBSA-N"
DEHP_INCHIKEY_NOSTEREO = "BJQHLKABXJIVAM-UHFFFAOYSA-N"

#: 1,3- and 1,4-di(2-ethylhexyl) benzenedicarboxylates. Same formula
#: C24H38O4; InChIKeys computed locally from constructed SMILES (obabel).
DEHP_ISOMER_DECOYS = {
    "di-(2-ethylhexyl) isophthalate": ("CCCCC(CC)COC(=O)c1cccc(C(=O)OCC(CC)CCCC)c1", "WXZOXVVKILCOPG-UHFFFAOYSA-N"),
    "di-(2-ethylhexyl) terephthalate": ("CCCCC(CC)COC(=O)c1ccc(C(=O)OCC(CC)CCCC)cc1", "RWPICVVBGZBXNA-UHFFFAOYSA-N"),
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
#: Same four solvent pairs as DEP; the numbers are DBP's rows in pin
#: ``866d769b…``, ``logd.solvent_key`` (not ``solvent_normalized``).
DBP_ANCHOR_PAIRS: tuple[tuple[str, str, float], ...] = (
    ("dichloromethane", "water", 7.05),
    ("cyclohexanol", "water", 5.17),
    ("hexane", "water", 4.98),
    ("dichloromethane", "methanol", 2.18),      # water-free
)
#: BBP's rows in the same pin / the same four pairs.
BBP_ANCHOR_PAIRS: tuple[tuple[str, str, float], ...] = (
    ("dichloromethane", "water", 7.18),
    ("cyclohexanol", "water", 5.04),
    ("hexane", "water", 4.61),
    ("dichloromethane", "methanol", 2.14),      # water-free
)
#: DEHP's rows in the same pin / the same four pairs. Not literature logP.
DEHP_ANCHOR_PAIRS: tuple[tuple[str, str, float], ...] = (
    ("dichloromethane", "water", 9.98),
    ("cyclohexanol", "water", 8.13),
    ("hexane", "water", 8.33),
    ("dichloromethane", "methanol", 2.60),      # water-free
)
WATER_FREE_PAIR = ("dichloromethane", "methanol")


def anchor_pairs_for(solute: str) -> tuple[tuple[str, str, float], ...]:
    """Table-Δ anchors for a named ladder solute.

    DEP stays the default ``ANCHOR_PAIRS`` so existing callers do not silently
    score a new molecule against DEP's numbers. A solute that is not yet
    pinned raises rather than inventing a column.
    """
    key = solute.strip().lower()
    if key in {"dep", "diethylphthalate", "diethyl phthalate"}:
        return ANCHOR_PAIRS
    if key in {"dbp", "dibutylphthalate", "di-n-butyl phthalate", "dibutyl phthalate"}:
        return DBP_ANCHOR_PAIRS
    if key in {"bbp", "butylbenzylphthalate", "butyl benzyl phthalate"}:
        return BBP_ANCHOR_PAIRS
    if key in {"dehp", "di-2-ethylhexylphthalate", "di-(2-ethylhexyl) phthalate",
               "diethylhexylphthalate"}:
        return DEHP_ANCHOR_PAIRS
    raise CosmoError(
        f"no table-Δ anchors pinned for solute {solute!r}; "
        "the phthalate ladder pins DEP/DBP/BBP/DEHP only"
    )

#: Our solvent names -> COSMObase file stems. Needed because the corpus and the
#: .cosmo library disagree on naming for a third of the solvents.
SOLVENT_FILE_ALIASES = {
    "dichloromethane": "ch2cl2", "water": "h2o", "chloroform": "chcl3",
    "acetone": "propanone", "tetrahydrofuran": "thf", "2-butanone": "butanone",
    "o-xylene": "1,2-dimethylbenzene", "1-propanol": "propanol",
    "1,2-propanediol": "propyleneglycol", "ethylene glycol": "glycol",
    "heptane": "n-heptane", "n,n-dimethylformamide": "dimethylformamide",
    "dimethyl sulfoxide": "dimethylsulfoxide", "isopropanol": "2-propanol",
    "tetrahydropyran": "thp", "acetic acid": "aceticacid",
    "diphenyl ether": "diphenylether", "ethyl acetate": "ethylacetate",
}

#: Distinct ``logd.solvent_key`` values in pin ``866d769b…``. Coverage is a
#: number over this set, not over COSMObase. ``xylene`` is in the table and
#: is not ``o-xylene``; do not silently substitute.
TABLE_SOLVENT_KEYS = frozenset({
    "1,2-propanediol", "1-propanol", "2,3-dihydropyran", "2-butanone",
    "acetic acid", "acetone", "acetylacetone", "benzene", "chloroform",
    "cyclohexane", "cyclohexanol", "dichloromethane", "dimethyl sulfoxide",
    "diphenyl ether", "dodecane", "ethanol", "ethyl acetate",
    "ethylene glycol", "heptane", "hexane", "isopropanol", "isopropylamine",
    "methanol", "methylacetate", "n,n-dimethylformamide", "o-xylene",
    "tert-butanol", "tetrahydrofuran", "tetrahydropyran", "toluene",
    "triethylamine", "water", "xylene",
})

#: Stage-1 ``.orcacosmo`` solvents. Five files, not nine: the other four
#: ``.orcacosmo`` names are solutes (dep/dbp/bbp/dehp).
ORCA_ROUTE_SOLVENTS = frozenset({
    "water", "methanol", "dichloromethane", "hexane", "cyclohexanol",
})
ORCA_ROUTE = "orca"
ORCA_PARAMETERISATION = "24a"
COSMOBASE_ROUTE = "cosmobase"
COSMOBASE_PARAMETERISATION = "2002"
COSMOBASE_ENGINE = "turbomole"
DEFAULT_COSMOBASE_SOLVENTS_DIR = Path(
    "/home/aaltamimi2/COSMO-POLYMER-ML/results/oligomers/all-cosmotherm-solvents"
)
DEFAULT_ORCA_ARTIFACTS_DIR = Path("/home/aaltamimi2/cosmo-artifacts/stage1")

#: Stage-1 ORCA solvent file stems. Matches scripts/cosmo/compute_delta_logd.py.
ORCA_SOLVENT_FILE_STEMS = {
    "dichloromethane": "dcm",
    "water": "water",
    "methanol": "methanol",
    "hexane": "hexane",
    "cyclohexanol": "cyclohexanol",
}

#: InChIKey → stage-1 solute stem. No guessing for an unknown SMILES.
SOLUTE_ORCA_STEM_BY_INCHIKEY = {
    DEP_INCHIKEY: "dep",
    DBP_INCHIKEY: "dbp",
    BBP_INCHIKEY: "bbp",
    DEHP_INCHIKEY: "dehp",
}

#: Exact COSMObase solute filenames. Not a substring search.
SOLUTE_COSMOBASE_FILE_BY_INCHIKEY = {
    DEP_INCHIKEY: "diethylphthalate_c0.cosmo",
    DBP_INCHIKEY: "dibutylphthalate_c0.cosmo",
    BBP_INCHIKEY: "butylbenzylphthalate_c0.cosmo",
    DEHP_INCHIKEY: "di-2-ethylhexylphthalate_c0.cosmo",
}

VALIDATION_VALIDATED = "validated"
VALIDATION_COMPUTED_UNVALIDATED = "computed_unvalidated"
VALIDATION_NO_BASIS = "no_validation_basis"
ABSOLUTE_REFUSE_TOKENS = frozenset({"", "none", "absolute", "abs"})

#: Same molecule, different string. Not a neighbour-solvent fallback.
SOLVENT_IDENTITY_ALIASES = {
    "dcm": "dichloromethane",
    "ch2cl2": "dichloromethane",
    "methylene chloride": "dichloromethane",
    "h2o": "water",
    "meoh": "methanol",
    "methyl alcohol": "methanol",
    "dmso": "dimethyl sulfoxide",
    "dmf": "n,n-dimethylformamide",
    "n,n-dimethylformamide": "n,n-dimethylformamide",
    "thf": "tetrahydrofuran",
    "thp": "tetrahydropyran",
    "oxane": "tetrahydropyran",
    "ipa": "isopropanol",
    "2-propanol": "isopropanol",
    "propan-2-ol": "isopropanol",
    "chcl3": "chloroform",
    "mek": "2-butanone",
    "butanone": "2-butanone",
    "1,2-dimethylbenzene": "o-xylene",
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


def ingest_smiles(smiles: str) -> dict[str, Any]:
    """P-1: parse a user SMILES claim. No DFT. Bind identity on InChIKey.

    A SMILES is a claim about a molecule; the InChIKey computed from it is
    the fact. Atom count and rotatable bonds are reported before any ORCA
    job because they predict cost -- 14 rotatable bonds is hours, not minutes.
    """
    claim = "" if smiles is None else str(smiles).strip()
    failed = {
        "success": False,
        "error_code": "invalid_smiles",
        "smiles": smiles,
        "dft_ran": False,
    }
    if not claim:
        return {**failed, "error": "SMILES is empty"}
    try:
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
    except ImportError as exc:
        raise CosmoDependencyError(
            f"rdkit is required to ingest SMILES: {exc}"
        ) from exc
    mol = Chem.MolFromSmiles(claim)
    if mol is None:
        return {**failed, "smiles": claim, "error": "SMILES did not parse"}
    inchikey = Chem.MolToInchiKey(mol)
    if not inchikey:
        return {**failed, "smiles": claim, "error": "could not compute InChIKey"}
    with_h = Chem.AddHs(mol)
    return {
        "success": True,
        "smiles": claim,
        "inchikey": inchikey,
        "n_atoms": int(with_h.GetNumAtoms()),
        "n_heavy_atoms": int(mol.GetNumAtoms()),
        "n_rotatable_bonds": int(rdMolDescriptors.CalcNumRotatableBonds(mol)),
        "dft_ran": False,
        "identity_basis": "inchikey",
    }


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


def evaluate_anchor_pairs(
    predicted: Mapping[str, float],
    pairs: Sequence[tuple[str, str, float]] | None = None,
) -> dict[str, Any]:
    """Score predicted delta logD against ``pairs`` (default: DEP ``ANCHOR_PAIRS``).

    The water-free pair MUST be among those passing. Water appears in three of
    the four anchors, and its sigma-profile is the best-validated and most
    idiosyncratic in COSMO-RS; passing only the water pairs is consistent with a
    correct water profile masking a broken solute, which is not evidence the
    pipeline works.
    """
    anchors = tuple(pairs) if pairs is not None else ANCHOR_PAIRS
    rows = []
    for solvent_a, solvent_b, ours in anchors:
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


def _fold_solvent(name: str) -> str:
    return " ".join(str(name or "").strip().casefold().split())


def canonical_solvent_key(name: str) -> str:
    """Identity alias only. Does not map a neighbour solvent onto a cousin."""
    folded = _fold_solvent(name)
    if folded in TABLE_SOLVENT_KEYS:
        return folded
    return SOLVENT_IDENTITY_ALIASES.get(folded, folded)


def _cosmotherm_basename_candidates(key: str) -> tuple[str, ...]:
    stem = solvent_file_stem(key)
    compact = stem.replace(" ", "").replace(",", "")
    names = (f"{stem}_c0.cosmo", f"{compact}_c0.cosmo", f"{stem}.cosmo")
    # Preserve order, drop duplicates that compacting can create.
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def cosmotherm_file_for(
    name: str, *, solvents_dir: str | Path | None = None,
) -> Path | None:
    """Exact filename in COSMObase, or None. No substring / neighbour match."""
    key = canonical_solvent_key(name)
    if not key:
        return None
    root = Path(solvents_dir) if solvents_dir is not None else DEFAULT_COSMOBASE_SOLVENTS_DIR
    if not root.is_dir():
        return None
    available = {path.name.casefold(): path for path in root.glob("*.cosmo")}
    for basename in _cosmotherm_basename_candidates(key):
        hit = available.get(basename.casefold())
        if hit is not None:
            return hit
    return None


def resolve_solvent(
    name: str, *, solvents_dir: str | Path | None = None,
) -> dict[str, Any]:
    """P-2: which routes can serve this solvent. No DFT. No silent fallback.

    A COSMObase hit is labelled 2002 / Turbomole and is never presented as 24a.
    ``xylene`` does not become ``o-xylene``. A missing name is
    ``solvent_not_available``, not a cousin.
    """
    query = "" if name is None else str(name).strip()
    key = canonical_solvent_key(query)
    orca = bool(key) and key in ORCA_ROUTE_SOLVENTS
    cosmotherm = cosmotherm_file_for(key, solvents_dir=solvents_dir) if key else None
    payload: dict[str, Any] = {
        "query": query,
        "solvent_key": key or query,
        "in_table": key in TABLE_SOLVENT_KEYS,
        "orca": {
            "available": orca,
            "route": ORCA_ROUTE,
            "parameterisation": ORCA_PARAMETERISATION if orca else None,
        },
        "cosmobase": {
            "available": cosmotherm is not None,
            "route": COSMOBASE_ROUTE,
            "parameterisation": (
                COSMOBASE_PARAMETERISATION if cosmotherm is not None else None
            ),
            "engine": COSMOBASE_ENGINE if cosmotherm is not None else None,
            "file": None if cosmotherm is None else cosmotherm.name,
        },
        "dft_ran": False,
    }
    if not orca and cosmotherm is None:
        payload["success"] = False
        payload["error_code"] = "solvent_not_available"
        payload["error"] = (
            "solvent is on neither the ORCA 24a route nor COSMObase 2002; "
            "no neighbour solvent will be substituted"
        )
        return payload
    payload["success"] = True
    payload["error_code"] = None
    return payload


def resolve_solvents(
    names: Sequence[str], *, solvents_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    return [resolve_solvent(name, solvents_dir=solvents_dir) for name in names]


def solvent_route_coverage(*, solvents_dir: str | Path | None = None) -> dict[str, Any]:
    """Coverage is a number over the 33 table solvents, not over COSMObase."""
    rows = [
        resolve_solvent(key, solvents_dir=solvents_dir)
        for key in sorted(TABLE_SOLVENT_KEYS)
    ]
    n_orca = sum(1 for row in rows if row["orca"]["available"])
    n_cosmobase = sum(1 for row in rows if row["cosmobase"]["available"])
    n_neither = sum(1 for row in rows if not row["success"])
    return {
        "n_table": len(TABLE_SOLVENT_KEYS),
        "n_orca": n_orca,
        "n_cosmobase": n_cosmobase,
        "n_neither": n_neither,
        "orca_parameterisation": ORCA_PARAMETERISATION,
        "cosmobase_parameterisation": COSMOBASE_PARAMETERISATION,
        "cosmobase_engine": COSMOBASE_ENGINE,
        "dft_ran": False,
        "unavailable": [
            row["solvent_key"] for row in rows if not row["success"]
        ],
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _anchor_pairs_for_inchikey(inchikey: str) -> tuple[tuple[str, str, float], ...] | None:
    if inchikey == DEP_INCHIKEY:
        return ANCHOR_PAIRS
    if inchikey == DBP_INCHIKEY:
        return DBP_ANCHOR_PAIRS
    if inchikey == BBP_INCHIKEY:
        return BBP_ANCHOR_PAIRS
    if inchikey == DEHP_INCHIKEY:
        return DEHP_ANCHOR_PAIRS
    return None


def orca_solvent_cosmo_path(
    key: str, *, artifacts_dir: str | Path | None = None,
) -> Path | None:
    stem = ORCA_SOLVENT_FILE_STEMS.get(key)
    if not stem:
        return None
    root = Path(artifacts_dir) if artifacts_dir is not None else DEFAULT_ORCA_ARTIFACTS_DIR
    for name in (f"{stem}_cosmo.solute.orcacosmo", f"{stem}_cosmo.solvent.orcacosmo"):
        path = root / name
        if path.is_file():
            return path
    return None


def orca_solute_cosmo_path(
    inchikey: str, *, artifacts_dir: str | Path | None = None,
) -> Path | None:
    stem = SOLUTE_ORCA_STEM_BY_INCHIKEY.get(inchikey)
    if not stem:
        return None
    root = Path(artifacts_dir) if artifacts_dir is not None else DEFAULT_ORCA_ARTIFACTS_DIR
    path = root / f"{stem}_cosmo.solute.orcacosmo"
    return path if path.is_file() else None


def cosmobase_solute_path(
    inchikey: str, *, solvents_dir: str | Path | None = None,
) -> Path | None:
    name = SOLUTE_COSMOBASE_FILE_BY_INCHIKEY.get(inchikey)
    if not name:
        return None
    root = Path(solvents_dir) if solvents_dir is not None else DEFAULT_COSMOBASE_SOLVENTS_DIR
    path = root / name
    return path if path.is_file() else None


def _pair_route_files(
    solvent_key: str,
    reference_key: str,
    inchikey: str,
    *,
    artifacts_dir: str | Path | None = None,
    solvents_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    """Pick one honest route. Never label a COSMObase file as 24a."""
    solute_orca = orca_solute_cosmo_path(inchikey, artifacts_dir=artifacts_dir)
    solvent_orca = orca_solvent_cosmo_path(solvent_key, artifacts_dir=artifacts_dir)
    reference_orca = orca_solvent_cosmo_path(reference_key, artifacts_dir=artifacts_dir)
    if solute_orca is not None and solvent_orca is not None and reference_orca is not None:
        return {
            "route": ORCA_ROUTE,
            "parameterisation": ORCA_PARAMETERISATION,
            "engine": None,
            "solute": solute_orca,
            "solvent": solvent_orca,
            "reference": reference_orca,
            "level_of_theory": (
                f"{DFT_FUNCTIONAL}/{DFT_BASIS_OPT}//{DFT_FUNCTIONAL}/{DFT_BASIS_SP}"
            ),
        }
    solute_cb = cosmobase_solute_path(inchikey, solvents_dir=solvents_dir)
    solvent_cb = cosmotherm_file_for(solvent_key, solvents_dir=solvents_dir)
    reference_cb = cosmotherm_file_for(reference_key, solvents_dir=solvents_dir)
    if solute_cb is not None and solvent_cb is not None and reference_cb is not None:
        return {
            "route": COSMOBASE_ROUTE,
            "parameterisation": COSMOBASE_PARAMETERISATION,
            "engine": COSMOBASE_ENGINE,
            "solute": solute_cb,
            "solvent": solvent_cb,
            "reference": reference_cb,
            "level_of_theory": f"{COSMOBASE_PARAMETERISATION}/{COSMOBASE_ENGINE}",
        }
    return None


def _delta_from_files(
    files: Mapping[str, Any],
    *,
    ln_gamma: Callable[..., float],
    temperature: float = STANDARD_T,
) -> dict[str, Any]:
    ln_solvent = ln_gamma(files["solute"], files["solvent"], temperature=temperature)
    ln_reference = ln_gamma(files["solute"], files["reference"], temperature=temperature)
    solvent_key = files.get("solvent_key")
    reference_key = files.get("reference_key")
    volume_a = MOLAR_VOLUMES_CM3.get(solvent_key) if solvent_key else None
    volume_b = MOLAR_VOLUMES_CM3.get(reference_key) if reference_key else None
    if volume_a is None or volume_b is None:
        volume_a = volume_b = None
        volume_correction = 0.0
        delta = delta_log_d(ln_solvent, ln_reference)
    else:
        mole_frac = delta_log_d(ln_solvent, ln_reference)
        delta = delta_log_d(
            ln_solvent, ln_reference, volume_a=volume_a, volume_b=volume_b,
        )
        volume_correction = delta - mole_frac
    return {
        "delta_logd": delta,
        "ln_gamma_solvent": ln_solvent,
        "ln_gamma_reference": ln_reference,
        "volume_correction": volume_correction,
        "temperature": temperature,
        "n_conformers": 1,
        "solute_file": str(files["solute"]),
        "solvent_file": str(files["solvent"]),
        "reference_file": str(files["reference"]),
        "solute_sha256": _sha256_file(Path(files["solute"])),
        "solvent_sha256": _sha256_file(Path(files["solvent"])),
        "reference_sha256": _sha256_file(Path(files["reference"])),
        "route": files["route"],
        "parameterisation": files["parameterisation"],
        "engine": files["engine"],
        "level_of_theory": files["level_of_theory"],
        "dft_ran": False,
    }


def _validation_status(
    inchikey: str,
    *,
    artifacts_dir: str | Path | None,
    solvents_dir: str | Path | None,
    ln_gamma: Callable[..., float],
) -> dict[str, Any]:
    """Molecule-level label. `validated` only when the regression actually passes."""
    pairs = _anchor_pairs_for_inchikey(inchikey)
    if pairs is None:
        return {
            "validation_status": VALIDATION_NO_BASIS,
            "validated_ok": False,
        }
    predicted: dict[str, float] = {}
    for solvent_a, solvent_b, _ours in pairs:
        files = _pair_route_files(
            solvent_a, solvent_b, inchikey,
            artifacts_dir=artifacts_dir, solvents_dir=solvents_dir,
        )
        if files is None:
            continue
        files = {**files, "solvent_key": solvent_a, "reference_key": solvent_b}
        try:
            computed = _delta_from_files(files, ln_gamma=ln_gamma)
        except CosmoDependencyError:
            continue
        predicted[f"{solvent_a}-{solvent_b}"] = computed["delta_logd"]
    scored = evaluate_anchor_pairs(predicted, pairs=pairs)
    xs = [row["ours"] for row in scored["rows"] if row["predicted"] is not None]
    ys = [row["predicted"] for row in scored["rows"] if row["predicted"] is not None]
    slope_contains_one = False
    if len(xs) >= 3:
        fit = linear_fit(xs, ys)
        slope_contains_one = fit.contains_unit_slope()
    if scored["accept"] and slope_contains_one:
        status = VALIDATION_VALIDATED
    else:
        status = VALIDATION_COMPUTED_UNVALIDATED
    return {
        "validation_status": status,
        "validated_ok": status == VALIDATION_VALIDATED,
        "anchor_evaluation": scored,
        "n_anchor_predicted": len(predicted),
    }


def compute_delta_logd(
    smiles: str,
    solvents: Sequence[str],
    *,
    reference: str | None = "water",
    absolute: bool = False,
    solvents_dir: str | Path | None = None,
    artifacts_dir: str | Path | None = None,
    ln_gamma: Callable[..., float] | None = None,
) -> dict[str, Any]:
    """P-3: Δ logD vs a named reference, with provenance. No new DFT.

    Default reference is water. A single-phase absolute is refused. A COSMObase
    number is labelled 2002 / Turbomole and is never presented as 24a.
    Existing .orcacosmo / .cosmo surfaces are reused; 1-octanol DFT is not started.
    """
    ingested = ingest_smiles(smiles)
    gamma_fn = ln_gamma if ln_gamma is not None else ln_gamma_infinite_dilution
    base = {
        "dft_ran": False,
        "reference": None if reference is None else str(reference).strip() or None,
        "results": [],
        "validated_ok": False,
        "validation_status": VALIDATION_NO_BASIS,
    }
    if not ingested["success"]:
        return {**base, **ingested}
    inchikey = ingested["inchikey"]
    ref_token = "" if reference is None else str(reference).strip().casefold()
    if absolute or ref_token in ABSOLUTE_REFUSE_TOKENS:
        return {
            **ingested,
            **base,
            "success": False,
            "error_code": "absolute_logp_refused",
            "error": (
                "a partition coefficient is a difference between two named phases; "
                "a single-solvent absolute is not served"
            ),
            "reference": None,
        }
    ref_name = str(reference).strip() if reference is not None else "water"
    if not ref_name:
        ref_name = "water"
    ref_row = resolve_solvent(ref_name, solvents_dir=solvents_dir)
    payload = {
        **ingested,
        "success": True,
        "error_code": None,
        "dft_ran": False,
        "reference": ref_row["solvent_key"],
        "reference_query": ref_row["query"],
        "results": [],
    }
    if not ref_row["success"]:
        payload["success"] = False
        payload["error_code"] = "solvent_not_available"
        payload["error"] = f"reference {ref_row['query']!r} is not available"
        payload["validation_status"] = VALIDATION_NO_BASIS
        payload["validated_ok"] = False
        return payload
    validation = _validation_status(
        inchikey,
        artifacts_dir=artifacts_dir,
        solvents_dir=solvents_dir,
        ln_gamma=gamma_fn,
    )
    payload["validation_status"] = validation["validation_status"]
    payload["validated_ok"] = validation["validated_ok"]
    if "anchor_evaluation" in validation:
        payload["anchor_evaluation"] = validation["anchor_evaluation"]

    for name in solvents:
        solvent_row = resolve_solvent(name, solvents_dir=solvents_dir)
        row: dict[str, Any] = {
            "query": solvent_row["query"],
            "solvent_key": solvent_row["solvent_key"],
            "reference": payload["reference"],
            "dft_ran": False,
            "validation_status": payload["validation_status"],
            "validated_ok": payload["validated_ok"],
        }
        if not solvent_row["success"]:
            row["success"] = False
            row["error_code"] = solvent_row["error_code"]
            row["error"] = solvent_row["error"]
            payload["results"].append(row)
            continue
        files = _pair_route_files(
            solvent_row["solvent_key"], payload["reference"], inchikey,
            artifacts_dir=artifacts_dir, solvents_dir=solvents_dir,
        )
        if files is None:
            solute_orca = orca_solute_cosmo_path(inchikey, artifacts_dir=artifacts_dir)
            solute_cb = cosmobase_solute_path(inchikey, solvents_dir=solvents_dir)
            row["success"] = False
            if solute_orca is None and solute_cb is None:
                row["error_code"] = "solute_cosmo_unavailable"
                row["error"] = (
                    "no local COSMO surface for this InChIKey; DFT is not started in P-3"
                )
            else:
                row["error_code"] = "route_unavailable"
                row["error"] = (
                    "solvent and reference are not both on the ORCA 24a route or "
                    "both on COSMObase 2002; routes are not mixed and 24a is not faked"
                )
            payload["results"].append(row)
            continue
        files = {
            **files,
            "solvent_key": solvent_row["solvent_key"],
            "reference_key": payload["reference"],
        }
        try:
            computed = _delta_from_files(files, ln_gamma=gamma_fn)
        except CosmoDependencyError as exc:
            row["success"] = False
            row["error_code"] = "cosmo_rs_unavailable"
            row["error"] = str(exc)
            payload["results"].append(row)
            continue
        if computed["parameterisation"] == ORCA_PARAMETERISATION:
            if computed["route"] != ORCA_ROUTE:
                row["success"] = False
                row["error_code"] = "route_label_conflict"
                payload["results"].append(row)
                continue
        if computed["route"] == COSMOBASE_ROUTE:
            if computed["parameterisation"] == ORCA_PARAMETERISATION:
                row["success"] = False
                row["error_code"] = "route_label_conflict"
                payload["results"].append(row)
                continue
        row["success"] = True
        row["error_code"] = None
        row.update(computed)
        payload["results"].append(row)
    return payload


def compute_delta_logd_batch(
    source: str | Path,
    solvents: Sequence[str],
    *,
    reference: str | None = "water",
    absolute: bool = False,
    solvents_dir: str | Path | None = None,
    artifacts_dir: str | Path | None = None,
    ln_gamma: Callable[..., float] | None = None,
) -> dict[str, Any]:
    """P-4: one SMILES per line. A failure does not abort the rest. No new DFT.

    Each non-empty line is computed independently via ``compute_delta_logd``.
    Empty lines are skipped and named. DFT is not started; concurrency is
    serialised at ``MAX_CONCURRENT_DFT`` (4) if a later slice launches ORCA.
    """
    path = Path(source)
    base = {
        "success": False,
        "error_code": None,
        "dft_ran": False,
        "max_concurrent_dft": MAX_CONCURRENT_DFT,
        "source": str(path),
        "n_lines": 0,
        "n_ok": 0,
        "n_refused": 0,
        "n_skipped": 0,
        "results": [],
        "skipped": [],
        "refused": [],
        "validation_status": VALIDATION_NO_BASIS,
        "validated_ok": False,
        "reference": None if reference is None else str(reference).strip() or None,
    }
    if not path.is_file():
        base["error_code"] = "batch_file_unavailable"
        base["error"] = f"SMILES file {path} is not a readable file"
        return base
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    base["n_lines"] = len(raw_lines)
    base["success"] = True
    for index, raw in enumerate(raw_lines, start=1):
        stripped = raw.strip()
        if not stripped:
            skip = {
                "line": index,
                "smiles": raw,
                "reason": "empty_line",
                "error_code": "empty_line",
                "dft_ran": False,
            }
            base["skipped"].append(skip)
            base["n_skipped"] += 1
            continue
        try:
            computed = compute_delta_logd(
                stripped,
                solvents,
                reference=reference,
                absolute=absolute,
                solvents_dir=solvents_dir,
                artifacts_dir=artifacts_dir,
                ln_gamma=ln_gamma,
            )
        except Exception as exc:
            computed = {
                "success": False,
                "error_code": "batch_line_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "smiles": stripped,
                "dft_ran": False,
                "results": [],
                "validation_status": VALIDATION_NO_BASIS,
                "validated_ok": False,
            }
        computed = dict(computed)
        computed["line"] = index
        computed["smiles"] = computed.get("smiles", stripped)
        computed["dft_ran"] = bool(computed.get("dft_ran"))
        base["results"].append(computed)
        if computed.get("dft_ran"):
            base["dft_ran"] = True
        if computed.get("success"):
            base["n_ok"] += 1
        else:
            base["n_refused"] += 1
            base["refused"].append({
                "line": index,
                "smiles": stripped,
                "error_code": computed.get("error_code"),
                "error": computed.get("error"),
                "dft_ran": False,
            })
    return base
