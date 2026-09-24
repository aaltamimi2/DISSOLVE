"""Explain gray cells using frozen signatures and explicitly stored failure records."""
import json,csv,collections,hashlib,shutil
from pathlib import Path
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/progress-2026-09-17/contaminant-overview-draft-v1');l=json.loads((D/'frozen-ledger.json').read_text());a=json.loads((D/'availability.json').read_text());mapping={r['panel_key']:r['common_key'] for r in a['rows'] if r.get('panel_key')};mapping['acetic acid']='acetic acid';reverse={v:k for k,v in mapping.items()};no_surface={r['common_key'] for r in a['rows'] if not r.get('surface_parser_pass')};pred={(r['input_inchikey'],r['solvent']) for r in csv.DictReader((D/'predictions.csv').open())};keys=[r['input_inchikey'] for r in csv.DictReader((D/'contaminant-rows.csv').open())];cols=[r['solvent'] for r in csv.DictReader((D/'solvent-columns.csv').open())];attempts={};failures={}
for k,v in l.items():
 if v['library_signature'] not in attempts or v.get('failed_activity_count'):
  p=D/'snapshot/panel'/f'{k}.json';raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==v['result_sha256'];record=json.loads(raw);attempts[v['library_signature']]=set(record['activities']);failures[k]={n:x['status'] for n,x in record['activities'].items() if x['status']!='converged'}
counts=collections.Counter()
for k in keys:
 for solvent in cols:
  if (k,solvent) in pred:status='predicted'
  elif solvent in no_surface:status='compatible_surface_not_available'
  elif solvent not in reverse:status='future_grid_not_launched'
  elif k not in l:status='panel_record_not_yet_processed'
  elif reverse[solvent] not in attempts[l[k]['library_signature']]:status='solvent_activity_not_yet_processed'
  elif reverse[solvent] in failures.get(k,{}):status=failures[k][reverse[solvent]]
  elif 'water' in failures.get(k,{}):status='water_reference_failed'
  else:raise AssertionError((k,solvent,'Unexplained missing cell'))
  counts[solvent,status]+=1
rows=[{'solvent':s,'status':status,'cells':n} for (s,status),n in sorted(counts.items())]
with (D/'cell-status-counts.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
total=collections.Counter()
for (_,status),n in counts.items():total[status]+=n
assert sum(total.values())==5803*70 and total['predicted']==70572
(D/'missingness-summary.json').write_text(json.dumps(dict(total),indent=2)+'\n');(D/'code').mkdir(exist_ok=True);shutil.copyfile(__file__,D/'code'/Path(__file__).name);print(dict(total))
