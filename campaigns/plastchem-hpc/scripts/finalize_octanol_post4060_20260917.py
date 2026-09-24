from pathlib import Path
import json,time,subprocess
pid=820810
while Path(f'/proc/{pid}').exists():
 assert 'run_octanol_post4060_handoff_20260917.py' in Path(f'/proc/{pid}/cmdline').read_text()
 time.sleep(30)
r=json.loads(Path('state/octanol-post4060-worker-handoff-20260917.json').read_text());assert r['batch_exit_code']==0,r
subprocess.run(['/home/aaltamimi2/.venvs/cosmo-logp/bin/python','scripts/audit_octanol_post4060_20260917.py'],check=True)
