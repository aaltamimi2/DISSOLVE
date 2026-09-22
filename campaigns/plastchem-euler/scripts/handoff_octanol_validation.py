"""Transfer the one numerical-worker slot at an idle boundary, then restore production."""
import datetime as dt,json,os,signal,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/progress-2026-09-14';PYTHON='/home/aaltamimi2/.venvs/cosmo-logp/bin/python';pid=2516052
proc=Path('/proc')/str(pid)
assert b'scripts/watch_thermodynamics.py' in (proc/'cmdline').read_bytes().split(b'\0')
assert (proc/'wchan').read_text().strip()=='hrtimer_nanosleep','Worker is not idle'
children=(proc/'task'/str(pid)/'children').read_text().strip();assert not children,'Worker has an active child'
receipt={'started_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'previous_worker_pid':pid,'idle_evidence':'hrtimer_nanosleep; no child processes','validation_status':'starting'}
def save():(P/'octanol-worker-handoff.json').write_text(json.dumps(receipt,indent=2)+'\n')
save();os.kill(pid,signal.SIGTERM)
for _ in range(100):
 if not proc.exists():break
 time.sleep(.05)
assert not proc.exists(),'Old worker still present; no second solver launched'
try:
 with (ROOT/'logs/octanol-validation.log').open('a') as log:
  child=subprocess.Popen([PYTHON,'-u','scripts/process_octanol_validation.py'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
  receipt.update(validation_pid=child.pid,validation_status='running');save();code=child.wait()
  receipt.update(validation_returncode=code,validation_status='finished' if code==0 else 'failed');save()
finally:
 with (ROOT/'logs/thermodynamic-processing.jsonl').open('a') as log:
  worker=subprocess.Popen([PYTHON,'-u','scripts/watch_thermodynamics.py'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 receipt.update(restored_worker_pid=worker.pid,restored_utc=dt.datetime.now(dt.timezone.utc).isoformat());save()
print(json.dumps(receipt),flush=True)
