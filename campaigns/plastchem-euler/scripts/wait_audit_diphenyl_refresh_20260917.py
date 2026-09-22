from pathlib import Path
import json,time,subprocess
root=Path(__file__).resolve().parents[1]
while True:
 boundary=root/'state/octanol-post4455-export-boundary-20260917.json'
 handoff=root/'state/octanol-post4455-worker-handoff-20260917.json'
 ready=handoff.exists()
 if boundary.exists():
  ready=ready or json.loads(boundary.read_text()).get('status')=='export_terminal_parent_stopped_existing_handoff_can_continue'
 if ready:break
 assert Path('/proc/1141100').exists() or Path('/proc/1141101').exists(), 'Handoff monitors ended without completed-pass evidence'
 time.sleep(5)
subprocess.run(['python3',str(root/'scripts/audit_diphenyl_refresh_20260917.py')],cwd=root,check=True)
