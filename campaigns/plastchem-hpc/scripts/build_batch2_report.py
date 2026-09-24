"""Cumulative measured-logKow comparison and batch report; no empirical correction."""
import csv,datetime as dt,hashlib,json,math
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
R=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler/batch-2');F=Path('/mnt/r/plastchem-euler/progress-2026-09-14');D=B/'validation';D.mkdir(exist_ok=True)
def read(p):return json.loads(Path(p).read_text())
def rows(p):return list(csv.DictReader(Path(p).open()))
def save(p,x):Path(p).write_text(json.dumps(x,indent=2)+'\n')
def csvout(p,rs):
 if not rs:return
 with Path(p).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
v={r['input_inchikey']:r for r in rows(B/'cumulative-octanol.csv')};newrefs=rows(B/'pubchem-measured-validation/best-measured-logKow.csv');oldrefs=rows(F/'pubchem-measured-validation/best-measured-logKow.csv');refs=oldrefs+newrefs;assert len({r['input_inchikey'] for r in refs})==len(refs)
parity=[]
anchors={'FLKPEMZONWLCSK-UHFFFAOYSA-N':'DEP','DOIRQSBPFJWKBE-UHFFFAOYSA-N':'DBP','IRIAEXORFWYRCZ-UHFFFAOYSA-N':'BBP','BJQHLKABXJIVAM-UHFFFAOYSA-N':'DEHP'}
# Anchor labels are matched to the pinned original table's names and keys below.
oldparity=rows(F/'octanol-sign-review/four-anchor-parity.csv')
for r in refs:
 k=r['input_inchikey']
 if k not in v:continue
 p=float(v[k]['predicted_logKow']);obs=float(r['measured_logKow']);parity.append({**r,'cohort':v[k]['cohort'],'predicted_logKow':p,'residual_predicted_minus_measured':p-obs,'anchor':anchors.get(k,''),'source_question':'Owner unresolved: PubChem 9.05 versus primary abstract 8.83 +/- 0.05' if 'didecyl' in r['name'].lower() else ''})
assert sum(bool(r['anchor']) for r in parity)==4
for r in parity:
 if r['anchor']:
  old=next(x for x in rows(F/'octanol-validation/validation/best-measured-parity.csv') if x['input_inchikey']==r['input_inchikey']);assert float(old['predicted_logKow'])==r['predicted_logKow'] and float(old['measured_logKow'])==float(r['measured_logKow'])
res=np.array([r['residual_predicted_minus_measured'] for r in parity]);obs=np.array([float(r['measured_logKow']) for r in parity]);pred=np.array([r['predicted_logKow'] for r in parity]);slope,intercept=np.polyfit(obs,pred,1)
stats={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'n':len(parity),'batch2_matched':sum(r['cohort']=='batch2' for r in parity),'original_matched':sum(r['cohort']=='released' for r in parity),'MAE':float(np.mean(abs(res))),'RMSE':float(np.sqrt(np.mean(res**2))),'bias':float(np.mean(res)),'slope':float(slope),'intercept':float(intercept),'outliers':[{'name':r['name'],'input_inchikey':r['input_inchikey'],'measured':float(r['measured_logKow']),'predicted':r['predicted_logKow'],'residual':r['residual_predicted_minus_measured'],'source_url':r['source_url']} for r in parity if abs(r['residual_predicted_minus_measured'])>1],'empirical_correction_applied':False}
save(D/'statistics.json',stats);csvout(D/'best-measured-parity.csv',parity);csvout(D/'four-anchor-parity.csv',[r for r in parity if r['anchor']]);csvout(D/'experimental-reference-candidates.csv',rows(F/'pubchem-measured-validation/experimental-reference-candidates.csv')+rows(B/'pubchem-measured-validation/experimental-reference-candidates.csv'))
plt.rcParams.update({k:14 for k in ['font.size','axes.titlesize','axes.labelsize','xtick.labelsize','ytick.labelsize','legend.fontsize']});plt.rcParams.update({'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(10,10),layout='constrained');lo=min(min(obs),min(pred))-.5;hi=max(max(obs),max(pred))+.5;ax.plot([lo,hi],[lo,hi],color='black',label='1:1')
for cohort,color,label in [('released','#3274a1','Released rehearsal'),('batch2','#c47c26','Batch 2')]:
 rs=[r for r in parity if r['cohort']==cohort]
 if rs:ax.scatter([float(r['measured_logKow']) for r in rs],[r['predicted_logKow'] for r in rs],s=65,color=color,label=label)
ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Measured octanol/water logKow',ylabel='Predicted octanol/water logKow',title=f'Cumulative experimental comparison (n = {len(parity)})');ax.legend();fig.savefig(B/'figures/predicted-vs-experimental.png',dpi=300);plt.close(fig)
summary=read(B/'cumulative-summary.json');audit=read(B/'numerical-audit.json');surf=read(B/'completed-surface-audit.json');retrieval=read(B/'pubchem-measured-validation/summary.json');manifest=read(B/'manifest.json');live=read(R/'state/campaign-v1/summary.json')['counts'];batch=read(B/'summary.json')
caption=f"n = {len(parity)}; MAE {stats['MAE']:.3f}, RMSE {stats['RMSE']:.3f}, bias {stats['bias']:+.3f} log units. Black line: 1:1. One selected qualified measured point per molecule; no recalibration."
(B/'figures/parity-caption.txt').write_text(caption+'\n')
outliers='\n'.join(f"| {r['name']} | {r['measured']:.3f} | {r['predicted']:.3f} | {r['residual']:+.3f} | [Source]({r['source_url']}) |" for r in stats['outliers'])
anchor_table='\n'.join(f"| {r['anchor']} | {float(r['measured_logKow']):.3f} | {r['predicted_logKow']:.3f} | [Source]({r['source_url']}) |" for r in parity if r['anchor'])
report=f"""# Contaminant progress — Batch 2, 15 September 2026

Generated {stats['utc']}. Batch cohort pinned {manifest['utc']}. This is a cumulative progress report, not campaign completion. The released 648-molecule package is untouched (release 90ec6c7d43911ad9350b4a0feca5359f3bd5ddc532893e3ef92d278f19719616; 1,483 files).

5,833 pinned − 9 excluded isotope-labelled structures = 5,824 eligible; no parent mapping. Released 648 + batch 2 296 = **944 report molecules**. Later returns are excluded from report predictions. Current operational counts (separate from the fixed cohort): **{live['converged']} converged, {live['failed']} failed, {live['running']} running, {live['not_yet_run']} not yet run / 5,824**. Figure-time dispositions are pinned in campaign-dispositions.csv, so later live changes do not alter figures.

The six support solvents remain 6/6 converged, 0/6 failed. Batch-2 surfaces passed **{surf['passed']}/296** provenance, deck, connectivity, coordinate and surface-integrity checks, with {surf['failed']} failures. The released audit passed 648/648 separately. Full perceived keys and perception-engine records remain in the cohort records and figure CSV. Three post-freeze DFT completions failed the required first-block match: HECMZBWRWWVBJI-UHFFFAOYSA-N, IESSXLDTYTXHDL-UHFFFAOYSA-N and BGVYDWVAGZBEMJ-UHFFFAOYSA-N. They receive no predictions or retries. The other four failures are three preparation failures and one geometry nonconvergence, as recorded in the released report.

## Validation-only octanol/water comparison

Batch 2 has **296/296 predictions, 0/296 failed, 0/296 unprocessed**. Cumulative octanol coverage is **886/944** report molecules (590 original rehearsal + 296 new); 58 released molecules have no octanol calculation and remain unavailable. Octanol is validation-only and does not change the production panel. The frozen serial ORCA recipe and unchanged openCOSMO-RS 24a at 298.15 K apply.

PubChem retrieval attempted {retrieval['retrieval_molecules_completed']}/296 batch-2 keys; {retrieval['qualified_observations']} qualified observations yielded {retrieval['best_point_value_molecules']} selected point-value molecules. A missing LogP section does not prove no experiment exists. Raw JSON, citations, hashes and UTC retrieval times are under reference-sources/ and pubchem-measured-validation/. Explicitly modelled values are excluded; HSDB/Hansch literature and Sangster curated data remain separately labelled. Source selection rules are unchanged and independent of predictions.

**Cumulative n = {stats['n']} ({stats['original_matched']} original + {stats['batch2_matched']} batch 2); MAE {stats['MAE']:.3f}, RMSE {stats['RMSE']:.3f}, bias {stats['bias']:+.3f}.** Predicted-on-measured regression: slope {slope:.6f}, intercept {intercept:.6f}. This descriptive, nonrandom sample does not establish campaign-wide accuracy. No recalibration is applied.

![Cumulative parity](figures/predicted-vs-experimental.png)

{caption}

| Outlier (absolute residual >1) | Measured | Predicted | Residual | Citation |
|---|---:|---:|---:|---|
{outliers}

All four anchors are numerically unchanged:

| Anchor | Measured | Predicted | Citation |
|---|---:|---:|---|
{anchor_table}

The calculation uses neutral dry pure-component references; standard experiments use mutually saturated water/octanol, and pH, temperature and measurement method may differ. High-hydrophobicity measurement difficulty and the dry/wet mismatch remain possible contributors, with neither assigned dominance. See [OECD 123](https://doi.org/10.1787/9789264015845-en). Every parity row retains the raw citation. Didecyl's unresolved source discrepancy remains flagged; its original selected value is unchanged pending owner decision.

## Original product-panel partitioning and validation

Batch 2 contributes **1,776 predictions / 296 molecules**. Cumulative panel coverage is **5,664 predictions / 944 molecules**, six available solvent/water pairs per molecule out of 32 requested. Missing pairs remain unavailable. These are neutral concentration-ratio predictions, not pH-dependent logD, polymer partitioning, mixed-solvent equilibria or validation of PFAS chemistry.

Stored numerical/provenance checks passed **944/944 panel records and 886/886 octanol records**. Checks include identity, dilution plateau, finite conversion arithmetic and source hashes; batch octanol water activities are exactly those in its digest-verified panel records. Multi-solvent closure is algebraic consistency, not independent experimental validation or a new water-only solver run.

The original same-solvent experimental panel comparison remains n=1 (DEP/chloroform), MAE/RMSE/bias +1.836. The four workstation implementation comparisons remain unchanged: workstation Intel Core i7-13700 versus Euler AMD EPYC 7763. Maximum production anchor difference from workstation is 0.189156 log units; maximum same-solute/reference hybrid difference is 0.003663. These are implementation checks, not experimental accuracy. Full supporting data remain in the [released report]({F/'REPORT.md'}).

## Figures and timing

All figures are cumulative and saved at 300 dpi with uniform 14-point black text. CSVs accompany the figures; figures distinguish the fixed 944-molecule report cohort from later completions and pending work.

![Cumulative distributions](figures/computed-value-distribution.png)

Six solvent distributions, each n=944; no missing value is interpolated.

![Campaign progress](figures/campaign-progress.png)

All 5,824 eligible structures are accounted for by chunk; completed-later is a separate category.

![Cumulative wall time](figures/walltime-vs-atoms.png)

Measured OPT+COSMORS wall times on AMD EPYC 7763, with the unchanged Milan pilot mean fit over 15–80 atoms. Residuals are in walltime-vs-atoms.csv. Report-cohort measured main cost **{summary['main_hours']:.2f} CPU-hours**, tail cost **{summary['tail_hours']:.2f} CPU-hours**, separately. These totals exclude failed attempts and unfinished jobs. Maximum recorded RSS among report molecules: **{summary['maxrss_mib']:.1f} MiB** versus the 4,096 MiB request; this is not proof every unmeasured transient stayed below that value.

## Execution, pending work and reproduction

Batch processing completed with exit 0; the normal serial processor restarted as PID 3678400. Collector and capacity monitoring survived. Use the current process table to verify liveness rather than treating this historical PID as proof. Concurrency remains authorised at 32 (28 main + 4 tail), with no change during batch preparation. Main 02 is released only as Main 01 drains, with recorded readback and total bound ≤32; tail is untouched.

Open owner questions: **xylene ortho/para identity**, and **didecyl phthalate PubChem 9.05 versus cited primary abstract 8.83 ±0.05**. Neither is resolved here. Missing solvent references and later campaign returns remain pending.

Reproduce within /home/aaltamimi2/plastchem-euler: `python3 scripts/fetch_batch2_measured.py --all-cohort`; `python3 scripts/qualify_batch2_measured.py`; `python3 scripts/build_batch2_figures.py`; `python3 scripts/build_batch2_report.py`. Raw calculations: `scripts/process_batch2_20260915.py`, requiring the exclusive serial worker lock; do not launch alongside the live processor. Surface audit: `PLASTCHEM_PROGRESS_ROOT=/mnt/r/plastchem-euler/batch-2 /home/aaltamimi2/.venvs/cosmo-logp/bin/python scripts/audit_completed_surfaces.py --frozen`. Cohort and science provenance: manifest.json, provenance.json, numerical-audit.json. Bulk artifacts remain on R:, transferred with cp/scp, never rsync.

## Octanol sign and hydrophobicity review

The original DEP hand check remains 4.215662 − 0.943149 = 3.272513; conversion sign is correct. Batch results use the same convention. Original n=21 regression and hydrophobicity-bin findings remain in the released review; the cumulative regression above is an extension, not a calibration. The original low-logKow group was not uniformly within ±0.3. No new causal attribution is made from the extended sample.
"""
(B/'REPORT.md').write_text(report);print(json.dumps(stats),flush=True)
