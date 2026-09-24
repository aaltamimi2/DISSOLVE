"""Wait for the exact live serial worker, then audit and build a separate cumulative comparison."""
import json,time,subprocess,sys,datetime,hashlib,fcntl
from pathlib import Path
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/octanol-final1171-2026-09-17');PID=1350755
lock=(R/'state/octanol-final1171-audit-supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
state=R/'state/octanol-final1171-audit-supervisor.json'
def save(status,**kw):state.write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'worker_pid':PID,'status':status,**kw},indent=2)+'\n')
try:
 p=Path(f'/proc/{PID}');assert p.exists() and b'process_final1171_octanol_independent_water.py' in (p/'cmdline').read_bytes()
 save('waiting_for_live_worker')
 while p.exists():
  cmd=(p/'cmdline').read_bytes()
  if not cmd:break
  assert b'process_final1171_octanol_independent_water.py' in cmd,'PID was reused'
  time.sleep(10)
 s=json.loads((D/'summary.json').read_text());assert s['processed']==s['denominator']==1171 and s['not_run']==0
 resume=json.loads((R/'state/octanol-final1171-independent-water-resume.json').read_text());assert resume['coordinator_pid']==PID and resume['processed']==1171
 save('auditing',summary=s)
 for script in ['audit_octanol_final1171_20260917.py','compare_final5803_20260917.py']:
  with (D/(script+'.log')).open('w') as f:subprocess.run([sys.executable,str(R/'scripts'/script)],stdout=f,stderr=subprocess.STDOUT,check=True)
 save('audit_and_comparison_complete_requires_seal_and_figure_review',summary=s,audit_sha256=hashlib.sha256((D/'numerical-audit.json').read_bytes()).hexdigest())
except Exception as e:
 save('failed',error=repr(e));raise
