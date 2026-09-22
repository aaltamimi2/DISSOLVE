"""One authorised solvent-support array, after both existing campaign chains. No blind retries."""
import hashlib,json,shlex
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/solvent-library-v1';group=P/'solvent_library';receipt=group/'submission-receipt.json'
if receipt.exists():print(receipt.read_text());raise SystemExit(0)
s=json.loads((group/'staging-summary.json').read_text());archive=Path(s['archive'])
assert hashlib.sha256(archive.read_bytes()).hexdigest()==s['archive_sha256']
def call(kind,args):
 r=run(kind,args,capture_output=True,text=True)
 if r.returncode:raise RuntimeError(f'{kind}: {r.stderr[:800]} {r.stdout[:800]}')
 return r.stdout
call('ssh',['euler','mkdir -p ~/plastchem-euler/solvent-library-v1/logs'])
call('scp',['-q',str(archive),'euler:plastchem-euler/solvent-library-v1/'+archive.name])
code="import hashlib,tarfile;from pathlib import Path;p=Path.home()/'plastchem-euler/solvent-library-v1/solvent_library-staging.tar.gz';assert hashlib.sha256(p.read_bytes()).hexdigest()=="+repr(s['archive_sha256'])+";tarfile.open(p).extractall(p.parent)"
call('ssh',['euler','python3 -c '+shlex.quote(code)])
r=json.loads(call('ssh',['euler','python3 ~/plastchem-euler/solvent-library-v1/submit_solvent_library_remote.py solvent_library']))
if r['decision']=='submitted':array=r['stdout'].strip().split(';')[0]
elif r['decision']=='existing_job_found_no_resubmit' and len(r['existing_array_ids'])==1:array=r['existing_array_ids'][0]
else:raise RuntimeError('Reconciliation needs review; no resubmit: '+json.dumps(r))
assert array.isdigit();r.update(array_job_id=array,submission_tasks=6,role='solvent_library_support',campaign_denominator_unchanged=5824)
receipt.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
