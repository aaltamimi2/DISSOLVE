"""Seal the fully audited octanol cohort and its separate validation comparisons."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,hashlib,os,datetime
ROOT=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/octanol-followup-2026-09-17')
a=json.loads((D/'numerical-audit.json').read_text());s=json.loads((D/'summary.json').read_text());v=json.loads((D/'later-validation/statistics.json').read_text())
assert a['passed']==a['denominator']==s['denominator']==s['octanol_predicted']==1032 and a['failed']==s['octanol_failed']==0
assert not Path('/proc/785536').exists(), 'Validation finalizer still active'
(D/'REPORT.md').write_text('# Audited octanol catch-up, 2026-09-17\n\n'
 'This separate cohort adds 1,032 accepted contaminants, all with successful validation-only dry-octanol predictions. All 1,032 passed identity, provenance, surface-hash, infinite-dilution and independent decimal partition-equation checks; zero audit failures. Together with the earlier 2,851 audited predictions, coverage is 3,883. This does not imply full panel-solvent coverage or campaign completion.\n\n'
 f"Combined-source experimental comparison: n={v['n']}, MAE={v['MAE']:.6f}, RMSE={v['RMSE']:.6f}, bias={v['bias']:+.6f}, slope={v['slope']:.6f}, intercept={v['intercept']:.6f}. See later-validation for sources, four anchors and outliers. The comparison using the earlier 322 selected references is separately retained in cumulative-validation. No recalibration or reference-choice changes were made to the previous 64 entries.\n\n"
 'The released 648 and fixed batch-2 cumulative 944 packages remain unchanged. Xylene identity and the didecyl-phthalate experimental reference discrepancy remain owner questions. Numerical verification is distinct from experimental accuracy; dry-octanol and measured-condition limitations remain.\n\n'
 'Reproduction scripts are copied under code/. Processing uses the fixed manifest and PLASTCHEM_OCTANOL_FILL_ROOT; the auditor verifies every outcome. The comparison scripts preserve prior reference choices. artifacts.sha256 pins the package files.\n')
code=D/'code';code.mkdir(exist_ok=True)
for name in ['process_octanol_catchup_20260917.py','audit_octanol_followup_20260917.py','compare_later_followup_20260917.py','compare_combined_followup_20260917.py',Path(__file__).name]:
 (code/name).write_bytes((ROOT/'scripts'/name).read_bytes())
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(262144),b''):h.update(b)
 return h.hexdigest()
paths=[]
for folder,dirs,files in os.walk(D):
 for name in files:
  p=Path(folder)/name
  if p not in [D/'artifacts.sha256',D/'artifacts.sha256.tmp']:paths.append(p)
paths.sort()
with ThreadPoolExecutor(max_workers=4) as pool:hs=list(pool.map(digest,paths))
content=''.join(h+'  '+str(p.relative_to(D))+'\n' for p,h in zip(paths,hs));tmp=D/'artifacts.sha256.tmp';tmp.write_text(content);tmp.replace(D/'artifacts.sha256')
for p,h in zip(paths,hs):
 if p.name in ['REPORT.md','summary.json','statistics.json','numerical-audit.json']:assert digest(p)==h
receipt={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'directory':str(D),'files':len(paths),'manifest_sha256':digest(D/'artifacts.sha256'),'audit_passed':1032,'audit_failed':0,'cumulative_audited_octanol':3883,'experimental_n':v['n']}
(ROOT/'state/octanol-followup-sealed-20260917.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt),flush=True)
