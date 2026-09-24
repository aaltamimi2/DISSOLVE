import datetime as dt,json,os,signal,subprocess,time
from pathlib import Path
R=Path(__file__).resolve().parents[1];pid=825535;py='/home/aaltamimi2/.venvs/cosmo-logp/bin/python'
while Path(f'/proc/{pid}').exists():
 assert 'watch_thermodynamics.py' in Path(f'/proc/{pid}/cmdline').read_text()
 if 'nanosleep' in Path(f'/proc/{pid}/wchan').read_text():os.kill(pid,signal.SIGTERM);break
 time.sleep(2)
while Path(f'/proc/{pid}').exists():time.sleep(1)
try:
 result=subprocess.run([py,'scripts/process_octanol_catchup_20260917.py'],cwd=R,env={**os.environ,'PLASTCHEM_OCTANOL_FILL_ROOT':'/mnt/r/plastchem-euler/octanol-post4172-2026-09-17'})
finally:
 with (R/'logs/thermodynamic-processing.jsonl').open('a') as log:
  p=subprocess.Popen([py,'-u','scripts/watch_thermodynamics.py'],cwd=R,stdout=log,stderr=log,start_new_session=True)
 receipt={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'old_processor_pid':pid,'new_processor_pid':p.pid,'batch_exit_code':result.returncode if 'result' in locals() else None}
 (R/'state/octanol-post4172-worker-handoff-20260917.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt),flush=True)
