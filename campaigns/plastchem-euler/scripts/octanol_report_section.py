"""Render the validation-only octanol results separately from the product panel."""
import json
from pathlib import Path

def section(directory):
 d=Path(directory);path=d/'validation/statistics.json'
 if not path.exists():return ''
 s=json.loads(path.read_text());refs=json.loads((d/'reference-summary.json').read_text())
 outliers='\n'.join(f"| {r['name'].replace('|','/')} | {r['measured']:.3f} | {r['predicted']:.3f} | {r['residual']:+.3f} |" for r in s['outliers_abs_residual_gt_1'])
 caption=(d/'validation/parity-caption.txt').read_text().strip()
 return f'''## Validation-only octanol/water comparison

The initial **590-molecule rehearsal cohort** has **{s['predicted_molecules']}/590 octanol/water predictions**,
with {s['failed_molecules']}/590 failed and {s['unprocessed_molecules']}/590 unprocessed.
This additional validation solvent does not change the original 32-pair product panel.
The octanol reference reuses campaign task `54798_3677` (`57269`), completed in 5 min 31 s on Milan.
It ran with the original 24-hour allocation and completed before the later requested 4-hour override could be applied.
No duplicate DFT was submitted. The exact frozen OPT and COSMORS recipe, one CPU and 4 GB were retained.
Main 01 (`54786`) was released from its dependency at throttle 24, then raised to 27 after octanol finished.
The verified bound at that change was one remaining Main 00 task + 27 Main 01 tasks + four tail tasks = 32.
The tail was unchanged. The octanol task's sibling dependency was verified unchanged.
Scheduler before/after records are retained in `state/campaign-v1/main01-overlap-release.json`,
`state/campaign-v1/main01-throttle27.json` and `state/progress-2026-09-14/octanol-release.json`.

PubChem retrieval covered **{refs['retrieval_molecules_completed']}/590 InChIKeys**.
There are **{refs['qualified_observations']} cited observations for {refs['qualified_molecules']} molecules**;
**{refs['best_point_value_molecules']} molecules** have a selected best point value.
Raw citations, values, retrieval times and source hashes are retained in
`{d}/experimental-reference-candidates.csv`; the selected values are in `{d}/best-measured-logKow.csv`.
The primary statistics use one selected value per molecule, not every duplicate observation.
HSDB/Hansch literature values and Sangster curated experimental values retain distinct source-class labels;
Sangster may include logD-to-logP adjustment or consensus processing. No XLogP3 or explicitly modelled value is admitted.

**Matched n = {s['matched_molecules']}; MAE = {s['MAE']:.3f}, RMSE = {s['RMSE']:.3f},
bias (predicted minus measured) = {s['bias_predicted_minus_measured']:+.3f} log units.**
These are descriptive database comparisons; sample size, phase conditions and source heterogeneity limit generalisation.
The model uses neutral pure-component references at 298.15 K. Experimental wet phases, pH and temperatures may differ.
The [stearyl-acrylate source](https://doi.org/10.1039/A908863F) reports concentration-dependent partitioning,
and the [bile-acid source](https://pubmed.ncbi.nlm.nih.gov/2280184/) distinguishes protonated and ionized species;
the species assignment of its HSDB value was not independently recovered. These caveats remain in the observation rows.

![Octanol/water experimental parity]({d}/validation/predicted-vs-experimental.png)

{caption}

| Outlier (absolute residual >1) | Measured | Predicted | Residual |
|---|---:|---:|---:|
{outliers}

All predicted values and identity/provenance fields are in `{d}/validation/all-predicted-logKow.csv`;
the matched table is `{d}/validation/best-measured-parity.csv`.
The audit checks each result's surface hashes, connectivity, dilution plateau, finite output and conversion arithmetic.
Water activities are preserved from the verified rehearsal records. This is not an independent rerun of the water solver.
Octanol's molar volume uses a [measured density at 298.15 K](https://trc.nist.gov/ThermoML/10.1021/je100170v.html),
with the source, uncertainty and second experimental density check retained in `provenance.json`.

The original product-panel experimental comparison below is separate from this octanol validation.
'''
