"""Sequential handoffs of explicitly reviewed candidates; stop on ambiguity."""
import datetime
import json
import subprocess
import sys
from pathlib import Path

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')

def main(chunks):
    assert chunks==[54,55,56,57,43],chunks
    record=D/'selected-tail-launches.json'
    assert not record.exists(),'Reconcile the existing batch; never restart blindly'
    rows=[]
    def save():
        record.write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),rows=rows),indent=2)+'\n')
    for chunk in chunks:
        assert not (D/f'tail-{chunk:04d}-v1/launch-intent.json').exists()
        observation=json.loads((D/'production-observation.json').read_text())
        age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(observation['utc'])).total_seconds()
        assert 0<=age<900 and not observation['failures']
        row=dict(chunk=chunk,observation_utc=observation['utc'])
        rows.append(row)
        if chunk not in {c['chunk'] for c in observation['potential_stragglers']}:
            row['status']='no_longer_current_candidate_not_launched';save();print(json.dumps(row),flush=True);continue
        # Preserve the exact reviewed observation if the live observer advances.
        snapshot=D/f'selected-tail-{chunk:04d}-observation.json'
        assert not snapshot.exists()
        snapshot.write_text(json.dumps(observation,indent=2)+'\n')
        row.update(status='launch_in_progress',observation_path=str(snapshot));save()
        command=[sys.executable,'-u',str(R/'scripts/launch_phase10_tail.py'),str(chunk),str(snapshot)]
        with (D/f'selected-tail-{chunk:04d}-launch.log').open('x') as log:
            result=subprocess.run(command,cwd=R,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        row['returncode']=result.returncode
        if result.returncode:
            row['status']='inspection_required_no_retry';save();print(json.dumps(row),flush=True);return 1
        receipt=json.loads((D/f'production-retry-tail{chunk:04d}-submission.json').read_text())
        row.update(status='confirmed_submission',job_id=receipt['job_id']);save();print(json.dumps(row),flush=True)
    return 0

if __name__=='__main__':sys.exit(main([int(s) for s in sys.argv[1:]]))
