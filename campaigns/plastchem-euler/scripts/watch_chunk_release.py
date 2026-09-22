"""Serialized multiplexed scheduler checks; guarded owner-authorized releases, no sbatch."""
import fcntl,json,time,datetime
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];P=R/'state/campaign-v1';lock=(P/'chunk-release-loop.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
while True:
 started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 try:
  r=run('ssh',['euler','python3 -'],input=(R/'scripts/chunk_release_remote.py').read_text(),capture_output=True,text=True,timeout=60)
  assert r.returncode==0,r.stderr
  receipt=json.loads(r.stdout);receipt['local_received_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 except Exception as e:receipt={'utc':started,'status':'observation_unconfirmed','error':str(e)}
 (P/'chunk-release-loop-latest.json').write_text(json.dumps(receipt,indent=2)+'\n')
 with (R/'logs/chunk-release-loop.jsonl').open('a') as f:f.write(json.dumps(receipt)+'\n')
 if receipt.get('changes'):
  with (R/'logs/campaign-changes.jsonl').open('a') as f:f.write(json.dumps({'utc':started,'action':'guarded_chunk_release_loop','receipt':receipt})+'\n')
 print(json.dumps({k:v for k,v in receipt.items() if k!='operations'}),flush=True)
 time.sleep(300)
