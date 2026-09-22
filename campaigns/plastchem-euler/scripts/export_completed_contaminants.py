"""Snapshot accepted ORCA + successful available-solvent openCOSMO results, one molecule per row."""
from pathlib import Path
import collections, csv, datetime, hashlib, json, math, re, shutil
ROOT=Path(__file__).resolve().parents[1];BULK=Path('/mnt/r/plastchem-euler')
now=datetime.datetime.now(datetime.timezone.utc);stamp=now.strftime('%Y%m%dT%H%M%SZ')
D=BULK/'exports'/('completed-'+stamp);D.mkdir(parents=True,exist_ok=False)
def sha(raw):return hashlib.sha256(raw).hexdigest()
ledger_raw=(ROOT/'state/thermodynamics-v1/processing-ledger.json').read_bytes();ledger=json.loads(ledger_raw)
registry_raw=(ROOT/'state/thermodynamics-v1/library-registry.json').read_bytes();registry=json.loads(registry_raw)
solvents=[s['solvent_key'] for s in registry['solvents'] if s['solvent_key']!='water']
ready={s['solvent_key']:s['surface_sha256'] for s in registry['solvents'] if s['status']=='ready'}
assert 'water' in ready and len(solvents)==32
(D/'processing-ledger.json').write_bytes(ledger_raw);(D/'library-registry.json').write_bytes(registry_raw)
aliases=collections.defaultdict(list)
with (ROOT/'inputs/plastchem_organics_orca_opencosmo_firstpass.csv').open() as f:
 for r in csv.DictReader(f):aliases[r['inchikey']].append(r)
octanol={}
for r in csv.DictReader((BULK/'measured-expansion-2026-09-15/all-predictions.csv').open()):
 octanol[r['input_inchikey']]={'value':r['predicted_logKow'],'path':r['result_path'],'sha256':r['result_sha256']}
audit_paths=list(BULK.glob('post*-octanol-2026-09-15/numerical-audit.json'))
audit_paths += [BULK/folder/'numerical-audit.json' for folder in ['octanol-catchup-2026-09-17','octanol-followup-2026-09-17','octanol-post3883-2026-09-17','octanol-post4060-2026-09-17','octanol-post4172-2026-09-17','octanol-post4234-2026-09-17','octanol-post4244-2026-09-17','octanol-post4261-2026-09-17','octanol-post4353-2026-09-17','octanol-post4455-2026-09-17'] if (BULK/folder/'numerical-audit.json').exists()]
for p in sorted(audit_paths):
 audit=json.loads(p.read_text());assert audit['failed']==0,'Unresolved octanol audit failure'
 passed={r['inchikey']:r for r in audit['rows'] if r['status']=='passed'}
 for r in csv.DictReader((p.parent/'octanol.csv').open()):
  k=r['input_inchikey']
  if r['status']=='predicted' and k in passed:
   assert k not in octanol,'Duplicate octanol cohorts'
   octanol[k]={'value':r['logKow'],'path':str(p.parent/'octanol'/(k+'.json')),'sha256':passed[k]['result_sha256']}
def joined(records,field):return ' | '.join(sorted({str(r.get(field,'')) for r in records if r.get(field)}))
base=['name','smiles','inchikey','cas','plastchem_id','molecular_weight_g_mol','atoms','heavy_atoms',
      'all_source_names','all_source_cas','all_source_plastchem_ids','orca_status','opencosmo_status',
      'temperature_K','parameterization','perceived_inchikey','identity_match_basis','perceived_keys_by_engine',
      'cpu_model','available_panel_pair_count','requested_panel_pair_count','full_requested_panel_complete',
      'available_panel_solvents','missing_panel_solvents','octanol_validation_available','logKow_octanol_validation',
      'octanol_result_path','octanol_result_sha256','panel_result_path','panel_result_sha256',
      'solute_surface_path','solute_surface_sha256','source_record_sha256','snapshot_utc']
columns={s:re.sub('[^a-z0-9]+','_',s.lower()).strip('_') for s in solvents};assert len(set(columns.values()))==32
fields=base+[f'{basis}__{columns[s]}_over_water' for s in solvents for basis in ['log10_K_mole_fraction','log10_K_concentration']]
rows=[];excluded=[]
for index,(key,item) in enumerate(sorted(ledger.items()),1):
 try:
  src=(ROOT/'state/campaign-v1/records'/(key+'.json')).read_bytes();r=json.loads(src)
  assert r['status']=='converged' and r['identity_verified'] and r['connectivity_match'],'ORCA or identity not accepted'
  raw=Path(item['result_path']).read_bytes();assert sha(raw)==item['result_sha256'],'Result changed after ledger snapshot'
  result=json.loads(raw);assert result['input_inchikey']==key and result['solute_surface_sha256']==r['surface_sha256']
  assert result['perceived_inchikey']==r['perceived_inchikey'] and result['identity_match_basis']=='connectivity_first_block'
  assert item['failed_activity_count']==0 and all(a['status']=='converged' for a in result['activities'].values()),'Activity failure'
  assert all(s in result['activities'] and result['activities'][s]['solvent_surface_sha256']==h for s,h in ready.items()),'Available library not fully processed'
  pairs={p['solvent']:p for p in result['partitions_against_water']};predicted={s:p for s,p in pairs.items() if p['status']=='predicted'}
  assert set(predicted)==set(ready)-{'water'} and predicted,'No complete available-solvent predictions'
  assert len(predicted)==item['partition_count']==result['available_partition_count']
  inp=r['input'];row={f:inp.get(f,'') for f in base[:8]};row['inchikey']=key
  row.update(all_source_names=joined(aliases[key],'name'),all_source_cas=joined(aliases[key],'cas'),
             all_source_plastchem_ids=joined(aliases[key],'plastchem_id'),orca_status='converged_identity_accepted',
             opencosmo_status='successful_for_all_available_panel_solvents',temperature_K=result['config']['temperature_K'],
             parameterization=result['config']['parameterization'],perceived_inchikey=r['perceived_inchikey'],
             identity_match_basis=r['identity_match_basis'],perceived_keys_by_engine=json.dumps(r['perceived_keys_by_engine'],sort_keys=True),
             cpu_model=r['cpu_model'],available_panel_pair_count=len(predicted),requested_panel_pair_count=32,
             full_requested_panel_complete=len(predicted)==32,available_panel_solvents=' | '.join(sorted(predicted)),
             missing_panel_solvents=' | '.join(s for s in solvents if s not in predicted),
             octanol_validation_available=False,panel_result_path=item['result_path'],panel_result_sha256=item['result_sha256'],
             solute_surface_path=str(Path(r['archive_path'])/'surface.orcacosmo'),solute_surface_sha256=r['surface_sha256'],
             source_record_sha256=sha(src),snapshot_utc=now.isoformat())
  for s,pair in predicted.items():
   for basis in ['log10_K_mole_fraction','log10_K_concentration']:
    value=pair.get(basis)
    if value is not None:assert math.isfinite(value);row[f'{basis}__{columns[s]}_over_water']=value
  if key in octanol:
   o=octanol[key];assert sha(Path(o['path']).read_bytes())==o['sha256'],'Octanol result digest mismatch'
   value=float(o['value']);assert math.isfinite(value)
   row.update(octanol_validation_available=True,logKow_octanol_validation=value,octanol_result_path=o['path'],octanol_result_sha256=o['sha256'])
  rows.append(row)
 except Exception as exc:excluded.append({'inchikey':key,'reason':str(exc)})
 if index%100==0:print(json.dumps({'checked':index,'eligible':len(rows),'excluded':len(excluded)}),flush=True)
output=D/'completed-contaminants.csv'
with output.open('w',newline='',encoding='utf-8') as f:
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
assert len(rows)==len({r['inchikey'] for r in rows})
summary={'snapshot_utc':now.isoformat(),'campaign_denominator':5824,'ledger_candidates':len(ledger),'exported_molecules':len(rows),
 'panel_predictions':sum(r['available_panel_pair_count'] for r in rows),'octanol_validation_available':sum(r['octanol_validation_available'] for r in rows),
 'full_requested_panel_complete':sum(r['full_requested_panel_complete'] for r in rows),'excluded':excluded,'csv_sha256':sha(output.read_bytes()),
 'ledger_sha256':sha(ledger_raw),'registry_sha256':sha(registry_raw),'script_sha256':sha(Path(__file__).read_bytes()),
 'column_solvent_mapping':columns,'scope':'Accepted ORCA and successful openCOSMO for all currently available panel solvents; not full requested-panel completion.',
 'missing_values':'Blank means unavailable, never zero or interpolated. Octanol is validation-only and included when an audited result is available.'}
(D/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
(D/'export_completed_contaminants.py').write_bytes(Path(__file__).read_bytes())
local=ROOT/('completed-contaminants-'+now.strftime('%Y-%m-%dT%H%M%SZ')+'.csv');shutil.copyfile(output,local);assert sha(local.read_bytes())==summary['csv_sha256']
(ROOT/(local.stem+'.metadata.json')).write_text(json.dumps({**summary,'bulk_snapshot':str(D)},indent=2)+'\n')
print(json.dumps({**{k:v for k,v in summary.items() if k not in ['column_solvent_mapping','excluded']},'excluded_count':len(excluded),'local_csv':str(local),'bulk_snapshot':str(D)},indent=2),flush=True)
