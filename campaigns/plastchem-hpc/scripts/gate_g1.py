"""A-13 gate G1 (charter A-13 item 5c), on the first 100 new rows (chunk c01) once every one is disposed:

1. failure rate at most 10%: failures are preparation and ORCA failures; D-IDENT rejections are results, not failures, and
   a task the scheduler killed (preemption, node failure) is resubmitted and never counts (A-13 owner NOTE 2026-10-08).
2. cost per structure against the campaign fit (seconds = exp(-0.687758) * atoms^2.305719 * 1.113048, AMD EPYC 7763),
   refit with the CPU model named, and the projection for the whole work list at most 6,000 ORCA CPU-hours.
3. octanol/water logP of the new S, P and Si structures against measured values: the campaign's CompTox experimental set
   (public dashboard batch export by InChIKey, then by CAS; explicit experimental "LogKow: Octanol-Water" rows only, with
   a source and a citation, joined on the InChIKey connectivity block), computed exactly as the CHNO parity plots were
   (thermodynamic_prediction.activity and pair('octanol', 'water'): openCOSMO-RS 24a, 298.15 K, the bounded dilution
   ladder, the concentration basis with water 18.07 and octan-1-ol 158.528 cm3/mol), with RMSE and bias beside the CHNO
   campaign's. It fails if the S, P or Si RMSE is above 1.5 times the CHNO value or its mean bias above 1 log unit.

    python3 scripts/gate_g1.py fetch       CompTox export for the converged S, P and Si structures (resumable)
    python3 scripts/gate_g1.py evaluate    the gate: state/coverage-v1/gate-g1.json and reports/coverage-v1/G1/"""
import csv
import datetime
import hashlib
import json
import math
import re
import statistics
import sys
import time
from pathlib import Path

import numpy as np

R = Path(__file__).resolve().parents[1]
P = R / "state/coverage-v1"
OUT = R / "reports/coverage-v1/G1"
B = Path("/mnt/r/plastchem-euler/coverage-v1/G1")
EXPORT = "https://comptox.epa.gov/dashboard-api/batchsearch/export/"
#: the CHNO campaign's octanol/water validation (state/contaminant-overview-draft-20260917.json, recomputed independently):
#: the final cumulative parity, n = 1,179 qualified measured values (PubChem, OPERA, CompTox); and its CompTox-only stage
CHNO = {"n1179_combined": dict(n=1179, MAE=0.6969126168275085, RMSE=1.0225630844236853, bias=0.5496622707548298,
                               slope=0.9495450304362559, intercept=0.6738394827211347,
                               source="state/contaminant-overview-draft-20260917.json"),
        "n622_comptox_final1631": dict(n=622, MAE=0.8888911074873059, RMSE=1.249065108010809, bias=0.7534334171576113,
                                       slope=0.8434786589151252, intercept=1.3311758316003055,
                                       source="state/validation-comptox-final1631-verified-20260917.json")}
FIT = (-0.687758, 2.305719, 1.113048)
SCHEDULER = {"scheduler_signal_15", "slurm_preempted", "slurm_node_fail", "slurm_cancelled", "slurm_boot_fail"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def fit_seconds(atoms):
    return math.exp(FIT[0]) * atoms ** FIT[1] * FIT[2]


def cohort():
    manifest = json.loads((P / "c01/manifest.json").read_text())
    records = {m["inchikey"]: json.loads((P / "records" / f"{m['inchikey']}.json").read_text())
               if (P / "records" / f"{m['inchikey']}.json").exists() else None for m in manifest["molecules"]}
    return manifest["molecules"], records


def has_spsi(m):
    return bool(re.findall(r"S(?!i)|P|Si", m["elements"]))


def spsi(molecules, records):
    return [m for m in molecules if has_spsi(m) and (records[m["inchikey"]] or {}).get("status") == "converged"]


def fetch():
    import requests
    molecules, records = cohort()
    keys = [m["inchikey"] for m in molecules if has_spsi(m)]
    B.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    for label, ids, kind in (("inchikey", keys, "INCHIKEY"), ("cas", None, "CASRN")):
        target = B / f"comptox-{label}-properties.xlsx"
        if target.exists():
            continue
        if ids is None:  # CAS search for the keys the InChIKey export did not map
            found = mapped(B / "comptox-inchikey-properties.xlsx")
            cas = {c: m["inchikey"] for m in molecules if m["inchikey"] in keys and m["inchikey"] not in found
                   for c in m["cas"].split(";") if c and c != "nan"}
            (B / "comptox-cas-map.json").write_text(json.dumps(cas, indent=1) + "\n")
            ids = sorted(cas)
            if not ids:
                continue
        request = {"identifierTypes": [kind], "massError": 0, "downloadItems": ["CASRN", "INCHIKEY", "CHEMICAL_PROPERTIES_DETAILS"],
                   "searchItems": "\n".join(ids), "inputType": "IDENTIFIER", "downloadType": "EXCEL"}
        (B / f"comptox-{label}-request.json").write_text(json.dumps(request, indent=1) + "\n")
        r = session.post(EXPORT, json=request, timeout=60)
        r.raise_for_status()
        job = r.text.strip().strip('"')
        for _ in range(60):
            s = session.get(EXPORT + "status/" + job, timeout=40)
            s.raise_for_status()
            if s.text.strip().lower() == "true":
                break
            time.sleep(20)
        else:
            sys.exit(f"CompTox export {job} still pending")
        c = session.get(EXPORT + "content/" + job, timeout=120)
        c.raise_for_status()
        assert c.content.startswith(b"PK"), "expected XLSX bytes"
        target.write_bytes(c.content)
        (B / f"comptox-{label}-retrieval.json").write_text(json.dumps(dict(url=EXPORT + "content/" + job, export_id=job,
                                                                              retrieved_utc=utc(), sha256=sha(target),
                                                                              identifiers=len(ids)), indent=1) + "\n")
        print(json.dumps(dict(export=label, identifiers=len(ids), bytes=len(c.content))))
    observations = measured()  # parsed here (openpyxl); evaluate() reads the JSON in the openCOSMO-RS environment
    (B / "measured.json").write_text(json.dumps(observations, indent=1, sort_keys=True) + "\n")
    print(json.dumps(dict(structures_with_measured_logkow=len(observations),
                          observations=sum(len(v) for v in observations.values()))))


def mapped(path, cas_map=None):
    """InChIKey -> set of DTXSIDs whose structure has the same connectivity block (the campaign's join rule)."""
    import openpyxl
    out = {}
    if not path.exists():
        return out
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for row in list(book["Main Data"].values)[1:]:
        given, _found, dtxsid, _name, _cas, key = row[:6]
        if not dtxsid or not key:
            continue
        inputs = [cas_map[given]] if cas_map is not None and given in cas_map else ([given] if cas_map is None else [])
        for k in inputs:
            if key.split("-")[0] == k.split("-")[0]:
                out.setdefault(k, set()).add(dtxsid)
    return out


def measured():
    """InChIKey -> list of qualified explicit experimental logKow observations."""
    import openpyxl
    out = {}
    cas_map = json.loads((B / "comptox-cas-map.json").read_text()) if (B / "comptox-cas-map.json").exists() else {}
    for label, path in (("inchikey", B / "comptox-inchikey-properties.xlsx"), ("cas", B / "comptox-cas-properties.xlsx")):
        if not path.exists():
            continue
        dtx = {}
        for key, ids in mapped(path, cas_map if label == "cas" else None).items():
            for i in ids:
                dtx.setdefault(i, set()).add(key)
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for index, row in enumerate(list(book["Chemical Properties"].values)[1:], 2):
            dtxsid, _cid, kind, name, value, units, source, description = row[:8]
            if kind != "experimental" or name != "LogKow: Octanol-Water":
                continue
            try:
                number = float(value)
                assert math.isfinite(number) and units == "Log10 unitless" and source and description
            except (TypeError, ValueError, AssertionError):
                continue
            for key in dtx.get(dtxsid, ()):
                obs = dict(value=number, source=source, description=description, dtxsid=dtxsid, export=label, row=index)
                if obs not in out.setdefault(key, []):
                    out[key].append(obs)
    return out


def octanol_water(molecules, records):
    sys.path.insert(0, str(R / "scripts"))
    from thermodynamic_prediction import VOLUME_DATA, VOLUMES, activity, pair
    octanol_record = json.loads((R / "state/campaign-v1/records/KBPLFHHGFOOTCA-UHFFFAOYSA-N.json").read_text())
    assert octanol_record["cpu_model"] == "AMD EPYC 7763 64-Core Processor"
    octanol = dict(solvent_key="octanol", surface=str(Path(octanol_record["archive_path"]) / "surface.orcacosmo"),
                   surface_sha256=octanol_record["surface_sha256"])
    water = dict(solvent_key="water", surface="/mnt/r/plastchem-euler/results/XLYOFNOQVPJJNP-UHFFFAOYSA-N/surface.orcacosmo",
                 surface_sha256="da09de46b52463d51ad9534346ff47eeda0d2e24803a67a00503ff65cd77f750")
    volume = json.loads((R / "state/progress-2026-09-14/octanol-molar-volume.json").read_text())
    assert sha(volume["source_xml_path"]) == volume["source_xml_sha256"]
    VOLUMES["octanol"] = volume["molar_volume_cm3_mol"]
    VOLUME_DATA["octanol"] = volume
    out = {}
    for m in molecules:
        r = records[m["inchikey"]]
        solute = dict(surface=str(Path(r["archive_path"]) / "surface.orcacosmo"), surface_sha256=r["surface_sha256"])
        values = {"water": activity(solute, water), "octanol": activity(solute, octanol)}
        out[m["inchikey"]] = pair("octanol", "water", values)
    return out


def stats(pairs):
    x = np.array([p[0] for p in pairs])
    y = np.array([p[1] for p in pairs])
    error = y - x
    slope, intercept = (np.polyfit(x, y, 1) if len(pairs) > 1 else (float("nan"), float("nan")))
    return dict(n=len(pairs), MAE=float(abs(error).mean()), RMSE=float(np.sqrt((error ** 2).mean())), bias=float(error.mean()),
                slope=float(slope), intercept=float(intercept))


def evaluate():
    molecules, records = cohort()
    outcome = {}
    for m in molecules:
        r = records[m["inchikey"]]
        if r is None or r.get("status") not in ("converged", "failed"):
            outcome[m["inchikey"]] = "not_final"
        elif r["status"] == "converged":
            outcome[m["inchikey"]] = "converged"
        elif r.get("failure_mode") == "return_integrity_or_connectivity":
            outcome[m["inchikey"]] = "rejected"
        elif r.get("failure_mode") in SCHEDULER or str((r.get("slurm_accounting") or {}).get("state", "")).startswith("PREEMPTED"):
            outcome[m["inchikey"]] = "scheduler_killed"
        elif r.get("failure_mode") in ("walltime_censored", "scheduler_signal_10", "slurm_timeout"):
            outcome[m["inchikey"]] = "time_limit"
        else:
            outcome[m["inchikey"]] = "failed"
    counts = {k: list(outcome.values()).count(k) for k in sorted(set(outcome.values()))}
    if counts.get("not_final") or counts.get("scheduler_killed") or counts.get("time_limit"):
        print(json.dumps(dict(gate="G1", status="waiting", counts=counts)))
        return
    failures = [dict(inchikey=k, name=next(m["name"] for m in molecules if m["inchikey"] == k),
                     failure_mode=records[k].get("failure_mode"), error=(records[k].get("error") or "")[:200])
                for k, v in outcome.items() if v == "failed"]
    rate = len(failures) / len(molecules)
    # cost
    rows = []
    for m in molecules:
        r = records[m["inchikey"]]
        if outcome[m["inchikey"]] != "converged":
            continue
        wall = sum(r["stages"][s]["wall_seconds"] for s in ("opt", "cosmo"))
        rows.append(dict(atoms=m["atoms"], wall=wall, fit=fit_seconds(m["atoms"]), cpu=r["cpu_model"], kind=m["kind"],
                         elements=m["elements"]))
    by_cpu = {}
    for row in rows:
        by_cpu.setdefault(row["cpu"], []).append(row)
    refits = {}
    for cpu, items in by_cpu.items():
        a = np.log([i["atoms"] for i in items])
        w = np.log([i["wall"] for i in items])
        b1, b0 = np.polyfit(a, w, 1)
        ratios = [i["wall"] / i["fit"] for i in items]
        refits[cpu] = dict(n=len(items), log_wall_intercept=float(b0), exponent=float(b1),
                           measured_over_fit_median=statistics.median(ratios), measured_over_fit_mean=statistics.fmean(ratios),
                           measured_cpu_hours=sum(i["wall"] for i in items) / 3600,
                           fit_cpu_hours=sum(i["fit"] for i in items) / 3600, atoms_range=[min(i["atoms"] for i in items),
                                                                                           max(i["atoms"] for i in items)])
    worklist = list(csv.DictReader((R / "inputs/plastchem_coverage_a13_worklist.csv").open()))
    never = [int(r["atoms_with_h"]) for r in worklist if not r["release_status"]]
    fit_total = sum(fit_seconds(a) for a in never) / 3600
    all_ratio = statistics.fmean([r["wall"] / r["fit"] for r in rows])
    projection = dict(fit_cpu_hours=fit_total, scaled_by_mean_measured_over_fit=fit_total * all_ratio,
                      mean_measured_over_fit=all_ratio,
                      note="c01 spans 3-24 atoms; the whole list reaches several hundred, so the exponent refit on c01 alone "
                           "is not extrapolated: the projection scales the campaign fit by c01's measured/fit ratio")
    # octanol/water for the S, P and Si structures
    targets = spsi(molecules, records)
    obs = json.loads((B / "measured.json").read_text())
    predictions = octanol_water([m for m in targets if m["inchikey"] in obs], records)
    pairs, table = [], []
    for m in targets:
        k = m["inchikey"]
        if k not in obs:
            continue
        values = sorted(o["value"] for o in obs[k])
        reference = statistics.median(values)
        pred = predictions[k]
        if pred.get("status") != "predicted" or pred.get("log10_K_concentration") is None:
            table.append(dict(inchikey=k, name=m["name"], elements=m["elements"], measured=reference, predicted=None,
                              status=pred.get("status")))
            continue
        pairs.append((reference, pred["log10_K_concentration"]))
        table.append(dict(inchikey=k, name=m["name"], elements=m["elements"], measured_logKow=reference,
                          observations=len(values), predicted_logKow=pred["log10_K_concentration"],
                          residual=pred["log10_K_concentration"] - reference, sources=sorted({o["source"] for o in obs[k]})))
    spsi_stats = stats(pairs) if pairs else None
    chno = CHNO["n1179_combined"]  # the stricter of the two CHNO baselines (RMSE 1.02 against 1.25)
    by_element = {}
    for element, pattern in (("S", r"S(?!i)"), ("P", r"P"), ("Si", r"Si")):
        sub = [(row["measured_logKow"], row["predicted_logKow"]) for row in table
               if row.get("predicted_logKow") is not None and re.search(pattern, row["elements"])]
        if sub:
            by_element[element] = stats(sub)
    checks = dict(
        failure_rate=dict(value=rate, limit=0.10, passed=rate <= 0.10),
        projection=dict(value=projection["scaled_by_mean_measured_over_fit"], limit=6000,
                        passed=projection["scaled_by_mean_measured_over_fit"] <= 6000),
        logp_rmse=dict(value=spsi_stats and spsi_stats["RMSE"], limit=1.5 * chno["RMSE"],
                       passed=bool(spsi_stats) and spsi_stats["RMSE"] <= 1.5 * chno["RMSE"]),
        logp_bias=dict(value=spsi_stats and spsi_stats["bias"], limit=1.0,
                       passed=bool(spsi_stats) and abs(spsi_stats["bias"]) <= 1.0))
    for element, st in by_element.items():  # each class at any n (the stricter reading of "the S, P or Si RMSE")
        checks[f"logp_rmse_{element}"] = dict(value=st["RMSE"], n=st["n"], limit=1.5 * chno["RMSE"],
                                              passed=st["RMSE"] <= 1.5 * chno["RMSE"])
        checks[f"logp_bias_{element}"] = dict(value=st["bias"], n=st["n"], limit=1.0, passed=abs(st["bias"]) <= 1.0)
    verdict = "pass" if all(c["passed"] for c in checks.values()) else "fail"
    result = dict(utc=utc(), gate="G1", verdict=verdict, cohort="chunk c01: the first 100 never-run work-list rows (orders 1-107)",
                  outcomes=counts, failures=failures, checks=checks, cost=dict(by_cpu_model=refits, projection=projection),
                  octanol_water=dict(spsi=spsi_stats, by_element=by_element, chno=CHNO, structures_converged=len(targets),
                                     with_measured=len(table), table=table,
                                     basis="openCOSMO-RS 24a, 298.15 K, dilution ladder, log10 K concentration basis; water "
                                           "18.07, octan-1-ol 158.528 cm3/mol; CompTox explicit experimental LogKow, median "
                                           "of a structure's observations"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "gate-g1.json").write_text(json.dumps(result, indent=2) + "\n")
    (P / "gate-g1.json").write_text(json.dumps({k: v for k, v in result.items() if k != "octanol_water"} |
                                               {"octanol_water": {k: v for k, v in result["octanol_water"].items() if k != "table"}},
                                               indent=2) + "\n")
    print(json.dumps(dict(verdict=verdict, outcomes=counts, checks=checks, spsi=spsi_stats), indent=1))


if __name__ == "__main__":
    {"fetch": fetch, "evaluate": evaluate}[sys.argv[1]]()
