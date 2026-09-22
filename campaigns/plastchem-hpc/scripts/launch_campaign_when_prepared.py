"""Owner-authorised one-shot arrays, staged only after local preparation and guarded reconciliation."""
import hashlib,json,subprocess,time
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1'
groups=['tail_gt80','main_le80']
def pin_check():
    for line in (P/'EXECUTION-PROVENANCE.sha256').read_text().splitlines():
        sha,path=line.split(None,1)
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=sha:raise RuntimeError('Pinned execution input changed; do not submit: '+path)
def command(kind,args):
    r=run(kind,args,capture_output=True,text=True)
    if r.returncode:raise RuntimeError(f'{kind} returned {r.returncode}: {r.stderr[:1000]} {r.stdout[:1000]}')
    return r.stdout
while True:
    try:
        pin_check()
        for group in groups:
            receipt=P/group/'submission-receipt.json'
            if receipt.exists():continue
            m=json.loads((P/group/'manifest.json').read_text())
            if not all((P/'prepared'/mol['inchikey']/'preparation.json').exists() for mol in m['molecules']):continue
            subprocess.run(['python3',str(ROOT/'scripts/stage_campaign_group.py'),group],check=True)
            stage=json.loads((P/group/'staging-summary.json').read_text());archive=Path(stage['archive'])
            pin_check()
            command('scp',['-q',str(archive),f'euler:plastchem-euler/campaign-v1/{archive.name}'])
            # Fixed lane-owned paths, fixed enum group, and a hexadecimal digest only.
            remote=f'python3 -c "import hashlib,tarfile;from pathlib import Path;p=Path.home()/\'plastchem-euler/campaign-v1/{archive.name}\';assert hashlib.sha256(p.read_bytes()).hexdigest()==\'{stage["archive_sha256"]}\';tarfile.open(p).extractall(p.parent)"'
            command('ssh',['euler',remote])
            pin_check()
            out=command('ssh',['euler',f'python3 ~/plastchem-euler/campaign-v1/submit_campaign_remote.py {group}'])
            r=json.loads(out)
            if r['decision']=='submitted':array=r['stdout'].strip().split(';')[0]
            elif r['decision']=='existing_job_found_no_resubmit' and len(r['existing_array_ids'])==1:array=r['existing_array_ids'][0]
            else:raise RuntimeError('Submission reconciliation needs review: '+out[:1000])
            assert array.isdigit()
            r.update(array_job_id=array,submission_tasks=stage['submission_tasks'],preparation_failures=stage['preparation_failures'])
            receipt.write_text(json.dumps(r,indent=2)+'\n')
            print(json.dumps({'group':group,'array_job_id':array,'tasks':stage['submission_tasks'],'cap':m['concurrency'],'decision':r['decision']}),flush=True)
            subprocess.run(['python3',str(ROOT/'scripts/summarize_campaign.py')],check=True,stdout=subprocess.DEVNULL)
        if all((P/g/'submission-receipt.json').exists() for g in groups):
            print('BOTH_CAMPAIGN_ARRAYS_RECONCILED_AND_SUBMITTED',flush=True);break
    except Exception as exc:
        print(json.dumps({'launch_error':str(exc),'epoch':time.time()}),flush=True)
        if 'Pinned execution input changed' in str(exc):raise
    state=json.loads((ROOT/'state/ssh-transport.json').read_text())
    time.sleep(max(60,state.get('retry_after_epoch',0)-time.time()))
