"""Read-only 120-second pilot monitor. Never submits, cancels, or retries calculations."""
import collections,json,re,time,datetime
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'state/pilot-v1'
TERMINAL={'COMPLETED','FAILED','TIMEOUT','OUT_OF_MEMORY','CANCELLED','NODE_FAIL','PREEMPTED','BOOT_FAIL','DEADLINE'}
previous=STATE/'latest-snapshot.json'
if previous.exists():
 stamp=json.loads(previous.read_text())['utc']
 last=datetime.datetime.strptime(stamp,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc).timestamp()
 time.sleep(max(0,120-(time.time()-last)))
while True:
 try:
  result=run('ssh',['euler','python3 ~/plastchem-euler/pilot-v1/collect_pilot_remote.py'],capture_output=True,text=True)
  if result.returncode:raise RuntimeError(f'SSH {result.returncode}: {result.stderr[:300]}')
  data=json.loads(result.stdout);tmp=STATE/'snapshot.tmp';tmp.write_text(result.stdout);tmp.replace(STATE/'latest-snapshot.json')
  stages=collections.Counter(r['status'] for r in data['results'].values());tasks={}
  for line in data['sacct'].splitlines():
   parts=line.split('|')
   if re.fullmatch(data['array_job_id']+r'_\d+',parts[0]):tasks[parts[0]]=parts[3].split()[0]
  terminal=sum(s in TERMINAL for s in tasks.values())
  exception_file=STATE/'preflight-exceptions.json'
  exceptions=json.loads(exception_file.read_text()) if exception_file.exists() else {}
  cancelled_without_job_record=sum(str(data['array_job_id'])+'_'+str(r['array_task_id']) not in tasks for r in exceptions.values() if r.get('action')=='cancelled_own_pending_task_before_DFT')
  brief={'utc':data['utc'],'array':data['array_job_id'],'records':dict(stages),'scheduler_terminal':terminal,'denominator':56,'cpu_models':sorted({r.get('cpu_model') for r in data['results'].values() if r.get('cpu_model')})}
  with (STATE/'monitor-history.jsonl').open('a') as f:f.write(json.dumps(brief)+'\n')
  print(json.dumps(brief),flush=True)
  if terminal+cancelled_without_job_record==56 and not data['squeue'].strip():
   print('PILOT_ALL_TASKS_DISPOSED: terminal jobs plus documented preflight cancellation',flush=True);break
 except Exception as exc:print(json.dumps({'monitor_error':str(exc),'utc_epoch':time.time()}),flush=True)
 transport=json.loads((ROOT/'state/ssh-transport.json').read_text())
 delay=max(120,transport.get('retry_after_epoch',0)-time.time())
 time.sleep(delay)
