"""Check snapshot data attribution and render artifacts; seal this draft without changing science."""
import csv,json,hashlib,datetime,math,shutil,concurrent.futures,collections
from pathlib import Path
from PIL import Image
import numpy as np
R=Path('/home/aaltamimi2/plastchem-euler');B=Path('/mnt/r/plastchem-euler');D=B/'progress-2026-09-17/contaminant-overview-draft-v1'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
s=json.loads((D/'summary.json').read_text());pred=list(csv.DictReader((D/'predictions.csv').open()));meta=list(csv.DictReader((D/'contaminant-rows.csv').open()));cols=list(csv.DictReader((D/'solvent-columns.csv').open()));accepted={p.stem for p in (R/'state/campaign-v1/records').glob('*.json') if json.loads(p.read_text())['status']=='converged'};assert len(meta)==5803 and {r['input_inchikey'] for r in meta}==accepted
lookup={};availability=json.loads((D/'availability.json').read_text());mapping={r['panel_key']:r['common_key'] for r in availability['rows'] if r.get('panel_key')};mapping['acetic acid']='acetic acid'
for r in pred:lookup.setdefault(r['source_path'],[]).append(r)
def verify(item):
 path,rows=item;raw=Path(path).read_bytes();h=hashlib.sha256(raw).hexdigest();r=json.loads(raw);assert all(x['source_sha256']==h and x['input_inchikey']==r['input_inchikey'] for x in rows)
 if rows[0]['source']=='production_panel':expected={mapping[x['solvent']]:x for x in r['partitions_against_water'] if x['status']=='predicted'}
 else:expected={'1-octanol':r['prediction']}
 assert len(expected)==len(rows)
 for x in rows:
  p=expected[x['solvent']];assert p['status']=='predicted' and float(x['temperature_K'])==p['temperature_K']==298.15
  assert float(x['log10_K_mole_fraction'])==p['log10_K_mole_fraction']
  conc=None if not x['log10_K_concentration'] else float(x['log10_K_concentration']);assert conc==p['log10_K_concentration']
 return path,h
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:verified=dict(pool.map(verify,lookup.items()))
array=np.load(D/'heatmap-matrix.npz');matrix=array['values'];assert matrix.shape==(5803,70);assert np.isfinite(matrix).sum()==len(pred)==s['displayed_predictions'];ix={k:i for i,k in enumerate(array['input_inchikey'])};jx={k:i for i,k in enumerate(array['solvents'])}
for r in pred:assert matrix[ix[r['input_inchikey']],jx[r['solvent']]]==float(r['log10_K_mole_fraction'])
assert sha(D/'contaminant-data-overview-draft.png')==sha(R/'contaminant-data-overview-draft-2026-09-17.png')
im=Image.open(D/'contaminant-data-overview-draft.png');assert all(abs(v-300)<.01 for v in im.info['dpi']);assert im.width==2100
p=B/'common69-pilot-20260917/composition-grid.csv';ex=[r for r in csv.DictReader(p.open()) if r['solvent']=='ethanol'];assert len(ex)==11
with (D/'worked-example.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(ex[0]));w.writeheader();w.writerows(ex)
validation=B/'validation-final5803-2026-09-17';stats=json.loads((validation/'statistics.json').read_text());vrows=list(csv.DictReader((validation/'parity.csv').open()));x=[float(r['measured_logKow']) for r in vrows];y=[float(r['predicted_logKow']) for r in vrows];e=[b-a for a,b in zip(x,y)];n=len(e);xm=sum(x)/n;ym=sum(y)/n;slope=sum((a-xm)*(b-ym) for a,b in zip(x,y))/sum((a-xm)**2 for a in x);vals={'MAE':sum(abs(z) for z in e)/n,'RMSE':math.sqrt(sum(z*z for z in e)/n),'bias':sum(e)/n,'slope':slope,'intercept':ym-slope*xm}
assert n==stats['n']==1179 and all(abs(vals[k]-stats[k])<1e-12 for k in vals)
val_manifest=validation/'artifacts.sha256'
for line in val_manifest.read_text().splitlines():h,name=line.split('  ',1);assert sha(validation/name)==h
shutil.copyfile(validation/'parity.png',R/'2026-09-17-cumulative-parity-n1179.png')
review={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'snapshot_rows':5803,'solvent_columns':70,'predictions':len(pred),'source_result_files_readback_verified':len(verified),'matrix_matches_csv':True,'png_dpi':im.info['dpi'],'png_dimensions':list(im.size),'local_png_sha256':sha(D/'contaminant-data-overview-draft.png'),'validation_n':n,'validation_connectivity_blocks':len({r['input_inchikey'].split('-')[0] for r in vrows}),'validation_statistics_independently_recomputed':vals,'visual_review':'Overview and parity inspected; black uniform text; row-resolution and footer/title checks enforced by renderer; final revised overview visual review recorded separately','example_source_sha256':sha(p)}
(D/'verification.json').write_text(json.dumps(review,indent=2)+'\n')
spec=R/'reports/progress-2026-09-17/CONTAMINANT_OVERVIEW_AND_CALCULATION_SPEC.md';shutil.copyfile(spec,D/spec.name)
report=f'''# Contaminant overview draft — 17 September 2026

## Current snapshot

The draft displays **{len(pred):,} existing pure-solvent/water partition predictions** for **5,803 accepted contaminants**, grouped into eight atom-count bins. All 5,803 appear exactly once. There are 70 solvent columns: 69 product common keys plus acetic acid to retain existing panel data. All calculations shown are at **298.15 K (25°C)**. Unavailable cells: **{s['missing_cells']:,}**, shown gray rather than zero. This is a partial computed grid, not a claim of complete coverage or engine integration.

Source counts: {s['production_panel_predictions']:,} panel predictions and {s['octanol_predictions']:,} validation-only octanol predictions. The snapshot capture began {s['snapshot_started_utc']} and was finalized {s['snapshot_finalized_utc']}; two changing panel records were reconciled to newer hash-verified records and recorded in summary.json. All copied source results have now been read back and checked against their source hashes and extracted CSV values.

![Contaminant data overview](contaminant-data-overview-draft.png)

The DEP worked example uses 11 ethanol/water mole-fraction compositions from the bounded pilot. It illustrates calculated dilute activity ratios, not experimental data or phase-equilibrium partitioning. Its points are separate from the pure-solvent headline count. Heatmap colours show log10 K on the mole-fraction basis consistently; concentration-based coefficients are retained separately in CSV where available. Gray means no accepted prediction in this snapshot. The PNG gives every contaminant at least one pixel row at 300 dpi; PDF and complete matrix are included. Draft width is 7 inches; a publication-width reduction needs a fresh legibility/layout pass.

## Campaign and validation status

ORCA: 5,803 accepted / 21 failed / 0 running / 0 not yet run, denominator 5,824. Nine isotope-labelled structures were excluded upstream from 5,833 CHNO inputs. All 5,803 accepted surfaces have a matching surface audit. Total terminal-attempt CPU time: 3,676.9 h. The roughly 9,000-contaminant count is a future owner planning figure, not this lane's verified running or accepted denominator.

The final octanol cohort completed 1,171/1,171 predictions and passed 1,171/1,171 numerical/provenance audits; combined with prior 4,632, octanol covers all 5,803 accepted contaminants. The broader existing panel pass continues serially. Full 69-solvent precomputation, additional compositions/temperatures and product integration were not launched.

Experimental comparison: **n={n}; MAE={vals['MAE']:.4f}; RMSE={vals['RMSE']:.4f}; bias={vals['bias']:+.4f}; slope={vals['slope']:.4f}; intercept={vals['intercept']:.4f}**. These are log-unit statistics against qualified cited measured values, with no recalibration. The table has {n} full input keys spanning {review['validation_connectivity_blocks']} connectivity blocks; those are entry-weighted metrics, not {n} independent connectivity skeletons. Prior released n=21 and n=622 packages are unchanged. Dry-octanol modelling and measured water-saturated-octanol conditions differ; measured high-logKow source uncertainty remains. Named outliers and per-row citations are in `/mnt/r/plastchem-euler/validation-final5803-2026-09-17/`.

## Proposed work, not launched

See [the figure and calculation specification](CONTAMINANT_OVERVIEW_AND_CALCULATION_SPEC.md). It defines the 69-solvent readiness audit, missing surfaces, identity caveats, possible pure-solvent/composition/temperature grids, deduplicated counts, validation gates, cache keys and later engine integration. Chlorobenzene and sulfolane lack a verified compatible local surface. The DEP pilot passed 67 solvents and 737 mixture cells; this is not proof of every solvent/contaminant combination. Documented concentration-conversion volumes remain incomplete.

Xylene identity and the didecyl-phthalate measured-reference discrepancy remain owner questions. No new ORCA jobs or future grid calculations were submitted by this figure task.

## Reproduction and files

Snapshot directory: `{D}`. Figure: `contaminant-data-overview-draft.png` (300 dpi) and `.pdf`. CSVs: `predictions.csv`, `contaminant-rows.csv`, `solvent-columns.csv`, `size-bin-summary.csv`, `worked-example.csv`, `planned-grid-counts.csv`. Complete numeric matrix: `heatmap-matrix.npz`. Evidence: `summary.json`, `verification.json`, `snapshot/`, `SHA256SUMS` and copied `code/`.

Local PNG: `/home/aaltamimi2/plastchem-euler/contaminant-data-overview-draft-2026-09-17.png`.

Scripts are under `/home/aaltamimi2/plastchem-euler/scripts/`: `build_contaminant_overview_snapshot_20260917.py`, `finalize_contaminant_overview_data_20260917.py`, `render_contaminant_overview_20260917.py`, `seal_contaminant_overview_20260917.py`. Use `/home/aaltamimi2/.venvs/cosmo-logp/bin/python`. The builder refuses an existing snapshot directory; create a new version for later data rather than overwriting this draft. The renderer reads this frozen dataset. No product database writes are part of this workflow.
'''
(D/'REPORT.md').write_text(report)
code=D/'code';code.mkdir(exist_ok=True)
for name in ['build_contaminant_overview_snapshot_20260917.py','finalize_contaminant_overview_data_20260917.py','render_contaminant_overview_20260917.py','seal_contaminant_overview_20260917.py']:shutil.copyfile(R/'scripts'/name,code/name)
# Raw sources were independently read back above; hash every other artifact now.
files=sorted(set(Path(k) for k in verified) | {p for p in D.iterdir() if p.is_file() and p.name!='SHA256SUMS'} | {p for p in code.iterdir() if p.is_file()});hashes={Path(k):v for k,v in verified.items()}
for p in files:
 if p not in hashes:hashes[p]=sha(p)
(D/'SHA256SUMS').write_text(''.join(hashes[p]+'  '+str(p.relative_to(D))+'\n' for p in files))
receipt={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(D),'files':len(files),'manifest_sha256':sha(D/'SHA256SUMS'),'verification':review}
(R/'state/contaminant-overview-draft-20260917.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({k:v for k,v in receipt.items() if k!='verification'}),flush=True)
local=R/'reports/progress-2026-09-17/REPORT.md';local.write_text(report.replace('](contaminant-data-overview-draft.png)',f']({D}/contaminant-data-overview-draft.png)').replace('](CONTAMINANT_OVERVIEW_AND_CALCULATION_SPEC.md)',f']({spec})'))
