"""Wait for the serial worker, then verify its outcomes before any accuracy calculation."""
from pathlib import Path
import json,subprocess,time,datetime,hashlib
ROOT=Path(__file__).resolve().parents[1];pid=801713;py='/home/aaltamimi2/.venvs/cosmo-logp/bin/python'
while Path(f'/proc/{pid}').exists():
 assert 'run_octanol_post3883_handoff_20260917.py' in Path(f'/proc/{pid}/cmdline').read_text();time.sleep(30)
r=json.loads((ROOT/'state/octanol-post3883-worker-handoff-20260917.json').read_text());assert r['batch_exit_code']==0,r
for name in ['audit_octanol_post3883_20260917.py','compare_combined_post3883_20260917.py']:
 subprocess.run([py,str(ROOT/'scripts'/name)],check=True)
D=Path('/mnt/r/plastchem-euler/octanol-post3883-2026-09-17');O=D/'cumulative-validation'
for name in ['audit_octanol_post3883_20260917.py','compare_combined_post3883_20260917.py',Path(__file__).name]:(O/name).write_bytes((ROOT/'scripts'/name).read_bytes())
(O/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(O.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
(ROOT/'state/octanol-post3883-finalized-20260917.json').write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'audited_and_compared','figures_require_visual_review':True},indent=2)+'\n');print('FOLLOWUP_AUDITED_AND_COMPARED',flush=True)
