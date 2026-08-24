"""Polymer COSMO ingest (R-1) and polymer-as-solvent partition (R-2).

Never launches ORCA. Never writes the catalog partition column.
Never mixes 2002 polymer numbers with 24a. ``scripts/cosmo/run_orca_stage.py``
is not imported.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from dissolve import contaminants
from dissolve.cosmo_logp import (
    BOHR_TO_ANGSTROM,
    COSMOBASE_PARAMETERISATION,
    HARTREE_TO_KCAL,
    KNOWN_METHOD_FAILURES,
    Atom,
    CosmoError,
    boltzmann_combine,
    cosmotherm_file_for,
    delta_log_d,
    linear_fit,
    ln_gamma_infinite_dilution,
)

# Header ``area`` is bohr²; ``$segment_information`` areas are Å².
_BOHR2_TO_ANG2 = BOHR_TO_ANGSTROM ** 2
_AREA_RTOL = 1e-3

_Z_TO_SYMBOL = {
    1: "h", 6: "c", 7: "n", 8: "o", 9: "f",
    15: "p", 16: "s", 17: "cl", 35: "br", 53: "i",
}

#: Owner-measured oligomer atom counts (identity samples, not a DFT launch).
DFT_JOB_ORDER: tuple[tuple[str, int], ...] = (
    ("pe", 38),
    ("pvc", 44),
    ("pvdf", 44),
    ("evoh", 48),
    ("pp", 62),
    ("nylon6", 84),
    ("pet", 91),
    ("ps", 104),
)

ENGINE_GAUSSIAN_CONVERTED = "gaussian_converted"
ROUTE_POLYMER_COSMO = "polymer_cosmo"
GAUSSIAN_UNCONVERTED = "gaussian_cosmo_unconverted"
MCOS_NOT_SPLIT = "mcos_not_split"
SURFACE_DISCARDED = "surface_discarded"
WRONG_PHASE_ROLE = "wrong_phase_role"
SOLVENT_NOT_AVAILABLE = "solvent_not_available"
PARAMETERISATION_24A_REFUSED = "parameterisation_24a_refused"
TURBOMOLE_2002_PARAMETERIZATION = "default_turbomole"
QC_ORIGIN_GAUSSIAN_COSMO = "gaussian_cosmo"
DEP_CATALOG_KEY = "diethyl phthalate (dep)"
#: Water-referenced control (drop chloroform only). Not a target to beat.
WATER_REFERENCED_CONTROL = {
    "n": 30,
    "slope": 0.928,
    "r_squared": 0.909,
    "residual_sd": 0.257,
    "chloroform": "dropped",
}

POLYMER_SOURCE_DIR = Path("/home/aaltamimi2/polymers_cosmo")
PE_MCOS = POLYMER_SOURCE_DIR / "pe_mcos" / "config_1010.mcos"
PVC_MCOS = POLYMER_SOURCE_DIR / "pvc_mcos" / "config_9400.mcos"
PET_MCOS = POLYMER_SOURCE_DIR / "pet_mcos" / "config_16910.mcos"
PE_DODECANE_INCHIKEY = "SNRUBQQJIBEYMU-UHFFFAOYSA-N"

_DIR_BY_KEY = {
    "pe": "pe_mcos",
    "pvc": "pvc_mcos",
    "pvdf": "pvdf_mcos",
    "evoh": "evoh_mcos",
    "pp": "pp_mcos",
    "nylon6": "nylon6_mcos",
    "pet": "pet_mcos",
    "ps": "ps_mcos",
}


class PolymerCosmoError(CosmoError):
    """Named refuse for polymer ingest."""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code or "polymer_cosmo_error"


@dataclass(frozen=True)
class McosConformer:
    """One Gaussian COSMO body from an ``.mcos`` (or a bare Gaussian ``.cosmo``).

    ``w_vector`` / ``atom_mask`` is the ``w={...}`` bit-string, kept verbatim.
    On these files it is a 0/1 mask of length ``n_atoms``, identical across a
    polymer's files — not a Boltzmann vector over conformers. Boltzmann
    weights, if used, come from ``energy_hartree`` across files.
    """

    name: str
    source: Path
    w_vector: str
    w_bits: tuple[int, ...]
    gaussian_body: str
    nps: int
    area: float
    volume: float
    n_atoms: int
    energy_hartree: float | None

    @property
    def atom_mask(self) -> str:
        return self.w_vector


@dataclass(frozen=True)
class ConvertedCosmo:
    path: Path
    n_atoms: int
    n_segments: int
    nps: int
    header_area: float
    segment_area_sum: float
    area_angstrom2: float
    parameterisation: str
    engine: str
    qc_origin: str
    atom_mask: str
    energy_hartree: float | None = None


def _w_bits(raw: str) -> tuple[int, ...]:
    return tuple(1 if ch == "1" else 0 for ch in raw)


def is_gaussian_cosmo_text(text: str) -> bool:
    return "Gaussian COSMO output" in text or text.lstrip().startswith("Gaussian")


def split_mcos(path: str | Path) -> list[McosConformer]:
    """Split a COSMOtherm ``.mcos`` wrapper. Start+end is one conformer, not two."""
    src = Path(path)
    text = src.read_text(errors="replace")
    first = text.splitlines()[0] if text else ""
    w_match = re.search(r"w=\{([01]*)\}", first)
    w_vector = w_match.group(1) if w_match else ""
    blocks = re.findall(
        r"\$cosmofile;([^;]+);start;\n(.*?)\$cosmofile;\1;end;",
        text,
        flags=re.S,
    )
    if not blocks:
        if is_gaussian_cosmo_text(text):
            return [_conformer_from_gaussian(src, src.stem, text, w_vector)]
        raise PolymerCosmoError(
            f"no Gaussian COSMO body in {src}",
            error_code=MCOS_NOT_SPLIT if src.suffix.lower() == ".mcos" else None,
        )
    return [
        _conformer_from_gaussian(src, name.strip(), body, w_vector)
        for name, body in blocks
    ]


def _cosmo_data_floats(body: str) -> tuple[int, float, float]:
    nps_m = re.search(r"nps\s*=\s*(\d+)", body)
    area_m = re.search(r"area\s*=\s*([0-9.+-eE]+)", body)
    vol_m = re.search(r"volume\s*=\s*([0-9.+-eE]+)", body)
    if not nps_m or not area_m:
        raise PolymerCosmoError("Gaussian body missing nps/area in $cosmo_data")
    nps = int(nps_m.group(1))
    area = float(area_m.group(1))
    volume = float(vol_m.group(1)) if vol_m else float("nan")
    return nps, area, volume


def _energy_hartree(body: str) -> float | None:
    hit = re.search(
        r"Total energy corrected \[a\.u\.\]\s*=\s*([0-9.+-eE]+)",
        body,
    )
    return float(hit.group(1)) if hit else None


def _block_lines(body: str, header: str) -> list[str]:
    lines = body.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip().startswith(header))
    except StopIteration:
        return []
    out: list[str] = []
    for ln in lines[start + 1:]:
        if ln.strip().startswith("$"):
            break
        out.append(ln)
    return out


def _is_data_row(ln: str) -> bool:
    stripped = ln.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("!"):
        return False
    parts = stripped.split()
    token = parts[0].lstrip("+-").replace(".", "", 1)
    return len(parts) >= 5 and token.isdigit()


def _conformer_from_gaussian(
    src: Path, name: str, body: str, w_vector: str,
) -> McosConformer:
    nps, area, volume = _cosmo_data_floats(body)
    n_atoms = sum(1 for ln in _block_lines(body, "$coord_rad") if _is_data_row(ln))
    return McosConformer(
        name=name,
        source=src,
        w_vector=w_vector,
        w_bits=_w_bits(w_vector),
        gaussian_body=body if body.endswith("\n") else body + "\n",
        nps=nps,
        area=area,
        volume=volume,
        n_atoms=n_atoms,
        energy_hartree=_energy_hartree(body),
    )


def parse_segment_information(body: str) -> list[dict[str, Any]]:
    """Read the full Gaussian ``$segment_information`` block. Not ``$coord_rad``."""
    rows: list[dict[str, Any]] = []
    for ln in _block_lines(body, "$segment_information"):
        if not _is_data_row(ln):
            continue
        parts = ln.split()
        if len(parts) < 9:
            continue
        rows.append({
            "n": int(float(parts[0])),
            "atom": int(float(parts[1])),
            "x": float(parts[2]),
            "y": float(parts[3]),
            "z": float(parts[4]),
            "charge": float(parts[5]),
            "area": float(parts[6]),
            "sigma": float(parts[7]),
            "potential": float(parts[8]),
        })
    return rows


def parse_coord_rad_gaussian(body: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ln in _block_lines(body, "$coord_rad"):
        if not _is_data_row(ln):
            continue
        parts = ln.split()
        if len(parts) < 6:
            continue
        z_tok = parts[4]
        z = int(float(z_tok)) if z_tok.replace(".", "", 1).isdigit() else None
        symbol = _Z_TO_SYMBOL.get(z, z_tok.lower()) if z is not None else z_tok.lower()
        rows.append({
            "n": int(float(parts[0])),
            "x": float(parts[1]),
            "y": float(parts[2]),
            "z": float(parts[3]),
            "znum": z,
            "symbol": symbol,
            "radius": float(parts[5]),
        })
    return rows


def cavity_area_from_segments(segments: Sequence[Mapping[str, Any]]) -> float:
    return float(sum(float(row["area"]) for row in segments))


def area_matches_header(header_area: float, segment_area_sum: float) -> bool:
    """Gaussian header area is bohr²; segment areas are Å²."""
    expected = header_area * _BOHR2_TO_ANG2
    if expected == 0:
        return False
    return abs(segment_area_sum - expected) / expected <= _AREA_RTOL


def _assert_identity(
    n_atoms: int,
    nps: int,
    segments: Sequence[Mapping[str, Any]],
    header_area: float,
) -> float:
    n_seg = len(segments)
    if n_seg != nps:
        raise PolymerCosmoError(
            f"segment count {n_seg} != nps {nps}; a $coord_rad-only read is refused",
            error_code=SURFACE_DISCARDED,
        )
    if n_seg == n_atoms and nps != n_atoms:
        raise PolymerCosmoError(
            f"n_segments == n_atoms == {n_atoms} while nps={nps}; "
            "the surface was discarded",
            error_code=SURFACE_DISCARDED,
        )
    area_sum = cavity_area_from_segments(segments)
    if not area_matches_header(header_area, area_sum):
        raise PolymerCosmoError(
            f"cavity area from segments {area_sum:.4f} Å² does not match "
            f"header {header_area} bohr² ({header_area * _BOHR2_TO_ANG2:.4f} Å²)",
            error_code=SURFACE_DISCARDED,
        )
    return area_sum


def gaussian_body_to_turbomole(body: str) -> str:
    """Emit a Turbomole-shaped ``.cosmo`` that keeps every surface segment.

    ``SigmaProfileParser`` requires exactly 4- or 6-field ``$coord_rad`` rows
    and 9-field ``$segment_information`` rows. Gaussian ``$coord_rad`` is 9
    fields (per-atom area/charge/sigma); copying those through makes the
    Turbomole reader skip every atom.
    """
    nps, area, volume = _cosmo_data_floats(body)
    atoms = parse_coord_rad_gaussian(body)
    segments = parse_segment_information(body)
    if not segments:
        raise PolymerCosmoError(
            "no $segment_information rows; refusing a $coord_rad stand-in",
            error_code=SURFACE_DISCARDED,
        )
    _assert_identity(len(atoms), nps, segments, area)
    lines = [
        "$info",
        "prog.: gaussian_converted;A-mat. vers.:1.0;cav. vers.:1.0;"
        "gaussian;converted;from_gaussian",
        "$cosmo_data",
        f"  nps=  {nps}",
        f"  area=  {area}",
        f"  volume=      {volume}",
        "$coord_rad",
        "#atom   x                  y                  z             element  radius [A]",
    ]
    for atom in atoms:
        lines.append(
            f"{atom['n']:5d}{atom['x']:19.11f}{atom['y']:19.11f}"
            f"{atom['z']:19.11f}  {atom['symbol']:<2}{atom['radius']:10.5f}"
        )
    # SigmaProfileParser ends $coord_rad by consuming the next $ header.
    # COSMObase files put $coord_car there; without it, $segment_information
    # is swallowed and the parser reports n_segments == 0.
    lines.append("$coord_car")
    lines.append("!BIOSYM archive 3")
    lines.append("PBC=OFF")
    lines.append("coordinates from GAUSSIAN/COSMO converted")
    lines.append("!DATE")
    try:
        car_atoms = parse_coord_car_atoms(body)
    except PolymerCosmoError:
        car_atoms = []
    for i, atom in enumerate(car_atoms, 1):
        lines.append(
            f"{atom.element}{i:<3}{atom.x:15.9f}{atom.y:15.9f}{atom.z:15.9f} "
            f"GAUS 1      {atom.element.lower():<2}      {atom.element:<2}   0.0000"
        )
    lines.append("end")
    lines.append("end")
    lines.append("$segment_information")
    lines.append("# n atom x y z charge area sigma potential")
    for seg in segments:
        # Explicit spaces: SigmaProfileParser requires split() == 9.
        # Packed widths overflow and glue fields, which the parser then skips.
        lines.append(
            f"{seg['n']:6d} {seg['atom']:4d} "
            f"{seg['x']:16.9f} {seg['y']:14.9f} {seg['z']:14.9f} "
            f"{seg['charge']:14.9f} {seg['area']:14.9f} "
            f"{seg['sigma']:14.9f} {seg['potential']:14.9f}"
        )
    lines.append("$end")
    return "\n".join(lines) + "\n"


def refuse_unconverted(path: str | Path) -> None:
    src = Path(path)
    text = src.read_text(errors="replace")
    if src.suffix.lower() == ".mcos":
        raise PolymerCosmoError(
            f"{src} is an unsplit .mcos; convert first",
            error_code=MCOS_NOT_SPLIT,
        )
    if is_gaussian_cosmo_text(text):
        raise PolymerCosmoError(
            f"{src} is unconverted Gaussian COSMO",
            error_code=GAUSSIAN_UNCONVERTED,
        )


def convert_gaussian_cosmo(
    source: str | Path, dest: str | Path | None = None,
) -> ConvertedCosmo:
    """Convert one ``.mcos`` / Gaussian ``.cosmo`` to Turbomole-shaped COSMO."""
    src = Path(source)
    recs = split_mcos(src)
    if len(recs) != 1:
        raise PolymerCosmoError(
            f"{src} split into {len(recs)} conformers; convert one body at a time"
        )
    rec = recs[0]
    atoms = parse_coord_rad_gaussian(rec.gaussian_body)
    segments = parse_segment_information(rec.gaussian_body)
    area_sum = _assert_identity(len(atoms), rec.nps, segments, rec.area)
    text = gaussian_body_to_turbomole(rec.gaussian_body)
    out = Path(dest) if dest is not None else src.with_suffix(".turbomole.cosmo")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return ConvertedCosmo(
        path=out,
        n_atoms=len(atoms),
        n_segments=len(segments),
        nps=rec.nps,
        header_area=rec.area,
        segment_area_sum=area_sum,
        area_angstrom2=rec.area * _BOHR2_TO_ANG2,
        parameterisation=COSMOBASE_PARAMETERISATION,
        engine=ENGINE_GAUSSIAN_CONVERTED,
        qc_origin=QC_ORIGIN_GAUSSIAN_COSMO,
        atom_mask=rec.w_vector,
        energy_hartree=rec.energy_hartree,
    )


def parse_coord_car_atoms(body: str) -> list[Atom]:
    """BIOSYM ``$coord_car`` block: element symbols, angstrom."""
    atoms: list[Atom] = []
    for ln in _block_lines(body, "$coord_car"):
        stripped = ln.strip()
        if not stripped or stripped in {"end", "END"}:
            continue
        if stripped.startswith("!") or stripped.startswith("#"):
            continue
        low = stripped.lower()
        if "coordinates from" in low or "biosym" in low or stripped.startswith("PBC="):
            continue
        parts = stripped.split()
        if len(parts) < 4:
            continue
        token = parts[0]
        elem = "".join(ch for ch in token if ch.isalpha())
        if not elem:
            continue
        try:
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        except (TypeError, ValueError):
            continue
        atoms.append(Atom(elem.capitalize(), x, y, z))
    if not atoms:
        raise PolymerCosmoError("no atoms in $coord_car")
    return atoms


def first_source_file(polymer_key: str) -> Path:
    folder = POLYMER_SOURCE_DIR / _DIR_BY_KEY[polymer_key]
    if polymer_key == "pvc":
        named = folder / "config_9400.mcos"
        if named.is_file():
            return named
    if polymer_key == "pe":
        if PE_MCOS.is_file():
            return PE_MCOS
    if polymer_key == "pet":
        if PET_MCOS.is_file():
            return PET_MCOS
    mcos = sorted(folder.glob("*.mcos"))
    if mcos:
        return mcos[0]
    cosmo = sorted(p for p in folder.glob("*.cosmo") if "old" not in p.parts)
    if not cosmo:
        raise PolymerCosmoError(f"no COSMO source for {polymer_key}")
    return cosmo[0]


def _default_ln_gamma(solute: Path, solvent: Path, **kwargs: Any) -> float:
    token = next(
        (str(value) for key, value in kwargs.items() if "param" in key),
        TURBOMOLE_2002_PARAMETERIZATION,
    )
    _refuse_24a_parameterization(token)
    return ln_gamma_infinite_dilution(solute, solvent, parameterization=token)


def _as_paths(value: str | Path | Sequence[str | Path]) -> list[Path]:
    if isinstance(value, (str, Path)):
        return [Path(value)]
    return [Path(item) for item in value]


def _refuse_24a_parameterization(parameterization: str) -> None:
    token = str(parameterization or "").strip().casefold()
    if "24a" in token or token in {"opencosmors24a", "orca"}:
        raise PolymerCosmoError(
            "polymer path is 2002 / default_turbomole; 24a is refused",
            error_code=PARAMETERISATION_24A_REFUSED,
        )


def _ensemble_ln_gamma(
    solute: Path,
    solvent_files: Sequence[Path],
    energies_hartree: Sequence[float | None] | None,
    ln_gamma: Callable[..., float],
    parameterization: str,
) -> float:
    values = [
        float(
            ln_gamma(
                solute,
                path,
                parameterization=parameterization,
            )
        )
        for path in solvent_files
    ]
    if len(values) == 1:
        return values[0]
    if energies_hartree is None or all(item is None for item in energies_hartree):
        relative_kcal = [0.0] * len(values)
    else:
        kcal = [
            0.0 if item is None else float(item) * HARTREE_TO_KCAL
            for item in energies_hartree
        ]
        floor = min(kcal)
        relative_kcal = [item - floor for item in kcal]
    return boltzmann_combine(values, relative_kcal)


def _served_labels(polymer_name: str, solvent_key: str) -> dict[str, Any]:
    return {
        "polymer_name": polymer_name,
        "reference_phase": polymer_name,
        "solvent_key": solvent_key,
        "parameterisation": COSMOBASE_PARAMETERISATION,
        "route": ROUTE_POLYMER_COSMO,
        "engine": ENGINE_GAUSSIAN_CONVERTED,
        "qc_origin": QC_ORIGIN_GAUSSIAN_COSMO,
        "dft_ran": False,
        "basis": "mole_fraction",
    }


def compute_log10_p_solvent_over_polymer(
    solute_cosmo: str | Path,
    solvent_cosmo: str | Path,
    polymer_cosmo: str | Path | Sequence[str | Path],
    *,
    polymer_name: str,
    solvent_key: str,
    polymer_role: str = "solvent",
    solute_role: str = "contaminant",
    ln_gamma: Callable[..., float] | None = None,
    energies_hartree: Sequence[float | None] | None = None,
    parameterization: str = TURBOMOLE_2002_PARAMETERIZATION,
) -> dict[str, Any]:
    """log10 P(solvent/polymer) for a contaminant solute. Polymer is the solvent."""
    if polymer_role != "solvent" or solute_role != "contaminant":
        raise PolymerCosmoError(
            "polymer is the solvent phase; the contaminant is the solute",
            error_code=WRONG_PHASE_ROLE,
        )
    _refuse_24a_parameterization(parameterization)
    polymer_files = _as_paths(polymer_cosmo)
    if not polymer_files:
        raise PolymerCosmoError("no polymer COSMO files")
    gamma_fn = ln_gamma if ln_gamma is not None else _default_ln_gamma
    ln_in_solvent = _ensemble_ln_gamma(
        Path(solute_cosmo),
        _as_paths(solvent_cosmo),
        None,
        gamma_fn,
        parameterization,
    )
    ln_in_polymer = _ensemble_ln_gamma(
        Path(solute_cosmo),
        polymer_files,
        energies_hartree,
        gamma_fn,
        parameterization,
    )
    value = delta_log_d(ln_in_solvent, ln_in_polymer)
    row = _served_labels(polymer_name, solvent_key)
    row["success"] = True
    row["error_code"] = None
    row["log10_p_solvent_over_polymer"] = value
    row["ln_gamma_solvent"] = ln_in_solvent
    row["ln_gamma_polymer"] = ln_in_polymer
    return row


def format_served_row(row: Mapping[str, Any]) -> str:
    """Human line that names the polymer and the reference phase."""
    return (
        f"polymer={row.get('polymer_name')} "
        f"reference_phase={row.get('reference_phase')} "
        f"solvent={row.get('solvent_key')} "
        f"log10_P={row.get('log10_p_solvent_over_polymer')} "
        f"parameterisation={row.get('parameterisation')} "
        f"route={row.get('route')} "
        f"engine={row.get('engine')} "
        f"dft_ran={row.get('dft_ran')}"
    )


def _dep_catalog_rows() -> list[tuple[str, float]]:
    connection = contaminants._connection()
    return [
        (str(solvent), float(value))
        for solvent, value in connection.execute(
            "SELECT solvent_key, logd FROM logd "
            "WHERE contaminant_key = ? ORDER BY solvent_key",
            [DEP_CATALOG_KEY],
        ).fetchall()
        if value is not None and math.isfinite(float(value))
    ]


def _intercept_ci95(
    x: Sequence[float], intercept: float, slope_stderr: float,
) -> tuple[float, float, float]:
    n = len(x)
    mean_x = sum(x) / n
    sxx = sum((xi - mean_x) ** 2 for xi in x)
    intercept_stderr = float(slope_stderr) * math.sqrt(mean_x * mean_x + sxx / n)
    half = 1.96 * intercept_stderr
    return intercept_stderr, intercept - half, intercept + half


def intercept_test_dep(
    polymer_name: str,
    polymer_cosmo: str | Path | Sequence[str | Path],
    *,
    solute_cosmo: str | Path | None = None,
    ln_gamma: Callable[..., float] | None = None,
    energies_hartree: Sequence[float | None] | None = None,
    parameterization: str = TURBOMOLE_2002_PARAMETERIZATION,
    catalog_rows: Sequence[tuple[str, float]] | None = None,
    solvents_dir: str | Path | None = None,
    solvent_file_for: Callable[[str], Path | None] | None = None,
) -> dict[str, Any]:
    """OLS of catalog partition (y) on computed log10 P(solvent/polymer) (x).

    Chloroform handling IDENT the water-referenced control: drop chloroform
    only. Do not drop a second solvent. Do not write the catalog column.
    Do not apply the removability threshold in this SHA.
    """
    _refuse_24a_parameterization(parameterization)
    if solute_cosmo is None:
        raise PolymerCosmoError("DEP solute COSMO path is required")
    rows_in = list(catalog_rows) if catalog_rows is not None else _dep_catalog_rows()
    dropped: list[dict[str, Any]] = []
    fit_x: list[float] = []
    fit_y: list[float] = []
    served: list[dict[str, Any]] = []
    for solvent_key, catalog_value in rows_in:
        folded = str(solvent_key).strip().casefold()
        if folded in KNOWN_METHOD_FAILURES:
            dropped.append({
                "solvent_key": solvent_key,
                "reason": KNOWN_METHOD_FAILURES[folded],
                "dropped": True,
            })
            continue
        if solvent_file_for is not None:
            solvent_path = solvent_file_for(solvent_key)
        else:
            solvent_path = cosmotherm_file_for(
                solvent_key, solvents_dir=solvents_dir,
            )
        if solvent_path is None:
            served.append({
                **_served_labels(polymer_name, solvent_key),
                "success": False,
                "error_code": SOLVENT_NOT_AVAILABLE,
                "log10_p_solvent_over_polymer": None,
                "catalog_value": catalog_value,
                "in_fit": False,
            })
            continue
        computed = compute_log10_p_solvent_over_polymer(
            solute_cosmo,
            solvent_path,
            polymer_cosmo,
            polymer_name=polymer_name,
            solvent_key=solvent_key,
            ln_gamma=ln_gamma,
            energies_hartree=energies_hartree,
            parameterization=parameterization,
        )
        computed["catalog_value"] = catalog_value
        computed["in_fit"] = True
        served.append(computed)
        fit_x.append(float(computed["log10_p_solvent_over_polymer"]))
        fit_y.append(float(catalog_value))
    if len(fit_x) < 3:
        raise PolymerCosmoError(
            f"need at least 3 solvent points for the intercept test; got {len(fit_x)}"
        )
    fit = linear_fit(fit_x, fit_y)
    intercept_stderr, ci_low, ci_high = _intercept_ci95(
        fit_x, fit.intercept, fit.slope_stderr,
    )
    consistent = ci_low <= 0.0 <= ci_high
    return {
        "polymer_name": polymer_name,
        "reference_phase": polymer_name,
        "solute": "DEP",
        "parameterisation": COSMOBASE_PARAMETERISATION,
        "route": ROUTE_POLYMER_COSMO,
        "engine": ENGINE_GAUSSIAN_CONVERTED,
        "qc_origin": QC_ORIGIN_GAUSSIAN_COSMO,
        "dft_ran": False,
        "n": fit.n,
        "slope": fit.slope,
        "intercept": fit.intercept,
        "r_squared": fit.r_squared,
        "residual_sd": fit.residual_sd,
        "intercept_stderr": intercept_stderr,
        "intercept_ci95": (ci_low, ci_high),
        "intercept_consistent_with_zero": consistent,
        "water_referenced_control": dict(WATER_REFERENCED_CONTROL),
        "chloroform_dropped": True,
        "second_solvent_dropped": False,
        "removability_threshold_applied": False,
        "catalog_column_written": False,
        "rows": served,
        "dropped": dropped,
    }
