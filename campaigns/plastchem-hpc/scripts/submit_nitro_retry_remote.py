"""Queue seven salvaged 48h retries held, after A-9 A/B are submitted.
The shared controller releases them last after archival and registration.
This command never releases or cancels any job.
"""
import datetime, fcntl, hashlib, json, re, subprocess
from pathlib import Path
ROOT=Path.home()/'plastchem-euler';R=ROOT/'polymer-v1/nitro-retry1'
lock=(ROOT/'phase83-v1/throttle.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
m=json.loads((R/'body/manifest.json').read_text());name=m['name']
assert name=='contam-polymer24a-nitro-retry1' and len(m['molecules'])==7 and m['walltime']=='48:00:00'
A=json.loads((ROOT/'phase9-v1/production-submission.json').read_text());assert A['launch_utc']
B=json.loads((ROOT/'phase9-solvent-library-v1/submission.json').read_text());assert B['job_id']
receipt=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),name=name,operations=[],manifest_sha256=sha(R/'body/manifest.json'),priority='last_after_A_B_large_polymers_and_tier2',held_for_original_archive_registration=True)
def call(args):
    p=subprocess.run(args,capture_output=True,text=True)
    receipt['operations'].append(dict(command=args,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr))
    assert p.returncode==0,(args,p.stderr)
    return p.stdout
def save():
    p=R/'submission.tmp';p.write_text(json.dumps(receipt,indent=2)+'\n');p.replace(R/'submission.json')
q=call(['squeue','-u','aaltamimi2','--name='+name,'-h','-r','-o','%i|%j|%T'])
a=call(['sacct','-u','aaltamimi2','--starttime=2026-09-22','--name='+name,'-nP','--format=JobID,JobName%40,State,Elapsed,NodeList'])
receipt.update(squeue_reconciliation=q,sacct_reconciliation=a)
ids={re.match(r'\d+',line).group() for line in (q+'\n'+a).splitlines() if re.match(r'\d+',line)}
if ids:
    assert len(ids)==1,'Multiple retry arrays require reconciliation'
    job=next(iter(ids));old=R/'submission.json'
    if old.exists():
        prior=json.loads(old.read_text());assert prior['job_id']==job and prior['manifest_sha256']==receipt['manifest_sha256']
        receipt['original_submission']=prior
    receipt.update(job_id=job,decision='existing_no_resubmit')
else:
    assert not (R/'submission.started').exists(),'Unconfirmed prior attempt: do not blindly resubmit'
    raw=call(['squeue','-u','aaltamimi2','-p','research','-h','-r','-o','%i|%T|%C|%j'])
    rows=[line.split('|') for line in raw.splitlines() if line]
    allowed={str(A['job_id']),str(B['job_id']),'63873','65677'}
    assert all(r[0].split('_')[0] in allowed and r[2]=='1' for r in rows),'Unexpected research work'
    assert sum(r[1]!='PENDING' for r in rows)<=64
    states=call(['sacct','-j','65676','--starttime=2026-09-21','-nP','--format=JobID,State'])
    states={p[0]:p[1].split()[0] for p in (line.split('|') for line in states.splitlines()) if len(p)>=2}
    assert {mol['restart_provenance']['task'] for mol in m['molecules']}=={f'65676_{i}' for i in range(166,173)}
    for mol in m['molecules']:
        source=mol['restart_provenance'];task=source['task']
        assert states.get(task) in ['TIMEOUT','FAILED','CANCELLED']
        assert states[task]=='TIMEOUT' or source['source_failure_mode'] in ['scheduler_signal_10','walltime_censored']
        prep=json.loads((R/'prepared'/mol['entry_id']/'preparation.json').read_text())
        assert sha(R/'prepared'/mol['entry_id']/'input.xyz')==prep['xyz_sha256']==source['xyz_sha256']
        identity=source['geometry_identity'];assert identity['identity_verified'] and identity['connectivity_match']
        assert identity['input_inchikey']==mol['inchikey'] and identity['geometry_sha256']==source['xyz_sha256']
    parents=sorted({r[0].split('_')[0] for r in rows})
    (R/'logs').mkdir(exist_ok=True)
    args=['sbatch','--parsable','--hold','--array=0-6%7','--nodes=1','--ntasks=1','--cpus-per-task=1','--mem=4G','--time=48:00:00',
          '--partition=research','--constraint=milan&cpu','--exclude=euler09,euler10','--chdir='+str(R),
          '--output='+str(R/'logs/%A_%a.out'),'--error='+str(R/'logs/%A_%a.err')]
    if parents:args.append('--dependency=afterany:'+':'.join(parents))
    args.append(str(R/'body.sbatch'))
    receipt.update(dependency_parent_arrays=parents,command=args)
    (R/'submission.started').write_text(json.dumps(receipt,indent=2)+'\n')
    p=subprocess.run(args,capture_output=True,text=True)
    receipt.update(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
    (R/'submission-attempt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    assert p.returncode==0,receipt
    job=p.stdout.strip().split(';')[0];assert job.isdigit()
    receipt.update(job_id=job,decision='submitted_held');save()
receipt['queue_after']=call(['squeue','-u','aaltamimi2','-p','research','-h','-r','-o','%i|%T|%C|%j|%R'])
current=[line for line in receipt['queue_after'].splitlines() if line.split('|')[0].split('_')[0]==job]
if current:
    receipt['readback']=call(['scontrol','show','job',job,'-o'])
    if receipt['decision']=='submitted_held':assert all('|PENDING|1|'+name+'|(JobHeldUser)' in line for line in current)
save();print(json.dumps(receipt))
