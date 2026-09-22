"""Wait for existing reference and numerical jobs before calculating the enlarged comparison."""
from pathlib import Path
import json,subprocess,time,hashlib
R=Path(__file__).resolve().parents[1]
for pid,name in [(668332,'finalize_pubchem_catchup_20260917.py'),(664810,'finalize_octanol_catchup_20260917.py')]:
 while Path(f'/proc/{pid}').exists():
  assert name in Path(f'/proc/{pid}/cmdline').read_text();time.sleep(30)
assert json.loads((R/'state/octanol-catchup-finalized-20260917.json').read_text())['status']=='audited_and_compared'
subprocess.run(['python',str(R/'scripts/build_combined_references_20260917.py')],check=True)
subprocess.run(['/home/aaltamimi2/.venvs/cosmo-logp/bin/python',str(R/'scripts/compare_combined_catchup_20260917.py')],check=True)
D=Path('/mnt/r/plastchem-euler/octanol-catchup-2026-09-17/cumulative-validation')
for name in ['build_combined_references_20260917.py','compare_combined_catchup_20260917.py','finalize_combined_validation_20260917.py']:(D/name).write_bytes((R/'scripts'/name).read_bytes())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
print('COMBINED_COMPARISON_READY_FOR_FIGURE_REVIEW',flush=True)
