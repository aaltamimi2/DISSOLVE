from pathlib import Path
import json,hashlib,time,datetime,csv,re
D=Path('/mnt/r/plastchem-euler/pubchem-post4579-2026-09-17');pid=1154904
while Path(f'/proc/{pid}').exists():time.sleep(5)
lines=(D/'artifacts.sha256').read_text().splitlines()
for line in lines:
 h,n=line.split('  ',1);assert hashlib.sha256((D/n).read_bytes()).hexdigest()==h
s=json.loads((D/'pubchem-measured-validation/summary.json').read_text());rows=list(csv.DictReader((D/'pubchem-measured-validation/best-measured-logKow.csv').open()))
assert len(rows)==s['best_point_value_molecules']==239
assert len({r['input_inchikey'] for r in rows})==239
for r in rows:
 assert r['qualification']=='qualified' and r['observed_operator']=='=' and r['raw_reference_string'] and r['retrieved_utc']
 assert not re.search(r'estimat|comput|predict|calculat|XLogP',r['raw_value_text'],re.I)
 assert not re.search(r'GEMS|KOWWIN|CLOGP|EPI.?Suite|estimat|predict|calculat',r['raw_reference_string'],re.I)
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files_verified':len(lines),'manifest_sha256':hashlib.sha256((D/'artifacts.sha256').read_bytes()).hexdigest(),'qualified_molecules':239,'qualified_observations':s['qualified_observations'],'scope':'Package integrity and qualification-rule checks; not independent primary-paper verification'}
Path('state/pubchem-post4579-verified-20260917.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r),flush=True)
