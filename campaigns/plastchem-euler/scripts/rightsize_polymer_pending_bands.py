"""Owner-directed pending-only time limits; temporary body holds prevent start races."""
import datetime,json,shlex,hashlib
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];P=R/'state/polymer-v1'
limits={38:3,44:3,48:3,62:5,84:9,88:10,91:11,98:12,104:14,155:30,158:30,167:30}
plan=[]
for group,array in [('body','65676'),('large','65677')]:
 for m in json.loads((P/group/'manifest.json').read_text())['molecules']:
  plan.append({'task':f"{array}_{m['array_index']}",'group':group,'index':m['array_index'],'atoms':m['atoms'],'hours':limits[m['atoms']]})
remote='PLAN='+repr(plan)+'\n'+r'''
import json,subprocess,datetime
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'actions':[],'updated':[],'skipped':[]}
def cmd(args):
 p=subprocess.run(args,text=True,capture_output=True);r={'args':args,'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr};out['actions'].append(r);return r
def queue():
 r=cmd(['squeue','-h','-r','-u','aaltamimi2','-o','%i|%T|%R|%l|%N']);assert r['code']==0
 return {v[0]:v for line in r['stdout'].splitlines() if len(v:=line.split('|'))==5}
try:
 before=queue();out['before']=before
 for m in PLAN:
  task=m['task'];row=before.get(task)
  if not row or row[1]!='PENDING':out['skipped'].append(task);continue
  # Body holds are temporary; large jobs remain on their pre-existing owner hold.
  temporary=m['group']=='body' and row[2]!='(JobHeldUser)'
  if temporary:
   h=cmd(['scontrol','hold',task])
   if h['code']!=0:out['skipped'].append(task);continue
  detail=cmd(['scontrol','show','job',task,'-o'])
  if detail['code'] or 'JobState=PENDING' not in detail['stdout']:
   if temporary:cmd(['scontrol','release',task])
   out['skipped'].append(task);continue
  assert 'Reason=JobHeldUser' in detail['stdout'], 'Expected held pending task before update'
  hours=m['hours'];value=f'{hours//24}-{hours%24:02}:00:00' if hours>=24 else f'{hours:02}:00:00'
  update=cmd(['scontrol','update',f'JobId={task}',f'TimeLimit={value}'])
  if temporary:
   release=cmd(['scontrol','release',task]);assert release['code']==0,'Temporary hold release failed'
  assert update['code']==0,'Time update failed'
  out['updated'].append(m)
 out['after']=queue();out['completed']=True
except Exception as e:out['error']=str(e);out['completed']=False
print(json.dumps(out))
'''
r=run('ssh',['aaltamimi2@euler.engr.wisc.edu','python3 -c '+shlex.quote(remote)],capture_output=True,text=True,timeout=180)
d={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'returncode':r.returncode,'stderr':r.stderr,'plan':plan,'remote':json.loads(r.stdout) if r.returncode==0 else {'raw_stdout':r.stdout}}
p=P/'pending-band-walltime-update.json';p.write_text(json.dumps(d,indent=2)+'\n')
with (R/'logs/campaign-launcher.jsonl').open('a') as f:f.write(json.dumps({'utc':d['utc'],'event':'pending_polymer_band_limits','receipt':str(p),'completed':d['remote'].get('completed',False)})+'\n')
print(json.dumps({'completed':d['remote'].get('completed',False),'updated':len(d['remote'].get('updated',[])),'error':d['remote'].get('error'),'receipt':str(p)}))
