"""Bounded I/O-only diagnostic in an existing production allocation.

Writes only disposable files beneath our Euler home. No COSMO calculation,
worker patch, job submission, cancellation, or concurrency change.
"""
import json
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1]
probe=r'''import pathlib,tempfile,os,time,json,hashlib,statistics
root=pathlib.Path.home()/'plastchem-euler/phase83-v1'
payload=json.dumps({'activities':{str(i/4096):[i/10000,-i/10000] for i in range(2087)}},indent=2).encode()
observations=[]
with tempfile.TemporaryDirectory(prefix='io-only-probe-',dir=root) as folder:
 p=pathlib.Path(folder)/'checkpoint.json'
 for i in range(5):
  for sync in ([True,False] if i%2==0 else [False,True]):
   start=time.monotonic();cpu=time.process_time();tmp=p.with_suffix('.tmp')
   with tmp.open('wb') as f:
    f.write(payload);f.flush()
    if sync:os.fsync(f.fileno())
   tmp.replace(p)
   elapsed=time.monotonic()-start;used=time.process_time()-cpu
   assert hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256(payload).digest()
   observations.append({'fsync':sync,'wall_seconds':elapsed,'process_cpu_seconds':used})
 # Force pending buffered writes before cleanup; record that deferred cost too.
 start=time.monotonic()
 with p.open('rb') as f:os.fsync(f.fileno())
 deferred=time.monotonic()-start
print(json.dumps({'bytes_per_checkpoint':len(payload),'observations':observations,'final_flush_seconds':deferred,
 'median_fsync_seconds':statistics.median(r['wall_seconds'] for r in observations if r['fsync']),
 'median_buffered_seconds':statistics.median(r['wall_seconds'] for r in observations if not r['fsync'])}))
'''
remote=r'''import subprocess,json,re,datetime
q=subprocess.check_output(['squeue','-h','-r','-j','68234','--states=RUNNING','-o','%i'],text=True)
tasks=[x for x in q.splitlines() if re.fullmatch(r'68234_\d+',x)];assert tasks,'No live production allocation; no new job submitted'
task=min(tasks,key=lambda x:int(x.split('_')[1]));detail=subprocess.check_output(['scontrol','show','job',task,'-o'],text=True)
assert 'JobName=contam-phase83-production-v1 ' in detail and 'JobState=RUNNING ' in detail
job=re.search(r'\bJobId=(\d+)',detail).group(1)
args=['srun','--jobid='+job,'--overlap','--ntasks=1','--cpus-per-task=1','--immediate=10','--time=00:01:00','python3','-c',PROBE]
p=subprocess.run(args,capture_output=True,text=True,timeout=50)
print(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'existing_task':task,'job_id':job,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'command':args}))
'''.replace('PROBE',repr(probe))
if __name__=='__main__':
 r=run('ssh',['euler','python3 -'],input=remote,capture_output=True,text=True,timeout=90)
 assert r.returncode==0,r.stderr
 result=json.loads(r.stdout)
 (R/'state/phase83-v1/checkpoint-io-benchmark.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='command'},indent=2))
