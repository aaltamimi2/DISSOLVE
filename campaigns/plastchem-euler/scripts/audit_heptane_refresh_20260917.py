"""Snapshot and audit current panel records without stopping the serial worker."""
import datetime,hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/audits/heptane-numeric-refresh-20260917T1238')
D.mkdir(exist_ok=False)
P=D/'sealed-thermodynamics';(P/'results').mkdir(parents=True)
C=D/'freeze/state/campaign-v1/records';C.mkdir(parents=True)
def dump(p,d):p.write_text(json.dumps(d,indent=2)+'\n')
state=ROOT/'state/thermodynamics-v1'
library=(state/'library-registry.json').read_bytes()
ledger=json.loads((state/'processing-ledger.json').read_text())
sealed={};deferred=[]
for key,item in ledger.items():
 raw=Path(item['result_path']).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=item['result_sha256']:
  deferred.append(key);continue
 record=(ROOT/'state/campaign-v1/records'/f'{key}.json').read_bytes()
 r=json.loads(record);assert r['status']=='converged'
 out=P/'results'/f'{key}.json';out.write_bytes(raw)
 assert out.read_bytes()==raw
 (C/f'{key}.json').write_bytes(record)
 sealed[key]=dict(item,result_path=str(out),original_result_path=item['result_path'])
dump(P/'processing-ledger.json',sealed);(P/'library-registry.json').write_bytes(library)
dump(D/'snapshot.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'ledger_entries':len(ledger),'sealed_entries':len(sealed),'changed_during_copy_deferred':deferred,'scope':'Numerical consistency of copied records; no new surface audit or experimental accuracy claim'})
script=ROOT/'scripts/audit_thermodynamic_records.py';(D/script.name).write_bytes(script.read_bytes())
subprocess.run(['/home/aaltamimi2/.venvs/cosmo-logp/bin/python',str(script),'--frozen'],env=dict(os.environ,PLASTCHEM_PROGRESS_ROOT=str(D)),check=True)
a=json.loads((P/'production-record-audit.json').read_text());assert a['failed']==0
(D/'REPORT.md').write_text(f"# Updated panel numerical audit\n\n{a['passed']}/{a['snapshot_ledger_entries']} stored records passed; {a['failed']} failed. {len(deferred)} records changed while being copied and were deferred.\n\nChecks cover hashes, provenance, activity dilution convergence, partition algebra and explicit missing predictions. Recorded activity failures are retained, not replaced. This is not full-panel completion or experimental validation. Original releases are unchanged.\n")
(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
files=sorted(p for p in D.rglob('*') if p.is_file())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(D))+'\n' for p in files))
print(json.dumps({'directory':str(D),'passed':a['passed'],'failed':a['failed'],'deferred':len(deferred),'files':len(files)}),flush=True)
