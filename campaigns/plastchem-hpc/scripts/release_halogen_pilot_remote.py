"""A-11 pilot: release the staged 40-task cohort (atom-count spread, every halogen, iodine included); the rest stay
held until the local cost refit."""
import json,subprocess,datetime
from pathlib import Path
root=Path.home()/'plastchem-euler/halogen-v1';p=root/'halogen'
def run(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout
receipt_path=p/'submission.json'
if not receipt_path.exists():receipt_path=p/'reconciled-submission.json'
receipt=json.loads(receipt_path.read_text())
assert receipt['returncode']==0
job=receipt['stdout'].strip().split(';')[0];assert job.isdigit()
indices=set(json.loads((p/'submission-indices.json').read_text()))
chosen=json.loads((p/'pilot-indices.json').read_text())
assert len(chosen)==len(set(chosen)) and set(chosen)<=indices
queue=run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T|%P|%C'])
actions=[]
for i in chosen:
    detail=run(['scontrol','show','job',f'{job}_{i}','-o'])
    if 'JobState=PENDING' in detail and 'Reason=JobHeldUser' in detail:
        run(['scontrol','release',f'{job}_{i}']);actions.append(i)
readback=run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T|%P|%C'])
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'array_job_id':job,'selected_indices':chosen,'released_this_call':actions,'queue_before':queue,'queue_after':readback,'shared_cap':64,'pilot_gate':'Remaining tasks held until the pilot refit; release without asking if it projects <= 3,000 CPU-h (A-11)'}
(p/'pilot-release.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
