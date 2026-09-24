"""Queue owner-authorised held arrays with measured time limits and a serial array dependency chain."""
from pathlib import Path
import json,shlex,datetime
from euler_transport import run
R=Path(__file__).resolve().parents[1];fit=json.loads((R/'state/polymer-v1/owner-release-measured-refits.json').read_text())
remote='PLANS='+repr(fit['plans'])+'\n'+r'''
import subprocess,json,datetime
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'actions':[],'released':[]}
def call(a):
 p=subprocess.run(a,capture_output=True,text=True);r={'command':a,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr};out['actions'].append(r);assert p.returncode==0,str(r);return p.stdout
def queue():
 return {r[0]:r for line in call(['squeue','-h','-r','-u','aaltamimi2','-o','%i|%T|%R|%l|%N']).splitlines() if len(r:=line.split('|'))==5}
try:
 before=queue();out['before']=before
 assert sum(r[1]=='RUNNING' for k,r in before.items() if k.startswith(('65676_','65677_','63873_')))<=64
 selected={}
 for kind,job,dep,expected in [('polymer','65677','65676',40),('tier2','63873','65677',236)]:
  chosen=[p for p in PLANS[kind] if (row:=before.get(f"{job}_{p['index']}")) and row[1]=='PENDING' and row[2]=='(JobHeldUser)'];assert len(chosen)==expected,(kind,len(chosen))
  call(['scontrol','update',f'JobId={job}','ArrayTaskThrottle=64'])
  for p in chosen:
   h=p['limit_hours'];limit=f'{h//24}-{h%24:02}:00:00' if h>=24 else f'{h:02}:00:00'
   call(['scontrol','update',f"JobId={job}_{p['index']}",f'TimeLimit={limit}',f'Dependency=afterany:{dep}'])
  selected[kind]=(job,chosen)
 # All dependencies are installed before any hold is removed.
 for kind,(job,chosen) in selected.items():
  for p in chosen:call(['scontrol','release',f"{job}_{p['index']}"]);out['released'].append(f"{job}_{p['index']}")
 out['after']=queue();out['large_detail']=call(['scontrol','show','job','65677','-o']);out['tier2_detail']=call(['scontrol','show','job','63873','-o']);out['completed']=True
except Exception as e:out['completed']=False;out['error']=str(e)
print(json.dumps(out))
'''
p=run('ssh',['aaltamimi2@euler.engr.wisc.edu','python3 -c '+shlex.quote(remote)],capture_output=True,text=True,timeout=180)
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'transport_returncode':p.returncode,'stderr':p.stderr,'remote':json.loads(p.stdout) if p.returncode==0 else {'raw_stdout':p.stdout}}
path=R/'state/polymer-v1/owner-held-array-release.json';path.write_text(json.dumps(r,indent=2)+'\n')
with (R/'logs/campaign-launcher.jsonl').open('a') as f:f.write(json.dumps({'utc':r['utc'],'event':'owner_held_arrays_queued','receipt':str(path),'completed':r['remote'].get('completed',False)})+'\n')
print(json.dumps({'completed':r['remote'].get('completed',False),'released_tasks':len(r['remote'].get('released',[])),'error':r['remote'].get('error'),'receipt':str(path)}))
