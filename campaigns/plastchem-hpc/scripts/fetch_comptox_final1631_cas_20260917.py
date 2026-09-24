"""Resumable public CompTox export; raw experimental qualification is separate."""
import datetime, hashlib, json, time
from pathlib import Path
import requests
D=Path('/mnt/r/plastchem-euler/comptox-final1631-2026-09-17/sources')
BASE='https://comptox.epa.gov/dashboard-api/batchsearch/export/'
session=requests.Session()
def stamp(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
response_file=D/'comptox-CAS-export-response.txt'
if not response_file.exists():
    r=session.post(BASE,json=json.loads((D/'comptox-CAS-export-request.json').read_text()),timeout=60)
    response_file.write_text(r.text)
    (D/'comptox-CAS-export-receipt.json').write_text(json.dumps({'url':BASE,'status':r.status_code,'utc':stamp(),'sha256':hashlib.sha256(r.content).hexdigest()},indent=2)+'\n')
    r.raise_for_status()
job=response_file.read_text().strip().strip('"')
import uuid
uuid.UUID(job)
for attempt in range(60):
    url=BASE+'status/'+job
    r=session.get(url,timeout=40)
    (D/'comptox-CAS-export-status.json').write_text(json.dumps({'url':url,'status':r.status_code,'body':r.text,'utc':stamp()},indent=2)+'\n')
    r.raise_for_status()
    if r.text.strip().lower()=='true': break
    time.sleep(30)
else: raise RuntimeError('Export still pending; reuse saved export id when polling again')
url=BASE+'content/'+job
r=session.get(url,timeout=60);r.raise_for_status()
assert r.content.startswith(b'PK'), 'Expected XLSX ZIP bytes'
p=D/'comptox-CAS-properties.xlsx';p.write_bytes(r.content)
p.with_suffix('.retrieval.json').write_text(json.dumps({'url':url,'status':r.status_code,'retrieved_utc':stamp(),'sha256':hashlib.sha256(r.content).hexdigest(),'bytes':len(r.content)},indent=2)+'\n')
print(json.dumps({'export_id':job,'bytes':len(r.content),'utc':stamp()}),flush=True)
