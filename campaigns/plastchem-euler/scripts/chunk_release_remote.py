"""Owner-authorized overlap of main arrays; never submits jobs or touches the tail."""
import subprocess,json,datetime,re,pathlib
IDS=['54755','54786','54787','54789','54795','54796','54797','54798','54799','54800','54801','54802'];TAIL='54733'
CAP=64  # Owner authorised 2026-09-16; 1 CPU and 4G per task unchanged.
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cap':CAP,'tail_reserved':4,'operations':[],'changes':[]}
def cmd(args):
 r=subprocess.run(args,capture_output=True,text=True);out['operations'].append({'command':args,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr});assert r.returncode==0,(args,r.stderr);return r.stdout

def queue():
 raw=cmd(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C|%r']);return [x.split('|') for x in raw.splitlines() if x.strip()]
def detail(j):
 text=cmd(['scontrol','show','job',j,'-o']);assert f'JobName=contam-p2-main-c{IDS.index(j):03d}-v1 ' in text and 'Partition=research ' in text
 caps=re.findall(r'ArrayTaskThrottle=(\d+)',text);assert caps and len(set(caps))==1
 return text,int(caps[0])
def change(j,field,value):
 assert j in IDS
 args=['scontrol','update',f'JobId={j}',f'{field}={value}']
 r=subprocess.run(args,capture_output=True,text=True)
 out['operations'].append({'command':args,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
 # Slurm may apply the array throttle yet return nonzero for terminal elements.
 # Accept only this precise case, and only after the requested throttle reads back.
 terminal_race=(field=='ArrayTaskThrottle' and bool(r.stderr.strip()) and all(line.endswith(': Job has already finished') for line in r.stderr.strip().splitlines()))
 assert r.returncode==0 or terminal_race,(args,r.stderr)
 text,cap=detail(j)
 if field=='ArrayTaskThrottle':assert cap==int(value)
 if field=='Dependency':
  if not value:assert 'Dependency=(null)' in text
  else:
   dep=re.search(r'Dependency=(\S+)',text).group(1);assert all(x in dep for x in value.split(':')[1:])
 out['changes'].append({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'job_id':j,'field':field,'value':value,'readback':text})
 # Persist each confirmed mutation on Euler before returning over SSH.
 (pathlib.Path.home()/'plastchem-euler/campaign-v1/chunk-release-latest.json').write_text(json.dumps(out,indent=2)+'\n')
try:
 rows=queue();by={j:[r for r in rows if r[0].split('_')[0]==j] for j in IDS};running=[r for r in rows if r[2]=='RUNNING'];out['running_before']={j:sum(r[0].split('_')[0]==j for r in running) for j in [*IDS,TAIL]};out['running_total_before']=sum(int(r[3]) for r in running);assert out['running_total_before']<=CAP,'Running cap exceeded before controller; no increase permitted'
 other=[r for r in rows if r[0].split('_')[0] not in IDS+[TAIL]];assert not any(r[2]=='PENDING' for r in other),'Unbudgeted pending research jobs'
 # The tail has --no-requeue and no automatic retries. Its remaining tasks,
 # including pending/transitioning tasks, bound its future concurrency by four.
 # Reclaim reservations only as tasks disappear; never modify the tail array.
 tail_rows=[r for r in rows if r[0].split('_')[0]==TAIL]
 assert all(int(r[3])==1 for r in tail_rows),'Unexpected multi-CPU tail task'
 tail_reserve=min(4,len(tail_rows))
 reserve=tail_reserve+sum(int(r[3]) for r in other if r[2]!='PENDING')
 out['tail_reserved']=tail_reserve;out['tail_tasks_remaining']=len(tail_rows)
 out['other_research_reserved']=reserve-tail_reserve;budget=CAP-reserve
 active=[];blocked=[]
 for j in IDS:
  if not by[j]:continue
  text,cap=detail(j)
  if 'Dependency=(null)' in text:active.append((j,cap))
  else:blocked.append(j)
 # When an array has fewer remaining tasks than its cap, its remaining task count is
 # a safe upper bound even if all pending tasks start during this transaction.
 desired={j:min(cap,max(1,len(by[j]))) for j,cap in active};used=sum(desired.values())
 # Give freed slots back to the latest active body before opening another array.
 if active and used<budget:
  latest=active[-1][0];target=min(len(by[latest]),desired[latest]+budget-used)
  if target>desired[latest]:
   for a,cap in active:
    if a!=latest and desired[a]!=cap:change(a,'ArrayTaskThrottle',str(desired[a]))
   change(latest,'ArrayTaskThrottle',str(target));desired[latest]=target;used=sum(desired.values())
 out['main_cap_budget']=budget;out['main_remaining_bounds']=desired
 if used<=budget and blocked and budget-used>0:
  j=blocked[0];assert all(IDS.index(a)<IDS.index(j) for a,_ in active)
  for a,cap in active:
   if desired[a]!=cap:change(a,'ArrayTaskThrottle',str(desired[a]))
  # Guard the successor before clearing this dependency. It must not auto-start at
  # its stored throttle while any older overlapping array still has a long task running.
  ix=IDS.index(j)
  if ix+1<len(IDS) and by[IDS[ix+1]]:
   guard='afterany:'+':'.join([a for a,_ in active]+[j]);change(IDS[ix+1],'Dependency',guard)
  change(j,'ArrayTaskThrottle',str(budget-used));change(j,'Dependency','');out['released']=j
 else:out['released']=None
 after=queue();out['running_after']={j:sum(r[0].split('_')[0]==j and r[2]=='RUNNING' for r in after) for j in [*IDS,TAIL]};out['running_total_after']=sum(int(r[3]) for r in after if r[2]=='RUNNING');assert out['running_total_after']<=CAP
 out['status']='verified'
except Exception as e:out['status']='failed';out['error']=repr(e)
print(json.dumps(out),flush=True)
