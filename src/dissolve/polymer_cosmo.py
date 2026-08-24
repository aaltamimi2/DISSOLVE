"""Polymer COSMO ingest (R-1): split, convert, identity-check.

Never launches ORCA. Never writes the catalog partition column. R-2 intercept
/ ``log10 P(solvent/polymer)`` is a later SHA. ``scripts/cosmo/run_orca_stage.py``
is not imported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from dissolve.cosmo_logp import (
    BOHR_TO_ANGSTROM,
    COSMOBASE_PARAMETERISATION,
    Atom,
    CosmoError,
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
        qc_origin="gaussian_cosmo",
        atom_mask=rec.w_vector,
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
