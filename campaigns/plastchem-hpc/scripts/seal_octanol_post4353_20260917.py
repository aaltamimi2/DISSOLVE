"""Seal the audited disjoint cohort; no new experimental matches or recalibration."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,hashlib,datetime
ROOT=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/octanol-post4353-2026-09-17')
a=json.loads((D/'numerical-audit.json').read_text());s=json.loads((D/'summary.json').read_text());m=json.loads((D/'manifest.json').read_text())
assert a['denominator']==a['passed']==s['denominator']==102 and a['failed']==0
assert s['octanol_predicted']+s['octanol_failed']==102
assert json.loads((ROOT/'state/octanol-post4353-worker-handoff-20260917.json').read_text())['batch_exit_code']==0
assert m['matched_selected_reference_count']==37
(D/'REPORT.md').write_text(f"# Audited octanol catch-up\n\nThis disjoint cohort contains 102 accepted contaminants. Predictions: {s['octanol_predicted']}/102; explicitly unavailable: {s['octanol_failed']}/102. All 102 outcomes passed provenance, identity, surface-hash, dilution and partition-algebra checks.\n\nEarlier audited predictions: 4353; cumulative available predictions: {4353+s['octanol_predicted']}. The cohort includes {m['matched_selected_reference_count']} selected measured references. A refreshed experimental comparison is a separate step; numerical consistency is not experimental accuracy. No recalibration was applied.\n\nReleased and batch-2 packages remain unchanged. The full solvent panel remains incomplete. Xylene identity and the didecyl-phthalate reference discrepancy remain owner questions.\n")
code=D/'code';code.mkdir(exist_ok=True)
for name in ['process_octanol_catchup_20260917.py','prepare_octanol_post4353_20260917.py','audit_octanol_post4353_20260917.py',Path(__file__).name]:(code/name).write_bytes((ROOT/'scripts'/name).read_bytes())
files=sorted(p for p in D.rglob('*') if p.is_file() and p!=D/'artifacts.sha256')
with ThreadPoolExecutor(max_workers=4) as pool:hashes=list(pool.map(lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),files))
(D/'artifacts.sha256').write_text(''.join(h+'  '+str(p.relative_to(D))+'\n' for p,h in zip(files,hashes)))
with ThreadPoolExecutor(max_workers=4) as pool:assert hashes==list(pool.map(lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),files))
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files_verified':len(files),'manifest_sha256':hashlib.sha256((D/'artifacts.sha256').read_bytes()).hexdigest(),'audit_passed':102,'audit_failed':0,'predicted':s['octanol_predicted'],'cumulative_audited_predictions':4353+s['octanol_predicted']}
(ROOT/'state/octanol-post4353-sealed-20260917.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
