"""Owner-authorized allocation under the shared cap; no submissions or cancellations.

Run through euler_transport. A tier-2 transition is guarded before reclaiming its
reservation: lowering an array throttle does not stop already-running tasks.
"""
import collections,datetime,fcntl,json,pathlib,re,subprocess
D=pathlib.Path.home()/'plastchem-euler/phase83-v1'
# The existing cadence remains live. Once A-9 has a reconciled submission
# receipt, its allocator owns the same lock and reserves the solvent side task.
a9=D.parent/'phase9-v1'
if (a9/'production-submission.json').exists():
 controller=a9/'phase9_throttle_remote.py'
 activation=a9/'active-throttle-controller.json'
 if activation.exists():
  import hashlib
  selected=json.loads(activation.read_text())
  assert selected['filename'] in ['phase9_throttle_remote_v2.py','phase9_throttle_remote_v3.py','phase9_throttle_remote_v4.py','phase9_throttle_remote_v5.py','phase9_throttle_remote_v6.py']
  controller=a9/selected['filename']
  assert hashlib.sha256(controller.read_bytes()).hexdigest()==selected['sha256']
 exec(compile(controller.read_text(),str(controller),'exec'))
 raise SystemExit(0)
lock=(D/'throttle.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
CAP=64;PROD='68234';TIER='63873';LARGE='65677'
NAMES={PROD:'contam-phase83-production-v1',TIER:'contam-tier2-chno500700-v1',LARGE:'contam-polymer24a-large-v1'}
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cap':CAP,'changes':[],'operations':[]}
def cmd(args):
 p=subprocess.run(args,capture_output=True,text=True)
 out['operations'].append({'command':args,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
 assert p.returncode==0,(args,p.stderr)
 return p.stdout
def queue():
 return [l.split('|') for l in cmd(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%T|%C|%j']).splitlines() if l]
def detail(j):
 raw=cmd(['scontrol','show','job',j,'-o'])
 rows=[dict(re.findall(r'(\S+?)=(\S+)',line)) for line in raw.splitlines() if line.strip()]
 rows=[r for r in rows if r['JobState'] not in ['COMPLETED','CANCELLED','FAILED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY']]
 assert rows and all(r['JobName']==NAMES[j] and r['Partition']=='research' for r in rows)
 caps={int(r['ArrayTaskThrottle']) for r in rows};assert len(caps)==1
 return rows,caps.pop()
def change(j,old,new,reason):
 if old==new:return
 args=['scontrol','update',f'JobId={j}',f'ArrayTaskThrottle={new}']
 p=subprocess.run(args,capture_output=True,text=True)
 out['operations'].append({'command':args,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
 terminal=bool(p.stderr.strip()) and all(s.endswith(': Job has already finished') for s in p.stderr.strip().splitlines())
 assert p.returncode==0 or terminal,(args,p.stderr)
 rows,actual=detail(j);assert actual==new
 event={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'job_id':j,'from':old,'to':new,'reason':reason,'readback_throttle':actual,'command':args}
 with (D/'throttle-changes.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
 out['changes'].append(event)
def main():
 rows=queue();by={j:[r for r in rows if r[0].split('_')[0]==j] for j in NAMES}
 # A-9 explicitly authorizes diagnostic/gate and common-solvent jobs alongside
 # held 8.3 production. Reserve their entire possible concurrency, not merely
 # their current running count, before restoring any tier-2 capacity.
 external=[r for r in rows if r[0].split('_')[0] not in NAMES]
 allowed={'contam-phase9-sweep-v1','contam-phase9-grid-probe-v1','contam-phase9-chunk-probe-v1','contam-phase9-dilution-probe-v1','contam-phase9-gate-v1','contam-phase9-genoa-gate-v1','contam-phase9-genoa-gate-v2','contam-phase9-common-solvents-v1'}
 assert all(r[3] in allowed for r in external),'Unexpected research jobs: reconcile before changing allocation'
 external_bound=0
 for job in {r[0].split('_')[0] for r in external}:
  group=[r for r in external if r[0].split('_')[0]==job]
  raw=cmd(['scontrol','show','job',group[0][0],'-o'])
  caps=re.findall(r'ArrayTaskThrottle=(\d+)',raw)
  external_bound+=max(sum(r[1]!='PENDING' for r in group),min(len(group),int(caps[0]))) if caps else len(group)
 out['a9_reserved_slots']=external_bound
 # Fail closed for an unexpected campaign; never borrow against its pending jobs.
 assert all(int(r[2])==1 for r in rows),'Unexpected multi-CPU task'
 running=lambda j:sum(r[1]!='PENDING' for r in by[j])
 out['running_before']={j:running(j) for j in NAMES};assert sum(out['running_before'].values())+sum(r[1]!='PENDING' for r in external)<=CAP
 hold=D/'cost-hold.json'
 if hold.exists() and json.loads(hold.read_text()).get('active'):
  # The numerical cost gate can hold new production starts without cancelling
  # existing tasks. Restore the earlier tier-2 reservation as production drains.
  if by[TIER]:
   tr,tc=detail(TIER)
   budget=CAP-running(PROD)-len(by[LARGE])-external_bound
   assert budget>=running(TIER) and budget>=1
   change(TIER,tc,min(37,budget),'Cost gate holds production starts; safely restore tier-2 capacity as existing production tasks drain')
  out['status']='cost_gate_held';out['running_after']={j:running(j) for j in NAMES};return
 assert not external,'A-9 work requires original production to remain held'
 if not by[PROD]:
  if by[TIER]:
   tr,tc=detail(TIER);change(TIER,tc,37,'Production drained; restore prior tier-2 allowance')
  out['status']='production_drained';return
 pr,pc=detail(PROD);assert all(r['Dependency']=='(null)' for r in pr)
 if by[LARGE]:
  lr,lc=detail(LARGE)
  # Count every surviving large task, including pending/transitioning, as reserved.
  large_bound=len(by[LARGE]);assert large_bound<=6
  if by[TIER]:
   tr,tc=detail(TIER)
   assert all(r['JobState']=='PENDING' and 'afterany:65677_' in r['Dependency'] and '(unfulfilled)' in r['Dependency'] for r in tr),'Tier 2 no longer dependency-blocked; retry transition with a fresh queue'
   change(TIER,tc,max(1,large_bound),'Guard automatic tier-2 start before lending its unused reservation to production')
  target=CAP-max(large_bound,1 if by[TIER] else 0)
  change(PROD,pc,target,'Owner-authorized use of all slots not reserved by the remaining polymer tasks; tier-2 dependency transition guarded')
 else:
  # Restore tier 2 progressively: shrink production first, then allow only slots
  # that are truly free even while older production tasks drain above the new cap.
  tier_bound=min(37,len(by[TIER])) if by[TIER] else 0
  target=CAP-tier_bound
  change(PROD,pc,target,'Polymer array drained; return up to 37 slots to tier 2 without cancelling production tasks')
  if by[TIER]:
   tr,tc=detail(TIER)
   budget=CAP-max(running(PROD),target)
   assert budget>=running(TIER) and budget>=1
   change(TIER,tc,min(tier_bound,budget),'Restore tier-2 slots only as running production tasks drain; combined possible running count stays at most 64')
 after=queue();out['running_after']=dict(collections.Counter(r[0].split('_')[0] for r in after if r[1]!='PENDING'))
 assert sum(out['running_after'].values())<=CAP
 out['status']='verified'
try:main()
except Exception as e:out['status']='failed';out['error']=repr(e)
(D/'throttle-latest.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
