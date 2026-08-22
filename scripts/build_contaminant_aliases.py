#!/usr/bin/env python3
"""Add contaminant_aliases to the existing contaminants.duckdb pin.

Commit D only: table + pin. Does not delete the trailing-parenthetical regex.
CAS numbers come from Solvent_Data.csv via the cosmobase / normalized name.
PFAS CAS are an explicit NULL absence. No registry call. No BioSTEAM.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
PARENT_ASSET_SHA256 = (
    "19e585e019ad0ad1aac6e31ff49b5d47477789a5903b4fc3821e2d16a8596721"
)
HELD_SHA256 = (
    "eca9233329f8a33612996fd40669d94bdcd8036ed2641e51439bb3e18ee8654c"
)
SOLVENT_DATA_SHA256 = (
    "c9dfd6556ec3f755157b951408a852df44b7cbdc18dea02c36266000d0c9bd49"
)
PERFLUORO_SOLVENT_CAS = {
    "355-25-9",  # perfluorobutane — not perfluorobutanoic acid
    "335-57-9",  # perfluoroheptane
    "355-42-0",  # perfluorohexane
    "678-26-2",  # perfluoropentane
    "355-02-2",  # perfluoromethylcyclohexane
    "116-14-3",  # tetrafluoroethene
    "76-05-1",   # trifluoroacetic acid
}
FORBIDDEN_ALIASES = {
    "2-ethylhexyl",
    "heptafluoropropoxy",
    "dioctyl phthalate",
    "genx",
}
PHTHALATE_SHORTS = {
    "butyl benzyl phthalate (bbp)": "BBP",
    "di-(2-ethylhexyl) phthalate (dehp)": "DEHP",
    "di-isodecyl phthalate(didp)": "DiDP",
    "di-isononyl phthalate (dinp)": "DiNP",
    "di-n-butyl phthalate (dbp)": "DBP",
    "di-n-hexyl phthalate (dnhp)": "DnHP",
    "di-n-octyl phthalate (dnop)": "DnOP",
    "diethyl phthalate (dep)": "DEP",
}


def _key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha(path: Path, expected: str, label: str) -> None:
    digest = _sha256(path)
    if digest != expected:
        raise SystemExit(f"{label} sha256 {digest} != {expected}")


def _load_held(path: Path) -> dict[str, dict[str, str]]:
    _require_sha(path, HELD_SHA256, path.name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "dissolve.contaminant-cas.local.v1":
        raise SystemExit(f"unexpected held schema: {payload.get('schema')}")
    rows = {}
    for item in payload["rows"]:
        key = _key(item["contaminant_key"])
        if key in rows:
            raise SystemExit(f"held file repeats contaminant_key {key}")
        rows[key] = item
    if len(rows) != 8:
        raise SystemExit(f"held file has {len(rows)} rows, expected 8 phthalates")
    return rows


def _load_normalized_cas(path: Path) -> dict[str, dict[str, str]]:
    _require_sha(path, SOLVENT_DATA_SHA256, path.name)
    by_normalized: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            normalized = _key(row.get("Solvent name in cosmobase"))
            cas = str(row.get("CAS number") or "").strip()
            if not normalized or not cas:
                continue
            prior = by_normalized.get(normalized)
            if prior and prior["cas_number"] != cas:
                raise SystemExit(
                    f"Solvent_Data.csv normalized {normalized} maps to "
                    f"{prior['cas_number']} and {cas}"
                )
            by_normalized[normalized] = {
                "cas_number": cas,
                "cid": str(row.get("CID") or "").strip(),
                "display": str(row.get("Solvent name") or "").strip(),
                "normalized": str(row.get("Solvent name in cosmobase") or "").strip(),
            }
    return by_normalized


def _phthalate_cas(
    held: dict[str, dict[str, str]],
    by_normalized: dict[str, dict[str, str]],
) -> dict[str, str]:
    assigned: dict[str, str] = {}
    cas_to_key: dict[str, str] = {}
    for key, item in held.items():
        normalized = _key(item["source_normalized_name"])
        joined = by_normalized.get(normalized)
        if joined is None:
            raise SystemExit(
                f"no Solvent_Data.csv normalized row for {key} "
                f"({item['source_normalized_name']})"
            )
        if joined["cas_number"] != item["cas_number"]:
            raise SystemExit(
                f"held CAS {item['cas_number']} != Solvent_Data.csv "
                f"{joined['cas_number']} for normalized {normalized}"
            )
        if joined["cas_number"] in PERFLUORO_SOLVENT_CAS:
            raise SystemExit(
                f"refusing perfluorinated solvent CAS {joined['cas_number']} "
                f"on contaminant {key}"
            )
        prior_key = cas_to_key.get(joined["cas_number"])
        if prior_key and prior_key != key:
            raise SystemExit(
                f"CAS {joined['cas_number']} would belong to {prior_key} and {key}"
            )
        cas_to_key[joined["cas_number"]] = key
        assigned[key] = joined["cas_number"]
    return assigned


def _add_alias(
    rows: list[tuple[Any, ...]],
    folded_owner: dict[str, tuple[str, str | None]],
    cas_owner: dict[str, str],
    *,
    alias: str,
    contaminant_key: str,
    canonical_name: str,
    family: str,
    cas_number: str | None,
    cas_status: str,
) -> None:
    folded = _key(alias)
    if not folded:
        return
    if folded in FORBIDDEN_ALIASES:
        raise SystemExit(f"refusing forbidden alias {alias!r}")
    if cas_number == "":
        raise SystemExit(f"blank CAS on alias {alias!r}")
    if cas_status == "local":
        if not cas_number:
            raise SystemExit(f"local CAS row {alias!r} is missing cas_number")
    elif cas_status == "absent":
        if cas_number is not None:
            raise SystemExit(f"absent CAS row {alias!r} must be NULL, not {cas_number}")
    else:
        raise SystemExit(f"cas_status must be local or absent, not {cas_status}")
    if cas_number in PERFLUORO_SOLVENT_CAS:
        raise SystemExit(
            f"refusing perfluorinated solvent CAS {cas_number} on {alias!r}"
        )
    prior = folded_owner.get(folded)
    identity = (contaminant_key, cas_number)
    if prior and prior != identity:
        raise SystemExit(
            f"folded alias {folded!r} maps to {prior} and {identity}"
        )
    if cas_number:
        other = cas_owner.get(cas_number)
        if other and other != contaminant_key:
            raise SystemExit(
                f"CAS {cas_number} maps to {other} and {contaminant_key}"
            )
        cas_owner[cas_number] = contaminant_key
    if prior:
        return
    folded_owner[folded] = identity
    rows.append((
        alias, contaminant_key, canonical_name, family, cas_number, cas_status,
    ))


def build(parent: Path, output: Path, held_path: Path, solvent_csv: Path) -> str:
    if _sha256(parent) != PARENT_ASSET_SHA256:
        source = duckdb.connect(str(parent), read_only=True)
        tables = {row[0] for row in source.execute("SHOW TABLES").fetchall()}
        source.close()
        if "contaminant_aliases" not in tables:
            raise SystemExit(
                f"parent asset sha256 {_sha256(parent)} != {PARENT_ASSET_SHA256}"
            )
    held = _load_held(held_path)
    by_normalized = _load_normalized_cas(solvent_csv)
    phthalate_cas = _phthalate_cas(held, by_normalized)

    source = duckdb.connect(str(parent), read_only=True)
    contaminants = source.execute(
        "SELECT family, contaminant, contaminant_key FROM contaminants "
        "ORDER BY family, contaminant"
    ).fetchall()
    logd_count = source.execute("SELECT COUNT(*) FROM logd").fetchone()[0]
    misc_count = source.execute("SELECT COUNT(*) FROM miscibility").fetchone()[0]
    families = {
        str(row[0]) for row in source.execute(
            "SELECT DISTINCT family FROM contaminants"
        ).fetchall()
    }
    source.close()
    if len(contaminants) != 34:
        raise SystemExit(f"contaminants has {len(contaminants)}, expected 34")
    if families != {"PFAS", "Phthalates"}:
        raise SystemExit(f"unexpected families {sorted(families)}")
    if logd_count != 1088 or misc_count != 1344:
        raise SystemExit(
            f"logd/miscibility counts moved: {logd_count}/{misc_count}"
        )

    aliases: list[tuple[Any, ...]] = []
    folded_owner: dict[str, tuple[str, str | None]] = {}
    cas_owner: dict[str, str] = {}
    seen_keys: set[str] = set()
    for family, name, key in contaminants:
        family_s = str(family)
        name_s = str(name)
        key_s = str(key)
        seen_keys.add(key_s)
        if family_s == "Phthalates":
            cas = phthalate_cas.get(_key(key_s))
            if not cas:
                raise SystemExit(f"phthalate {key_s} has no local CAS")
            status = "local"
        elif family_s == "PFAS":
            cas = None
            status = "absent"
        else:
            raise SystemExit(f"unexpected family {family_s}")
        _add_alias(
            aliases, folded_owner, cas_owner,
            alias=name_s, contaminant_key=key_s, canonical_name=name_s,
            family=family_s, cas_number=cas, cas_status=status,
        )
        _add_alias(
            aliases, folded_owner, cas_owner,
            alias=key_s, contaminant_key=key_s, canonical_name=name_s,
            family=family_s, cas_number=cas, cas_status=status,
        )
        if cas:
            _add_alias(
                aliases, folded_owner, cas_owner,
                alias=cas, contaminant_key=key_s, canonical_name=name_s,
                family=family_s, cas_number=cas, cas_status=status,
            )
            short = PHTHALATE_SHORTS.get(_key(key_s))
            if not short:
                raise SystemExit(f"phthalate {key_s} missing named short")
            _add_alias(
                aliases, folded_owner, cas_owner,
                alias=short, contaminant_key=key_s, canonical_name=name_s,
                family=family_s, cas_number=cas, cas_status=status,
            )
            normalized = held[_key(key_s)]["source_normalized_name"]
            _add_alias(
                aliases, folded_owner, cas_owner,
                alias=normalized, contaminant_key=key_s, canonical_name=name_s,
                family=family_s, cas_number=cas, cas_status=status,
            )

    missing_shorts = set(PHTHALATE_SHORTS) - {_key(key) for key in seen_keys}
    if missing_shorts:
        raise SystemExit(f"named shorts for missing keys: {sorted(missing_shorts)}")
    if set(phthalate_cas) - {_key(key) for key in seen_keys}:
        raise SystemExit("held CAS for a contaminant_key that is not in the catalog")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "contaminants.duckdb"
        shutil.copy2(parent, staged)
        connection = duckdb.connect(str(staged))
        connection.execute("DROP TABLE IF EXISTS contaminant_aliases")
        connection.execute(
            """
            CREATE TABLE contaminant_aliases (
                alias VARCHAR,
                contaminant_key VARCHAR,
                canonical_name VARCHAR,
                family VARCHAR,
                cas_number VARCHAR,
                cas_status VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO contaminant_aliases VALUES (?,?,?,?,?,?)",
            aliases,
        )
        connection.execute(
            "CREATE INDEX contaminant_alias_name ON contaminant_aliases(alias)"
        )
        held_bytes = held_path.stat().st_size
        existing_sources = {
            str(row[0])
            for row in connection.execute(
                "SELECT source_path FROM asset_sources"
            ).fetchall()
        }
        held_label = "src/dissolve/data/contaminant_cas_phthalates.local.v1.json"
        if held_label not in existing_sources:
            connection.execute(
                "INSERT INTO asset_sources VALUES (?,?,?)",
                [held_label, HELD_SHA256, held_bytes],
            )
        connection.execute("DELETE FROM metadata WHERE key LIKE 'cas_%'")
        connection.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("cas_local_count", "8"),
                ("cas_absent_count", "26"),
                ("cas_absent_family", "PFAS"),
                ("cas_source", "Solvent_Data.csv cosmobase_name"),
                ("cas_held_file", held_label),
                ("cas_held_sha256", HELD_SHA256),
                (
                    "cas_absent_note",
                    "26 PFAS CAS are not local; cas_number is explicit NULL",
                ),
            ],
        )
        connection.execute("CHECKPOINT")
        connection.close()
        output.write_bytes(staged.read_bytes())
    return _sha256(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--parent",
        type=Path,
        default=ROOT / "src/dissolve/data/contaminants.duckdb",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "src/dissolve/data/contaminants.duckdb",
    )
    parser.add_argument(
        "--held",
        type=Path,
        default=ROOT / "src/dissolve/data/contaminant_cas_phthalates.local.v1.json",
    )
    parser.add_argument(
        "--solvent-data",
        type=Path,
        default=ROOT / "src/dissolve/data/Solvent_Data.csv",
    )
    args = parser.parse_args()
    parent = args.parent.resolve()
    output = args.output.resolve()
    if parent == output:
        raw = parent.read_bytes()
        if hashlib.sha256(raw).hexdigest() != PARENT_ASSET_SHA256:
            source = duckdb.connect(str(parent), read_only=True)
            has_aliases = "contaminant_aliases" in {
                row[0] for row in source.execute("SHOW TABLES").fetchall()
            }
            source.close()
            if has_aliases:
                raise SystemExit(
                    "in-place rebuild needs the parent pin "
                    f"{PARENT_ASSET_SHA256}; restore contaminants.duckdb first"
                )
        with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as handle:
            backup = Path(handle.name)
        backup.write_bytes(raw)
        try:
            digest = build(backup, output, args.held.resolve(), args.solvent_data.resolve())
        finally:
            backup.unlink(missing_ok=True)
    else:
        digest = build(parent, output, args.held.resolve(), args.solvent_data.resolve())
    print(f"built {output} sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
