# Contaminant ORCA–openCOSMO-RS workflow

Original solvent/water brief prepared 16 September 2026; extended 23 September 2026 for the A-8/A-9 polymer-partition and liquid-liquid-equilibrium workflow. This document describes **methods** for a future methods figure; no methods figure is created here. Sections 1–8 describe the initial solvent/water pipeline. The current A-8/A-9 extension below specifies the additional branches and their execution paths; the dated CSV exports remain historical snapshots.

## Workflow overview

**Pinned molecular identities → candidate conformer generation → MMFF minimisation and ranking → selection of one conformer → ORCA DFT geometry optimisation → ORCA COSMO surface generation → integrity and connectivity acceptance → openCOSMO-RS activity coefficients → solvent/water partition coefficients.**

The distinction to preserve in the figure is that **multiple geometries are screened with a force field, but only one selected conformer undergoes DFT optimisation and supplies the thermodynamic surface**. Each contaminant supplies one DFT surface. The later polymer branch uses explicit polymer-conformer ensemble averaging, described below; this does not add contaminant DFT conformers.

## 1. Define the eligible structures

The pinned CHNO first-pass input contains 5,833 unique InChIKeys. Nine isotope-labelled structures are explicitly excluded under the owner’s policy, leaving **5,824 eligible structures**. Duplicate source rows do not become duplicate calculations. Structures are treated as neutral singlets. Parked heteroatom and Si/B tiers are outside this campaign.

- Input: `/home/aaltamimi2/plastchem-euler/inputs/plastchem_organics_orca_opencosmo_firstpass.csv`
- Input digests: `/home/aaltamimi2/plastchem-euler/state/INPUTS.sha256`
- Campaign initialisation: `/home/aaltamimi2/plastchem-euler/scripts/initialize_campaign.py`
- Eligible identities: `/home/aaltamimi2/plastchem-euler/state/campaign-v1/eligible.json`
- Explicit exclusions: `/home/aaltamimi2/plastchem-euler/state/campaign-v1/exclusions.json`

Previously completed pilot calculations that satisfy the campaign recipe and identity policy are reused with their provenance retained.

## 2. Generate and minimise candidate conformers locally

RDKit parses the SMILES, checks its InChIKey against the input, and adds explicit hydrogens. ETKDGv3 requests **up to 300 candidate conformers**, using seed **12345**, RMS pruning threshold **0.5 Å**, and one thread per molecule. Pruning and embedding success can leave fewer than 300 actual candidates.

Each embedded candidate is minimised with RDKit’s **MMFF94 force field**, allowing **2,000 iterations**. Only candidates with converged MMFF minimisation are eligible for selection. The candidate with the lowest MMFF energy among those converged candidates supplies the starting XYZ for ORCA.

This selects a low-energy starting geometry from the sampled candidates; it does **not** establish a global minimum or rank a DFT conformer ensemble. Missing MMFF parameters, failed embedding, or absence of a converged candidate are recorded as preparation failures, without substituting another force field.

- Per-molecule preparation: `/home/aaltamimi2/plastchem-euler/scripts/prepare_campaign.py` — the `prepare()` function
- Preparation orchestration: `/home/aaltamimi2/plastchem-euler/scripts/prepare_campaign_parallel.py`
- Prepared geometry: `/home/aaltamimi2/plastchem-euler/state/campaign-v1/prepared/<InChIKey>/input.xyz`
- Preparation provenance: `/home/aaltamimi2/plastchem-euler/state/campaign-v1/prepared/<InChIKey>/preparation.json`

The parallel wrapper dispatches up to eight independent molecule-preparation processes, with each molecule internally serial. It calls `prepare()` directly. The older standalone main block in `prepare_campaign.py` is not the revised preparation orchestration. Deterministic re-preparation checks preceded the switch to parallel preparation.

## 3. Refine the selected geometry with ORCA on Euler

Exactly **one MMFF-selected conformer per molecule** enters ORCA 6.1.1, GIT `487d211c`. It undergoes a second geometry minimisation, now using DFT:

```text
! OPT BP86 def2-TZVP(-f) TightSCF
%maxcore 1500
* xyz 0 1
<selected starting coordinates>
*
```

The OPT input contains no solvent keyword. The optimised coordinates are taken from the final ORCA geometry block, after checking both normal termination and explicit geometry convergence. The next stage receives these **DFT-optimised coordinates**, not the original MMFF geometry.

- Two-stage ORCA implementation: `/home/aaltamimi2/plastchem-euler/scripts/campaign_runner.py`
- Main-body SLURM wrapper: `/home/aaltamimi2/plastchem-euler/scripts/campaign-main_le80.sbatch`
- Large-molecule SLURM wrapper: `/home/aaltamimi2/plastchem-euler/scripts/campaign-tail_gt80.sbatch`

Execution is serial: one CPU per molecule, 4 GB scheduler memory, `%maxcore 1500`, and no `%pal`. Jobs target Euler’s `research` partition with `milan&cpu`, excluding `euler09` and `euler10`; the recorded CPU is AMD EPYC 7763. Scheduler wall limits are 24 hours for the main body and 48 hours for the >80-atom tail. These are resource limits, not additional molecular sampling.

## 4. Generate the COSMO surface in ORCA

After successful geometry optimisation, the same runner invokes:

```text
! COSMORS(Water)
%maxcore 1500
* xyz 0 1
<DFT-optimised coordinates>
*
```

For this frozen ORCA version, the runner verifies that the subsidiary solute calculation reports **BP86/def2-TZVPD CPCM** and terminates normally. It requires a nonempty solute `.orcacosmo` file and records its SHA-256 digest.

The `COSMORS(Water)` keyword belongs to the **surface-generation stage**. The downstream reported multi-solvent partition coefficients are calculated with openCOSMO-RS. They are not obtained simply by subtracting the ORCA total energies.

The main returned files for each molecule are:

- `/mnt/r/plastchem-euler/results/<InChIKey>/surface.orcacosmo`
- `/mnt/r/plastchem-euler/results/<InChIKey>/optimized.xyz`
- `/mnt/r/plastchem-euler/results/<InChIKey>/opt.inp`
- `/mnt/r/plastchem-euler/results/<InChIKey>/cosmo.inp`
- `/mnt/r/plastchem-euler/results/<InChIKey>/result.json`

Full ORCA work files remain in the cluster account; the compact result bundle is returned by `scp`.

## 5. Accept the returned molecular identity and files

Successful ORCA execution alone is insufficient for acceptance. The collector checks the returned surface and input-deck digests. Bond perception from the optimised XYZ is attempted using RDKit `DetermineBonds` and Open Babel, and the resulting InChIKeys are retained.

The implemented owner policy requires **at least one successfully perceived InChIKey first block to match the input first block**. It does not require full-key equality or unanimous agreement between engines. The selected full perceived key, each engine’s key, connectivity matches, and engine agreement are recorded explicitly. Stereo-layer differences do not by themselves reject the result.

A connectivity rejection can therefore follow a numerically converged DFT calculation. That entry remains reported as rejected and receives no attributed partition prediction; it is not silently relabelled as another molecule.

- Collector and acceptance pipeline: `/home/aaltamimi2/plastchem-euler/scripts/watch_campaign.py`
- Geometry perception: `/home/aaltamimi2/plastchem-euler/scripts/geometry_identity.py`
- Current identity-policy wrapper: `/home/aaltamimi2/plastchem-euler/scripts/identity_campaign.py`
- Per-molecule accepted/failed state: `/home/aaltamimi2/plastchem-euler/state/campaign-v1/records/<InChIKey>.json`

Use `identity_campaign.py` when describing campaign acceptance. The underlying geometry-perception function has older policy defaults; the wrapper applies the current connectivity policy to its observations.

## 6. Supply compatible solvent references

Each thermodynamic calculation needs both a contaminant COSMO surface and a solvent COSMO surface. The library resolver accepts verified compatible ORCA/Milan surfaces and checks their identity and digests. Suitable campaign molecules can also supply solvent references, avoiding duplicate DFT work. Missing or ambiguous references remain explicit.

- Solvent resolver: `/home/aaltamimi2/plastchem-euler/scripts/thermodynamic_library.py`
- Solvent identities: `/home/aaltamimi2/plastchem-euler/state/thermodynamics-v1/solvent-inventory.json`
- Verified availability: `/home/aaltamimi2/plastchem-euler/state/thermodynamics-v1/library-registry.json`

The verified Milan water diagnostic is reused with its original preparation provenance. Water has no conformational torsions. Validation-only 1-octanol is separate from the production solvent panel. Xylene identity remains an owner question; another isomer is not substituted automatically.

## 7. Calculate activity coefficients with openCOSMO-RS locally

For each accepted contaminant and available solvent, openCOSMO-RS uses the two surfaces with the **openCOSMORS24a** parameterisation, **298.15 K**, and the **pure-component reference state**.

The solute mole fraction is successively reduced over **10⁻⁵, 10⁻⁶, 10⁻⁷, 10⁻⁸**, stopping once the change between consecutive activity coefficients is at most **0.005 in log10 units**. At least two points are required. Failure to reach this bounded dilution criterion is recorded, rather than treated as a successful infinite-dilution estimate.

- Activity coefficients, dilution checks and pair conversion: `/home/aaltamimi2/plastchem-euler/scripts/thermodynamic_prediction.py`
- Incremental serial panel worker: `/home/aaltamimi2/plastchem-euler/scripts/watch_thermodynamics.py`
- Validation-only octanol batch processor: `/home/aaltamimi2/plastchem-euler/scripts/process_expansion_octanol.py`
- Python environment: `/home/aaltamimi2/.venvs/cosmo-logp/bin/python`

Thermodynamic processing is serial and guarded by an exclusive worker lock. The panel worker pauses when available local memory falls below its configured threshold. The same accepted contaminant surface is reused across solvents; ORCA is not rerun once for each solvent.

## 8. Convert activity coefficients to partition coefficients

For an organic solvent S relative to water W, the calculation uses:

```text
log10(K_x) = [ln(gamma_solute_in_W) - ln(gamma_solute_in_S)] / ln(10)

log10(K_concentration) = log10(K_x) + log10(V_m,W / V_m,S)
```

Here `K_x = x_solute,S / x_solute,W`, while `K_concentration = c_solute,S / c_solute,W`; `V_m` is the solvent molar volume in consistent units. Both solvent activity calculations must pass. Molar volumes and their provenance are retained; without a documented volume, the concentration-based value remains unavailable even if a mole-fraction value exists.

These are neutral-solute, liquid-reference predictions. This initial solvent/water branch does not model pH-dependent ionisation/logD, contaminant conformer ensembles, polymer partitioning or mixed-solvent equilibria. The later A-8/A-9 extension adds polymer-ensemble partitioning and binary liquid-liquid equilibrium from the same accepted contaminant surfaces. The validation-only octanol calculation uses dry pure-component references, which differ from mutually saturated experimental octanol/water phases.

- Full table export: `/home/aaltamimi2/plastchem-euler/scripts/export_thermodynamic_table.py`
- Per-contaminant panel output: `/mnt/r/plastchem-euler/thermodynamics-v1/<InChIKey>.json`
- Current complete-denominator table: `/mnt/r/plastchem-euler/thermodynamics-v1/partitioning-current.csv`
- Processing ledger: `/home/aaltamimi2/plastchem-euler/state/thermodynamics-v1/processing-ledger.json`

The export retains input and perceived keys, connectivity-match basis, source hashes, solvent/reference identities, temperature, values and missingness/failure reasons. No value is interpolated for an unrun, failed or rejected molecule.

## Completed-contaminant CSV for RDKit structure depictions

The fixed **16 September 2026, 14:38:28 UTC** export contains **1,833 contaminants** with accepted ORCA/identity results and successful openCOSMO calculations for all currently available panel solvents. Each row includes the name, **`smiles`**, CAS, **`inchikey`**, PlastChem IDs, molecular weight, provenance and partition coefficients.

- Local CSV: [completed-contaminants-2026-09-16.csv](/home/aaltamimi2/plastchem-euler/completed-contaminants-2026-09-16.csv)
- Full local path: `/home/aaltamimi2/plastchem-euler/completed-contaminants-2026-09-16.csv`
- Windows-accessible path: `\\wsl.localhost\Ubuntu-20.04\home\aaltamimi2\plastchem-euler\completed-contaminants-2026-09-16.csv`
- Export metadata: `/home/aaltamimi2/plastchem-euler/completed-contaminants-2026-09-16.metadata.json`
- Archived snapshot: `/mnt/r/plastchem-euler/exports/completed-20260916T143828Z/completed-contaminants.csv`
- Export script: `/home/aaltamimi2/plastchem-euler/scripts/export_completed_contaminants.py`

For future RDKit depictions, parse the **`smiles`** column with `Chem.MolFromSmiles`, use **`name`** as a display label, and retain **`inchikey`** as the structure identifier. These SMILES represent the input chemical structures; they are not coordinates of the DFT-optimised conformer. The separate **`perceived_inchikey`** column records the identity perceived from that optimised geometry.

This snapshot has **six available solvent-versus-water predictions per molecule**; **1,170 molecules additionally have audited validation-only octanol predictions**. Missing solvent values are blank, and `full_requested_panel_complete` explicitly records incomplete full-panel coverage. The snapshot does not automatically gain contaminants that finish later. No RDKit depictions or methods figure have been created as part of this documentation.

## Verification and figure-author notes

Supporting integrity checks are implemented in:

- `/home/aaltamimi2/plastchem-euler/scripts/audit_completed_surfaces.py` — surface/deck provenance, CPU/version, geometry correspondence and surface numerical structure.
- `/home/aaltamimi2/plastchem-euler/scripts/audit_thermodynamic_records.py` — stored-result provenance, dilution checks, conversion arithmetic and missingness accounting.
- `/home/aaltamimi2/plastchem-euler/scripts/audit_post1160_octanol.py` — example of a fixed-cohort octanol audit with independent Decimal reconstruction of the concentration conversion. This script targets one historical batch, not the whole campaign.

Numerical audits and algebraic solvent-cycle closure check implementation consistency; comparison with qualified experimental measurements is a separate accuracy assessment. Neither should be drawn as proof that all predicted coefficients are experimentally correct.

For the initial solvent/water methods figure, use the eight stages above as the text source. For the current extended workflow, add the separate A-8/A-9 branches below. Show the solvent-reference input feeding the openCOSMO-RS stage, and show preparation, ORCA, identity and thermodynamic failures leaving the prediction path as recorded outcomes. Label the conformer funnel “up to 300 candidates → one selected DFT conformer.” Distinguish the local preparation/post-processing stages from the two ORCA stages on Euler. Avoid progress counts, measured-error statistics or result plots in this methods figure.

This document is descriptive, not a command sequence to rerun a live campaign. The scripts include locks, state and historical batch assumptions; do not execute all listed files in sequence.

### Updated snapshot: 17 September 2026

The newer success-only snapshot contains **3,980 contaminants**, each with seven available panel solvent/water predictions; **3,872 also have audited validation-only octanol values**. It was captured at 2026-09-17 06:41:28 UTC. Eleven molecules with failed solvent activities are excluded from this CSV and listed in its metadata; they remain in the campaign accounting. Full requested-panel coverage remains incomplete.

- Local CSV: [completed-contaminants-2026-09-17.csv](/home/aaltamimi2/plastchem-euler/completed-contaminants-2026-09-17.csv)
- Full path: `/home/aaltamimi2/plastchem-euler/completed-contaminants-2026-09-17.csv`
- Metadata: `/home/aaltamimi2/plastchem-euler/completed-contaminants-2026-09-17.metadata.json`
- Archived snapshot: `/mnt/r/plastchem-euler/exports/completed-20260917T064128Z/completed-contaminants.csv`

The same `smiles`, `name`, and `inchikey` columns can be used for future RDKit depictions. The September 16 snapshot is retained unchanged. These dated exports do not automatically gain later results.


## Additional verified export: 17 September 2026, 07:45 UTC snapshot

The timestamped CSV is `/home/aaltamimi2/plastchem-euler/completed-contaminants-2026-09-17T074502Z.csv`. Its bulk source is `/mnt/r/plastchem-euler/exports/completed-20260917T074502Z/completed-contaminants.csv`. It contains 4,217 unique contaminants with accepted ORCA results and successful calculations for all seven currently available non-self panel pairs; 4,161 also have audited validation-only octanol predictions. It includes names, SMILES, CAS, input/perceived InChIKeys and provenance for structure rendering and downstream analysis.

This is not full 32-pair panel completion. Missing values remain blank. Twelve molecules with an activity failure were excluded from this successful-results export; their records and reasons remain in the snapshot summary. Earlier exports remain unchanged. Verification receipt: `/home/aaltamimi2/plastchem-euler/state/completed-export-refresh-verified-20260917T0745.json`.


Latest available-library snapshot (17 September, 13:54 UTC): `/home/aaltamimi2/plastchem-euler/completed-contaminants-2026-09-17T135403Z.csv`. Contains 2,951 structures with names, SMILES and provenance, suitable for RDKit structure rendering. Eligibility requires the new diphenyl-ether pass, so this snapshot is a subset while that pass runs; earlier exports remain unchanged. Nine mole-fraction pairs per molecule; diphenyl-ether concentration values remain blank. Bulk snapshot and exclusions: `/mnt/r/plastchem-euler/exports/completed-20260917T135403Z/`.


## A-8/A-9 extension: polymer partitioning and binary LLE

Status, 24 September 2026: production finished and the complete release `promotion-v1` was sealed; DISSOLVE serves it, with the normalized convention chosen by the owner. The sealed release, with its README, summary and validation, is `/mnt/r/plastchem-euler/promotion-v1/`.

This extension reuses accepted contaminant surfaces; it does not repeat contaminant ORCA calculations. It is the current methods specification for the additional thermodynamic quantities. Preparing scripts and a release preview does not mean the full production set has finished.

The methods figure should branch after **accepted surface + verified identities**:

1. **Solvent/water partitioning**, as in the original workflow above.
2. **Solvent/polymer partitioning at infinite dilution**, using a solvent surface and an explicitly averaged polymer-conformer ensemble.
3. **Contaminant/solvent binary LLE**, using finite-composition activity curves and a coexistence solver.

LLE is evaluated from its own finite-composition curve; it must not be drawn as a result derived from a single infinite-dilution coefficient. None of these branches supplies a new polymer solid-liquid solubility model: the product's polymer S(T) input remains legacy data.

### Inputs, CPU scope and reuse

Contaminants still contribute one accepted ORCA surface per structure. Polymer conformers start from the independently supplied geometry library and each undergo the same frozen two-stage ORCA recipe. Every conformer remains represented; optimisation merges are recorded rather than silently dropping ensemble members.

All ORCA work, including new common-solvent references, stays on Milan. Activity/LLE calculations may run on Milan or Genoa after the owner-required numerical usability comparison; each job records its actual CPU model. This CPU exception does not apply to DFT. Each calculation job remains serial, with one CPU and 4 GB under the shared cap of 64.

- Frozen contaminant cohort and surface identities: `/mnt/r/plastchem-euler/phase83-v1/cohort.json`
- Polymer conformers, energies, cavity volumes and solvent references: `/mnt/r/plastchem-euler/phase8-v1/manifest.json`
- Workbook quantities, temperatures and source definitions: `/mnt/r/plastchem-euler/phase8-v1/validation-inputs.json`
- Pinned thermodynamic environment: `/mnt/r/plastchem-euler/phase8-v1/package-pins.json`

A job parses each contaminant and phase surface once. Compact sigma profiles are reused through independently indexed engine objects; mutable segment-index state is not shared between engines. Partition solves use measured small sub-batches, with every contaminant at exact **x = 0** and the phase at mole fraction one, relative to pure-component reference states. Checkpointed production uses chunks of contaminants, with sealed output after each phase and each LLE system; retries retain completed evidence.

- Parsed-profile cache and independent engine assembly: `/home/aaltamimi2/plastchem-euler/scripts/phase9_profiles.py`
- Frozen Milan scientific worker: `/home/aaltamimi2/plastchem-euler/scripts/phase9_worker.py`
- Numerically identical worker with owner-authorised CPU/resource assertions: `/home/aaltamimi2/plastchem-euler/scripts/phase9_worker_cpu.py`
- Resume entry point and completed-chunk verification: `/home/aaltamimi2/plastchem-euler/scripts/phase9_entry.py`
- Gated launch orchestration: `/home/aaltamimi2/plastchem-euler/scripts/launch_phase9.py`
- Shared-cap allocation controller: `/home/aaltamimi2/plastchem-euler/scripts/phase9_throttle_remote.py`

### Infinite-dilution solvent/polymer partitioning

For polymer conformer c, the normalized ensemble uses Boltzmann weights from relative energies and an inverse-activity average:

```text
w_c = exp[-(E_c - E_min)/(R T)] / sum_c exp[-(E_c - E_min)/(R T)]
ln(gamma_polymer) = -ln[sum_c w_c exp(-ln(gamma_c))]
V_polymer = sum_c w_c V_c

logP_x = [ln(gamma_polymer) - ln(gamma_solvent)] / ln(10)
logP_concentration = logP_x + log10(V_polymer / V_solvent)
```

The sign is **log10 P(solvent/polymer)**: positive favours the solvent. Partition calculations are at 298.15 K and represent neutral-species, liquid-reference thermodynamics without pH-dependent speciation.

Both normalized and existing ensemble conventions are retained. The existing convention preserves the historical normalization/volume choices, but **both now evaluate activity coefficients at exact x = 0**. The historical first-ladder-sample x = 1e-5 is not relabelled as exact infinite dilution. The owner chooses which convention to promote; no fitted offset or empirical recalibration is applied.

### Binary liquid-liquid equilibrium

At each finite contaminant mole fraction x, the binary calculation obtains both components' activity coefficients. The dimensionless mixing free energy is

```text
g_mix/(R T) = x ln(x) + (1-x) ln(1-x)
              + x ln(gamma_contaminant) + (1-x) ln(gamma_solvent)
```

The lower convex hull identifies potential two-phase regions. Coexistence is refined by equality of both components' chemical potentials and qualified by tangent stability and agreement between the 1,000- and 2,000-interval grids with dilute tails. The vectorized initial grid preserves the validated solver; nonlinear refinement still uses the original activity engine. A coarse-grid screen alone cannot qualify a single-phase result.

The calculation is repeated at 298.15 K and each solvent's literal workbook high temperature. Outputs retain mole-fraction and weight-percent solubility, both phase compositions, tie lines, grid checks, and both 15 mol% and 15 wt% verdicts. The near-threshold indeterminate band and all unresolved statuses remain explicit. This is **binary LLE**, not fusion-corrected solid-liquid solubility or an arbitrary mixed-solvent composition grid.

- Initial-grid activity batching: `/home/aaltamimi2/plastchem-euler/scripts/phase9_grid.py`
- Unmodified validated coexistence solver: `/mnt/r/plastchem-euler/phase8-v1/phase8_lle.py`
- Literal reference/basis/layout inventory: `/home/aaltamimi2/plastchem-euler/reports/phase8-0-inventory/REPORT.md`

### Verification and delivery

- Digest-verified collection: `/home/aaltamimi2/plastchem-euler/scripts/collect_phase9.py`
- Milan reference reproduction: `/home/aaltamimi2/plastchem-euler/scripts/analyze_phase9_gate.py`
- Genoa-versus-Milan usability comparison: `/home/aaltamimi2/plastchem-euler/scripts/analyze_phase9_genoa.py`
- Independent reconstruction from recorded activities, provenance and LLE qualification checks: `/home/aaltamimi2/plastchem-euler/scripts/audit_phase9_results.py`
- Exact-zero workbook comparisons: `/home/aaltamimi2/plastchem-euler/scripts/refresh_phase9_workbook_validation.py`
- Streaming, completion-gated exporter: `/home/aaltamimi2/plastchem-euler/scripts/build_phase9_release.py`
- Raw checkpoint archive directory: `/mnt/r/plastchem-euler/phase9-v1/returns/`
- Rehearsal output, explicitly partial and not promotable: `/mnt/r/plastchem-euler/phase9-v1/release-preview-v1/`
- Final target, created only after complete-cohort checks: `/mnt/r/plastchem-euler/promotion-v1/`

The delivery separates `partition.parquet`, `binary-lle.parquet`, `contaminants.csv.gz`, and an explicit polymer-to-product mapping, with hashes and validation provenance. PE maps to both LDPE and HDPE without inventing distinct crystallinity or fusion properties. Failed, excluded, unresolved and not-run identities remain visible. Surface reuse, numerical agreement and algebraic audits are distinct from comparison with measured experimental data.

For the methods figure, show **one contaminant surface reused**, **multiple explicit polymer conformers averaged**, and **a separate finite-composition LLE branch**. The added activity/LLE work runs on Euler; local collection, arithmetic audit and delivery packaging remain serial and memory limited. Do not use progress counts, error statistics or the partial preview as evidence that production has finished. No methods figure is generated by this document.

## Polymer conformer inputs (amendment A-5)

The 284 polymer conformer geometries (14 polymers, 16 species) are packed in
`inputs/polymers/conformers_v1.tar.gz`. Unpack them in this folder with
`tar -xzf inputs/polymers/conformers_v1.tar.gz` before running the polymer workflow;
`scripts/verify_polymer_inputs.py` then checks every file against the sha256 in
`inputs/polymers/polymer_conformers_v1.csv`. The scripts write their records under `state/` and
`logs/`, which are not tracked: create them first with `mkdir -p state logs`.
