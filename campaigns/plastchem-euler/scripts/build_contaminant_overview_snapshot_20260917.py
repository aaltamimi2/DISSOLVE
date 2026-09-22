"""Freeze existing predictions for overview only; no calculation, submission or product writes."""
import json,csv,hashlib,datetime,concurrent.futures,math
from pathlib import Path
R=Path('/home/aaltamimi2/plastchem-euler');B=Path('/mnt/r/plastchem-euler');D=B/'progress-2026-09-17/contaminant-overview-draft-v1';D.mkdir(parents=True,exist_ok=False)
sha=lambda b:hashlib.sha256(b).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2)+'\n')
started=datetime.datetime.now(datetime.timezone.utc).isoformat();ledger=json.loads((R/'state/thermodynamics-v1/processing-ledger.json').read_text());availability=json.loads((B/'common69-pilot-20260917/availability.json').read_text());common=[r['common_key'] for r in availability['rows']];mapping={r['panel_key']:r['common_key'] for r in availability['rows'] if r.get('panel_key')};mapping['acetic acid']='acetic acid'
records={p.stem:json.loads(p.read_text()) for p in (R/'state/campaign-v1/records').glob('*.json')};accepted={k:r for k,r in records.items() if r['status']=='converged'};assert len(accepted)==5803
save(D/'frozen-ledger.json',ledger);save(D/'availability.json',availability);out=[];issues=[]
def panel_task(item):
 k,v=item;raw=Path(v['result_path']).read_bytes()
 if sha(raw)!=v['result_sha256']:return [],{'inchikey':k,'reason':'Result changed since ledger snapshot; omitted rather than mixed'}
 r=json.loads(raw);assert r['input_inchikey']==k and k in accepted and r['solute_surface_sha256']==accepted[k]['surface_sha256']
 p=D/'snapshot/panel'/f'{k}.json';p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes(raw)
 rows=[]
 for x in r['partitions_against_water']:
  if x['status']!='predicted':continue
  solvent=mapping[x['solvent']];value=x['log10_K_mole_fraction'];assert math.isfinite(value)
  rows.append({'input_inchikey':k,'solvent':solvent,'temperature_K':x['temperature_K'],'log10_K_mole_fraction':value,'log10_K_concentration':x.get('log10_K_concentration'),'source':'production_panel','audit_status':'solver_and_dilution_passed; batch_audit_coverage_varies','source_path':str(p),'source_sha256':sha(raw)})
 return rows,None
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 for rows,issue in pool.map(panel_task,ledger.items()):out.extend(rows);issues.extend([issue] if issue else [])
# Pin octanol candidates from existing immutable audits, then include current completed final-cohort files.
lookup={}
for r in csv.DictReader((B/'measured-expansion-2026-09-15/all-predictions.csv').open()):lookup[r['input_inchikey']]=(Path(r['result_path']),r['result_sha256'],'prior_numerical_audit_passed')
paths=list(B.glob('post*-octanol-2026-09-15/numerical-audit.json'))
for folder in ['octanol-catchup-2026-09-17','octanol-followup-2026-09-17','octanol-post3883-2026-09-17','octanol-post4060-2026-09-17','octanol-post4172-2026-09-17','octanol-post4234-2026-09-17','octanol-post4244-2026-09-17','octanol-post4261-2026-09-17','octanol-post4353-2026-09-17','octanol-post4455-2026-09-17']:
 paths.append(B/folder/'numerical-audit.json')
for p in paths:
 a=json.loads(p.read_text());assert a['failed']==0
 for r in a['rows']:
  if r['status']=='passed':lookup[r['inchikey']]=(p.parent/'octanol'/f"{r['inchikey']}.json",r['result_sha256'],'prior_numerical_audit_passed')
final=B/'octanol-final1171-2026-09-17';finalpaths=sorted((final/'octanol').glob('*.json'));final_audit=json.loads((final/'numerical-audit.json').read_text()) if (final/'numerical-audit.json').exists() else None
for p in finalpaths:
 raw=p.read_bytes();k=p.stem;assert k not in lookup;lookup[k]=(p,sha(raw),'numerical_audit_passed' if final_audit and final_audit['failed']==0 else 'solver_and_dilution_passed; full_audit_pending')
def oct_task(item):
 k,(p,h,status)=item;raw=p.read_bytes();assert sha(raw)==h;r=json.loads(raw);assert k in accepted and r['solute_surface_sha256']==accepted[k]['surface_sha256'];pred=r['prediction']
 if pred['status']!='predicted':return None
 dst=D/'snapshot/octanol'/f'{k}.json';dst.parent.mkdir(exist_ok=True,parents=True);dst.write_bytes(raw)
 return {'input_inchikey':k,'solvent':'1-octanol','temperature_K':pred['temperature_K'],'log10_K_mole_fraction':pred['log10_K_mole_fraction'],'log10_K_concentration':pred['log10_K_concentration'],'source':'validation_only_octanol','audit_status':status,'source_path':str(dst),'source_sha256':h}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:out.extend(x for x in pool.map(oct_task,lookup.items()) if x)
assert len({(r['input_inchikey'],r['solvent'],r['temperature_K']) for r in out})==len(out)
assert set(r['temperature_K'] for r in out)=={298.15}
def csvwrite(name,rows):
 with (D/name).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
csvwrite('predictions.csv',out)
meta=[]
for k,r in accepted.items():
 inp=r['input'];n=inp['atoms'];bin_index=0 if n<=20 else min(7,(n-1)//10-1)
 meta.append({'input_inchikey':k,'name':inp['name'],'smiles':inp['smiles'],'cas':inp['cas'],'atoms_including_hydrogen':n,'size_bin':bin_index,'perceived_inchikey':r['perceived_inchikey'],'surface_sha256':r['surface_sha256']})
meta.sort(key=lambda r:(r['size_bin'],r['atoms_including_hydrogen'],r['input_inchikey']));csvwrite('contaminant-rows.csv',meta);csvwrite('solvent-columns.csv',[{'column_1_based':i+1,'solvent':s,'scope':'common' if s in common else 'existing_panel_extra'} for i,s in enumerate(common+['acetic acid'])])
summary={'snapshot_started_utc':started,'snapshot_finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'eligible':5824,'orca_accepted':5803,'orca_failed':21,'rows_in_figure':len(meta),'solvent_columns':70,'common_solvents':69,'additional_existing_panel_solvent':'acetic acid','temperature_K':[298.15],'displayed_predictions':len(out),'production_panel_predictions':sum(r['source']=='production_panel' for r in out),'octanol_predictions':sum(r['source']=='validation_only_octanol' for r in out),'molecules_with_predictions':len({r['input_inchikey'] for r in out}),'missing_cells':5803*70-len(out),'snapshot_issues':issues,'scope':'Draft computed results, not a product-serving claim. Snapshot panel captured at start; octanol completion list captured after panel read; exact interval retained. No interpolation.'};save(D/'summary.json',summary);print(json.dumps(summary),flush=True)
