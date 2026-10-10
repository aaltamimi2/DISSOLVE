"""Fetch melting points of the solvents the pathway tool can cost from PubChem and save them with their source.

    .venv/bin/python campaigns/waste-pathway-film-case/fetch_melting_points.py

DISSOLVE stores boiling points but no melting points, yet a solvent that is solid at the process's cooling temperature cannot be
used for a dissolve-then-precipitate wash. For each costable solvent this reads every 'Melting Point' string PubChem lists for
its compound, converts the temperatures it can parse to Celsius (a range counts as its midpoint) and keeps the median. Values
PubChem does not state, or that cannot be parsed, are left out and the solvent is treated as unchecked. Writes
src/dissolve/data/solvent_melting_points.json.
"""
from __future__ import annotations

import json
import re
import statistics
import time
import urllib.request
from datetime import date
from pathlib import Path

import duckdb

from dissolve import waste_pathway_data as dl

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "src" / "dissolve" / "data"
PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:to|-|–|—)?\s*(-?\d+(?:\.\d+)?)?\s*°\s*([CF])", re.I)


def parse(text: str) -> list[float]:
    if re.search(r"heat of fusion|decompos|sublim|boil", text, re.I):
        return []
    found = []
    for low, high, unit in PATTERN.findall(text):
        values = [float(low)] + ([float(high)] if high else [])
        mid = sum(values) / len(values)
        found.append((mid - 32.0) * 5.0 / 9.0 if unit.upper() == "F" else mid)
    return found


def melting_strings(cid: int) -> list[str]:
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON?heading=Melting+Point"
    with urllib.request.urlopen(url, timeout=30) as response:
        record = json.load(response)
    out: list[str] = []

    def walk(sections):
        for section in sections:
            if section.get("TOCHeading") == "Melting Point":
                for info in section.get("Information", []):
                    out.extend(v["String"] for v in info.get("Value", {}).get("StringWithMarkup", []))
            walk(section.get("Section", []))

    walk(record["Record"]["Section"])
    return out


def main() -> None:
    con = duckdb.connect(str(DATA / "thermodynamics.duckdb"), read_only=True)
    cids = {row[0]: row[1] for row in con.execute("select cosmobase_name, cid from solvent_data where cid is not null").fetchall()}
    result: dict[str, dict] = {}
    for name in dl.admitted_solvents(in_scope_only=False):
        cid = cids.get(name)
        record: dict = {"cid": cid}
        if cid is None:
            record["status"] = "no PubChem CID in DISSOLVE"
        else:
            try:
                values = [v for s in melting_strings(int(cid)) for v in parse(s)]
            except Exception as error:  # a missing heading or a network error leaves the solvent unchecked
                values, record["status"] = [], f"fetch failed: {type(error).__name__}"
            if values:
                record.update({"melting_point_c": round(statistics.median(values), 1), "n_values": len(values),
                               "min_c": round(min(values), 1), "max_c": round(max(values), 1)})
            else:
                record.setdefault("status", "no parsable melting point")
        result[name] = record
        print(f"{name:34s} {record.get('melting_point_c', '-')!s:>8}  ({record.get('n_values', 0)} values; {record.get('status', 'ok')})", flush=True)
        time.sleep(0.25)
    out = {"source": "PubChem PUG-View, heading 'Melting Point', median of parsed Celsius values", "retrieved": date.today().isoformat(),
           "solvents": result}
    (DATA / "solvent_melting_points.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    print("wrote", DATA / "solvent_melting_points.json")


if __name__ == "__main__":
    main()
