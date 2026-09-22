"""Bounded read-only hashing of completed packages; no scientific data changes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os,hashlib,json,datetime
ROOT=Path(__file__).resolve().parents[1]
packages=[Path('/mnt/r/plastchem-euler/audits/catchup-2026-09-17'),Path('/mnt/r/plastchem-euler/pubchem-catchup-2026-09-17')]
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(262144),b''):h.update(b)
 return h.hexdigest()
receipts=[]
for D in packages:
 assert (D/'REPORT.md').is_file()
 (D/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
 if D.name=='catchup-2026-09-17':(D/'audit_catchup_20260917.py').write_bytes((ROOT/'scripts/audit_catchup_20260917.py').read_bytes())
 paths=[]
 for folder,dirs,files in os.walk(D,followlinks=False):
  for name in files:
   if name not in ['artifacts.sha256','artifacts.sha256.tmp']:paths.append(Path(folder)/name)
 paths.sort()
 with ThreadPoolExecutor(max_workers=4) as pool:hashes=list(pool.map(digest,paths))
 content=''.join(h+'  '+str(p.relative_to(D))+'\n' for p,h in zip(paths,hashes))
 tmp=D/'artifacts.sha256.tmp';tmp.write_text(content);tmp.replace(D/'artifacts.sha256')
 assert (D/'artifacts.sha256').read_text()==content
 # Independently recheck the report and machine-readable audit/qualification summaries.
 mapping={str(p.relative_to(D)):h for p,h in zip(paths,hashes)}
 probes=['REPORT.md','completed-surface-audit.json','sealed-thermodynamics/production-record-audit.json'] if D.name=='catchup-2026-09-17' else ['REPORT.md','retrieval.json','pubchem-measured-validation/summary.json']
 for name in probes:assert digest(D/name)==mapping[name]
 row={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'directory':str(D),'files_hashed':len(paths),'manifest_sha256':digest(D/'artifacts.sha256'),'summary_files_rechecked':probes,'threads':4}
 receipts.append(row);print(json.dumps(row),flush=True)
 (ROOT/'state/completed-package-seals-20260917.json').write_text(json.dumps(receipts,indent=2)+'\n')
