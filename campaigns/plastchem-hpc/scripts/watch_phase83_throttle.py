"""Multiplexed, serialized owner-authorized shared-cap controller; collectors untouched."""
import datetime,fcntl,json,os,sys,time
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];S=R/'state/phase83-v1';D=Path('/mnt/r/plastchem-euler/phase83-v1')
def once():
 code=(R/'scripts/phase83_throttle_remote.py').read_text()
 r=run('ssh',['euler','python3 -'],input=code,capture_output=True,text=True,timeout=90)
 assert r.returncode==0,r.stderr
 receipt=json.loads(r.stdout)
 (S/'throttle-latest.json').write_text(json.dumps(receipt,indent=2)+'\n')
 if receipt['changes']:
  archive=D/'throttle-receipts';archive.mkdir(exist_ok=True)
  (archive/(receipt['utc'].replace(':','-')+'.json')).write_text(json.dumps(receipt,indent=2)+'\n')
 for event in receipt['changes']:
  for p in [S/'throttle-changes.jsonl',D/'throttle-changes.jsonl',R/'logs/campaign-changes.jsonl']:
   with p.open('a') as f:f.write(json.dumps(event)+'\n')
 # Fetch the authoritative durable history, including a mutation whose SSH
 # confirmation might have been lost in a previous pass.
 history_code="from pathlib import Path\np=Path.home()/'plastchem-euler/phase83-v1/throttle-changes.jsonl'\nprint(p.read_text() if p.exists() else '',end='')\n"
 h=run('ssh',['euler','python3 -'],input=history_code,capture_output=True,text=True,timeout=90)
 assert h.returncode==0,h.stderr
 for p in [S/'throttle-changes.jsonl',D/'throttle-changes.jsonl']:p.write_text(h.stdout)
 events=[json.loads(l) for l in h.stdout.splitlines() if l]
 text='# Phase 8.3 shared-cap throttle history\n\nOwner authorization: A-8 NOTE, 2026-09-23. Shared cap remains 64; per-task allocation remains one CPU and 4 GB. No tasks are cancelled or resubmitted by this controller.\n\n'
 text+='Tier 2 waits for the large-polymer array. Before lending its reservation to production, tier 2 is limited to the remaining polymer-task count. Once polymers drain, production is reduced first and tier 2 is restored toward its previous 37 slots only as running production tasks release capacity. Lowering a throttle never terminates running tasks. Unknown research jobs cause a reconciliation alert rather than an unbudgeted increase.\n\n'
 text+='| UTC | Array | From | To | Reason |\n|---|---|---:|---:|---|\n'
 for e in events:text+=f"| {e['utc']} | {e['job_id']} | {e['from']} | {e['to']} | {e['reason']} |\n"
 text+='\nEvery change has an explicit scheduler readback in `state/phase83-v1/throttle-latest.json`; the durable event history is mirrored at `/mnt/r/plastchem-euler/phase83-v1/throttle-changes.jsonl`. Controller: `/home/aaltamimi2/plastchem-euler/scripts/watch_phase83_throttle.py`.\n'
 for p in [R/'reports/phase83-2026-09-23/THROTTLE.md',D/'THROTTLE.md']:p.write_text(text)
 print(json.dumps({k:v for k,v in receipt.items() if k!='operations'}),flush=True)
 assert receipt['status']!='failed',receipt.get('error')
 return receipt['status']
def main():
 lock=(S/'throttle-local.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 (S/'throttle-process.json').write_text(json.dumps({'pid':os.getpid(),'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}))
 while True:
  try:
   if once()=='production_drained':return
  except Exception as e:
   status={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'error':str(e)}
   (S/'throttle-error.json').write_text(json.dumps(status,indent=2)+'\n');print(json.dumps(status),flush=True)
  if '--once' in sys.argv:return
  time.sleep(60)
if __name__=='__main__':main()
