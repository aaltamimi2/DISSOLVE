"""Periodic read-only evidence for the owner's concurrency condition; never raises a cap."""
import fcntl,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
lock=(ROOT/'state/campaign-v1/capacity-monitor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
while True:
 try:
  r=subprocess.run(['python3',str(ROOT/'scripts/sample_research_capacity.py')],capture_output=True,text=True)
  if r.returncode:print(json.dumps({'capacity_error':r.stderr[:500]}),flush=True)
  else:print(r.stdout,flush=True)
 except Exception as exc:print(json.dumps({'capacity_error':str(exc)}),flush=True)
 time.sleep(1800)
