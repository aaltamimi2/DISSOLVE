"""A-4 one-array launcher: pinned inputs, reconciliation, held first-30 gate."""
import hashlib,json,subprocess,time
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/tier2-v1'
REMOTE='~/plastchem-euler/tier2-v1'
def save(p,v):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(v,indent=2)+'\n');t.replace(p)
def pins():
 for line in (P/'EXECUTION-PROVENANCE.sha256').read_text().splitlines():
  h,path=line.split(None,1)
  assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==h, 'Pinned execution input changed: '+path
def command(kind,args):
 r=run(kind,args,capture_output=True,text=True)
 if r.returncode:raise RuntimeError(f'{kind} returned {r.returncode}: {r.stderr[:700]} {r.stdout[:700]}')
 return r.stdout
while True:
 try:
  pins();m=json.loads((P/'tier2/manifest.json').read_text())
  if not all((P/'prepared'/v['inchikey']/'preparation.json').exists() for v in m['molecules']):
   time.sleep(10);continue
  receipt=P/'tier2/submission-receipt.json'
  if not receipt.exists():
   subprocess.run(['python3',str(ROOT/'scripts/stage_tier2.py'),'tier2'],check=True)
   stage=json.loads((P/'tier2/staging-summary.json').read_text());archive=Path(stage['archive'])
   command('ssh',['euler',f'mkdir -p {REMOTE}/logs {REMOTE}/runs {REMOTE}/returns'])
   command('scp',['-q',str(archive),f'euler:plastchem-euler/tier2-v1/{archive.name}'])
   remote=f'python3 -c "import hashlib,tarfile;from pathlib import Path;p=Path.home()/\'plastchem-euler/tier2-v1/{archive.name}\';assert hashlib.sha256(p.read_bytes()).hexdigest()==\'{stage["archive_sha256"]}\';tarfile.open(p).extractall(p.parent)"'
   command('ssh',['euler',remote]);pins()
   result=json.loads(command('ssh',['euler',f'python3 {REMOTE}/submit_tier2_remote.py tier2']))
   if result['decision']=='submitted':job=result['stdout'].strip().split(';')[0]
   elif result['decision']=='existing_job_found_no_resubmit' and len(result['existing_array_ids'])==1:job=result['existing_array_ids'][0]
   else:raise RuntimeError('Submission reconciliation needs review: '+json.dumps(result))
   assert job.isdigit();result.update(array_job_id=job,submission_tasks=stage['submission_tasks'],preparation_failures=stage['preparation_failures'],shared_cap=64)
   save(receipt,result);print(json.dumps(result),flush=True)
  release=P/'tier2/initial-release.json'
  if not release.exists():
   r=json.loads(command('ssh',['euler',f'python3 {REMOTE}/release_tier2_initial_remote.py']))
   save(release,r);save(P/'tier2/first30-indices.json',r['selected_indices']);print(json.dumps(r),flush=True)
  save(P/'submission-complete.json',{'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'first30_gate':True,'rest_held':True})
  print('TIER2_SUBMITTED_INITIAL_COHORT_RELEASED',flush=True);break
 except Exception as exc:
  print(json.dumps({'launch_error':str(exc),'epoch':time.time()}),flush=True)
  if isinstance(exc,AssertionError):raise
  state=json.loads((ROOT/'state/ssh-transport.json').read_text())
  time.sleep(max(120,state.get('retry_after_epoch',0)-time.time()))
