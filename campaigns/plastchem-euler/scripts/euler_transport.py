"""Lane-only multiplexing and persistent connection-failure backoff.
Calls never sleep: they refuse early calls so the operator can keep communicating.
"""
import json,subprocess,time,sys,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'state/ssh-transport.json'
OPTIONS=['-o','ControlMaster=auto','-o',f'ControlPath={ROOT}/state/ssh-%r@%h-%p','-o','ControlPersist=600','-o','BatchMode=yes','-o','ConnectTimeout=30','-o','LogLevel=ERROR']
def _run(kind,args,**kwargs):
 assert kind in ['ssh','scp']
 s=json.loads(STATE.read_text()) if STATE.exists() else {}
 now=time.time()
 if now<s.get('retry_after_epoch',0):raise RuntimeError(f"Backoff active: retry after {s['retry_after_epoch']}, in {s['retry_after_epoch']-now:.0f}s")
 # A live multiplexed socket does not open a new network connection.
 socket=ROOT/'state/ssh-aaltamimi2@euler.engr.wisc.edu-22'
 if not socket.exists() and now<s.get('last_connection_attempt_epoch',0)+120:
  raise RuntimeError('Wait at least 120s before another connection attempt')
 if not socket.exists():s['last_connection_attempt_epoch']=now
 STATE.write_text(json.dumps(s,indent=2)+'\n')
 r=subprocess.run([kind,*OPTIONS,*args],**kwargs)
 if r.returncode!=0:
  n=s.get('consecutive_failures',0)+1;s.update(consecutive_failures=n,retry_after_epoch=time.time()+min(120*2**(n-1),1800))
 elif r.returncode==0:s.update(consecutive_failures=0,retry_after_epoch=0)
 STATE.write_text(json.dumps(s,indent=2)+'\n')
 with (ROOT/'state/ssh-transport-events.jsonl').open('a') as f:
  f.write(json.dumps({'utc_epoch':time.time(),'kind':kind,'returncode':r.returncode,'multiplex_socket':str(socket),'retry_after_epoch':s.get('retry_after_epoch',0)})+'\n')
 return r
def run(kind,args,**kwargs):
 with (ROOT/'state/ssh-transport.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  return _run(kind,args,**kwargs)
if __name__=='__main__':sys.exit(run(sys.argv[1],sys.argv[2:]).returncode)
