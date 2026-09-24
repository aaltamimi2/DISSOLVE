import json,csv,hashlib,datetime,shutil
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/measured-expansion-2026-09-15');R=Path(__file__).resolve().parents[1];F=Path('/mnt/r/plastchem-euler/progress-2026-09-14')
read=lambda p:json.loads(p.read_text());s=read(D/'statistics.json');o=read(D/'opera-match-summary.json');c=read(D/'comptox-match-summary.json');a=read(D/'numerical-audit.json');live=read(R/'state/campaign-v1/summary.json')['counts'];parity=list(csv.DictReader((D/'best-measured-parity.csv').open()))
outliers='\n'.join(f"| {r['name']} | {r['measured']:.3f} | {r['predicted']:.3f} | {r['residual']:+.3f} | [Source]({r['source_url']}) |" for r in s['outliers'])
anchors='\n'.join(f"| {r['anchor']} | {float(r['measured_logKow']):.3f} | {float(r['predicted_logKow']):.3f} | [Source]({r['source_url']}) |" for r in parity if r['anchor'])
text=f'''# Expanded measured-reference validation — 15 September 2026

Generated {s['utc']}. This supplementary snapshot covers **960 converged structures** pinned in cohort.json: released 648 + batch 2 296 + 16 later returns. It does not modify the released package or batch-2 report. The original release remains **90ec6c7d43911ad9350b4a0feca5359f3bd5ddc532893e3ef92d278f19719616** (1,483 files). Returns after this snapshot belong to subsequent processing.

At report generation the separate live campaign counts are {live['converged']} converged / {live['failed']} failed / {live['running']} running / {live['not_yet_run']} not yet run, denominator **5,824**. The 960 validation cohort is fixed; it is not a claim of campaign completion. Eligibility remains 5,833 − 9 excluded isotope-labelled structures = 5,824. No isotope-parent mapping or interpolation.

## Experimental coverage and source qualification

**{s['n']}/960 molecules have a qualified selected measured logKow**; 915 have no qualified measured point from these searches. This measures reference availability, not prediction completion. All **960/960 have dry-octanol/water predictions**, including the 74 previously missing octanol calculations (74 successful, zero failures). Their corresponding panel records were reused, yielding six available panel-solvent pairs each. The full 32-solvent production panel remains incomplete.

The original PubChem query covered 590 molecules: 556 had no LogP section, 33 had a section, and one identity/retrieval failure; 21 yielded qualified measured point values. Batch 2 added 13 measured molecules; the additional 74-key query added three, making **37 PubChem matches**. Explicitly estimated, computed and predicted values remain excluded. No LogP section is a retrieval outcome, not evidence no measurement exists anywhere.

OPERA supplied **40 first-block matches**, 36 also confirmed by CAS, zero CAS-only accepted. It contributes **eight molecules not in the 37 PubChem matches**, bringing the union to 45. The pinned public repository distributes LogP_QR.sdf inside OPERA_Data.zip rather than standalone training/test CSVs; the reproducible extraction writes observed training and test CSVs. Of 13,963 SDF records, 11,131 have a training flag, 2,770 a test flag and 62 neither. Requiring a training/test flag, finite observed LogP and a nonempty/non-model citation retains 13,891 rows. There is no per-row experimental boolean; the experimental status comes from the published curated experimental dataset, and the observed LogP field is used, never a prediction field. See [OPERA repository](https://github.com/kmansouri/OPERA) and [Mansouri et al. 2018](https://doi.org/10.1186/s13321-018-0263-1).

OPERA commit `{o['source_receipt']['commit']}`; retrieved `{o['source_receipt']['retrieved_utc']}`; archive SHA-256 `{o['source_receipt']['sha256']}`; SDF SHA-256 `{o['sdf_sha256']}`. Extraction CSV digests and raw source citations are in opera-match-summary.json and candidates CSV. The newer update CSV was not used because its experimental status was not established.

CompTox public batch exports succeeded without a key: **{c['qualified_observations']} experimental rows / {c['matched_molecules']} molecules**, including six additional matches from secondary CAS search. They add **zero new molecules** beyond the 45-source union. Only TYPE=experimental, NAME=LogKow: Octanol-Water, finite point values, Log10 unitless and a source description were accepted. Rows labelled OPERA2.8 experimental explicitly describe observed SDF data; separately labelled predicted rows are excluded. CAS results still require a matching InChIKey first block; mismatches or missing skeletons are recorded and excluded. The direct experimental API returned 401 requiring x-api-key; the public batch export was a successful alternative, not an API bypass. Raw XLSX files, request bodies, export IDs, source descriptions, retrieval times and hashes are in sources/. [Public batch interface](https://comptox.epa.gov/dashboard/batch-search).

Every accepted source row is retained in experimental-reference-candidates.csv with citation text, numeric parse, provenance class, source URL, retrieval UTC and SHA-256. Multiple compilations can repeat the same original experiment; these rows are **not independent experimental replicates**. Primary statistics use one point per molecule: existing published selections remain unchanged, then previously unrepresented molecules use OPERA observed data, then CompTox experimental fallback. Selection does not use prediction residuals. First-block matching intentionally ignores stereochemistry under D-IDENT; source stereoisomers and computed structures are not claimed to be identical full identities. Per-molecule-best-measured.csv includes all 960 keys with explicit missing-reference status.

## Enlarged accuracy comparison

**n={s['n']}; MAE {s['MAE']:.3f}, RMSE {s['RMSE']:.3f}, bias {s['bias']:+.3f} log units.** Predicted-on-measured least-squares slope **{s['slope']:.6f}**, intercept **{s['intercept']:.6f}**. No empirical correction or recalibration. This selected, nonrandom experimental subset does not establish accuracy across all 5,824 structures. Some entries share connectivity despite differing stereochemistry, and source compilations overlap; n counts campaign entries, not independent chemistry classes. Specifically, the 45 entries represent **42 distinct connectivity skeletons**: methyl oleate/elaidate, methyl linoleate/linolelaidate, and methyl erucate/methyl docos-13-enoate each share a first block. These source transfers are explicit connectivity matches, not new independent measurements.

![Expanded parity](predicted-vs-experimental.png)

Black line: 1:1. Caption: n={s['n']}; MAE {s['MAE']:.3f}, RMSE {s['RMSE']:.3f}, bias {s['bias']:+.3f} log units. PNG 300 dpi, uniform 14-point black text. Per-row values and source citations are in best-measured-parity.csv. No fitted correction is applied to predictions.

The **released n=21 figure** remains unchanged: MAE 1.525749, RMSE 1.879302, bias +1.450602, slope 1.102869, intercept 0.823937. The separate batch-2 cumulative n=34 result also remains unchanged. The expanded regression differs because additional chemical classes entered the sample; it does not invalidate or replace the historical numerical record.

Named outliers, absolute residual above one log unit:

| Molecule | Measured | Predicted | Residual | Citation |
|---|---:|---:|---:|---|
{outliers}

All four anchor values are unchanged:

| Anchor | Measured | Predicted | Citation |
|---|---:|---:|---|
{anchors}

## Numerical checks and interpretation

Stored dilution, surface identity/provenance and volume-conversion checks passed **{a['passed']}/960**, zero failures. The octanol convention remains logKconc = (ln gamma_water − ln gamma_octanol)/ln(10) + log10(Vwater/Voctanol). DEP remains 4.215662 − 0.943149 = 3.272513. The additional octanol calculations reuse water activities from SHA-verified panel records. These are numerical consistency checks, not independent experimental validation. The original four workstation implementation-agreement tests remain in the released report and are separate from measured accuracy.

Dry pure octanol is used; standard measured octanol/water partitioning uses mutually saturated phases. Temperature, pH and ionization can differ, especially for acids and bases. High-hydrophobicity measurements can also be difficult; no contributor is asserted to dominate and no empirical correction is applied. Per-source values above approximately 7–8 should be interpreted with their original methods and uncertainty. [OECD slow-stirring guideline](https://doi.org/10.1787/9789264015845-en).

## Pending owner questions and reproduction

**Xylene identity** and **didecyl phthalate source discrepancy (PubChem 9.05 versus primary abstract 8.83 ±0.05)** remain owner questions. The original didecyl selection is retained, with the discrepancy visible in its parity row. No solvent-panel change was made; octanol remains validation-only. Campaign concurrency remains capped at 32; no scheduler change was needed during this expansion.

Run from /home/aaltamimi2/plastchem-euler: `python3 scripts/fetch_opera_measured_20260915.py`; `python3 scripts/match_opera_measured_20260915.py`; `python3 scripts/fetch_expansion_pubchem.py --all-cohort`; `python3 scripts/qualify_expansion_pubchem.py`; `python3 scripts/qualify_comptox_expansion.py`; `python3 scripts/build_measured_expansion.py`; `python3 scripts/report_measured_expansion.py`. Public CompTox export requests and download endpoints are stored under sources/ for replay through requests.post/json and requests.get; do not blindly reuse an expired export ID. Raw octanol generation is scripts/process_expansion_octanol.py and requires the exclusive thermodynamics worker lock. Do not rerun a historical PID handoff script. Bulk outputs remain on /mnt/r/plastchem-euler, copied with cp/scp, never rsync.
'''
(D/'REPORT.md').write_text(text)
S=D/'software-sources';S.mkdir(exist_ok=True)
for name in ['fetch_opera_measured_20260915.py','match_opera_measured_20260915.py','fetch_expansion_pubchem.py','qualify_expansion_pubchem.py','qualify_comptox_expansion.py','build_measured_expansion.py','report_measured_expansion.py','process_expansion_octanol.py','finalize_measured_expansion.py']:shutil.copyfile(R/'scripts'/name,S/name);assert hashlib.sha256((R/'scripts'/name).read_bytes()).hexdigest()==hashlib.sha256((S/name).read_bytes()).hexdigest()
print(D/'REPORT.md')
