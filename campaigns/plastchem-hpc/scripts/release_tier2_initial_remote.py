"""Release only the fixed first-30 timing cohort, with Phase 2 priority."""
import json,subprocess,datetime
from pathlib import Path
root=Path.home()/'plastchem-euler/tier2-v1';p=root/'tier2'
def run(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout
receipt_path=p/'submission.json'
if not receipt_path.exists():receipt_path=p/'reconciled-submission.json'
receipt=json.loads(receipt_path.read_text())
assert receipt['returncode']==0
job=receipt['stdout'].strip().split(';')[0];assert job.isdigit()
queue=run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T|%P|%C'])
assert not any(line.split('|')[1].startswith('contam-p2-') for line in queue.splitlines())
manifest=json.loads((p/'manifest.json').read_text())
indices=json.loads((p/'submission-indices.json').read_text())
ordered=sorted(indices,key=lambda i:(manifest['molecules'][i]['atoms'],i))
chosen=[ordered[round(i*(len(ordered)-1)/29)] for i in range(30)]
assert len(set(chosen))==30
selection=p/'first30-indices.json'
if selection.exists():assert json.loads(selection.read_text())==chosen
else:selection.write_text(json.dumps(chosen)+'\n')
actions=[]
for i in chosen:
    detail=run(['scontrol','show','job',f'{job}_{i}','-o'])
    if 'JobState=PENDING' in detail and 'Reason=JobHeldUser' in detail:
        run(['scontrol','release',f'{job}_{i}']);actions.append(i)
readback=run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T|%P|%C'])
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'array_job_id':job,'selected_indices':chosen,'released_this_call':actions,'queue_before':queue,'queue_after':readback,'shared_cap':64,'first30_gate':'Remaining tasks held until first-30 cost refit; report before continuing if projected cost exceeds 3540 CPU-h'}
(p/'initial-release.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
