"""Verify the timestamped CSV after its existing exporter exits; preserve old snapshots."""
from pathlib import Path
import json,csv,hashlib,datetime,time,math
ROOT=Path(__file__).resolve().parents[1];receipt=json.loads((ROOT/'state/completed-export-refresh-20260917T0745.json').read_text());pid=receipt['pid']
while Path(f'/proc/{pid}').exists():
 assert 'export_completed_contaminants.py' in Path(f'/proc/{pid}/cmdline').read_text()
 time.sleep(30)
start=datetime.datetime.fromisoformat(receipt['utc'])
candidates=[]
for p in ROOT.glob('completed-contaminants-2026-09-17T*.metadata.json'):
 m=json.loads(p.read_text())
 if datetime.datetime.fromisoformat(m['snapshot_utc'])>=start:candidates.append((p,m))
assert len(candidates)==1,[(str(p),m['snapshot_utc']) for p,m in candidates]
p,m=candidates[0];local=p.with_name(p.name.replace('.metadata.json','.csv'));raw=local.read_bytes();assert hashlib.sha256(raw).hexdigest()==m['csv_sha256']
bulk=Path(m['bulk_snapshot']);assert raw==(bulk/'completed-contaminants.csv').read_bytes()
rows=list(csv.DictReader(raw.decode().splitlines()));assert len(rows)==m['exported_molecules']==len({r['inchikey'] for r in rows})
assert all(r['name'] and r['smiles'] and r['identity_match_basis']=='connectivity_first_block' and r['full_requested_panel_complete']=='False' for r in rows)
assert all(int(r['available_panel_pair_count'])==7 for r in rows)
assert sum(r['octanol_validation_available']=='True' for r in rows)==m['octanol_validation_available']
for r in rows:
 for k,v in r.items():
  if k.startswith('log10_K_') and v:assert math.isfinite(float(v))
 assert sum(bool(v) for k,v in r.items() if k.startswith('log10_K_concentration__'))==7
 assert bool(r['logKow_octanol_validation'])==(r['octanol_validation_available']=='True')
assert len(rows)+len(m['excluded'])==m['ledger_candidates']
assert hashlib.sha256((ROOT/receipt['preserved_previous_csv']).read_bytes()).hexdigest()==receipt['previous_csv_sha256']
a={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'local_csv':str(local),'bulk_snapshot':str(bulk),'rows':len(rows),'columns':len(rows[0]),'octanol_available':m['octanol_validation_available'],'excluded':m['excluded'],'csv_sha256':m['csv_sha256'],'status':'verified','prior_csv_unchanged':True,'full_panel_complete':0}
(ROOT/'state/completed-export-refresh-verified-20260917T0745.json').write_text(json.dumps(a,indent=2)+'\n')
print(json.dumps(a),flush=True)
