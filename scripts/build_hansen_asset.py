#!/usr/bin/env python3
"""Build the auditable Hansen RED asset used by ``solubility_query``.

The HSP component records already live in ``analysis.json.gz`` and are not
copied here.  This asset contains only the corrected dense RED grid, its
isolated superseded calculation, and reviewed cross-catalog identity maps.
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Iterable

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dissolve import thermodynamics as thermo  # noqa: E402


DEFAULT_SOURCE_DIR = Path("/home/aaltamimi2/HSP-ML-Models/data-agent")
DEFAULT_OUTPUT = REPO_ROOT / "src/dissolve/data/hansen.duckdb"
ANALYSIS_ASSET = REPO_ROOT / "src/dissolve/data/analysis.json.gz"
SOLVENT_BRIDGE = REPO_ROOT / "src/dissolve/data/Solvent_Data.csv"
ANALYSIS_ASSET_SHA256 = "6fa37a21e4ff492654172883ca3a941f7820b704f899ad86839d1fe9e17ee9a0"
SOLVENT_BRIDGE_SHA256 = "c9dfd6556ec3f755157b951408a852df44b7cbdc18dea02c36266000d0c9bd49"

SOURCE_FILES = {
    "POLYMER-HSPs-FINAL.csv": (
        "de72adc57925945c69fc6baf8763baadcdb73aafc5aaa59e6ce9e9075914e124",
        466,
    ),
    "SOLVENT-HSPs-FINAL.csv": (
        "903166c502f6c874a4e1733b919cf566b50d655fa75a27530bdc94df83467b33",
        1_180,
    ),
    "RED_values_complete_CORRECTED.csv": (
        "f7ca6c5be942c541d3949b604ba542cc868cc9cd2e19a8367ccb0010bd6377b3",
        549_880,
    ),
}
EXPECTED_CURATED_TARGETS = {
    "EVOH", "HDPE", "LDPE", "NYLON6", "NYLON66",
    "PC", "PES", "PP", "PS", "PVC",
}
EXPECTED_TYPE_TARGETS = {"PS", "PU", "PVC"}
EXPECTED_POLYMER_TARGETS = EXPECTED_CURATED_TARGETS | {"PU"}
EXPECTED_SOLVENT_ROUTE_COUNTS = {"exact": 121, "normalized": 227, "bridge": 32}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _validate_sources(source_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name, (expected_hash, expected_rows) in SOURCE_FILES.items():
        path = source_dir / name
        if not path.is_file():
            raise RuntimeError(f"required source is absent: {path}")
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"{name} sha256 {actual_hash} does not match reviewed source {expected_hash}"
            )
        rows = _read_csv(path)
        if len(rows) != expected_rows:
            raise RuntimeError(f"{name} has {len(rows):,} rows; expected {expected_rows:,}")
        paths[name] = path
    for path, expected_hash in (
        (ANALYSIS_ASSET, ANALYSIS_ASSET_SHA256),
        (SOLVENT_BRIDGE, SOLVENT_BRIDGE_SHA256),
    ):
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"{path.name} sha256 {actual_hash} does not match reviewed source "
                f"{expected_hash}"
            )
    return paths


def _number(value: object) -> Decimal:
    return Decimal(str(value).strip())


def _verify_component_home(
    polymer_rows: list[dict[str, str]], solvent_rows: list[dict[str, str]],
) -> None:
    """Prove that copying HSP component tables into DuckDB would be redundant."""
    with gzip.open(ANALYSIS_ASSET, "rt", encoding="utf-8") as handle:
        hsp = json.load(handle)["hsp"]

    stored_polymers = hsp["polymer_source_records"]
    stored_solvents = hsp["solvent_source_records"]
    if len(stored_polymers) != len(polymer_rows):
        raise RuntimeError("polymer component source count diverges from analysis.json.gz")
    if len(stored_solvents) != len(solvent_rows):
        raise RuntimeError("solvent component source count diverges from analysis.json.gz")

    polymer_fields = {
        "Dispersion": "dispersion",
        "Polar": "polar",
        "Hydrogen Bonding": "hydrogen_bonding",
        "Interaction Radius": "interaction_radius",
    }
    solvent_fields = {
        "Dispersion": "dispersion",
        "Polarity": "polar",
        "Hydrogen Bonding": "hydrogen_bonding",
    }
    for row_id, (csv_row, stored) in enumerate(zip(polymer_rows, stored_polymers)):
        if stored["source_record_id"] != row_id or stored["raw_label"] != csv_row["Polymer"]:
            raise RuntimeError(f"polymer identity diverges at source row {row_id}")
        for csv_field, stored_field in polymer_fields.items():
            if _number(csv_row[csv_field]) != _number(stored[stored_field]):
                raise RuntimeError(
                    f"polymer {stored_field} diverges at source row {row_id}"
                )
    for row_id, (csv_row, stored) in enumerate(zip(solvent_rows, stored_solvents)):
        if stored["source_record_id"] != row_id or stored["raw_label"] != csv_row["Name"]:
            raise RuntimeError(f"solvent identity diverges at source row {row_id}")
        for csv_field, stored_field in solvent_fields.items():
            if _number(csv_row[csv_field]) != _number(stored[stored_field]):
                raise RuntimeError(
                    f"solvent {stored_field} diverges at source row {row_id}"
                )


def _alphanumeric(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _single_target_index(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    grouped: dict[str, set[str]] = {}
    for label, target in items:
        grouped.setdefault(_alphanumeric(label), set()).add(target)
    return {
        key: next(iter(targets))
        for key, targets in grouped.items()
        if len(targets) == 1
    }


def _solvent_identity_map(
    solvent_rows: list[dict[str, str]],
) -> list[tuple[str, str, str]]:
    """Build only reviewed, collision-free solvent identity rungs."""
    labels = sorted({row["Name"].strip() for row in solvent_rows})
    grid_solvents = sorted(thermo.get_available_solvents())
    bindings: dict[str, tuple[str, str]] = {}

    for label in labels:
        target = thermo.resolve_solvent(label)
        if target is not None:
            bindings[label] = (target, "exact")

    hsp_keys: dict[str, set[str]] = {}
    grid_keys: dict[str, set[str]] = {}
    for label in labels:
        hsp_keys.setdefault(_alphanumeric(label), set()).add(label)
    for target in grid_solvents:
        grid_keys.setdefault(_alphanumeric(target), set()).add(target)
    accepted_collisions = [
        key for key in hsp_keys.keys() & grid_keys.keys()
        if len(hsp_keys[key]) > 1 or len(grid_keys[key]) > 1
    ]
    if accepted_collisions:
        raise RuntimeError(
            f"alphanumeric solvent matching is no longer collision-free: "
            f"{accepted_collisions[:5]}"
        )
    for label in labels:
        key = _alphanumeric(label)
        if label not in bindings and key in grid_keys:
            bindings[label] = (next(iter(grid_keys[key])), "normalized")

    bridge_rows = _read_csv(SOLVENT_BRIDGE)
    bridge_pairs: list[tuple[str, str]] = []
    unresolved_bridge: list[str] = []
    for row in bridge_rows:
        target = thermo.resolve_solvent(row["Solvent name in cosmobase"])
        if target is None:
            unresolved_bridge.append(row["Solvent name in cosmobase"])
        else:
            bridge_pairs.append((row["Solvent name"], target))
    if unresolved_bridge:
        raise RuntimeError(
            f"{len(unresolved_bridge)} Solvent_Data bridge rows no longer resolve: "
            f"{unresolved_bridge[:5]}"
        )
    if len(bridge_rows) != 1_007 or len({target for _, target in bridge_pairs}) != 990:
        raise RuntimeError(
            f"Solvent_Data bridge moved: {len(bridge_rows)} rows resolve to "
            f"{len({target for _, target in bridge_pairs})} grid solvents"
        )
    bridge_index = _single_target_index(bridge_pairs)
    for label in labels:
        key = _alphanumeric(label)
        if label not in bindings and key in bridge_index:
            bindings[label] = (bridge_index[key], "bridge")

    route_counts: dict[str, int] = {}
    for _, route in bindings.values():
        route_counts[route] = route_counts.get(route, 0) + 1
    targets = {target for target, _ in bindings.values()}
    if route_counts != EXPECTED_SOLVENT_ROUTE_COUNTS or len(targets) != 380:
        raise RuntimeError(
            f"solvent ladder moved: routes={route_counts}, distinct targets={len(targets)}"
        )

    # This rejected rung is measured, never used. Removing leading numeric
    # locants merges constitutional isomers such as 1,2- and
    # 1,3-dichloropropane. A missed match is safer than a false identity.
    dropped_locant_keys: dict[str, set[str]] = {}
    for target in grid_solvents:
        key = re.sub(r"^\d+", "", _alphanumeric(target))
        dropped_locant_keys.setdefault(key, set()).add(target)
    locant_collisions = sum(len(items) > 1 for items in dropped_locant_keys.values())
    if locant_collisions != 38:
        raise RuntimeError(
            f"rejected leading-locant rung now has {locant_collisions} collisions; expected 38"
        )

    return [
        (label, target, route)
        for label, (target, route) in sorted(bindings.items())
    ]


def _one_polymer_target(labels: Iterable[object]) -> str | None:
    targets: set[str] = set()
    for value in labels:
        if value is None:
            continue
        label = str(value).strip()
        if not label:
            continue
        expanded = thermo.expand_polymer_identity(label)
        if len(expanded) > 1:
            continue
        if len(expanded) == 1:
            targets.add(expanded[0])
            continue
        resolved = thermo.resolve_polymer(label)
        if resolved is not None:
            targets.add(resolved)
    return next(iter(targets)) if len(targets) == 1 else None


def _polymer_identity_map(
    polymer_rows: list[dict[str, str]],
) -> list[tuple[int, str, str]]:
    """Union curated non-proxy records with unambiguous Type bindings."""
    with gzip.open(ANALYSIS_ASSET, "rt", encoding="utf-8") as handle:
        curated = json.load(handle)["hsp"]["curated_polymers"]

    curated_bindings: dict[int, str] = {}
    for entry in curated:
        # PET's available catalog entry is explicitly a Mylar proxy, not a
        # generic PET identity. The reviewed join does not force that proxy.
        if entry.get("quality") == "proxy":
            continue
        target = _one_polymer_target([
            entry.get("id"), entry.get("display_name"), entry.get("raw_hsp_name"),
            *(entry.get("aliases") or []),
        ])
        if target is None:
            continue
        for raw_row_id in entry.get("source_record_ids") or []:
            row_id = int(raw_row_id)
            prior = curated_bindings.get(row_id)
            if prior is not None and prior != target:
                raise RuntimeError(
                    f"curated HSP row {row_id} binds to both {prior} and {target}"
                )
            curated_bindings[row_id] = target
    if set(curated_bindings.values()) != EXPECTED_CURATED_TARGETS:
        raise RuntimeError(
            f"curated polymer targets moved: {sorted(set(curated_bindings.values()))}"
        )

    type_bindings: dict[int, str] = {}
    ambiguous_type_rows: list[tuple[int, tuple[str, ...]]] = []
    for row_id, row in enumerate(polymer_rows):
        expanded = thermo.expand_polymer_identity(row["Type"])
        if len(expanded) > 1:
            ambiguous_type_rows.append((row_id, expanded))
            continue
        target = expanded[0] if expanded else thermo.resolve_polymer(row["Type"])
        if target is not None:
            type_bindings[row_id] = target
    if set(type_bindings.values()) != EXPECTED_TYPE_TARGETS:
        raise RuntimeError(
            f"unambiguous Type targets moved: {sorted(set(type_bindings.values()))}"
        )
    if len(ambiguous_type_rows) != 3 or {
        target for _, targets in ambiguous_type_rows for target in targets
    } != {"NYLON6", "NYLON66"}:
        raise RuntimeError(
            "the rejected Polyamide Type expansion no longer matches its reviewed shape"
        )

    # Curated bindings are reviewed first. Type supplies PU and additional PS
    # and PVC grades, but never assigns either nylon to a generic Polyamide row.
    combined = {
        row_id: (target, "type") for row_id, target in type_bindings.items()
    }
    for row_id, target in curated_bindings.items():
        prior = combined.get(row_id)
        if prior is not None and prior[0] != target:
            raise RuntimeError(
                f"HSP row {row_id} binds to both {prior[0]} and {target}"
            )
        combined[row_id] = (target, "curated")
    targets = {target for target, _ in combined.values()}
    if targets != EXPECTED_POLYMER_TARGETS:
        raise RuntimeError(f"polymer identity union moved: {sorted(targets)}")
    return [
        (row_id, target, route)
        for row_id, (target, route) in sorted(combined.items())
    ]


def _build_database(
    output: Path,
    red_csv: Path,
    solvent_map: list[tuple[str, str, str]],
    polymer_map: list[tuple[int, str, str]],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False,
    )
    temporary = Path(handle.name)
    handle.close()
    temporary.unlink()
    try:
        connection = duckdb.connect(str(temporary))
        connection.execute("""
            CREATE TABLE red_grid AS
            SELECT
                CAST("Polymer_ID" AS INTEGER) AS hsp_polymer_row_id,
                CAST("Solvent_ID" AS INTEGER) AS hsp_solvent_row_id,
                CAST("Polymer" AS VARCHAR) AS hsp_polymer_name,
                CAST("Solvent" AS VARCHAR) AS hsp_solvent_name,
                CAST("Ra" AS DOUBLE) AS ra,
                CAST("RED" AS DOUBLE) AS red
            FROM read_csv(?, header = true, auto_detect = true)
        """, [str(red_csv)])
        connection.execute("""
            CREATE TABLE red_superseded AS
            SELECT
                CAST("Polymer_ID" AS INTEGER) AS hsp_polymer_row_id,
                CAST("Solvent_ID" AS INTEGER) AS hsp_solvent_row_id,
                CAST("Ra_WRONG" AS DOUBLE) AS superseded_ra,
                CAST("RED_WRONG" AS DOUBLE) AS superseded_red
            FROM read_csv(?, header = true, auto_detect = true)
        """, [str(red_csv)])
        connection.execute("""
            CREATE TABLE solvent_identity_map (
                hsp_name VARCHAR PRIMARY KEY,
                grid_solvent VARCHAR NOT NULL,
                route VARCHAR NOT NULL
            )
        """)
        connection.executemany(
            "INSERT INTO solvent_identity_map VALUES (?, ?, ?)", solvent_map,
        )
        connection.execute("""
            CREATE TABLE polymer_identity_map (
                hsp_row_id INTEGER PRIMARY KEY,
                thermo_polymer VARCHAR NOT NULL,
                route VARCHAR NOT NULL
            )
        """)
        connection.executemany(
            "INSERT INTO polymer_identity_map VALUES (?, ?, ?)", polymer_map,
        )
        connection.execute(
            "CREATE UNIQUE INDEX red_grid_source_key ON red_grid "
            "(hsp_polymer_row_id, hsp_solvent_row_id)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX red_superseded_source_key ON red_superseded "
            "(hsp_polymer_row_id, hsp_solvent_row_id)"
        )
        connection.execute(
            "CREATE INDEX red_grid_polymer_source ON red_grid (hsp_polymer_row_id)"
        )

        row_count, key_count = connection.execute("""
            SELECT count(*), count(DISTINCT (hsp_polymer_row_id, hsp_solvent_row_id))
            FROM red_grid
        """).fetchone()
        if row_count != 549_880 or key_count != row_count:
            raise RuntimeError(
                f"RED grid grain is invalid: rows={row_count}, source keys={key_count}"
            )
        bounds = connection.execute("""
            SELECT min(hsp_polymer_row_id), max(hsp_polymer_row_id),
                   min(hsp_solvent_row_id), max(hsp_solvent_row_id)
            FROM red_grid
        """).fetchone()
        if bounds != (0, 465, 0, 1179):
            raise RuntimeError(f"RED source-id bounds moved: {bounds}")
        connection.execute("CHECKPOINT")
        connection.close()
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    paths = _validate_sources(args.source_dir)
    polymer_rows = _read_csv(paths["POLYMER-HSPs-FINAL.csv"])
    solvent_rows = _read_csv(paths["SOLVENT-HSPs-FINAL.csv"])
    _verify_component_home(polymer_rows, solvent_rows)
    solvent_map = _solvent_identity_map(solvent_rows)
    polymer_map = _polymer_identity_map(polymer_rows)
    _build_database(
        args.output,
        paths["RED_values_complete_CORRECTED.csv"],
        solvent_map,
        polymer_map,
    )
    print(
        f"built {args.output}: 549,880 RED rows; "
        f"{len(solvent_map)} solvent names -> 380 grid solvents; "
        f"{len(polymer_map)} HSP polymer rows -> 11 thermo polymers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
