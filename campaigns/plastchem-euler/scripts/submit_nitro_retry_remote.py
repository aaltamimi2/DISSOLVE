"""Reconcile both scheduler views before isolated last-priority retry submission."""
import json,subprocess,re,datetime
from pathlib import Path
R=Path.home()/'plastchem-euler/polymer-v1/nitro-retry1';m=json.loads((R/'body/manifest.json').read_text());name=m['name']
def run(a):return subprocess.check_output(a,text=True)
q=run(['squeue','-u','aaltamimi2','--name='+name,'-h','-r','-o','%i|%j|%T'])
a=run(['sacct','-u','aaltamimi2','--starttime=2026-09-22','--name='+name,'-nP','--format=JobID,JobName%40,State,Elapsed,NodeList'])
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'squeue':q,'sacct':a,'name':name};(R/'reconciliation.json').write_text(json.dumps(r,indent=2)+'\n');ids={re.match(r'\d+',l).group() for l in (q+'\n'+a).splitlines() if re.match(r'\d+',l)}
if ids:
 assert len(ids)==1;r.update(job_id=next(iter(ids)),decision='existing_no_resubmit')
else:
 assert not (R/'submission.started').exists(),'Unconfirmed earlier submission; reconcile rather than blindly resubmit'
 active=run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T'])
 assert not any(l.split('|')[0] in {x['restart_provenance']['task'] for x in m['molecules']} for l in active.splitlines()),'Original still active'
 (R/'logs').mkdir(exist_ok=True);(R/'submission.started').write_text(r['utc'])
 args=['sbatch','--parsable','--array=0-'+str(len(m['molecules'])-1)+'%7','--dependency=afterany:65676:65677:63873','--chdir='+str(R),'--output='+str(R/'logs/%A_%a.out'),'--error='+str(R/'logs/%A_%a.err'),str(R/'body.sbatch')]
 p=subprocess.run(args,capture_output=True,text=True);r.update(command=args,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr);(R/'submission-attempt.json').write_text(json.dumps(r,indent=2)+'\n');assert p.returncode==0,r
 job=p.stdout.strip().split(';')[0];assert job.isdigit();r.update(job_id=job,decision='submitted')
r['readback']=run(['scontrol','show','job',r['job_id'],'-o']);r['queue_after']=run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T|%R']);(R/'submission.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
