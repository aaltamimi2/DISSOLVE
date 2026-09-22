"""One-shot, reconciled main block with an afterany dependency on its predecessor."""
import hashlib,json,re,subprocess,sys,time
from pathlib import Path
root=Path.home()/'plastchem-euler/campaign-v1';label=sys.argv[1];assert re.fullmatch(r'main_chunk_\d{3}',label)
p=root/label;c=json.loads((p/'manifest.json').read_text());main=json.loads((root/'main_le80/manifest.json').read_text());name=c['name']
def run(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout
def receipt(folder):
    for name in ['submission.json','reconciled-submission.json']:
        f=folder/name
        if f.exists():
            r=json.loads(f.read_text())
            if r.get('returncode')==0:return r
    return None
queue=run(['squeue','-u','aaltamimi2','--name='+name,'-h','-o','%i|%j|%T|%N'])
accounting=run(['sacct','-u','aaltamimi2','--starttime=2026-09-12','--name='+name,'--format=JobID,JobName%40,State%32,Elapsed,NodeList','-nP'])
r={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'group':'main_le80','chunk':label,'name':name,'squeue':queue,'sacct':accounting}
(p/'reconciliation.json').write_text(json.dumps(r,indent=2)+'\n')
if queue.strip() or accounting.strip():
    ids={re.match(r'\d+',line).group() for line in (queue+'\n'+accounting).splitlines() if re.match(r'\d+',line)}
    r.update(decision='existing_job_found_no_resubmit',existing_array_ids=sorted(ids))
    if len(ids)==1 and not (p/'submission.json').exists():
        recovered=dict(r,returncode=0,stdout=next(iter(ids))+'\n',stderr='',reconciled_without_resubmission=True)
        (p/'reconciled-submission.json').write_text(json.dumps(recovered,indent=2)+'\n')
    print(json.dumps(r));raise SystemExit(0)
if (p/'submission.started').exists():print(json.dumps(dict(r,decision='prior_attempt_unconfirmed_no_resubmit')));raise SystemExit(2)
# The replaced full-main launcher must not have submitted independently.
old_name=main['name']
assert not run(['squeue','-u','aaltamimi2','--name='+old_name,'-h']).strip(),'Old full-main job exists'
assert not run(['sacct','-u','aaltamimi2','--starttime=2026-09-12','--name='+old_name,'--format=JobID','-nP']).strip(),'Old full-main accounting exists'
indices=json.loads((p/'submission-indices.json').read_text());assert indices and len(indices)==len(set(indices)) and set(indices)<=set(c['indices'])
assert c['concurrency']==28 and main['concurrency']==28
for other in root.glob('main_chunk_*/submission-indices.json'):
    if other.parent!=p and receipt(other.parent):assert not set(indices)&set(json.loads(other.read_text())),'Duplicate target across submitted chunks'
for i in indices:
    mol=main['molecules'][i];key=mol['inchikey'];prep=json.loads((root/'prepared'/key/'preparation.json').read_text())
    assert prep['status']=='prepared' and prep['input']['inchikey']==key
    assert hashlib.sha256((root/'prepared'/key/'input.xyz').read_bytes()).hexdigest()==prep['xyz_sha256']
    assert not (root/'runs'/key/'attempt.lock').exists()
for line in (p/'staging.sha256').read_text().splitlines():
    sha,relative=line.split();assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==sha,relative
previous=c['previous_chunk'];dependency=None
if previous:
    prev=receipt(root/previous);assert prev is not None,'Previous chunk submission unresolved'
    dependency=prev['stdout'].strip().split(';')[0];assert dependency.isdigit()
# Preserve afterany even when preceding tasks fail. It gates the entire preceding array.
ranges=[];start=last=indices[0]
for i in indices[1:]:
    if i==last+1:last=i;continue
    ranges.append(str(start) if start==last else f'{start}-{last}');start=last=i
ranges.append(str(start) if start==last else f'{start}-{last}')
args=['sbatch','--parsable','--job-name='+name,'--array='+','.join(ranges)+'%28','--chdir='+str(root),'--output='+str(root/'logs/%A_%a.out'),'--error='+str(root/'logs/%A_%a.err')]
if dependency:args+=['--dependency=afterany:'+dependency]
args+=[str(root/'campaign-main_le80.sbatch')]
r.update(dependency=('afterany:'+dependency) if dependency else None,sbatch_arguments=args)
with (p/'submission.started').open('x') as f:f.write(r['utc']+'\n')
result=subprocess.run(args,capture_output=True,text=True)
r.update(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,indices=indices,decision='submitted' if result.returncode==0 else 'submission_failed_or_unconfirmed')
tmp=p/'submission.tmp';tmp.write_text(json.dumps(r,indent=2)+'\n');tmp.replace(p/'submission.json');print(json.dumps(r));raise SystemExit(result.returncode)
