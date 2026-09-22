"""Seal the audited disjoint cohort; no new experimental matches or recalibration."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,hashlib,datetime
ROOT=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/octanol-post4244-2026-09-17')
a=json.loads((D/'numerical-audit.json').read_text());s=json.loads((D/'summary.json').read_text());m=json.loads((D/'manifest.json').read_text())
assert a['denominator']==a['passed']==s['denominator']==17 and a['failed']==0
assert s['octanol_predicted']+s['octanol_failed']==17
assert json.loads((ROOT/'state/octanol-post4244-worker-handoff-20260917.json').read_text())['batch_exit_code']==0
assert m['matched_selected_reference_count']==0
(D/'REPORT.md').write_text(f"# Audited octanol catch-up\n\nThis disjoint cohort contains 17 accepted contaminants. Predictions: {s['octanol_predicted']}/17; explicitly unavailable: {s['octanol_failed']}/17. All 17 outcomes passed the provenance, identity, surface-hash, dilution and partition-algebra audit. Numerical verification does not establish experimental accuracy or full solvent-panel coverage.\n\nEarlier audited octanol predictions: 4,244; cumulative available predictions: {4244+s['octanol_predicted']}. This cohort adds no matched measured references, so the n=447 experimental comparison in `/mnt/r/plastchem-euler/octanol-post4060-2026-09-17/cumulative-validation/` remains unchanged. No empirical correction or changed reference selection was applied.\n\nThe released 648 and fixed batch-2 cumulative 944 packages remain unchanged. Xylene identity and the didecyl-phthalate reference discrepancy remain owner questions. The panel-solvent library is still incomplete.\n\nFrozen manifest and source copies support reproduction. Run `process_octanol_catchup_20260917.py` with PLASTCHEM_OCTANOL_FILL_ROOT set to this directory, then the cohort auditor; processing is serial and resumable.\n")
code=D/'code';code.mkdir(exist_ok=True)
for name in ['process_octanol_catchup_20260917.py','prepare_octanol_post4244_20260917.py','audit_octanol_post4244_20260917.py',Path(__file__).name]:(code/name).write_bytes((ROOT/'scripts'/name).read_bytes())
files=sorted(p for p in D.rglob('*') if p.is_file() and p!=D/'artifacts.sha256')
with ThreadPoolExecutor(max_workers=4) as pool:hashes=list(pool.map(lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),files))
(D/'artifacts.sha256').write_text(''.join(h+'  '+str(p.relative_to(D))+'\n' for p,h in zip(files,hashes)))
with ThreadPoolExecutor(max_workers=4) as pool:assert hashes==list(pool.map(lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),files))
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files_verified':len(files),'manifest_sha256':hashlib.sha256((D/'artifacts.sha256').read_bytes()).hexdigest(),'audit_passed':17,'audit_failed':0,'predicted':s['octanol_predicted'],'cumulative_audited_predictions':4244+s['octanol_predicted']}
(ROOT/'state/octanol-post4244-sealed-20260917.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
