from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,datetime
D=Path('/mnt/r/plastchem-euler/audits/diphenyl-numeric-refresh-20260917T1423');ROOT=Path(__file__).resolve().parents[1]
import time
while Path('/proc/1146729').exists():time.sleep(5)
raw=(D/'artifacts.sha256').read_bytes();pins=[line.split('  ',1) for line in raw.decode().splitlines()]
assert len(pins)==len({name for _,name in pins})==8923
assert {name for _,name in pins}=={str(p.relative_to(D)) for p in D.rglob('*') if p.is_file() and p.name!='artifacts.sha256'}
def verify(item):
 h,name=item;assert hashlib.sha256((D/name).read_bytes()).hexdigest()==h,name
with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(verify,pins))
a=json.loads((D/'sealed-thermodynamics/production-record-audit.json').read_text());assert a['passed']==a['snapshot_ledger_entries']==4458 and a['failed']==0
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files_rehashed':len(pins),'manifest_sha256':hashlib.sha256(raw).hexdigest(),'passed':4458,'failed':0,'deferred_changed_records':186,'scope':'Immutable numerical snapshot only; not full current-panel or experimental validation'}
(ROOT/'state/diphenyl-numeric-audit-verified-20260917.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r),flush=True)
