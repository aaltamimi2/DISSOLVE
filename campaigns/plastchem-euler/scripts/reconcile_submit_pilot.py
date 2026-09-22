"""Execute on Euler. Reconcile deterministically before any pilot submission."""
import json,subprocess,time
from pathlib import Path
root=Path.home()/'plastchem-euler/pilot-v1';name='contam-p1-milan-v1'
queue=subprocess.run(['squeue','-u','aaltamimi2','--name='+name,'-h','-o','%i|%j|%T|%N'],capture_output=True,text=True,check=True).stdout
accounting=subprocess.run(['sacct','-u','aaltamimi2','--starttime=today','--name='+name,'--format=JobID,JobName%40,State,Elapsed,NodeList','-nP'],capture_output=True,text=True,check=True).stdout
record={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'name':name,'squeue':queue,'sacct':accounting}
(root/'reconciliation.json').write_text(json.dumps(record,indent=2)+'\n')
if queue.strip() or accounting.strip():
 print(json.dumps(dict(record,decision='existing_job_found_do_not_submit'),indent=2));raise SystemExit(0)
if (root/'submission.started').exists():
 print(json.dumps(dict(record,decision='prior_submission_attempt_marker_requires_review_no_resubmit'),indent=2));raise SystemExit(2)
# A failure between sbatch and receipt leaves this marker, forcing explicit reconciliation.
with (root/'submission.started').open('x') as f:f.write(record['utc']+'\n')
r=subprocess.run(['sbatch','--parsable','--chdir='+str(root),'--output='+str(root/'logs/%A_%a.out'),'--error='+str(root/'logs/%A_%a.err'),str(root/'pilot.sbatch')],capture_output=True,text=True)
record.update(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr,decision='submitted' if r.returncode==0 else 'submission_failed_or_unconfirmed')
(root/'submission.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2));raise SystemExit(r.returncode)
