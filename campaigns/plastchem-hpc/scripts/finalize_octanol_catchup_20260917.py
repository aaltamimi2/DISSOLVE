"""Wait for the already-started serial handoff, then audit and compare its results."""
import subprocess,json,time,datetime
from pathlib import Path
R=Path(__file__).resolve().parents[1];pid=662929;py='/home/aaltamimi2/.venvs/cosmo-logp/bin/python'
while Path(f'/proc/{pid}').exists():
 assert 'run_octanol_catchup_handoff_20260917.py' in Path(f'/proc/{pid}/cmdline').read_text()
 time.sleep(30)
receipt=json.loads((R/'state/octanol-catchup-worker-handoff-20260917.json').read_text())
assert receipt['batch_exit_code']==0,receipt
for name in ['audit_octanol_catchup_20260917.py','compare_opera_catchup_20260917.py']:
 subprocess.run([py,str(R/'scripts'/name)],check=True)
(R/'state/octanol-catchup-finalized-20260917.json').write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'audited_and_compared','figures_require_visual_review':True},indent=2)+'\n')
