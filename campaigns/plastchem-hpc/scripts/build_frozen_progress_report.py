"""Build the owner report from frozen cohort, sealed numbers, and completed audits."""
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from octanol_report_section import section as octanol_section

ROOT = Path(__file__).resolve().parents[1]
BULK = Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14'))

def read(path):
    return json.loads(path.read_text())

def main():
    freeze = BULK/'freeze'
    numeric = BULK/'sealed-thermodynamics'
    manifest = read(freeze/'manifest.json')
    seal = read(numeric/'manifest.json')
    cohort_id = hashlib.sha256((freeze/'manifest.json').read_bytes()).hexdigest()
    assert seal['cohort_snapshot_id'] == cohort_id
    surface = read(BULK/'completed-surface-audit.json')
    support = read(BULK/'support-solvent-surface-audit.json')
    numbers = read(numeric/'production-record-audit.json')
    figures = read(BULK/'figures/figure-manifest.json')
    stats = read(BULK/'experimental-validation/statistics.json')
    table = read(numeric/'table-export.json')
    assert table['frozen'] and table['table_rows']==5824*32
    assert table['concentration_prediction_rows']==figures['partition_rows']
    table_audit=read(BULK/'table-audit.json')
    assert table_audit['status']=='passed' and table_audit['frozen']
    assert table_audit['rows_checked']==5824*32 and table_audit['table_sha256']==table['csv_sha256']
    with (BULK/'production-anchor-agreement.csv').open() as f:
        production_anchors=list(csv.DictReader(f))
    assert len(production_anchors)==4
    max_production_anchor_delta=max(abs(float(r['production_minus_workstation'])) for r in production_anchors)
    max_production_hybrid_delta=max(abs(float(r['production_minus_hybrid'])) for r in production_anchors)
    assert surface['completed_snapshot'] == len(manifest['cohort'])
    assert surface['failed'] == support['failed'] == numbers['failed'] == 0
    assert support['passed'] == 6
    assert numbers['snapshot_ledger_entries'] == seal['captured_results']
    assert numbers['passed'] == seal['captured_results']
    assert figures['mode'] == 'Frozen' and stats['frozen']
    assert stats['cohort_molecules'] == len(manifest['cohort'])
    records = {p.stem: read(p) for p in (freeze/'state/campaign-v1/records').glob('*.json')}
    eligible = read(freeze/'state/campaign-v1/eligible.json')
    ledger = read(numeric/'processing-ledger.json')
    exclusions = read(numeric/'exclusions.json')
    assert len(eligible) == 5824 and len(exclusions['records']) == 9
    rows = []
    costs = {'main_le80': 0.0, 'tail_gt80': 0.0}
    failed_cost = 0.0
    ram = [r['slurm_accounting']['maxrss_kib'] for r in records.values()
           if r.get('slurm_accounting', {}).get('maxrss_kib') is not None]
    max_ram_mib = max(ram)/1024 if ram else None
    for m in eligible:
        key = m['inchikey']; r = records.get(key, {})
        seconds = sum(s.get('wall_seconds', 0) for s in r.get('stages', {}).values())
        if r.get('status') == 'converged': costs[m['group']] += seconds/3600
        if r.get('status') == 'failed': failed_cost += seconds/3600
        rows.append({'input_inchikey': key, 'name': m['name'], 'group': m['group'],
                     'atoms': m['atoms'], 'status': r.get('status', 'not_yet_run'),
                     'failure_mode': r.get('failure_mode', ''),
                     'perceived_inchikey': r.get('perceived_inchikey', ''),
                     'identity_match_basis': r.get('identity_match_basis', ''),
                     'perceived_keys_by_engine': json.dumps(r.get('perceived_keys_by_engine', {}), sort_keys=True),
                     'perception_engines_agreeing_on_perceived_key': json.dumps(r.get('perception_engines_agreeing_on_perceived_key', [])),
                     'cpu_model': r.get('cpu_model', ''),
                     'measured_stage_wall_hours': seconds/3600 if r.get('stages') else '',
                     'available_partition_count_at_numeric_seal': ledger.get(key, {}).get('partition_count', 0),
                     'surface_sha256': r.get('surface_sha256', ''),
                     'result_sha256': ledger.get(key, {}).get('result_sha256', '')})
    for name, data in [('campaign-dispositions.csv', rows), ('excluded-isotopologues.csv', exclusions['records'])]:
        with (BULK/name).open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(data[0])); writer.writeheader(); writer.writerows(data)
    failures = [r for r in rows if r['status'] == 'failed']
    c = manifest['campaign_counts_from_frozen_records']
    counts_table = '\n'.join(f'| {k.replace("_", " ")} | {v:,} / 5,824 |' for k,v in c.items())
    failures_table = '\n'.join(f"| {r['name']} | `{r['input_inchikey']}` | {r['failure_mode']} |" for r in failures)
    if stats['matched_observations']:
        accuracy = (f"Experimental comparison covers **{stats['matched_molecules']} molecules / {stats['matched_observations']} observations**. "
                    f"MAE **{stats['MAE']:.3f}**, RMSE **{stats['RMSE']:.3f}**, bias (predicted minus observed) **{stats['bias_predicted_minus_observed']:+.3f} log units**.")
    else:
        accuracy = 'No matching predicted/experimental solvent pairs; MAE, RMSE and bias are undefined.'
    outliers = '; '.join(f"{r['name']} ({r['solvent']}: {r['residual_predicted_minus_observed']:+.3f})" for r in stats['outliers_abs_residual_gt_1']) or 'None among matched observations.'
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    rehearsal = bool(os.environ.get('PLASTCHEM_PROGRESS_ROOT'))
    cutoff_description = ('Dress rehearsal on the current 590-molecule cohort; the owner cutoff at 20:30 CDT is still pending.'
                          if rehearsal else 'ORCA cohort cutoff: **20:30 CDT, 14 September 2026**.')
    environment_command = f'export PLASTCHEM_PROGRESS_ROOT={BULK}' if rehearsal else 'unset PLASTCHEM_PROGRESS_ROOT'
    report = f'''# Contaminant progress — 14 September 2026

Report generated {stamp}. {cutoff_description}
Capture began {manifest['capture_started_utc']} and ended {manifest['captured_utc']}.
Effective local collector cutoff: {manifest['effective_collector_cutoff_utc']}; collector resumed {manifest['collector_resumed_utc']}.
Cohort snapshot: `{cohort_id}`. Numeric records sealed {seal['sealed_utc']}.
This package uses a fixed report cohort; it is not completion of the full campaign.

The scope reconciles as **5,833 pinned structures − 9 excluded isotopologues = 5,824 eligible**.
The nine exclusions and their reasons appear in `excluded-isotopologues.csv`; no parent mapping is made.

| ORCA disposition | Count / eligible denominator |
|---|---:|
{counts_table}

Counts come from frozen locally returned records; the retained scheduler snapshot may precede the cutoff by a monitor interval.
Later ORCA returns belong to the next batch. The six support solvents are a separate denominator: **6/6 converged, 0/6 failed**.
The identity rule is connectivity-first-block matching, with full perceived keys and agreement engines retained in `campaign-dispositions.csv`
and `figures/computed-partition-values.csv`. No missing molecule is interpolated.

## Partitioning and validation

The sealed values contain **{figures['partition_rows']:,} solvent/water predictions for {figures['partition_molecules']:,} molecules**
out of {len(manifest['cohort']):,} frozen ORCA completions. The requested panel is 32 solvent/water pairs per molecule;
only pairs with available, verified solvent references are reported. Neutral pure-component reference states at 298.15 K
are used. These are concentration-ratio partition predictions, not pH-dependent logD or polymer partition coefficients.
Each solvent activity is calculated separately for solute plus that solvent. The multi-solvent panel is not a
calculation of a mixed-solvent solution or of liquid–liquid phase coexistence. Its transfer ratios alone do not
establish whether two solvent phases coexist, or give partitioning between mutually saturated phases.
The panel follows the existing product solvent keys, but this CHNO campaign does not validate PFAS chemistry or ionisation treatment.
Missing pairs remain absent; per-molecule available counts are explicit in `campaign-dispositions.csv`.
The full `partitioning-frozen.csv` contains all **186,368 requested molecule/pair rows**,
including unavailable pairs with explicit statuses and blank numerical values. It records identities, CPU,
source hashes, volume corrections and phase notes. The smaller plotted-values CSV contains only available values.
The full-table audit checked **186,368/186,368 rows**, including pair uniqueness, explicit missingness,
identity provenance, phase notes and exact numerical agreement with the plotted subset.

{accuracy}
Reference coverage before same-solvent matching: {stats['reference_molecules']} molecules / {stats['reference_observations']} candidate observations.
Of these, {stats['point_reference_molecules']} molecules have point-value references; {stats['censored_reference_observations']} observations are censored bounds and excluded from point-error statistics.
Outliers with absolute residual greater than one log unit: {outliers}

The sample is small and does not establish accuracy across the campaign. Experimental values are kept separate from
screening proxies and modelled database values. Octanol is absent from the authorised panel: octanol/water references
are retained as unmatched rather than compared with another solvent. Row-level sources, retrieval times and conditions
are in `sealed-thermodynamics/experimental-reference-candidates.csv` and `experimental-validation/reference-match-dispositions.csv`.
DEP's chloroform/water reference is at 298.15 K but uses mutually saturated phases, unlike the pure-solvent calculation.

Sources: [Liang supporting data, observed column only](https://doi.org/10.1021/acs.est.7b01737.s001),
[EPA measured slow-stir phthalates](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=30003VNC.TXT),
[Sprunger chloroform reference conditions](https://digital.library.unt.edu/ark:/67531/metadc155630/m2/1/high_res_d/Man-Pub-488.pdf).
The separate [EPA phenolic benzotriazoles table](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=P1013BCS.TXT)
supplies a measured lower bound, retained with its inequality rather than converted into a point value.
[Perylene's HSDB entry through PubChem](https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/9142/JSON?heading=LogP)
cites [Andersson and Schrader's direct partition method](https://doi.org/10.1021/ac9902291);
the entry's temperature and primary numerical table were not independently retrieved.
PlastChem inputs and the inspected product schema provide no qualified experimental partition field.
Targeted local searches found no further qualified experimental dataset; web search failures and rejected modelled values are recorded.

The frozen surface audit passed **{surface['passed']}/{surface['completed_snapshot']}** contaminants and **6/6** support solvents.
Checks cover digests, geometry, surface area, finite data, CPU, frozen ORCA version and identity provenance.
Stored numerical/provenance checks passed **{numbers['passed']}/{numbers['snapshot_ledger_entries']}** sealed results,
including dilution convergence and conversion between mole-fraction and concentration ratios. These are not experimental validation.

Four workstation anchors are reported separately in `sealed-thermodynamics/workstation-anchor-agreement.csv`: Intel Core i7-13700 workstation
solute surfaces versus AMD EPYC 7763 Milan solute surfaces, holding historical solvent references fixed. Sixteen pair
comparisons have maximum absolute difference 0.359691 log units; preparation/geometry and machine effects are confounded.
Historical DBP/BBP/DEHP golden outputs agree in 12/12 saved comparisons; no DEP golden JSON was available.
The unmodified reference `delta_log_d` function and water-cache preservation audits are documented in the accompanying
`sealed-thermodynamics/reference-math-and-water-audit.json`; these check implementation and preservation, not independent chemical accuracy.
Its timestamp and coverage identify the audit checkpoint; it does not imply a new independent solver replay.

The current homogeneous-Milan DCM/water outputs are additionally compared for **4/4 anchors** in
`production-anchor-agreement.csv`. Maximum absolute difference from the historical workstation is
**{max_production_anchor_delta:.6f} log units**; the difference from the earlier Milan-solute/historical-solvent
comparison is at most **{max_production_hybrid_delta:.6f} log units**. Solvent geometry and dilution stopping
both differ in the latter comparison, so it is not an isolated CPU-effect measurement.

## Figures and timing

All figures use 300 dpi PNG, uniform 14-point black text and adjacent CSV data.

![Experimental parity]({BULK}/experimental-validation/predicted-vs-experimental.png)

{accuracy} Black line: 1:1. Phase conditions are qualified above; n counts molecules separately from observations.

![Value distributions]({BULK}/figures/computed-value-distribution.png)

Distributions are separated by solvent, with sample counts; they are computed values, not measurements.

![Campaign progress]({BULK}/figures/campaign-progress.png)

![Wall time and atom count]({BULK}/figures/walltime-vs-atoms.png)

Successful measured OPT+COSMORS cost: main body **{costs['main_le80']:.2f} CPU-hours**, >80-atom tail **{costs['tail_gt80']:.2f} CPU-hours**.
Failed records with measured stages add **{failed_cost:.2f} CPU-hours**; unfinished runtime is not included in those sums.
Slurm batch-step RAM high-water across {len(ram)} records with reported RSS is **{max_ram_mib:.1f} MiB**, against 4,096 MiB requested.
This is the maximum in the captured accounting records, not a bound on unfinished jobs or the remaining campaign.
All campaign timing measurements are from AMD EPYC 7763 Milan. The overlay is the original Milan pilot mean fit,
drawn only over its sampled 15–80 atom range. Successful timings alone cannot estimate the cost of failures or unfinished jobs.

| Failed molecule | Input key | Failure mode |
|---|---|---|
{failures_table}

The failures are retained with no substitute or automatic retry. Full dispositions and measured times are in CSV.

## Execution, pending work and reproduction

At 15:27 CDT, owner authority removed array 56079's remaining `afterany:54733` dependency.
Readback confirmed no dependency. The transient configured maximum was main 28 + tail 4 + support 4 = 36;
main and tail throttles were unchanged. The six support jobs completed and their monitor exited successfully.
Campaign and serial thermodynamic processing continue, subject to the existing memory guard, multiplexed SSH and backoff.

Xylene identity remains an owner question: the product maps the PFAS label to p-xylene, but approval to adopt that
interpretation is pending. Missing solvent references, unfinished ORCA jobs and later returns are next-batch work.

From `/home/aaltamimi2/plastchem-euler`, after the report cohort has been captured:

```bash
{environment_command}
python3 scripts/seal_progress_thermodynamics.py
/home/aaltamimi2/.venvs/cosmo-logp/bin/python scripts/audit_completed_surfaces.py --frozen
/home/aaltamimi2/.venvs/cosmo-logp/bin/python scripts/audit_completed_surfaces.py --frozen --support-solvents
python3 scripts/audit_thermodynamic_records.py --frozen
python3 scripts/plot_progress_20260914.py --frozen
python3 scripts/compare_progress_experiments.py --frozen
python3 scripts/compare_production_anchors.py --frozen
python3 scripts/export_thermodynamic_table.py --frozen
python3 scripts/audit_progress_table.py --frozen
python3 scripts/build_frozen_progress_report.py
```

The seal is a one-time operation that refuses overwrite. Subsequent commands rerun against the pinned snapshot.
After visually inspecting the four final PNGs and reviewing this report, record their SHA-256 hashes and
review outcomes in `final-visual-review.json`, then run `python3 scripts/seal_progress_release.py`.
Verify the released files with `python3 scripts/seal_progress_release.py --verify`.
The resulting `release-manifest.json` identifies the final package and excludes provisional preview outputs.
`sealed-thermodynamics/software-provenance.json` records the Python/dependency versions and source hashes;
`sealed-thermodynamics/software-sources/` preserves the installed scientific code captured during processing.
The installed openCOSMO-RS development package reports version 0.0.1; source hashes distinguish this code revision.
Bulk artifacts are under `{BULK}`; source data and retrieved documents are in `sealed-thermodynamics/reference-sources/`.
Final report path: `{ROOT}/reports/progress-2026-09-14/REPORT.md`.
'''
    supplement = octanol_section(numeric/'octanol-validation')
    if supplement:
        report = report.replace('## Partitioning and validation', supplement+'\n## Original product-panel partitioning and validation', 1)
    sign_report = numeric/'octanol-sign-review/REPORT.md'
    if sign_report.exists():
        report += '\n' + sign_report.read_text().replace('# Octanol sign and offset review', '## Octanol sign and offset review', 1)
    hydrophobicity_report = numeric/'octanol-hydrophobicity-review/REPORT.md'
    if hydrophobicity_report.exists():
        report += '\n' + hydrophobicity_report.read_text()
    (BULK/'REPORT.md').write_text(report)
    (ROOT/'reports/progress-2026-09-14/REPORT.md').write_text(report)
    print(json.dumps({'report': str(BULK/'REPORT.md'), 'cohort_snapshot': cohort_id,
                      'counts': c, 'partition_rows': figures['partition_rows']}))

if __name__ == '__main__':
    main()
