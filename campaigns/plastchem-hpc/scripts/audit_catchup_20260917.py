"""Seal a new cumulative audit cohort without modifying released packages or live results."""
import os,json,hashlib,datetime,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/audits/catchup-2026-09-17')
D.mkdir(exist_ok=False)
def dump(p,x):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2)+'\n')
records={p.stem:json.loads(p.read_text()) for p in (ROOT/'state/campaign-v1/records').glob('*.json')}
accepted={k:r for k,r in records.items() if r.get('status')=='converged'}
for key,r in accepted.items():dump(D/'freeze/state/campaign-v1/records'/f'{key}.json',r)
ledger_path=ROOT/'state/thermodynamics-v1/processing-ledger.json'
ledger=json.loads(ledger_path.read_text());libraw=(ROOT/'state/thermodynamics-v1/library-registry.json').read_bytes()
sealed={};deferred=[]
for key,item in ledger.items():
 if key not in accepted:
  deferred.append({'key':key,'reason':'Not in initial accepted snapshot'});continue
 raw=Path(item['result_path']).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=item['result_sha256']:
  deferred.append({'key':key,'reason':'Live result changed during snapshot; deferred, not integrity failure'});continue
 out=D/'sealed-thermodynamics/results'/f'{key}.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(raw)
 assert hashlib.sha256(out.read_bytes()).hexdigest()==item['result_sha256']
 sealed[key]=dict(item,result_path=str(out),original_result_path=item['result_path'])
dump(D/'sealed-thermodynamics/processing-ledger.json',sealed)
(D/'sealed-thermodynamics/library-registry.json').write_bytes(libraw)
dump(D/'snapshot.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'accepted_count':len(accepted),'numeric_records':len(sealed),'deferred':deferred,'scope':'Cumulative current results; frozen release and batch-2 unchanged'})
env=dict(os.environ,PLASTCHEM_PROGRESS_ROOT=str(D),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
for script in ['audit_completed_surfaces.py','audit_thermodynamic_records.py']:
 raw=(ROOT/'scripts'/script).read_bytes();(D/script).write_bytes(raw)
 with (D/(script+'.log')).open('w') as f:subprocess.run(['/home/aaltamimi2/.venvs/cosmo-logp/bin/python',str(ROOT/'scripts'/script),'--frozen'],env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
a=json.loads((D/'completed-surface-audit.json').read_text());b=json.loads((D/'sealed-thermodynamics/production-record-audit.json').read_text())
report=f'''# Cumulative catch-up audit, 17 September 2026

Surface provenance and geometry: {a['passed']}/{a['completed_snapshot']} passed; {a['failed']} failed.
Stored numerical/provenance consistency: {b['passed']}/{b['snapshot_ledger_entries']} passed; {b['failed']} failed.
Snapshot exclusions during concurrent collection/processing: {len(deferred)} (see snapshot.json).

These checks verify surface/deck hashes, ORCA version, Milan CPU, accepted connectivity, surface-to-XYZ correspondence, surface numerical integrity, result hashes, activity dilution convergence, partition algebra and missingness. They do not establish experimental accuracy or full solvent-panel coverage. A correctly recorded failed solvent calculation can pass the consistency audit; its partition value remains unavailable.

Run: `/home/aaltamimi2/.venvs/cosmo-logp/bin/python /home/aaltamimi2/plastchem-euler/scripts/audit_catchup_20260917.py` (creates a new directory and refuses overwrite).
Artifact directory: `{D}`. Original released and batch-2 packages are unchanged.
'''
(D/'REPORT.md').write_text(report)
files=sorted(p for p in D.rglob('*') if p.is_file())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(D))+'\n' for p in files))
print(json.dumps({'directory':str(D),'surfaces':{k:v for k,v in a.items() if k!='rows'},'numeric':{k:v for k,v in b.items() if k!='rows'}}),flush=True)
