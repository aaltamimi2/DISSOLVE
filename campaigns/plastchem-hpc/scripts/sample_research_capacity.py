"""Read-only research capacity evidence; no scheduling changes and no other-partition queries."""
import json,shlex,time
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1]
code=r'''import subprocess,json,time
q=subprocess.run(['squeue','-p','research','-h','-o','%i|%u|%T|%C|%j|%R'],capture_output=True,text=True,check=True).stdout
nodes=subprocess.run(['sinfo','-p','research','-N','-h','-o','%N|%f|%C|%t'],capture_output=True,text=True,check=True).stdout
ours=[];others=[]
for line in q.splitlines():
 a=line.split('|')
 if len(a)<6:continue
 row={'job_id':a[0],'state':a[2],'cpus':int(a[3]),'reason':a[5]}
 (ours if a[1]=='aaltamimi2' and a[4].startswith('contam-') else others).append(row)
pool=[]
for line in nodes.splitlines():
 a=line.split('|')
 if len(a)==4 and 'milan' in a[1].split(',') and 'cpu' in a[1].split(',') and a[0] not in ['euler09','euler10']:
  nums=[int(x) for x in a[2].split('/')];pool.append({'node':a[0],'allocated':nums[0],'idle':nums[1],'other':nums[2],'total':nums[3],'state':a[3]})
print(json.dumps({'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'research_milan_pool':pool,'other_research_running_cpus':sum(r['cpus'] for r in others if r['state']=='RUNNING'),'other_research_pending_records':sum(r['state']=='PENDING' for r in others),'own_campaign_running_tasks':sum(r['state']=='RUNNING' for r in ours),'own_campaign_running_cpus':sum(r['cpus'] for r in ours if r['state']=='RUNNING'),'concurrency_changed':False}))'''
r=run('ssh',['euler','python3 -c '+shlex.quote(code)],capture_output=True,text=True)
if r.returncode:raise RuntimeError(r.stderr)
data=json.loads(r.stdout)
with (ROOT/'state/campaign-v1/research-capacity-history.jsonl').open('a') as f:f.write(json.dumps(data)+'\n')
print(json.dumps(data,indent=2))
