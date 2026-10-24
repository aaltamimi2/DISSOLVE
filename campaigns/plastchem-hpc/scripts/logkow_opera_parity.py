"""Octanol/water partition coefficients (log Kow) of the structures DISSOLVE serves, compared with the OPERA QSAR
log Kow of the PlastChem inventory (Monclus et al., Nature 2025, doi:10.1038/s41586-025-09184-8, source data of Extended
Data Fig. 3, file 41586_2025_9184_MOESM8_ESM.csv). Validation only: nothing here enters a release.

Our log Kow is the validation-only quantity of September 2026, with its frozen recipe (thermodynamic_prediction.activity
and pair): openCOSMO-RS 24a, 298.15 K, pure-component reference, infinite dilution checked on a dilution grid, the
campaign's own dry 1-octanol surface (KBPLFHHGFOOTCA) against the A-1 water surface (XLYOFNOQVPJJNP), log10 K in the
concentration basis with the NIST/TRC octanol molar volume and 18.07 cm3/mol for water. The 5,803 structures of the main
campaign keep their audited September results (digests checked). The other tiers (halogen, tier 2, coverage,
publication) never had it; "compute" calculates it for them, after recomputing 20 September results bit for bit, into
state/logkow-octanol-v1/octanol/<InChIKey>.json.

The OPERA values (OPERA v2.9.1, LogP v2.6) are the paper's: only predictions inside OPERA's applicability domain are
finite, the rest are -Inf and are not used. They are QSAR predictions, not measurements. A CSV row is paired with the
structure DISSOLVE serves for its PlastChem ID (directly or as the neutral parent of a salt), and only when OPERA's
standardized structure has the same monoisotopic mass as ours (|difference| < 0.001 Da); a structure several rows reach
is kept once, and only when those rows agree on the value.

The served set is promotion-v14 when that release is sealed; until then it is promotion-v13 with the changes of the
v14 dry run (the converged coverage records it adds, Acid fuchsine FB withdrawn), and the outputs say which.

    ~/.venvs/cosmo-logp/bin/python scripts/logkow_opera_parity.py compute [--workers 4]
    ~/.venvs/cosmo-logp/bin/python scripts/logkow_opera_parity.py compare
writes exploratory/logkow-opera/: pairs.csv, summary.json, parity_opera.png"""
import os

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
import argparse
import csv
import datetime
import gzip
import hashlib
import json
import math
import random
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thermodynamic_prediction import CONFIG, VOLUME_DATA, VOLUMES, activity, pair  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
B = Path("/mnt/r/plastchem-euler")
OUT = ROOT / "exploratory/logkow-opera"
STATE = ROOT / "state/logkow-octanol-v1"
OPERA_CSV = Path("/home/aaltamimi2/41586_2025_9184_MOESM8_ESM.csv")
OPERA_CSV_SHA256 = "39fbcd5a6f736047c098434705130d453d0bfb79cc329688ffb9feca7d90c356"
WATER = dict(solvent_key="water", surface=str(B / "results/XLYOFNOQVPJJNP-UHFFFAOYSA-N/surface.orcacosmo"),
             surface_sha256="da09de46b52463d51ad9534346ff47eeda0d2e24803a67a00503ff65cd77f750")
OCTANOL = dict(solvent_key="octanol", surface=str(B / "results/KBPLFHHGFOOTCA-UHFFFAOYSA-N/surface.orcacosmo"),
               surface_sha256="dc75fb86de4537145effb6648e488437232d9a5224f86a97475e2dc06d9eb5a9")
#: the September octanol packages (scripts/compare_final5803_20260917.py reads the same set)
SEPTEMBER = ["octanol-catchup-2026-09-17", "octanol-followup-2026-09-17", "octanol-post3883-2026-09-17",
             "octanol-post4060-2026-09-17", "octanol-post4172-2026-09-17", "octanol-post4234-2026-09-17",
             "octanol-post4244-2026-09-17", "octanol-post4261-2026-09-17", "octanol-post4353-2026-09-17",
             "octanol-post4455-2026-09-17", "octanol-final1171-2026-09-17"]
RECORD_DIRS = {"main": "campaign-v1", "halogen": "halogen-v1", "tier2": "tier2-v1", "coverage": "coverage-v1"}
MASS_TOLERANCE = 1e-3
#: the September experimental validation (curated measured log Kow, n = 1,179)
MEASURED = B / "validation-final5803-2026-09-17/parity.csv"

vol = json.loads((ROOT / "state/progress-2026-09-14/octanol-molar-volume.json").read_text())
assert hashlib.sha256(Path(vol["source_xml_path"]).read_bytes()).hexdigest() == vol["source_xml_sha256"]
VOLUMES["octanol"] = vol["molar_volume_cm3_mol"]
VOLUME_DATA["octanol"] = vol


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def served():
    """InChIKey -> release row of every structure served, and a label of the release it is."""
    sealed = B / "promotion-v14/manifest.json"
    if sealed.exists() and json.loads(sealed.read_text())["status"] == "complete":
        with gzip.open(B / "promotion-v14/contaminants.csv.gz", "rt") as handle:
            rows = {r["input_inchikey"]: r for r in csv.DictReader(handle)}
        return {k: r for k, r in rows.items() if r["campaign_status_at_snapshot"] == "converged"}, "promotion-v14", {}
    with gzip.open(B / "promotion-v13/contaminants.csv.gz", "rt") as handle:
        cohort = {r["input_inchikey"]: r for r in csv.DictReader(handle)}
    out = {k: r for k, r in cohort.items() if r["campaign_status_at_snapshot"] == "converged"}
    coverage = {p.stem: json.loads(p.read_text()) for p in (ROOT / "state/coverage-v1/records").glob("*.json")}
    withdrawn = sorted(k for k in out if coverage.get(k, {}).get("identity_withdrawn") and coverage[k]["status"] == "failed")
    added = sorted(k for k, r in coverage.items() if r["status"] == "converged" and k not in out)
    for k in withdrawn:
        del out[k]
    from build_promotion_coverage import as_parent_entries, coverage_molecules
    molecules = coverage_molecules(coverage)
    parents = as_parent_entries(set(added))
    extra_ids = {}
    for k in added:
        row = dict(cohort.get(k) or dict(input_inchikey=k, tier="coverage", name=molecules[k]["name"],
                                          smiles=molecules[k]["smiles"], plastchem_id=molecules[k]["plastchem_id"]))
        row["surface_sha256"] = coverage[k]["surface_sha256"]
        out[k] = row
        extra_ids[k] = [pid for pid, *_ in parents.get(k, [])]
    label = f"promotion-v13 + v14 dry run ({len(added)} added, withdrawn {withdrawn})"
    return out, label, extra_ids


def surface_of(key, row):
    """The surface file of a served structure, found by its digest in the release row; an error when none matches."""
    candidates = []
    for directory in {RECORD_DIRS.get(row["tier"]), "coverage-v1"} - {None}:
        record = ROOT / "state" / directory / "records" / f"{key}.json"
        if record.exists() and json.loads(record.read_text()).get("archive_path"):
            candidates.append(Path(json.loads(record.read_text())["archive_path"]) / "surface.orcacosmo")
    candidates.append(ROOT / "state/publication-v1/incoming/returns" / key / "surface.orcacosmo")
    for path in candidates:
        if path.exists() and sha(path) == row["surface_sha256"]:
            return path
    raise SystemExit(f"{key}: no surface file with the released digest {row['surface_sha256']}")


def september():
    """InChIKey -> (result path, sha256) of the audited September octanol results, as compare_final5803 reads them."""
    lookup = {}
    for row in csv.DictReader((B / "measured-expansion-2026-09-15/all-predictions.csv").open()):
        lookup[row["input_inchikey"]] = (Path(row["result_path"]), row["result_sha256"])
    audits = [*sorted(B.glob("post*-octanol-2026-09-15/numerical-audit.json")), *(B / f / "numerical-audit.json" for f in SEPTEMBER)]
    for path in audits:
        audit = json.loads(path.read_text())
        assert audit["failed"] == 0, path
        for row in audit["rows"]:
            if row["status"] == "passed":
                lookup[row["inchikey"]] = (path.parent / "octanol" / f"{row['inchikey']}.json", row["result_sha256"])
    return lookup


def one(task):
    key, surface, surface_sha = task
    solute = dict(surface=surface, surface_sha256=surface_sha)
    water, octanol = activity(solute, WATER), activity(solute, OCTANOL)
    prediction = pair("octanol", "water", {"octanol": octanol, "water": water})
    return dict(input_inchikey=key, solute_surface=surface, solute_surface_sha256=surface_sha, water_activity=water,
                octanol_activity=octanol, prediction=prediction,
                utc=datetime.datetime.now(datetime.timezone.utc).isoformat())


def compute(workers):
    rows, label, _ = served()
    lookup = september()
    main = [k for k, r in rows.items() if r["tier"] == "main"]
    assert set(main) <= set(lookup), "a main-campaign structure without a September octanol result"
    # the recipe reproduces September bit for bit before anything new is computed
    random.seed(20261020)
    check = []
    for key in random.sample(sorted(main), 20):
        path, digest = lookup[key]
        old = json.loads(path.read_bytes())
        assert sha(path) == digest and old["solute_surface_sha256"] == rows[key]["surface_sha256"]
        new = one((key, str(surface_of(key, rows[key])), rows[key]["surface_sha256"]))
        same = new["prediction"] == old["prediction"] or (
            new["prediction"]["status"] == old["prediction"]["status"] == "predicted"
            and new["prediction"]["log10_K_concentration"] == old["prediction"]["log10_K_concentration"])
        check.append(dict(inchikey=key, september=old["prediction"].get("log10_K_concentration"),
                          now=new["prediction"].get("log10_K_concentration"), identical=same))
    assert all(c["identical"] for c in check), json.dumps(check, indent=1)
    todo = []
    for key, row in sorted(rows.items()):
        if row["tier"] == "main" or (STATE / "octanol" / f"{key}.json").exists():
            continue
        todo.append((key, str(surface_of(key, row)), row["surface_sha256"]))
    write(STATE / "provenance.json", dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), served=label,
                                          config=CONFIG, water=WATER, octanol=OCTANOL, octanol_volume=vol,
                                          water_volume_cm3_mol=VOLUMES["water"], reproduction_check=check,
                                          script_sha256=sha(__file__), to_compute=len(todo)))
    print(json.dumps(dict(served=label, reproduction_identical=len(check), to_compute=len(todo))), flush=True)
    done = 0
    with Pool(workers) as pool:
        for result in pool.imap_unordered(one, todo, chunksize=4):
            write(STATE / "octanol" / f"{result['input_inchikey']}.json", result)
            done += 1
            if done % 200 == 0:
                print(json.dumps(dict(done=done, of=len(todo))), flush=True)
    print(json.dumps(dict(done=done)), flush=True)


def ours():
    """InChIKey -> (status, log Kow, source) for every served structure; an error when one has no result yet."""
    rows, label, extra_ids = served()
    lookup = september()
    out = {}
    for key, row in rows.items():
        if row["tier"] == "main":
            path, digest = lookup[key]
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == digest, key
            source = "September audited result"
        else:
            path = STATE / "octanol" / f"{key}.json"
            if not path.exists():
                raise SystemExit(f"{key} ({row['tier']}) has no octanol result yet: run compute first")
            raw = path.read_bytes()
            source = "computed here"
        result = json.loads(raw)
        assert result["solute_surface_sha256"] == row["surface_sha256"], key
        p = result["prediction"]
        out[key] = dict(status=p["status"], logkow=p.get("log10_K_concentration"), source=source, result_path=str(path))
    return rows, label, extra_ids, out


def compare():
    from rdkit import Chem, RDLogger
    from rdkit.Chem import Descriptors
    RDLogger.DisableLog("rdApp.*")
    import numpy as np
    # acids whose neutral form, the species openCOSMO-RS describes here, is not what partitions at any usual pH
    STRONG_ACIDS = [Chem.MolFromSmarts(s) for s in ("[SX4](=O)(=O)[OX2H1]", "[PX4](=O)[OX2H1]")]
    rows, label, extra_ids, values = ours()
    assert sha(OPERA_CSV) == OPERA_CSV_SHA256, "the OPERA source data file changed"
    by_id = {}
    for key, row in rows.items():
        for pid in [i.strip() for i in row["plastchem_id"].replace(",", ";").split(";") if i.strip()] + extra_ids.get(key, []):
            assert by_id.setdefault(pid, key) == key, f"PlastChem entry {pid} reaches two structures"
    opera = list(csv.DictReader(OPERA_CSV.open()))
    finite = [r for r in opera if r["logKOW"] not in ("-Inf", "Inf", "NA", "") and math.isfinite(float(r["logKOW"]))]
    mass = {}
    reached, mismatched = {}, []
    for r in finite:
        key = by_id.get(r["plastChemID"])
        if key is None:
            continue
        if key not in mass:
            mol = Chem.MolFromSmiles(rows[key]["smiles"])
            mass[key] = Descriptors.ExactMolWt(mol)
        if abs(mass[key] - float(r["molecularWeight"])) >= MASS_TOLERANCE:
            mismatched.append(dict(plastchem_id=r["plastChemID"], inchikey=key, opera_mass=float(r["molecularWeight"]),
                                   our_mass=round(mass[key], 5)))
            continue
        reached.setdefault(key, []).append(r)
    pairs, conflicting, unavailable = [], [], []
    for key, matched in sorted(reached.items()):
        opera_values = sorted({float(r["logKOW"]) for r in matched})
        if len(opera_values) > 1:
            conflicting.append(dict(inchikey=key, plastchem_ids=[r["plastChemID"] for r in matched], opera=opera_values))
            continue
        v = values[key]
        if v["status"] != "predicted":
            unavailable.append(dict(inchikey=key, status=v["status"]))
            continue
        row = rows[key]
        mol = Chem.MolFromSmiles(row["smiles"])
        elements = {a.GetSymbol() for a in mol.GetAtoms()}
        group = ("S, P or Si" if elements & {"S", "P", "Si", "B", "Se", "Sn", "Ge", "As"} else
                 "halogenated" if elements & {"F", "Cl", "Br", "I"} else "CHNO" if elements <= {"C", "H", "N", "O"} else None)
        assert group, f"{key}: elements {sorted(elements)}"
        pairs.append(dict(input_inchikey=key, name=row["name"], tier=row["tier"], group=group,
                          strong_acid=any(mol.HasSubstructMatch(q) for q in STRONG_ACIDS), smiles=row["smiles"],
                          plastchem_ids=";".join(r["plastChemID"] for r in matched),
                          molecular_weight=round(Descriptors.MolWt(mol), 2), monoisotopic_mass=round(mass[key], 5),
                          opera_logkow=opera_values[0], opencosmors_logkow=round(v["logkow"], 4),
                          difference=round(v["logkow"] - opera_values[0], 4), our_source=v["source"]))
    if not pairs:
        raise SystemExit("no pair")
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "pairs.csv").open("w", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=list(pairs[0]))
        w.writeheader()
        w.writerows(pairs)

    def stats(sel):
        x = np.array([p["opera_logkow"] for p in sel])
        y = np.array([p["opencosmors_logkow"] for p in sel])
        d = y - x
        if len(sel) < 3:
            return dict(n=len(sel))
        slope, intercept = np.polyfit(x, y, 1)
        return dict(n=len(sel), MAE=round(float(abs(d).mean()), 3), RMSE=round(float(np.sqrt((d ** 2).mean())), 3),
                    mean_difference=round(float(d.mean()), 3), median_abs_difference=round(float(np.median(abs(d))), 3),
                    pearson_r=round(float(np.corrcoef(x, y)[0, 1]), 3),
                    r2_vs_identity=round(float(1 - (d ** 2).sum() / ((x - x.mean()) ** 2).sum()), 3),
                    slope=round(float(slope), 3), intercept=round(float(intercept), 3),
                    within_0_5=round(float((abs(d) <= 0.5).mean()), 3), within_1=round(float((abs(d) <= 1).mean()), 3))

    # where a measured log Kow exists (the September validation set), OPERA, trained on such data, is close to it
    measured = {r["input_inchikey"]: float(r["measured_logKow"]) for r in csv.DictReader(MEASURED.open())}
    overlap = [p for p in pairs if p["input_inchikey"] in measured]

    def versus_measured(field):
        d = np.array([p[field] - measured[p["input_inchikey"]] for p in overlap])
        return dict(MAE=round(float(abs(d).mean()), 3), RMSE=round(float(np.sqrt((d ** 2).mean())), 3),
                    mean_difference=round(float(d.mean()), 3), median_difference=round(float(np.median(d)), 3))

    tiers = ["main", "tier2", "halogen", "coverage", "publication"]
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), served=label, served_structures=len(rows),
                   opera_rows=len(opera), opera_finite_rows=len(finite),
                   opera_finite_rows_reaching_a_served_structure=sum(len(v) for v in reached.values()) + len(mismatched),
                   rows_rejected_by_mass=len(mismatched), structures_with_conflicting_opera_values=len(conflicting),
                   structures_without_our_prediction=unavailable, pairs=len(pairs),
                   overall=stats(pairs), without_strong_acids=stats([p for p in pairs if not p["strong_acid"]]),
                   strong_acids=stats([p for p in pairs if p["strong_acid"]]),
                   by_group={g: stats([p for p in pairs if p["group"] == g and not p["strong_acid"]])
                             for g in ("CHNO", "halogenated", "S, P or Si")},
                   by_tier={t: stats([p for p in pairs if p["tier"] == t]) for t in tiers},
                   with_a_measured_value=dict(n=len(overlap), source=str(MEASURED),
                                              opencosmors_minus_measured=versus_measured("opencosmors_logkow"),
                                              opera_minus_measured=versus_measured("opera_logkow")),
                   mass_mismatch_examples=mismatched[:15], conflicting_examples=conflicting[:15],
                   largest_differences=sorted(pairs, key=lambda p: -abs(p["difference"]))[:15],
                   opera_source="Monclus et al., Nature 2025, Extended Data Fig. 3 source data (OPERA v2.9.1, LogP v2.6; "
                                "inside the applicability domain only)",
                   ours="openCOSMO-RS 24a, dry 1-octanol vs water, 298.15 K, concentration basis; validation-only",
                   script_sha256=sha(__file__))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    figure(pairs, summary)
    print(json.dumps({k: summary[k] for k in ("served", "opera_finite_rows", "rows_rejected_by_mass",
                                               "structures_with_conflicting_opera_values", "pairs", "overall", "by_tier")},
                     indent=1))


def figure(pairs, summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    groups = [(lambda p: p["group"] == "CHNO" and not p["strong_acid"], "C, H, N, O only", "#1f77b4", "o"),
              (lambda p: p["group"] == "halogenated" and not p["strong_acid"], "halogenated", "#2ca02c", "o"),
              (lambda p: p["group"] == "S, P or Si" and not p["strong_acid"], "containing S, P or Si", "#ff7f0e", "o"),
              (lambda p: p["strong_acid"], "sulfonic, sulfuric or phosphoric acid", "black", "x")]
    LO, HI = -6.0, 14.0  # panel a; points beyond are drawn on the edge as triangles and counted
    DLO, DHI = -6.0, 16.0  # panel b
    fig = plt.figure(figsize=(12.5, 6.0))
    ax = fig.add_axes([0.065, 0.10, 0.44, 0.85])
    bx = fig.add_axes([0.60, 0.10, 0.38, 0.85])
    beyond_a = beyond_b = 0
    for test, text, color, marker in groups:
        sel = [p for p in pairs if test(p)]
        if not sel:
            continue
        x = np.array([p["opera_logkow"] for p in sel])
        y = np.array([p["opencosmors_logkow"] for p in sel])
        w = np.array([p["molecular_weight"] for p in sel])
        d = y - x
        style = dict(s=16, color=color, lw=0.8, alpha=0.8) if marker == "x" else dict(s=7, color=color, lw=0, alpha=0.45)
        inside = (x >= LO) & (x <= HI) & (y >= LO) & (y <= HI)
        ax.scatter(x[inside], y[inside], marker=marker, label=f"{text} (n={len(sel):,})", **style)
        if (~inside).any():
            beyond_a += int((~inside).sum())
            ax.scatter(np.clip(x[~inside], LO, HI), np.clip(y[~inside], LO, HI), marker="^", s=18, color=color,
                       lw=0, alpha=0.9, clip_on=False)
        inside_b = (d >= DLO) & (d <= DHI)
        bx.scatter(w[inside_b], d[inside_b], marker=marker, **style)
        if (~inside_b).any():
            beyond_b += int((~inside_b).sum())
            bx.scatter(w[~inside_b], np.clip(d[~inside_b], DLO, DHI), marker="^", s=18, color=color, lw=0, alpha=0.9,
                       clip_on=False)
    grid = np.array([LO, HI])
    ax.plot(grid, grid, color="black", lw=1)
    for band in (-1, 1):
        ax.plot(grid, grid + band, color="black", lw=0.8, ls="--")
    ax.set_xlim(LO, HI)
    ax.set_ylim(LO, HI)
    ax.set_aspect("equal")
    ax.set_xlabel("log $K_\\mathrm{OW}$, OPERA QSAR (Monclús et al. 2025)")
    ax.set_ylabel("log $K_\\mathrm{OW}$, openCOSMO-RS (this work)")
    ax.grid(alpha=0.3)
    leg = ax.legend(loc="upper left", fontsize=8, frameon=True, markerscale=2)
    for handle in leg.legend_handles:
        handle.set_alpha(1)
    a, b = summary["overall"], summary["without_strong_acids"]
    ax.text(0.98, 0.02, f"all pairs (n = {a['n']:,})\nMAE {a['MAE']:.2f}, RMSE {a['RMSE']:.2f}\nmean difference {a['mean_difference']:+.2f}, "
                        f"r = {a['pearson_r']:.2f}\n\nwithout the strong acids (n = {b['n']:,})\nMAE {b['MAE']:.2f}, RMSE {b['RMSE']:.2f}\n"
                        f"mean difference {b['mean_difference']:+.2f}, r = {b['pearson_r']:.2f}"
                        + (f"\n\n{beyond_a} points beyond {HI:g} drawn as ▲ on the edge" if beyond_a else ""),
            transform=ax.transAxes, ha="right", va="bottom", fontsize=8,
            bbox=dict(boxstyle="round", facecolor="white", edgecolor="#999999"))
    ax.text(-0.12, 1.01, "a", transform=ax.transAxes, fontsize=13, fontweight="bold")
    # running median of the difference against molecular weight, per group (the groups cover different weight ranges)
    from matplotlib import patheffects
    edges = np.arange(0, 1300, 50)
    for test, text, color, marker in groups[:3]:
        centres, medians = [], []
        for lo, hi in zip(edges[:-1], edges[1:]):
            sel = [p["difference"] for p in pairs if test(p) and lo <= p["molecular_weight"] < hi]
            if len(sel) >= 20:
                centres.append((lo + hi) / 2)
                medians.append(float(np.median(sel)))
        bx.plot(centres, medians, color=color, lw=2.2, marker="o", ms=3.5, label=f"{text}, running median",
                path_effects=[patheffects.Stroke(linewidth=3.6, foreground="black"), patheffects.Normal()])
    bx.axhline(0, color="black", lw=1)
    for band in (-1, 1):
        bx.axhline(band, color="black", lw=0.8, ls="--")
    bx.set_ylim(DLO, DHI)
    bx.set_xlim(0, max(p["molecular_weight"] for p in pairs) * 1.03)
    bx.set_xlabel("molecular weight (g/mol)")
    bx.set_ylabel("openCOSMO-RS − OPERA (log units)")
    bx.grid(alpha=0.3)
    bx.legend(loc="lower right", fontsize=8, frameon=True, title="50 g/mol bins with ≥20 structures", title_fontsize=8)
    if beyond_b:
        bx.text(0.98, 0.98, f"{beyond_b} points beyond {DHI:g} drawn as ▲ on the edge", transform=bx.transAxes,
                ha="right", va="top", fontsize=8)
    bx.text(-0.1, 1.01, "b", transform=bx.transAxes, fontsize=13, fontweight="bold")
    fig.savefig(OUT / "parity_opera.png", dpi=300)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["compute", "compare"])
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    compute(args.workers) if args.action == "compute" else compare()
