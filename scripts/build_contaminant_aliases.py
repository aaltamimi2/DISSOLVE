#!/usr/bin/env python3
"""Rebuild contaminant_aliases for v5 Commit D5.

resolution_basis is cas_verified / held_snapshot / catalog_declared.
Regex stays the lookup. No embed. No registry call. No BioSTEAM.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
CHEMISTRY_ASSET_SHA256 = (
    "19e585e019ad0ad1aac6e31ff49b5d47477789a5903b4fc3821e2d16a8596721"
)
CHEMISTRY_COMMIT = "1b61ebfb7d7fb5239f8c8aab11685d0cb87fbd31"
HELD_SHA256 = (
    "eca9233329f8a33612996fd40669d94bdcd8036ed2641e51439bb3e18ee8654c"
)
SOLVENT_DATA_SHA256 = (
    "c9dfd6556ec3f755157b951408a852df44b7cbdc18dea02c36266000d0c9bd49"
)
PERFLUORO_SOLVENT_CAS = {
    "355-25-9",
    "335-57-9",
    "355-42-0",
    "678-26-2",
    "355-02-2",
    "116-14-3",
    "76-05-1",
}
FORBIDDEN_ALIASES = {
    "2-ethylhexyl",
    "heptafluoropropoxy",
    "dioctyl phthalate",
    "genx",
    "pfoa",
}
DIMETHYL_PHTHALATE_CAS = "131-11-3"
RESOLUTION_BASIS = {"cas_verified", "held_snapshot", "catalog_declared"}
STRUCTURE_BASIS = {"cosmobase_local", "structure_unavailable"}
STRUCTURES_SHA256 = (
    "29838a0aa353bae765965bda444dad31ee288bcc27390d3bfa1d330669a72cd2"
)
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
PHTHALATE_CAS_CID = {
    "butyl benzyl phthalate (bbp)": ("85-68-7", "2347"),
    "di-(2-ethylhexyl) phthalate (dehp)": ("117-81-7", "8343"),
    "di-isodecyl phthalate(didp)": ("26761-40-0", "33599"),
    "di-isononyl phthalate (dinp)": ("28553-12-0", "590836"),
    "di-n-butyl phthalate (dbp)": ("84-74-2", "3026"),
    "di-n-hexyl phthalate (dnhp)": ("84-75-3", "6786"),
    "di-n-octyl phthalate (dnop)": ("117-84-0", "8346"),
    "diethyl phthalate (dep)": ("84-66-2", "6781"),
}


def _key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha(path: Path, expected: str, label: str) -> None:
    digest = _sha256(path)
    if digest != expected:
        raise SystemExit(f"{label} sha256 {digest} != {expected}")


def _cid(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except ValueError:
        return text


def _extract_chemistry(dest: Path) -> None:
    raw = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{CHEMISTRY_COMMIT}:src/dissolve/data/contaminants.duckdb"],
    )
    dest.write_bytes(raw)
    _require_sha(dest, CHEMISTRY_ASSET_SHA256, "chemistry parent")


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


def _load_structures(path: Path) -> dict[str, dict[str, str]]:
    _require_sha(path, STRUCTURES_SHA256, path.name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "dissolve.contaminant-structures.local.v1":
        raise SystemExit(f"unexpected structures schema: {payload.get('schema')}")
    rows: dict[str, dict[str, str]] = {}
    for item in payload["rows"]:
        key = _key(item["contaminant_key"])
        if key in rows:
            raise SystemExit(f"structures file repeats contaminant_key {key}")
        smiles = str(item["smiles"]).strip()
        inchikey = str(item["inchikey"]).strip()
        if not smiles or not inchikey:
            raise SystemExit(f"structures row {key} missing smiles/inchikey")
        rows[key] = {
            "smiles": smiles,
            "inchikey": inchikey,
            "cas_number": str(item["cas_number"]),
        }
    if len(rows) != 8:
        raise SystemExit(f"structures file has {len(rows)} rows, expected 8 phthalates")
    inchikeys = [row["inchikey"] for row in rows.values()]
    if len(set(inchikeys)) != 8:
        raise SystemExit("structures file has colliding InChIKeys")
    return rows


def _load_normalized(path: Path) -> dict[str, dict[str, str]]:
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
                "cid": _cid(row.get("CID")),
                "display": str(row.get("Solvent name") or "").strip(),
            }
    return by_normalized


def _phthalate_identity(
    held: dict[str, dict[str, str]],
    by_normalized: dict[str, dict[str, str]],
) -> dict[str, tuple[str, str]]:
    assigned: dict[str, tuple[str, str]] = {}
    cas_to_key: dict[str, str] = {}
    for key, item in held.items():
        expected_cas, expected_cid = PHTHALATE_CAS_CID[key]
        normalized = _key(item["source_normalized_name"])
        joined = by_normalized.get(normalized)
        if joined is None:
            raise SystemExit(
                f"no Solvent_Data.csv normalized row for {key} "
                f"({item['source_normalized_name']})"
            )
        if _key(joined["display"]) == "dioctyl phthalate":
            raise SystemExit("refusing display-name Dioctyl Phthalate join")
        if joined["cas_number"] != item["cas_number"]:
            raise SystemExit(
                f"held CAS {item['cas_number']} != CSV {joined['cas_number']}"
            )
        if joined["cas_number"] != expected_cas or joined["cid"] != expected_cid:
            raise SystemExit(
                f"{key} CSV CAS/CID {joined['cas_number']}/{joined['cid']} "
                f"!= {expected_cas}/{expected_cid}"
            )
        if joined["cas_number"] in PERFLUORO_SOLVENT_CAS:
            raise SystemExit(f"perfluoro solvent CAS on {key}")
        if joined["cas_number"] == DIMETHYL_PHTHALATE_CAS:
            raise SystemExit("DMP is not a catalog key")
        prior = cas_to_key.get(joined["cas_number"])
        if prior and prior != key:
            raise SystemExit(f"CAS {joined['cas_number']} on {prior} and {key}")
        cas_to_key[joined["cas_number"]] = key
        assigned[key] = (joined["cas_number"], joined["cid"])
    if set(assigned) != set(PHTHALATE_CAS_CID):
        raise SystemExit("phthalate identity set does not match v5 §2")
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
    resolution_basis: str,
    pubchem_cid: str | None,
    smiles: str | None,
    inchikey: str | None,
    structure_basis: str,
) -> None:
    folded = _key(alias)
    if not folded:
        return
    if folded in FORBIDDEN_ALIASES:
        raise SystemExit(f"refusing forbidden alias {alias!r}")
    if resolution_basis not in RESOLUTION_BASIS:
        raise SystemExit(f"unknown resolution_basis {resolution_basis!r}")
    if cas_number == "":
        raise SystemExit(f"blank CAS on alias {alias!r}")
    if resolution_basis in {"cas_verified", "held_snapshot"}:
        if not cas_number:
            raise SystemExit(f"{resolution_basis} row {alias!r} needs cas_number")
    elif cas_number is not None:
        raise SystemExit(f"catalog_declared row {alias!r} must have NULL CAS")
    if resolution_basis == "cas_verified":
        if not pubchem_cid:
            raise SystemExit(f"cas_verified row {alias!r} needs pubchem_cid")
    elif pubchem_cid is not None:
        raise SystemExit(
            f"{resolution_basis} row {alias!r} must not carry a CID"
        )
    if structure_basis not in STRUCTURE_BASIS:
        raise SystemExit(f"unknown structure_basis {structure_basis!r}")
    if structure_basis == "cosmobase_local":
        if not smiles or not inchikey:
            raise SystemExit(f"cosmobase_local row {alias!r} needs smiles/inchikey")
    elif smiles is not None or inchikey is not None:
        raise SystemExit(
            f"structure_unavailable row {alias!r} must not carry smiles/inchikey"
        )
    if cas_number in PERFLUORO_SOLVENT_CAS or cas_number == DIMETHYL_PHTHALATE_CAS:
        raise SystemExit(f"refusing CAS {cas_number} on {alias!r}")
    prior = folded_owner.get(folded)
    identity = (contaminant_key, cas_number)
    if prior and prior != identity:
        raise SystemExit(f"folded alias {folded!r} maps to {prior} and {identity}")
    if cas_number:
        other = cas_owner.get(cas_number)
        if other and other != contaminant_key:
            raise SystemExit(f"CAS {cas_number} maps to {other} and {contaminant_key}")
        cas_owner[cas_number] = contaminant_key
    if prior:
        return
    folded_owner[folded] = identity
    rows.append((
        alias, contaminant_key, canonical_name, family,
        cas_number, resolution_basis, pubchem_cid,
        smiles, inchikey, structure_basis,
    ))


def build(
    parent: Path, output: Path, held_path: Path, solvent_csv: Path,
    structures_path: Path,
) -> str:
    _require_sha(parent, CHEMISTRY_ASSET_SHA256, "chemistry parent")
    held = _load_held(held_path)
    structures = _load_structures(structures_path)
    identities = _phthalate_identity(held, _load_normalized(solvent_csv))
    if set(structures) != set(identities):
        raise SystemExit("structures keys do not match the eight held phthalates")

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
    if len(contaminants) != 34 or families != {"PFAS", "Phthalates"}:
        raise SystemExit("catalog is not 26 PFAS / 8 Phthalates")
    if logd_count != 1088 or misc_count != 1344:
        raise SystemExit(f"logd/miscibility moved: {logd_count}/{misc_count}")

    aliases: list[tuple[Any, ...]] = []
    folded_owner: dict[str, tuple[str, str | None]] = {}
    cas_owner: dict[str, str] = {}
    seen: set[str] = set()
    for family, name, key in contaminants:
        family_s, name_s, key_s = str(family), str(name), str(key)
        seen.add(_key(key_s))
        if family_s == "Phthalates":
            cas, cid = identities[_key(key_s)]
            basis = "cas_verified"
            pin = structures[_key(key_s)]
            if pin["cas_number"] != cas:
                raise SystemExit(
                    f"structure CAS {pin['cas_number']} != held {cas} for {key_s}"
                )
            smiles, inchikey, structure_basis = (
                pin["smiles"], pin["inchikey"], "cosmobase_local",
            )
        elif family_s == "PFAS":
            if _key(key_s) in structures:
                raise SystemExit(f"PFAS {key_s} must not carry a structure pin")
            cas, cid, basis = None, None, "catalog_declared"
            smiles, inchikey, structure_basis = None, None, "structure_unavailable"
        else:
            raise SystemExit(f"unexpected family {family_s}")
        _add_alias(
            aliases, folded_owner, cas_owner,
            alias=name_s, contaminant_key=key_s, canonical_name=name_s,
            family=family_s, cas_number=cas, resolution_basis=basis,
            pubchem_cid=cid, smiles=smiles, inchikey=inchikey,
            structure_basis=structure_basis,
        )
        _add_alias(
            aliases, folded_owner, cas_owner,
            alias=key_s, contaminant_key=key_s, canonical_name=name_s,
            family=family_s, cas_number=cas, resolution_basis=basis,
            pubchem_cid=cid, smiles=smiles, inchikey=inchikey,
            structure_basis=structure_basis,
        )
        if cas:
            _add_alias(
                aliases, folded_owner, cas_owner,
                alias=cas, contaminant_key=key_s, canonical_name=name_s,
                family=family_s, cas_number=cas, resolution_basis=basis,
                pubchem_cid=cid, smiles=smiles, inchikey=inchikey,
                structure_basis=structure_basis,
            )
            short = PHTHALATE_SHORTS.get(_key(key_s))
            if not short:
                raise SystemExit(f"phthalate {key_s} missing named short")
            _add_alias(
                aliases, folded_owner, cas_owner,
                alias=short, contaminant_key=key_s, canonical_name=name_s,
                family=family_s, cas_number=cas, resolution_basis=basis,
                pubchem_cid=cid, smiles=smiles, inchikey=inchikey,
                structure_basis=structure_basis,
            )

    if set(PHTHALATE_SHORTS) - seen:
        raise SystemExit("named shorts for missing keys")
    if set(identities) - seen:
        raise SystemExit("held CAS for a key that is not in the catalog")
    if any(row[5] == "held_snapshot" for row in aliases):
        raise SystemExit("held_snapshot must be 0 until contaminant_cas.held.v1")
    if any(row[5] == "catalog_declared" and row[4] is not None for row in aliases):
        raise SystemExit("catalog_declared row carries a CAS")
    if any(row[3] == "PFAS" and (row[7] or row[8]) for row in aliases):
        raise SystemExit("PFAS row carries a SMILES or InChIKey")
    if any(row[9] == "cosmobase_local" and row[3] != "Phthalates" for row in aliases):
        raise SystemExit("cosmobase_local is only for phthalates")
    phthalate_keys = {row[1] for row in aliases if row[3] == "Phthalates"}
    if phthalate_keys != set(identities):
        raise SystemExit("phthalate keys drifted from held CAS set")

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
                resolution_basis VARCHAR,
                pubchem_cid VARCHAR,
                smiles VARCHAR,
                inchikey VARCHAR,
                structure_basis VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO contaminant_aliases VALUES (?,?,?,?,?,?,?,?,?,?)",
            aliases,
        )
        connection.execute(
            "CREATE INDEX contaminant_alias_name ON contaminant_aliases(alias)"
        )
        held_label = "src/dissolve/data/contaminant_cas_phthalates.local.v1.json"
        existing_sources = {
            str(row[0])
            for row in connection.execute(
                "SELECT source_path FROM asset_sources"
            ).fetchall()
        }
        if held_label not in existing_sources:
            connection.execute(
                "INSERT INTO asset_sources VALUES (?,?,?)",
                [held_label, HELD_SHA256, held_path.stat().st_size],
            )
        connection.execute(
            "DELETE FROM metadata WHERE key LIKE 'cas_%' OR key LIKE 'held_%' "
            "OR key LIKE 'resolution_%' OR key LIKE 'structure_%'"
        )
        connection.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [
                ("cas_local_count", "8"),
                ("cas_absent_count", "26"),
                ("cas_absent_family", "PFAS"),
                ("held_snapshot_count", "0"),
                ("cas_source", "Solvent_Data.csv cosmobase_name"),
                ("cas_held_file", held_label),
                ("cas_held_sha256", HELD_SHA256),
                (
                    "cas_absent_note",
                    "26 PFAS CAS are not local; resolution_basis=catalog_declared",
                ),
                ("structure_local_count", "8"),
                ("structure_unavailable_count", "26"),
                (
                    "structure_held_file",
                    "src/dissolve/data/contaminant_structures.local.v1.json",
                ),
                ("structure_held_sha256", STRUCTURES_SHA256),
                (
                    "structure_absent_note",
                    "26 PFAS have no local geometry; structure_basis=structure_unavailable",
                ),
            ],
        )
        connection.execute("CHECKPOINT")
        connection.close()
        output.write_bytes(staged.read_bytes())
    return _sha256(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "src/dissolve/data/contaminants.duckdb")
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
    parser.add_argument(
        "--structures",
        type=Path,
        default=ROOT / "src/dissolve/data/contaminant_structures.local.v1.json",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "chemistry.duckdb"
        _extract_chemistry(parent)
        digest = build(
            parent, args.output.resolve(),
            args.held.resolve(), args.solvent_data.resolve(),
            args.structures.resolve(),
        )
    print(f"built {args.output.resolve()} sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
