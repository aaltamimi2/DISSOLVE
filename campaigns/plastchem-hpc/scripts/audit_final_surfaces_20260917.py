"""Snapshot and audit accepted surfaces not covered by an earlier matching hash."""
from pathlib import Path
import json,hashlib,datetime,os,subprocess
ROOT=Path(__file__).resolve().parents[1];R=Path('/mnt/r/plastchem-euler');D=R/'audits/surface-refresh-20260917T1537-final';D.mkdir(exist_ok=False)
prior={}
for p in sorted((R/'audits').glob('*/completed-surface-audit.json')):
 for r in json.loads(p.read_text())['rows']:
  if r['status']=='passed':prior.setdefault(r['inchikey'],set()).add(r['surface_sha256'])
cohort=[];folder=D/'freeze/state/campaign-v1/records';folder.mkdir(parents=True)
for p in sorted((ROOT/'state/campaign-v1/records').glob('*.json')):
 raw=p.read_bytes();r=json.loads(raw)
 if r.get('status')!='converged' or r['surface_sha256'] in prior.get(p.stem,set()):continue
 (folder/p.name).write_bytes(raw);cohort.append({'inchikey':p.stem,'record_sha256':hashlib.sha256(raw).hexdigest(),'surface_sha256':r['surface_sha256']})
(D/'snapshot.json').write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'denominator':len(cohort),'cohort':cohort},indent=2)+'\n')
env=dict(os.environ,PLASTCHEM_PROGRESS_ROOT=str(D),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
subprocess.run(['/home/aaltamimi2/.venvs/cosmo-logp/bin/python',str(ROOT/'scripts/audit_completed_surfaces.py'),'--frozen'],env=env,check=True)
a=json.loads((D/'completed-surface-audit.json').read_text());assert a['failed']==0 and a['passed']==len(cohort)
for p in [Path(__file__),ROOT/'scripts/audit_completed_surfaces.py']:(D/p.name).write_bytes(p.read_bytes())
(D/'REPORT.md').write_text(f"# Surface catch-up audit\n\n{a['passed']}/{len(cohort)} accepted returns passed surface/deck hashes, ORCA/Milan provenance, connectivity identity, geometry correspondence and surface numerical checks. No numerical partition audit or experimental-accuracy claim is made here. Earlier sealed packages remain unchanged.\n")
files=sorted(p for p in D.rglob('*') if p.is_file() and p.name!='artifacts.sha256');pins=[hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(D))+'\n' for p in files];(D/'artifacts.sha256').write_text(''.join(pins))
for line in pins:
 h,n=line.rstrip('\n').split('  ',1);assert hashlib.sha256((D/n).read_bytes()).hexdigest()==h
receipt={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'directory':str(D),'surface_passed':a['passed'],'failed':a['failed'],'files_verified':len(files),'manifest_sha256':hashlib.sha256((D/'artifacts.sha256').read_bytes()).hexdigest()};(ROOT/'state/surface-refresh-verified-20260917T1537-final.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
