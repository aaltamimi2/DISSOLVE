# Contaminant data overview and proposed openCOSMO calculation specification

Status: draft, 17 September 2026. This document specifies future calculation work; it does not authorize or launch it. Existing panel processing and the already-started final octanol run remain separate. No product database or serving engine has been modified.

## Figure purpose and reference

Create the contaminant counterpart of the supplied solubility overview: a counted data grid, one worked example, then the full dataset arranged into readable blocks. The supplied reference was read only at `/home/aaltamimi2/dissolve-v12-audit/astra/figures/data-overview/solubility/revisions/solubility-complete-rows-v16/solubility-complete-rows-v16-3p5in.png`; no other files in that lane were inspected or changed.

The current draft uses every ORCA-accepted contaminant exactly once, grouped by atom count including hydrogen: ≤20, 21–30, 31–40, 41–50, 51–60, 61–70, 71–80, >80. Within each bin, sort by atom count and full input InChIKey. Preserve this row order in a CSV. No rows are selected for attractiveness or ease of calculation.

The proposed future total of approximately 9,000 contaminants is an owner planning statement, not a reconciled completed or running count. Verified lane accounting is 5,833 unique CHNO inputs minus nine excluded isotopologues = 5,824 eligible; 5,803 accepted ORCA outputs and 21 failed dispositions. No campaign ORCA jobs remain running in this lane. The pinned broader input superset contains 7,792 unique structures across all three tiers, not 9,000; the parked tiers have not been approved for this campaign. Obtain the authoritative future manifest before using 9,000 as a data count.

## Current figure design

1. Headline: exact number of displayed computed water-referenced partition predictions from the frozen CSV. Do not label these as served predictions.
2. Coverage line: 5,803 accepted contaminant structures, 25°C only, and a dated draft status.
3. Columns: the product's 69 common-solvent keys, plus acetic acid as an explicitly labelled extra column to retain existing non-common panel results. Water is the reference, not a solvent/water prediction to itself. Validation-only octanol is one of the 69 common columns, not an additional duplicate column.
4. Quantity: log10 K on a mole-fraction basis, computed from infinite-dilution activity coefficients. This includes diphenyl ether, whose concentration conversion is not yet available. Do not mix logKx and concentration-based logK in the same colour scale. Fixed colour bins: <0, 0–2, 2–5, 5–10, ≥10. Gray means unavailable at capture, never zero.
5. Worked example above the heatmaps: an RDKit drawing of DEP and its measured-in-computation activity-ratio curve over 11 prescribed ethanol/water mole-fraction compositions at 25°C. Label it as a liquid-branch model calculation, not a measured experiment or demonstrated equilibrium partition between stable phases. The full DEP pilot spans 67 available solvents × 11 compositions; its illustrative curve is not added to the main pure-solvent count.
6. Eight heatmap blocks use the fixed atom-count bins. Every accepted contaminant appears once even if it has missing predictions. Allocate heatmap height proportionally to row count, with at least one output pixel per contaminant at 300 dpi; retain the complete matrix and row mapping. A smaller display preview may be rescaled by its viewer, but the delivered PNG must retain every row.
7. Uniform 12-point black text in the draft, 300-dpi PNG plus PDF, no overlapping labels, and no title wider than the content. Color applies only to data, not text. Review at actual display size. The draft is 7 inches wide; a later 3.5-inch version needs a separate legibility/layout pass, not blind shrinking.
8. Include capture start/end time, per-cell source hash, numerical-audit status and missing-cell count in the accompanying table/report. Preserve released packages. New snapshots receive versioned folders.

The heatmap can later gain temperature panels or composition panels once those calculations actually exist. Do not duplicate one-temperature values across a proposed temperature axis. Proposed multiplication belongs in the specification, not in the current computed-count headline.

## Existing feasibility evidence

`/mnt/r/plastchem-euler/common69-pilot-20260917/REPORT.md` records:

- 67/69 common solvent structures have verified, compatible frozen-recipe surfaces, obtained from accepted campaign returns or the panel library. Water is separately available.
- Chlorobenzene and sulfolane lack a verified compatible surface in the searched local sources. Legacy COSMObase surfaces exist but must not be substituted into the frozen 24a calculation. New ORCA work is on hold.
- DEP passed 67/67 pure-solvent tests and 737/737 specified solvent/water grid cells at 298.15 K. The 737 cells include repeated pure-water endpoints; there are 671 unique solvent-composition systems in that 67-solvent grid.
- Pilot runtime including I/O: 372.55 seconds, peak RSS about 242 MiB. The 67 pure-solvent activity tests together took about 28.08 seconds. These DEP-only costs are not validated estimates for all contaminants or all temperatures.
- Only 30/67 pilot organic-solvent predictions currently have documented molar volumes for concentration conversion. The other 37 have valid model activity/logKx results but blank concentration-based values.
- Three identity qualifications remain: product/campaign CAS labels differ for propylene glycol methyl ether and hexyl acetate; dipentene uses a single limonene enantiomer surface, not a verified commercial racemate model. Preserve full keys and the caveats rather than silently resolving them.

Passing DEP does not establish that every solvent works for every contaminant. Before committing a large cache, use a defined validation subset spanning atom counts, functional groups, all four phthalate anchors, strongly polar/zwitterionic structures, and known dilution failures. The subset and acceptance criteria must be pinned before execution; failure remains a reported outcome, not a reason to substitute an easier molecule.

## Proposed calculation grid — NOT LAUNCHED

Use N = the accepted, identity-verified contaminant manifest, not source rows or the planning estimate of 9,000. Retain the frozen ORCA recipe and reuse existing solute surfaces. Additional temperatures/compositions require openCOSMO work, not repeated contaminant geometry optimisation.

**Stage A: pure solvents.** N × S × T water-referenced partition cells, with S = 69 common organic solvent keys after the two surface gaps and identity qualifications are resolved. Initially T = {298.15 K}, matching existing validation. For N = 5,803, the complete one-temperature grid would contain 400,407 cells. This is a planned upper bound, not a completed count; failures stay unavailable.

**Stage B: solvent/water compositions.** Proposed grid f = {0, 0.1, ..., 1}, defined as the organic-solvent mole fraction of the solvent/water medium, excluding dilute contaminant. Actual engine composition is [x_contaminant, (1-x_contaminant)f, (1-x_contaminant)(1-f)]. At each medium composition, verify dilution using 1e-5, 1e-6 and, if needed, 1e-7 and 1e-8. Those numerical dilution evaluations are not separate displayed predictions. Deduplicate the shared pure-water endpoint: S × 10 + 1 distinct media per temperature for this grid. Store activity coefficients; do not call an arbitrary homogeneous mixture result an equilibrium two-phase partition coefficient. Mixture concentration conversion needs documented mixture volume/density; do not silently assume volume additivity.

**Stage C: temperatures.** A possible comparison grid, matching the solubility figure's range, is 25–160°C in 5°C increments (28 temperatures). This is a proposed grid only. It requires parameter/model-domain review, pressure definition, phase-state checks and reference-density data at temperature. Some pure solvents or compositions would not be stable liquid states at the chosen conditions; mark model liquid-reference results and invalid/unresolved physical states explicitly. Do not claim temperature validation from the present 25°C data. A smaller application-relevant temperature set may be more useful; selection remains open.

**Stage D: organic/organic mixtures.** Do not equate the 69-solvent list with a specified mixture library. If all unordered binary organic pairs were selected at 10% interior increments, there would be 69 pure endpoints + 9 × C(69,2) distinct media per temperature. Adding water as a 70th component gives 70 + 9 × C(70,2). This does not cover ternary or higher mixtures or the continuous composition space. Decide relevant combinations before precomputation; prefer on-demand calculation for unbounded combinations rather than describing a finite cache as “all compositions.”

No stage beyond current work is launched by this specification. Resolve target solvent set, temperature grid, physical-state interpretation, finite compositions, concentration basis and storage policy before issuing a launch manifest.

## Validation gates and cache/engine contract

Each calculation must bind input and perceived contaminant keys, connectivity-match basis, solvent full identities/qualifications, all surface digests, ORCA provenance, openCOSMO implementation and parameterization, temperature, full normalized composition vector, component order and reference state. Keep failed and not-run cells distinct from missing assets.

Acceptance requires solver convergence, finite outputs, normalized nonnegative composition, bounded dilution change, correct partition sign and standard-state conversion, matching single-solvent endpoints, and reproducible results from the pinned inputs. Cross-solvent closure is an implementation check, not experimental accuracy. Compare the four anchors to the reference implementation separately from measured logKow accuracy. Report experimental n, MAE, RMSE, bias, slope/intercept and named outliers with per-observation citations. Keep dry-octanol and experimental water-saturated-octanol conditions visible, with no recalibration. Do not resolve the didecyl-phthalate source discrepancy or generic xylene identity without the owner.

The cache key should bind all the above scientific inputs, including exact composition and temperature; a solvent name alone is insufficient. Store component activity outputs so compatible pair coefficients can be derived without repeating the expensive solve. Deduplicate shared endpoints and retain successful partial results. Store dense bulk artifacts on `/mnt/r/plastchem-euler/`; keep compact manifests and logs locally. Continue serial local processing unless concurrency is separately agreed; record per-cell wall time, memory, bytes and failure mode before estimating a full-run cost.

Integration is a later, explicit product step. Proposed service behavior: return only a matching validated cached result with provenance and units; otherwise return a clear missing/pending status or use a separately authorized on-demand path. Do not interpolate silently between compositions or temperatures, substitute different solvent identities, or advertise planned counts as served engine coverage. Import into the product must be staged, schema-checked, tested and approved separately; this lane does not write product databases.

## Output paths

Draft package: `/mnt/r/plastchem-euler/progress-2026-09-17/contaminant-overview-draft-v1/`.

Figure: `contaminant-data-overview-draft.png` (300 dpi) and `.pdf`. Data: `predictions.csv`, `contaminant-rows.csv`, `solvent-columns.csv`, `size-bin-summary.csv`, `heatmap-matrix.npz`, `summary.json`, and copied source-result snapshots. Exact counts are in `summary.json`; the report must quote that snapshot rather than a later live ledger.

Local PNG copy: `/home/aaltamimi2/plastchem-euler/contaminant-data-overview-draft-2026-09-17.png`.

Snapshot script: `/home/aaltamimi2/plastchem-euler/scripts/build_contaminant_overview_snapshot_20260917.py`.

Renderer: `/home/aaltamimi2/plastchem-euler/scripts/render_contaminant_overview_20260917.py`.

Interpreter: `/home/aaltamimi2/.venvs/cosmo-logp/bin/python`.

This Markdown: `/home/aaltamimi2/plastchem-euler/reports/progress-2026-09-17/CONTAMINANT_OVERVIEW_AND_CALCULATION_SPEC.md`.

## Grid-count bookkeeping

`planned-grid-counts.csv` in the draft package tabulates prospective counts for the verified 5,803-structure cohort and the unverified 9,000-structure planning case, at one or a proposed 28 temperatures. These are not computed or served output counts. The solvent/water grid has 691 unique media including water, but only 690 nonself water-referenced ratios per contaminant and temperature. Pure-solvent endpoints are reused between stages rather than added twice. The all-organic binary grid requires an additional shared water reference when deriving water-referenced ratios. Solver dilution samples are numerical verification evaluations, not distinct cached predictions.

## Gray-cell accounting in this draft

The 5,803 × 70 one-temperature display grid has 406,210 cells: 70,572 computed values and 335,638 unavailable cells. The unavailable cells comprise 208,908 in the future grid not launched; 20,367 whose panel record is not yet processed; 94,696 whose additional solvent activity is not yet processed; 11,606 corresponding to the two missing compatible solvent surfaces; and 61 recorded dilution failures. These categories are mutually exclusive and reconcile exactly. See `cell-status-counts.csv` and `missingness-summary.json` in the draft package. Gray is therefore not a synonym for calculation failure.

The current snapshot's counting and missingness helper is `/home/aaltamimi2/plastchem-euler/scripts/summarize_overview_missingness_20260917.py`. It uses frozen per-library activity sets and explicit failed-activity records, and rejects any unexplained missing cell.
