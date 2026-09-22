"""Build a metadata index across immutable result batches; no recalculation or attribution changes."""
from pathlib import Path
import csv, datetime, hashlib, json
ROOT=Path(__file__).resolve().parents[1];R=Path('/mnt/r/plastchem-euler')
D=R/'cumulative-result-index';D.mkdir(exist_ok=True)
def read(p):return json.loads(p.read_text())
ledger=read(ROOT/'state/thermodynamics-v1/processing-ledger.json')
surface={};numeric={}
for name,dest in [('completed-surface-audit.json',surface),('production-record-audit.json',numeric)]:
 paths=[ROOT/'state/thermodynamics-v1'/name]
 paths+=sorted((R/'audits').glob('*/'+name)) if dest is surface else sorted((R/'audits').rglob(name))
 for p in paths:
  if p.exists():
   audit=read(p);pinned=p.parent/'processing-ledger.json';pinned_ledger=read(pinned) if dest is numeric and str(p).startswith(str(R/'audits')) and pinned.exists() else {}
   for row in audit['rows']:
    if row['status']=='passed':
     key=row['inchikey']
     candidate={'path':str(p),'utc':audit['utc'],'sha':row.get('surface_sha256','')}
     if dest is numeric:candidate['sha']=pinned_ledger.get(key,{}).get('result_sha256','')
     if key not in dest or candidate['utc']>dest[key]['utc']:dest[key]=candidate
octanol={}
for row in csv.DictReader((R/'measured-expansion-2026-09-15/all-predictions.csv').open()):
 octanol[row['input_inchikey']]={'value':row['predicted_logKow'],'path':row['result_path'],'sha':row['result_sha256'],'audit':'/mnt/r/plastchem-euler/measured-expansion-2026-09-15'}
octanol_audits=list(R.glob('post*-octanol-2026-09-15/numerical-audit.json'))
for folder in ['octanol-catchup-2026-09-17','octanol-followup-2026-09-17','octanol-post3883-2026-09-17','octanol-post4060-2026-09-17','octanol-post4172-2026-09-17','octanol-post4234-2026-09-17','octanol-post4244-2026-09-17','octanol-post4261-2026-09-17','octanol-post4353-2026-09-17','octanol-post4455-2026-09-17','octanol-final1171-2026-09-17']:
 new_audit=R/folder/'numerical-audit.json'
 if new_audit.exists():
  candidate=read(new_audit)
  assert candidate['failed']==0, 'Do not publish a cohort with unresolved audit failures'
  octanol_audits.append(new_audit)
for p in sorted(octanol_audits):
 passed={r['inchikey']:r for r in read(p)['rows'] if r['status']=='passed'}
 for row in csv.DictReader((p.parent/'octanol.csv').open()):
  k=row['input_inchikey']
  if row['status']!='predicted' or k not in passed:continue
  assert k not in octanol,'Duplicate octanol attribution across cohorts'
  octanol[k]={'value':row['logKow'],'path':str(p.parent/'octanol'/(k+'.json')),'sha':passed[k]['result_sha256'],'audit':str(p)}
reference_path=R/'combined-validation-references-2026-09-17-pubchem-final571/experimental-reference-candidates.csv'
if not reference_path.exists():reference_path=R/'combined-validation-references-2026-09-17-opera-refresh/experimental-reference-candidates.csv'
if not reference_path.exists():reference_path=R/'combined-validation-references-2026-09-17-comptox-later/experimental-reference-candidates.csv'
if not reference_path.exists():reference_path=R/'combined-validation-references-2026-09-17/experimental-reference-candidates.csv'
measured={r['input_inchikey']:r for r in csv.DictReader(reference_path.open())}
snap=read(ROOT/'state/campaign-v1/latest-snapshot.json')
running_tasks={line.split('|')[0] for line in snap['squeue'].splitlines() if '|RUNNING|' in line}
inputs={};running_keys=set()
for group in ['main_le80','tail_gt80']:
 for item in read(ROOT/f'state/campaign-v1/{group}/manifest.json')['molecules']:
  inputs[item['inchikey']]=item
  for chunk,indices in snap['array_indices'].items():
   if (chunk=='tail_gt80') != (group=='tail_gt80'):continue
   if item['array_index'] in indices and f"{snap['groups'][chunk]}_{item['array_index']}" in running_tasks:running_keys.add(item['inchikey'])
records={p.stem:p for p in (ROOT/'state/campaign-v1/records').glob('*.json')}
rows=[]
for k in sorted(set(inputs)|set(records)):
 p=records.get(k)
 record_raw=p.read_bytes() if p else None
 r=json.loads(record_raw) if p else {'input':inputs[k],'status':'running' if k in running_keys else 'not_yet_run'}
 l=ledger.get(k,{});o=octanol.get(k,{});m=measured.get(k,{})
 if o or l:assert r['status']=='converged','Prediction attributed to unaccepted result'
 rows.append({'input_inchikey':k,'name':r.get('input',{}).get('name',''),'campaign_status':r.get('status',''),
  'input_record_path':str(p) if p else '', 'input_record_sha256':hashlib.sha256(record_raw).hexdigest() if p else '',
  'perceived_inchikey':r.get('perceived_inchikey',''),'identity_match_basis':r.get('identity_match_basis',''),
  'cpu_model':r.get('cpu_model',''),'failure_mode':r.get('failure_mode',''),
  'panel_prediction_count':l.get('partition_count',0),'panel_requested':32,
  'full_panel_complete':l.get('complete',False),'panel_result_path':l.get('result_path',''),
  'panel_result_sha256':l.get('result_sha256',''),'surface_prior_pass_audit':surface.get(k,{}).get('path',''),
  'surface_audit_matches_recorded_surface_hash':bool(surface.get(k,{}).get('sha')) and surface[k]['sha']==r.get('surface_sha256'),
  'panel_prior_pass_audit':numeric.get(k,{}).get('path',''),
  'panel_audit_matches_current_result_hash':bool(numeric.get(k,{}).get('sha')) and numeric[k]['sha']==l.get('result_sha256'),'octanol_logKow':o.get('value',''),
  'octanol_result_path':o.get('path',''),'octanol_result_sha256':o.get('sha',''),
  'octanol_audit_evidence':o.get('audit',''),'selected_measured_logKow':m.get('measured_logKow',''),
  'measurement_citation':m.get('raw_reference_string',''),'measurement_source_url':m.get('source_url','')})
assert len(rows)==5824
with (D/'results-index.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'denominator':len(rows),
 'converged':sum(r['campaign_status']=='converged' for r in rows),'panel_processed':len(ledger),
 'panel_predictions':sum(r['panel_prediction_count'] for r in rows),'octanol_audited':len(octanol),
 'selected_measured':len(measured),'selected_reference_path':str(reference_path),'selected_reference_sha256':hashlib.sha256(reference_path.read_bytes()).hexdigest(),'surface_hash_bound_audit_count':sum(r['surface_audit_matches_recorded_surface_hash'] for r in rows),'panel_hash_bound_audit_count':sum(r['panel_audit_matches_current_result_hash'] for r in rows),'full_panel_complete':sum(r['full_panel_complete'] for r in rows),
 'scope':'Metadata index referencing prior audited records. Does not freshly rehash every referenced bulk result or replace frozen packages.'}
(D/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
(D/'REPORT.md').write_text('# Cumulative result index\n\n'
 f"Updated {summary['utc']}. Campaign denominator: {len(rows)}; converged: {summary['converged']}. "
 f"Panel processed: {len(ledger)}; panel predictions: {summary['panel_predictions']}; "
 f"audited octanol predictions: {len(octanol)}. Full-panel complete: {summary['full_panel_complete']}.\n\n"
 f"Selected measured references: {len(measured)}; this is reference coverage, not the paired accuracy sample size. "
 f"Surface audits matching recorded surface hashes: {summary['surface_hash_bound_audit_count']}; "
 f"numerical audits matching current panel-result hashes: {summary['panel_hash_bound_audit_count']}.\n\n"
 'This metadata index links results and audit evidence; it does not freshly rehash all referenced bulk files. '
 'Live campaign records and the processing ledger are read separately, so this is not an atomic campaign freeze. '
 'Released and batch-2 reports remain separate and unchanged. Xylene identity and the didecyl-phthalate '
 'experimental reference remain owner questions. Missing predictions are not interpolated.\n\n'
 f'Reproduce with `{ROOT}/scripts/index_completed_results.py`.\n')
(D/'index_completed_results.py').write_bytes(Path(__file__).read_bytes())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
 for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
