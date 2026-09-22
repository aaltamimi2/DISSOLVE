"""Throughput steer: submit each ready main block immediately; cap 28, afterany chain."""
import hashlib,json,subprocess,time
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1';plan=json.loads((P/'chunk-plan.json').read_text());chunks=plan['main_chunks'];main=json.loads((P/'main_le80/manifest.json').read_text())
def pins():
    for line in (P/'THROUGHPUT-EXECUTION-PROVENANCE.sha256').read_text().splitlines():
        sha,path=line.split(None,1)
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=sha:raise RuntimeError('Pinned execution input changed: '+path)
def command(kind,args):
    r=run(kind,args,capture_output=True,text=True)
    if r.returncode:raise RuntimeError(f'{kind} returned {r.returncode}: {r.stderr[:700]} {r.stdout[:700]}')
    return r.stdout
while True:
    try:
        pins()
        for label in chunks:
            receipt=P/label/'submission-receipt.json'
            if receipt.exists():continue
            c=json.loads((P/label/'manifest.json').read_text())
            if not all((P/'prepared'/main['molecules'][i]['inchikey']/'preparation.json').exists() for i in c['indices']):break
            if c['previous_chunk']:assert (P/c['previous_chunk']/'submission-receipt.json').exists()
            subprocess.run(['python3',str(ROOT/'scripts/stage_campaign_chunk.py'),label],check=True)
            stage=json.loads((P/label/'staging-summary.json').read_text());archive=Path(stage['archive']);pins()
            command('scp',['-q',str(archive),f'euler:plastchem-euler/campaign-v1/{archive.name}'])
            remote=f'python3 -c "import hashlib,tarfile;from pathlib import Path;p=Path.home()/\'plastchem-euler/campaign-v1/{archive.name}\';assert hashlib.sha256(p.read_bytes()).hexdigest()==\'{stage["archive_sha256"]}\';tarfile.open(p).extractall(p.parent)"'
            command('ssh',['euler',remote]);pins()
            result=json.loads(command('ssh',['euler',f'python3 ~/plastchem-euler/campaign-v1/submit_campaign_chunk_remote.py {label}']))
            if result['decision']=='submitted':array=result['stdout'].strip().split(';')[0]
            elif result['decision']=='existing_job_found_no_resubmit' and len(result['existing_array_ids'])==1:array=result['existing_array_ids'][0]
            else:raise RuntimeError('Submission reconciliation needs review: '+json.dumps(result))
            assert array.isdigit()
            result.update(array_job_id=array,submission_tasks=stage['submission_tasks'],preparation_failures=stage['preparation_failures'])
            tmp=receipt.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(receipt)
            print(json.dumps({'chunk':label,'array_job_id':array,'tasks':stage['submission_tasks'],'cap':28,'dependency':result.get('dependency'),'decision':result['decision']}),flush=True)
            subprocess.run(['python3',str(ROOT/'scripts/summarize_campaign.py')],check=True,stdout=subprocess.DEVNULL)
        if all((P/c/'submission-receipt.json').exists() for c in chunks):
            (P/'submission-complete.json').write_text(json.dumps({'main_chunks':chunks,'tail_array':'54733','utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())},indent=2)+'\n')
            print('ALL_MAIN_CHUNKS_RECONCILED_AND_SUBMITTED',flush=True);break
    except Exception as exc:
        print(json.dumps({'launch_error':str(exc),'epoch':time.time()}),flush=True)
        if 'Pinned execution input changed' in str(exc):raise
    state=json.loads((ROOT/'state/ssh-transport.json').read_text());time.sleep(max(60,state.get('retry_after_epoch',0)-time.time()))
