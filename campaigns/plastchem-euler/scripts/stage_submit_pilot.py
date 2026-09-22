"""Stage only this fixed pilot; reconcile before submitting; no blind transport retries."""
import hashlib,json,subprocess
from pathlib import Path
from euler_transport import run
root=Path(__file__).resolve().parents[1];state=root/'state/pilot-v1';manifest=json.loads((state/'manifest.json').read_text())
for r in manifest['molecules']:
 p=state/r['inchikey'];prep=json.loads((p/'preparation.json').read_text())
 assert prep['status']=='prepared',(r['inchikey'],prep)
 assert hashlib.sha256((p/'input.xyz').read_bytes()).hexdigest()==prep['xyz_sha256']
def call(kind,args):
 r=run(kind,args,text=True,capture_output=True)
 if r.returncode:raise RuntimeError(f'{kind} failed ({r.returncode}): {r.stderr}')
 return r.stdout
print(call('ssh',['euler','mkdir -p ~/plastchem-euler/pilot-v1/inputs ~/plastchem-euler/pilot-v1/logs']),flush=True)
print(call('scp',['-q',str(state/'manifest.json'),*[str(root/'scripts'/s) for s in ['pilot_runner.py','pilot.sbatch','reconcile_submit_pilot.py','collect_pilot_remote.py']],'euler:plastchem-euler/pilot-v1/']),flush=True)
print(call('scp',['-rq',*[str(state/r['inchikey']) for r in manifest['molecules']],'euler:plastchem-euler/pilot-v1/inputs/']),flush=True)
# Verify the staged manifest and every XYZ before running the submitter.
check="""python3 - <<'REMOTE'
import hashlib,json
from pathlib import Path
r=Path.home()/'plastchem-euler/pilot-v1'
m=json.loads((r/'manifest.json').read_text())
assert len(m['molecules'])==56
for row in m['molecules']:
 p=r/'inputs'/row['inchikey'];s=json.loads((p/'preparation.json').read_text())
 assert s['status']=='prepared'
 assert hashlib.sha256((p/'input.xyz').read_bytes()).hexdigest()==s['xyz_sha256']
print('Verified 56 staged XYZ digests; manifest SHA256',hashlib.sha256((r/'manifest.json').read_bytes()).hexdigest())
REMOTE
"""
verified=call('ssh',['euler',check])
assert hashlib.sha256((state/'manifest.json').read_bytes()).hexdigest() in verified
print(verified,flush=True)
# This remote command itself reconciles queue AND accounting, and saves evidence before sbatch.
output=call('ssh',['euler','python3 ~/plastchem-euler/pilot-v1/reconcile_submit_pilot.py'])
print(output,flush=True)
(state/'submission-receipt.json').write_text(output)
