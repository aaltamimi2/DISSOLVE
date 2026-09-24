"""Owner override: immediate PC eligibility, shared cap, remaining tasks queued."""
import json,shlex,datetime
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];models=json.loads((R/'state/polymer-v1/large/manifest.json').read_text())['molecules'];assert [m['array_index'] for m in models if m['polymer']=='pc']==list(range(14,33));assert all(m['polymer']=='polyurethane' for m in models[:14]);assert all(m['polymer']=='petg' for m in models[33:])
remote=r'''
import subprocess,json,datetime,re
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'actions':[]}
def call(a):
 p=subprocess.run(a,capture_output=True,text=True);out['actions'].append({'command':a,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr});assert p.returncode==0,(a,p.stderr);return p.stdout
def queue():return {v[0]:v for line in call(['squeue','-h','-r','-u','aaltamimi2','-o','%i|%T|%R|%S|%N']).splitlines() if len(v:=line.split('|'))==5}
try:
 before=queue();out['before']=before
 body=[r for k,r in before.items() if k.startswith('65676_')];assert all(r[1]=='RUNNING' for r in body)
 n=len(body);quota=64-n;assert quota>=19
 # Body has no pending tasks, so its running count can only decrease.
 call(['scontrol','update','JobId=65677',f'ArrayTaskThrottle={quota}'])
 call(['scontrol','update','JobId=63873',f'ArrayTaskThrottle={quota}'])
 for i in range(40):call(['scontrol','hold',f'65677_{i}'])
 detail=call(['scontrol','show','job','65677','-o']);pcids={}
 for line in detail.splitlines():
  match=re.search(r'ArrayTaskId=(\d+)',line)
  if match and 14<=int(match[1])<=32:pcids[int(match[1])]=re.search(r'\bJobId=(\d+)',line)[1]
 assert len(pcids)==19
 for i in range(14,33):
  call(['scontrol','update',f'JobId=65677_{i}','Dependency='])
  call(['scontrol','release',f'65677_{i}'])
 out['after_pc_release']=queue()
 dependency='after:'+':'.join(pcids[i] for i in range(14,33))
 for i in [*range(14),*range(33,40)]:
  call(['scontrol','update',f'JobId=65677_{i}',f'Dependency={dependency}'])
  call(['scontrol','release',f'65677_{i}'])
 tier=[k for k,v in before.items() if k.startswith('63873_') and v[1]=='PENDING'];assert len(tier)==236
 for task in tier:
  call(['scontrol','update',f'JobId={task}','Dependency=afterany:65677'])
  call(['scontrol','release',task])
 out['after']=queue();out['body_reserved_slots']=n;out['large_and_tier_throttle']=quota;out['remaining_large_dependency']=dependency;out['completed']=True
except Exception as e:out['completed']=False;out['error']=str(e)
print(json.dumps(out))
'''
p=run('ssh',['aaltamimi2@euler.engr.wisc.edu','python3 -c '+shlex.quote(remote)],capture_output=True,text=True,timeout=180);d={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'transport_returncode':p.returncode,'stderr':p.stderr,'remote':json.loads(p.stdout) if p.returncode==0 else {'stdout':p.stdout}};f=R/'state/polymer-v1/pc-priority-release.json';f.write_text(json.dumps(d,indent=2)+'\n')
with (R/'logs/campaign-launcher.jsonl').open('a') as h:h.write(json.dumps({'utc':d['utc'],'event':'PC_priority_owner_override','receipt':str(f),'completed':d['remote'].get('completed',False)})+'\n')
print(json.dumps({k:v for k,v in d['remote'].items() if k not in ['actions','before','after','after_pc_release']}))
