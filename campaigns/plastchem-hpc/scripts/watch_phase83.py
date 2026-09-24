"""Serial monitor/verified collector and gated release builder for authorized 8.3/8.4.

Does not submit, resubmit, change throttles, or touch previous collectors.
"""
import collections,datetime,fcntl,gzip,json,os,subprocess,sys,time,traceback
from pathlib import Path
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase83-v1');S=R/'state/phase83-v1'
def save(p,v):
 t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,indent=2)+'\n');t.replace(p)
def call(script,*args):
 p=subprocess.run([sys.executable,str(R/'scripts'/script),*args],capture_output=True,text=True)
 if p.returncode:raise RuntimeError(script+': '+p.stderr[-5000:]+p.stdout[-1000:])
 return p.stdout
def main():
 lock=(S/'watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert json.loads((D/'cost-gate.json').read_text())['decision']=='continue'
 assert (S/'production-submission.json').exists(),'No production submission recorded'
 save(S/'watch-process.json',{'pid':os.getpid(),'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'script':str(Path(__file__))})
 while True:
  started=time.monotonic();building_release=False
  try:
   available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
   if available<2.5*1024**3:raise MemoryError('Local processing paused: available RAM under 2.5 GiB')
   call('status_phase83.py');snapshot=json.loads((D/'latest-status.json').read_text())
   call('collect_phase83.py','--limit','20')
   registry=json.loads((S/'collection.json').read_text());partition=collections.Counter();lle=collections.Counter();updated=False
   for key,entry in registry['collected'].items():
    if 'partition_status_counts' not in entry:
     with gzip.open(D/'collected'/(key+'.json.gz'),'rt') as f:data=json.load(f)
     entry['partition_status_counts']=dict(collections.Counter(r['status'] for r in data['partition']));entry['lle_status_counts']=dict(collections.Counter(r['status'] for r in data['lle'].values()));updated=True
    partition.update(entry['partition_status_counts']);lle.update(entry['lle_status_counts'])
   if updated:save(S/'collection.json',registry)
   cpu_seconds=0;interruptions=[]
   for line in snapshot['accounting'].splitlines():
    fields=line.split('|');job=fields[0]
    if '.' in job or '_' not in job or not job.split('_')[-1].isdigit():continue
    cpu_seconds+=int(fields[2])*int(fields[3])
    if fields[1].split()[0] in ['TIMEOUT','OUT_OF_MEMORY','FAILED','NODE_FAIL','CANCELLED','PREEMPTED']:interruptions.append({'job':job,'state':fields[1]})
   status={'utc':snapshot['utc'],'complete_contaminants':len(snapshot['completed']),'collected_contaminants':len(registry['collected']),
    'denominator':5830,'main_denominator':5803,'tier2_denominator':27,'running_cpus_shared_research':snapshot['running_cpus'],
    'queue_counts':snapshot['queue_counts'],'allocated_cpu_hours_to_date':cpu_seconds/3600,'collected_partition_statuses':dict(partition),
    'collected_lle_statuses':dict(lle),'scheduler_interruptions':interruptions,'task_failures':snapshot['task_failures']}
   if snapshot['running_cpus']>64:status['alert']='Observed shared research running count exceeds 64; operator reconciliation required'
   if interruptions:status['alert']='Scheduler interruptions require reconciled recovery; TIMEOUT is not a chemistry failure'
   save(D/'live-progress.json',status);save(S/'watch-status.json',status);print(json.dumps(status),flush=True)
   report=R/'reports/phase83-2026-09-23/REPORT.md';text=report.read_text().split('\n## Live production\n')[0]
   text+='\n## Live production\n\n'+f"Snapshot {status['utc']}: {status['complete_contaminants']:,}/5,830 contaminant tasks complete; {status['collected_contaminants']:,} collected with per-file digest verification. Shared research CPUs running: {status['running_cpus_shared_research']}/64. Phase8.3 allocated CPU-hours to date: {status['allocated_cpu_hours_to_date']:.2f}. Collected partition statuses: {dict(partition)}. Collected LLE statuses: {dict(lle)}.\n\n"
   text+='Interruptions are tracked separately from chemical/numerical failures. The watcher never submits or retries jobs and never changes throttles. Exact live state: `/mnt/r/plastchem-euler/phase83-v1/live-progress.json`.\n'
   report.write_text(text);(D/'REPORT.md').write_text(text)
   if len(registry['collected'])==5830:
    assert not interruptions and not snapshot['task_failures'],'Resolve execution interruptions before release'
    building_release=True
    print(call('build_phase84_release.py'),flush=True)
    save(S/'watch-complete.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'release_seal':json.loads((D/'release-seal.json').read_text())})
    return
  except Exception as e:
   error={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'error':str(e),'traceback':traceback.format_exc()};save(S/'watch-error.json',error);print(json.dumps(error),flush=True)
   if building_release:return
  # Short sleeps allow fast intervention; one serial processing pass every five minutes.
  while time.monotonic()-started<300:time.sleep(min(55,300-(time.monotonic()-started)))
if __name__=='__main__':main()
