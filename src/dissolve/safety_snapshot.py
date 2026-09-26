"""Build DISSOLVE's PubChem safety snapshot from a raw PubChem pull, and check it.

    python -m dissolve.safety_snapshot build RAW.jsonl OUT.duckdb
    python -m dissolve.safety_snapshot check OUT.duckdb

RAW holds one JSON line per compound: {cid, solvent_name, cas_number, headings: {heading: {ok, failure_class,
attempts, payload}}}. The pull of 987 solvents taken 2026-08-28 is ~/dissolve-v12-audit/safety/_pull_checkpoint.v2.jsonl
(38 MB, kept out of the repo). Every field is read by safety._pubchem_fields, the readers live safety cards use, so
a snapshot value and a live value mean the same thing.

The build adds the peroxide-former table (data/peroxide_formers.v1.json) and each solvent's CHEM21 Safety, Health
and Environment scores, computed once here so answers read them instead of recomputing them. `check` applies the
gates an audit of every field asked for on 2026-09-25 and exits non-zero when one fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Optional

import duckdb

from . import safety

PEROXIDE_TABLE = Path(str(files("dissolve").joinpath("data/peroxide_formers.v1.json")))
EU_CLASSIFICATION = Path(str(files("dissolve").joinpath("data/eu_classification.v2.json")))
# The classification bases under which a solvent's REACH status sets the CHEM21 Health/Environment rule. For "no EU
# classification found" (in ECHA, but no usable entry) the status is withheld, so the untested default applies.
_REACH_RULE_BASES = frozenset({"EU classification", "not classified in the EU"})
CHEM21_GUIDE = Path(str(files("dissolve").joinpath("data/chem21_guide.v1.json")))
MIN_GUIDE_FLASH_BANDS = 59  # of the guide's 62 numeric flash points; the audit's rule scored 59 (v2 stored 54)
THERMODYNAMICS = Path(str(files("dissolve").joinpath("data/thermodynamics.duckdb")))
FLAMMABILITY_CODES = frozenset({"H220", "H221", "H222", "H223", "H224", "H225", "H226", "H228", "H241", "H242",
                                "H250", "H251", "H252", "H260", "H261"})
SCHEMA = (
    """CREATE TABLE pubchem_safety (
        cid BIGINT, solvent_name VARCHAR, cas_number VARCHAR,
        flash_point_c DOUBLE, autoignition_c DOUBLE, vapor_pressure_kpa DOUBLE, vapor_pressure_temp_c DOUBLE,
        ghs_signal_word VARCHAR, ghs_pictograms VARCHAR, ghs_hazard_statements VARCHAR,
        occupational_exposure_limits VARCHAR, n_exposure_limits INTEGER, ld50_values VARCHAR, lc50_values VARCHAR,
        biodegradation VARCHAR, n_failed_headings INTEGER, ghs_basis VARCHAR, ghs_nonbasis_severe VARCHAR,
        ghs_codes_n INTEGER, flash_point_basis VARCHAR, nonflammable BOOLEAN, flammable_gas BOOLEAN,
        explodes BOOLEAN, autoignition_spread_c DOUBLE, vapor_pressure_basis VARCHAR, ghs_skipped_entries VARCHAR,
        reach_registered BOOLEAN, eu_code_sources VARCHAR)""",
    "CREATE TABLE pubchem_heading_raw (cid BIGINT, heading VARCHAR, ok BOOLEAN, failure_class VARCHAR, attempts INTEGER)",
    """CREATE TABLE ghs_statement_provenance (cid BIGINT, source_name VARCHAR, share_pct DOUBLE, codes VARCHAR,
        line_text VARCHAR, in_basis BOOLEAN)""",
    """CREATE TABLE peroxide_formers (cas VARCHAR, name VARCHAR, pubchem_cids VARCHAR, peroxide_class VARCHAR,
        class_basis VARCHAR, vumc VARCHAR, prudent_practices_2011 VARCHAR, illinois_drs VARCHAR,
        class_entry_rules VARCHAR, eu_euh019 BOOLEAN, lists_disagree BOOLEAN, structural_alert BOOLEAN,
        confidence VARCHAR, evidence VARCHAR)""",
    """CREATE TABLE chem21_scores (cid BIGINT, solvent_name VARCHAR, cas_number VARCHAR, safety INTEGER,
        health INTEGER, environment INTEGER, ranking VARCHAR, worst INTEGER, unavailable_reason VARCHAR,
        payload VARCHAR)""",
    "CREATE TABLE snapshot_metadata (key VARCHAR, value VARCHAR)",
)


def _boiling_points() -> dict[int, float]:
    """Boiling point per PubChem CID from DISSOLVE's solvent table (the first row when several share a CID)."""
    connection = duckdb.connect(str(THERMODYNAMICS), read_only=True)
    try:
        rows = connection.execute(
            "SELECT cid, boiling_point_c FROM solvent_data WHERE cid IS NOT NULL AND boiling_point_c IS NOT NULL "
            "ORDER BY source_row").fetchall()
    finally:
        connection.close()
    found: dict[int, float] = {}
    for cid, boiling in rows:
        found.setdefault(int(cid), float(boiling))
    return found


def _phrases(records: list[dict[str, Any]]) -> dict[str, str]:
    """The text PubChem gives each hazard code, from every line that states exactly one code (share and bracketed
    counts removed), the most frequent wording per code."""
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        entry = record["headings"].get("GHS Classification") or {}
        composed = safety._compose_ghs(entry.get("payload") if entry.get("ok") else {})
        for line in composed["provenance"]:
            if len(line["codes"]) != 1:
                continue
            text = re.sub(r"\s*\(\s*>?\s*~?\s*\d+(?:\.\d+)?\s*%\s*\)", "", line["line_text"])
            text = re.sub(r"\s+\*+(?=:)", "", text).strip()
            counts.setdefault(line["codes"][0], {}).setdefault(text, 0)
            counts[line["codes"][0]][text] += 1
    return {code: max(texts.items(), key=lambda item: (item[1], -len(item[0])))[0] for code, texts in counts.items()}


def eu_ghs(fields: dict[str, Any], eu: dict[str, Any], phrases: dict[str, str]) -> dict[str, Any]:
    """The hazard statements, signal word and pictograms under the solvent's own EU classification (see
    eu_classification.v2.json). PubChem's lines stay as provenance; a severe code PubChem carries that the EU
    classification does not is surfaced, not served."""
    codes = list(eu.get("codes") or [])
    statements = [phrases.get(code, code) for code in codes]
    signals = {match.group(1) for text in statements for match in [re.search(r"\[(Danger|Warning)\b", text)]
               if match}
    composition = fields["ghs_composition"]
    pubchem_codes = {code for line in composition["provenance"] for code in line["codes"]}
    return {
        "signal_word": "Danger" if "Danger" in signals else "Warning" if "Warning" in signals else None,
        "pictograms": safety._pictograms_for(set(codes)),
        "hazard_statements": statements,
        "basis": eu.get("basis") or "no EU data",
        "basis_codes": codes,
        "nonbasis_severe": [
            {"code": code, "sources": sorted({line["source_name"] for line in composition["provenance"]
                                              if code in line["codes"]}), "echa_share_pct": None}
            for code in sorted((pubchem_codes - set(codes)) & safety._GHS_SEVERE)
        ],
        "skipped_other_substance_entries": composition["skipped_other_substance_entries"],
        "provenance": [{**line, "in_basis": bool(set(line["codes"]) & set(codes))} for line in composition["provenance"]],
    }


def chem21_inputs(fields: dict[str, Any], cas: Optional[str], boiling: Optional[float],
                  peroxide: Optional[dict[str, Any]], reach_registered: Optional[bool] = None,
                  eu_basis: Optional[str] = None) -> dict[str, Any]:
    """What the CHEM21 recipe needs for one compound. A compound with no flash point is non-flammable when its text
    says so, or when it is classified, by hazard statements none of which is a flammability statement
    (dichloromethane) or by the EU's "not classified" (water): the guide scores that Safety 1. A flammable gas stays
    without a flash point."""
    statements = list((fields.get("ghs") or {}).get("hazard_statements") or [])
    codes = set(safety._chem21_parse_codes(statements))
    classified = bool(codes) or eu_basis == "not classified in the EU"
    inferred = (fields.get("flash_point_c") is None and not fields.get("flammable_gas") and classified
                and not codes & FLAMMABILITY_CODES)
    return {
        "boiling_point_c": boiling,
        "flash_point_c": fields.get("flash_point_c"),
        "autoignition_c": fields.get("autoignition_c"),
        "statements": statements,
        "cas_number": cas,
        "nonflammable": bool(fields.get("nonflammable") or inferred),
        "nonflammable_basis": ("its PubChem text says it does not burn" if fields.get("nonflammable")
                               else "no flash point and no flammability hazard statement" if inferred else None),
        "euh019": bool(peroxide and peroxide.get("eu_euh019")),
        "peroxide_class": (peroxide or {}).get("peroxide_class"),
        "reach_registered": reach_registered,
        "snapshot_signal_word": (fields.get("ghs") or {}).get("signal_word"),
        "resistivity_ohm_m": None,
        "decomposition_energy_j_g": None,
        "ghs_basis": (fields.get("ghs_composition") or {}).get("basis"),
        "ghs_nonbasis_severe": (fields.get("ghs_composition") or {}).get("nonbasis_severe") or [],
    }


def _peroxide_rows() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    document = json.loads(PEROXIDE_TABLE.read_text(encoding="utf-8"))
    rows = list(document["rows"])
    by_cas = {str(row["cas"]).strip(): row for row in rows if row.get("cas")}
    by_cid = {int(cid): row for row in rows for cid in row.get("pubchem_cids") or []}
    return rows, by_cas, by_cid


def build(raw: Path, out: Path, fetched_at_utc: str) -> str:
    """Write the snapshot to `out` and return its content digest (a digest over the data, stable across builds)."""
    records = sorted((json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines() if line.strip()),
                     key=lambda record: int(record["cid"]))
    if out.exists():
        out.unlink()
    boiling = _boiling_points()
    peroxide_rows, peroxide_by_cas, peroxide_by_cid = _peroxide_rows()
    eu_by_cid = {int(row["cid"]): row for row in json.loads(EU_CLASSIFICATION.read_text(encoding="utf-8"))["rows"]}
    phrases = _phrases(records)
    connection = duckdb.connect(str(out))
    for statement in SCHEMA:
        connection.execute(statement)
    for record in records:
        cid = int(record["cid"])
        payloads = {heading: (entry["payload"] if entry["ok"] else {}) for heading, entry in record["headings"].items()}
        fields = safety._pubchem_fields(payloads)
        eu = eu_by_cid.get(cid)
        if eu is not None:
            composition = eu_ghs(fields, eu, phrases)
            fields["ghs_composition"] = composition
            fields["ghs"] = {key: composition[key] for key in ("signal_word", "pictograms", "hazard_statements")}
        composition = fields["ghs_composition"]
        reach = eu.get("reach_registered") if eu is not None and eu.get("basis") in _REACH_RULE_BASES else None
        ghs = fields["ghs"]
        limits = fields["occupational_exposure_limits"]
        cas = record.get("cas_number")
        connection.execute(
            "INSERT INTO pubchem_safety VALUES (" + ",".join("?" * 28) + ")",
            [cid, record["solvent_name"], cas, fields["flash_point_c"], fields["autoignition_c"],
             fields["vapor_pressure_kpa"], fields["vapor_pressure_temp_c"], ghs["signal_word"],
             json.dumps(ghs["pictograms"]), json.dumps(ghs["hazard_statements"]), json.dumps(limits), len(limits),
             json.dumps(fields["ld50_values"]), json.dumps(fields["lc50_values"]),
             json.dumps(fields["biodegradation"]),
             sum(1 for entry in record["headings"].values() if not entry["ok"]), composition["basis"],
             json.dumps(composition["nonbasis_severe"]), len(composition["basis_codes"]), fields["flash_point_basis"],
             fields["nonflammable"], fields["flammable_gas"], fields["explodes"], fields["autoignition_spread_c"],
             fields["vapor_pressure_basis"], json.dumps(composition["skipped_other_substance_entries"]),
             reach, json.dumps((eu or {}).get("code_sources") or {})])
        for heading in safety._HEADINGS:
            entry = record["headings"][heading]
            connection.execute("INSERT INTO pubchem_heading_raw VALUES (?,?,?,?,?)",
                               [cid, heading, entry["ok"], entry["failure_class"], entry["attempts"]])
        for line in composition["provenance"]:
            connection.execute("INSERT INTO ghs_statement_provenance VALUES (?,?,?,?,?,?)",
                               [cid, line["source_name"], line["share_pct"], json.dumps(line["codes"]),
                                line["line_text"], line["in_basis"]])
        peroxide = peroxide_by_cid.get(cid) or peroxide_by_cas.get(str(cas or "").strip())
        scores = safety._chem21_score_from_inputs(
            record["solvent_name"], chem21_inputs(fields, cas, boiling.get(cid), peroxide, reach,
                                                  (eu or {}).get("basis")))
        connection.execute(
            "INSERT INTO chem21_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
            [cid, record["solvent_name"], cas, scores["chem21_safety_score"], scores["chem21_health_score"],
             scores["chem21_environment_score"], scores["chem21_default_ranking"], safety._chem21_worst(scores),
             scores["chem21_unavailable_reason"], json.dumps(scores, default=str, sort_keys=True)])
    for row in peroxide_rows:
        connection.execute(
            "INSERT INTO peroxide_formers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [row["cas"], row["name"], json.dumps(row["pubchem_cids"]), row["peroxide_class"], row["class_basis"],
             row["vumc"], row["prudent_practices_2011"], row["illinois_drs"], row["class_entry_rules"],
             row["eu_euh019"], row["lists_disagree"], row["structural_alert"], row["confidence"], row["evidence"]])
    metadata = {
        "schema": "dissolve.pubchem-safety-snapshot.v3",
        "fetched_at_utc": fetched_at_utc,
        "source": "PubChem PUG-View per (cid, heading); raw pull " + raw.name,
        "headings": json.dumps(list(safety._HEADINGS)),
        "parser": "dissolve.safety._pubchem_fields (shared with live safety cards)",
        "flash_point_rule": "lowest value a second source confirms within 3 °C, else the median of the sources; "
                            "a lower closed-cup value from ICSC or NIOSH, or one a second source confirms, replaces "
                            "it (never a higher one); a hyphen between numbers is a range; bounds count only when "
                            "nothing else exists",
        "autoignition_rule": "lowest stated value; spread across sources recorded",
        "vapor_pressure_rule": "value nearest 25 °C within 15-35 °C, else one with no stated temperature, else the "
                               "value measured nearest 25 °C",
        "ghs_basis_rule": "the solvent's own EU classification (eu_classification.v2.json): harmonised classes from "
                          "CLP Annex VI, other classes from the lead REACH registrant's records for the substance "
                          "itself (special grades left out; the most severe code per class), else the majority of at "
                          "least 10 notifying companies; no national list; PubChem's wording for each code; signal "
                          "word and pictograms follow the statements",
        "eu_classification": EU_CLASSIFICATION.name,
        "peroxide_formers": PEROXIDE_TABLE.name,
        "chem21_rule": "Prat et al. 2016 recipe (safety._chem21_score_from_inputs) on these fields; non-flammable "
                       "scores Safety 1; +1 for EU EUH019 peroxide formers; REACH registration from ECHA CHEM sets "
                       "the guide's Health and Environment rule for solvents without such statements",
    }
    for key, value in metadata.items():
        connection.execute("INSERT INTO snapshot_metadata VALUES (?,?)", [key, value])
    digest = content_digest(connection)
    connection.execute("INSERT INTO snapshot_metadata VALUES (?,?)", ["content_digest", digest])
    connection.close()
    return digest


def content_digest(connection: duckdb.DuckDBPyConnection) -> str:
    """A digest over every table's rows in a total order, so the same data always gives the same digest."""
    orders = {
        "pubchem_safety": "cid", "pubchem_heading_raw": "cid, heading",
        "ghs_statement_provenance": "cid, source_name, codes, line_text, share_pct, in_basis",
        "peroxide_formers": "cas, name", "chem21_scores": "cid",
    }
    payload = [connection.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
               for table, order in orders.items()]
    payload.append(connection.execute(
        "SELECT key, value FROM snapshot_metadata WHERE key <> 'content_digest' ORDER BY key").fetchall())
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def _flash_band(flash: Optional[float]) -> Optional[int]:
    return None if flash is None else safety._chem21_safety_basic(flash)


def guide_agreement(path: Path) -> dict[str, Any]:
    """How the snapshot compares with the published CHEM21 guide (Prat et al. 2016), matched by CAS number: flash
    points in the same Safety band, all three scores equal, and the same default ranking."""
    guide = json.loads(CHEM21_GUIDE.read_text(encoding="utf-8"))["rows"]
    connection = duckdb.connect(str(path), read_only=True)
    try:
        by_cas = {str(cas or "").strip(): (flash, s, h, e, rank) for cas, flash, s, h, e, rank in connection.execute(
            "SELECT p.cas_number, p.flash_point_c, c.safety, c.health, c.environment, c.ranking "
            "FROM pubchem_safety p JOIN chem21_scores c USING (cid)").fetchall()}
    finally:
        connection.close()
    flash_n = flash_same = scored_n = all_three = ranking = 0
    for row in guide:
        found = by_cas.get(str(row.get("cas") or "7732-18-5").strip())
        if found is None:
            continue
        flash, s, h, e, rank = found
        try:
            published_flash = float(row["fp_c"])
        except (TypeError, ValueError):
            published_flash = None
        if published_flash is not None:
            flash_n += 1
            flash_same += _flash_band(flash) == _flash_band(published_flash)
        scored_n += 1
        all_three += [s, h, e] == [row["safety"], row["health"], row["environment"]]
        ranking += str(rank or "").casefold() == str(row["ranking_default"]).casefold()
    return {"flash_points": flash_n, "flash_same_band": flash_same, "solvents": scored_n,
            "all_three_scores": all_three, "default_ranking": ranking}


def check(path: Path) -> list[str]:
    """The failures of the snapshot's gates (empty when it passes)."""
    connection = duckdb.connect(str(path), read_only=True)
    failures: list[str] = []
    try:
        boiling = _boiling_points()
        rows = connection.execute(
            "SELECT cid, solvent_name, flash_point_c, autoignition_c, vapor_pressure_kpa, vapor_pressure_basis, "
            "ghs_signal_word, ghs_hazard_statements, ghs_pictograms FROM pubchem_safety").fetchall()
        for cid, name, flash, autoignition, vapor, vapor_basis, signal, statements, pictograms in rows:
            if flash is not None and flash < -150 and boiling.get(cid, 0.0) > -100:
                failures.append(f"{name}: flash point {flash:.0f} °C is impossible for a liquid boiling at "
                                f"{boiling.get(cid)} °C")
            if autoignition is not None and autoignition < 0:
                failures.append(f"{name}: negative autoignition temperature {autoignition:.0f} °C (a misread range)")
            if vapor is not None and (vapor < 0 or not vapor_basis):
                failures.append(f"{name}: vapour pressure {vapor} without a basis")
            kept = json.loads(statements or "[]")
            danger = any("[Danger" in text for text in kept)
            expected_signal = "Danger" if danger else "Warning" if any("[Warning" in text for text in kept) else None
            if signal != expected_signal:
                failures.append(f"{name}: signal word {signal} but its statements give {expected_signal}")
            codes = {code for text in kept for code in safety._GHS_CODE.findall(text)}
            if set(json.loads(pictograms or "[]")) - set(safety._pictograms_for(codes)):
                failures.append(f"{name}: a pictogram none of its statements supports")
        skipped = connection.execute(
            "SELECT count(*) FROM pubchem_safety WHERE ghs_hazard_statements LIKE '%reaction mass%'").fetchone()[0]
        if skipped:
            failures.append(f"{skipped} solvents still carry statements from another substance's EU entry")
        missing_chem21 = connection.execute("SELECT count(*) FROM chem21_scores").fetchone()[0]
        if missing_chem21 != len(rows):
            failures.append(f"CHEM21 scores for {missing_chem21} of {len(rows)} solvents")
    finally:
        connection.close()
    agreement = guide_agreement(path)
    if agreement["flash_same_band"] < MIN_GUIDE_FLASH_BANDS:
        failures.append(f"only {agreement['flash_same_band']} of {agreement['flash_points']} CHEM21 guide flash "
                        f"points in the right Safety band (gate {MIN_GUIDE_FLASH_BANDS})")
    return failures


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dissolve.safety_snapshot", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build_command = commands.add_parser("build", help="build a snapshot from a raw PubChem pull")
    build_command.add_argument("raw", type=Path)
    build_command.add_argument("out", type=Path)
    build_command.add_argument("--fetched-at", default=None,
                               help="when the raw pull was taken (UTC ISO); defaults to the raw file's mtime")
    check_command = commands.add_parser("check", help="apply the snapshot's gates")
    check_command.add_argument("snapshot", type=Path)
    args = parser.parse_args(argv)
    if args.command == "build":
        fetched = args.fetched_at or datetime.fromtimestamp(args.raw.stat().st_mtime, timezone.utc).isoformat()
        digest = build(args.raw, args.out, fetched)
        print(f"content_digest {digest}\nfile_sha256 {hashlib.sha256(args.out.read_bytes()).hexdigest()}")
        failures = check(args.out)
    else:
        failures = check(args.snapshot)
    print("CHEM21 guide agreement:", json.dumps(guide_agreement(args.out if args.command == "build" else args.snapshot)))
    for failure in failures:
        print("FAIL", failure)
    print(f"{len(failures)} gate failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
