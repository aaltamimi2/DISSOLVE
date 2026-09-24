"""Recover only provenance-staging failures after the original array is terminal."""
import json,os,sys,subprocess,shutil
from pathlib import Path
D=Path(__file__).resolve().parent
sys.path.insert(0,str(D));import phase83_worker as worker
idx=json.loads((D/'calibration-repair-map.json').read_text())[int(sys.argv[1])]
assert os.environ.get('SLURM_JOB_ID')
q=subprocess.check_output(['squeue','-h','-u','aaltamimi2','--name=contam-phase83-calibration-v1','-o','%T'],text=True);assert not q.strip(),'Original calibration still active; do not overlap'
out=D/'results'/f'{idx:05d}';history=out/'attempts';history.mkdir(exist_ok=True)
old=out/'complete.json';saved=history/'initial-complete.json'
if not saved.exists():
 assert old.exists(),'Original task incomplete; needs separate reconciliation'
 data=json.loads(old.read_text());assert any(s=='failed' for s in data['lle_statuses'].values())
 old.replace(saved)
elif old.exists():
 # A finished recovery is idempotent, never discard the new stamp.
 raise SystemExit(0)
for n in range(idx*64,(idx+1)*64):
 folder=D/'phase82/lle'/f'{n:03d}';failure=folder/'failure.json';saved_failure=folder/'initial-failure.json'
 if failure.exists() and not saved_failure.exists():shutil.copyfile(failure,saved_failure)
worker.main(idx)
