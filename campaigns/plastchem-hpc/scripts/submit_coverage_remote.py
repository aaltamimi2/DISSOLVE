"""A-13, Euler-only: deterministic-name reconciliation and one guarded submission per chunk (copied from
submit_halogen_remote.py; no pilot hold, the chunk's own job name, memory and walltime, and the shared-cap check).

    python3 ~/plastchem-euler/coverage-v1/<chunk>/submit_coverage_remote.py <chunk> <throttle> [constraint]"""
import hashlib,json,re,subprocess,sys,time
from pathlib import Path
root=Path.home()/'plastchem-euler/coverage-v1';group=sys.argv[1];throttle=int(sys.argv[2]);constraint=sys.argv[3] if len(sys.argv)>3 else 'milan&cpu'
assert re.fullmatch(r'[cr]\d\d',group)
p=root/group;m=json.loads((p/'manifest.json').read_text());name=m['name']
def run(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout
queue=run(['squeue','-u','aaltamimi2','--name='+name,'-h','-o','%i|%j|%T|%N'])
accounting=run(['sacct','-u','aaltamimi2','--starttime=2026-10-08','--name='+name,'--format=JobID,JobName%40,State%32,Elapsed,NodeList','-nP'])
r={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'group':group,'name':name,'squeue':queue,'sacct':accounting}
(p/'reconciliation.json').write_text(json.dumps(r,indent=2)+'\n')
if queue.strip() or accounting.strip():
    ids={re.match(r'\d+',line).group() for line in (queue+'\n'+accounting).splitlines() if re.match(r'\d+',line)}
    r.update(decision='existing_job_found_no_resubmit',existing_array_ids=sorted(ids))
    if len(ids)==1 and not (p/'submission.json').exists():
        recovered=dict(r,returncode=0,stdout=next(iter(ids))+'\n',stderr='',reconciled_without_resubmission=True)
        (p/'reconciled-submission.json').write_text(json.dumps(recovered,indent=2)+'\n')
    print(json.dumps(r));raise SystemExit(0)
if (p/'submission.started').exists():
    print(json.dumps(dict(r,decision='prior_attempt_unconfirmed_no_resubmit')));raise SystemExit(2)
indices=json.loads((p/'submission-indices.json').read_text())
assert indices and len(indices)==len(set(indices))
for i in indices:
    mol=m['molecules'][i];key=mol['inchikey'];prep=json.loads((root/'prepared'/key/'preparation.json').read_text())
    assert prep['status']=='prepared' and prep['input']['inchikey']==key
    assert hashlib.sha256((root/'prepared'/key/'input.xyz').read_bytes()).hexdigest()==prep['xyz_sha256']
    assert not (root/'runs'/key/'attempt.lock').exists()
for line in (p/'staging.sha256').read_text().splitlines():
    sha,relative=line.split();assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==sha,relative
ranges=[];start=last=indices[0]
for i in indices[1:]:
    if i==last+1:last=i;continue
    ranges.append(str(start) if start==last else f'{start}-{last}');start=last=i
ranges.append(str(start) if start==last else f'{start}-{last}')
array=','.join(ranges)+'%'+str(throttle)
all_queue=run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T|%P|%C'])
r['all_user_queue_before']=all_queue
# the shared cap counts this campaign's running jobs only (other projects of the account do not count)
running_campaign=[line for line in all_queue.splitlines() if len(line.split('|'))>=5 and line.split('|')[1].startswith('contam-') and line.split('|')[2]=='RUNNING']
r['campaign_running_before']=len(running_campaign)
admitted={a['constraint'] for a in m.get('admitted',[{'constraint':'milan&cpu'}])}
assert constraint in admitted,'the pool must be an admitted CPU type'
assert 0<throttle<=(64 if constraint=='milan&cpu' else 500),'the Milan pool keeps the campaign cap of 64; other pools are bounded arrays (owner NOTE 2026-10-08 17:10)'
r['constraint']=constraint
with (p/'submission.started').open('x') as f:f.write(r['utc']+'\n')
result=subprocess.run(['sbatch','--parsable','--array='+array,'--constraint='+constraint,'--job-name='+name,'--mem='+m['mem'],'--time='+m['walltime'],'--chdir='+str(root),'--output='+str(root/'logs/%A_%a.out'),'--error='+str(root/'logs/%A_%a.err'),str(p/'coverage.sbatch'),group],capture_output=True,text=True)
r.update(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,indices=indices,throttle=throttle,decision='submitted' if result.returncode==0 else 'submission_failed_or_unconfirmed')
tmp=p/'submission.tmp';tmp.write_text(json.dumps(r,indent=2)+'\n');tmp.replace(p/'submission.json')
print(json.dumps({k:v for k,v in r.items() if k not in ('all_user_queue_before','squeue','sacct')}));raise SystemExit(result.returncode)
