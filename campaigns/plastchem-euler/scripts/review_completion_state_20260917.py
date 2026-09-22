"""Current requirement review; historical evidence is retained without claiming completion."""
from pathlib import Path
import json,hashlib,datetime,collections
ROOT=Path(__file__).resolve().parents[1];R=Path('/mnt/r/plastchem-euler')
sources={}
def read(p):
 raw=p.read_bytes();sources[str(p)]=hashlib.sha256(raw).hexdigest();return json.loads(raw)
s=read(ROOT/'state/campaign-v1/summary.json');l=read(ROOT/'state/thermodynamics-v1/processing-ledger.json');lib=read(ROOT/'state/thermodynamics-v1/library-registry.json');vol=read(ROOT/'state/thermodynamics-v1/density-coverage.json');val=read(R/'validation-final5803-2026-09-17/statistics.json')
surface_keys=set();numeric_keys=set()
records={p.stem:read(p) for p in (ROOT/'state/campaign-v1/records').glob('*.json')}
for audit_path in sorted((R/'audits').glob('*/completed-surface-audit.json')):
 d=audit_path.parent;a=read(audit_path);assert a['failed']==0
 surface_keys.update(r['inchikey'] for r in a['rows'] if r['status']=='passed' and r.get('surface_sha256') and records.get(r['inchikey'],{}).get('surface_sha256')==r['surface_sha256'])
for numeric_path in sorted((R/'audits').rglob('production-record-audit.json')):
 a=read(numeric_path);assert a['failed']==0
 frozen=read(numeric_path.parent/'processing-ledger.json')
 passed={r['inchikey'] for r in a['rows'] if r['status']=='passed'}
 numeric_keys.update(k for k,v in frozen.items() if k in passed and l.get(k,{}).get('result_sha256')==v['result_sha256'])
review={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'goal_status':'active_not_complete','campaign':s['counts'],'solvent_states':dict(collections.Counter(x['status'] for x in lib['solvents'])),'panel':{'processed':len(l),'predictions':sum(v.get('partition_count',0) for v in l.values()),'failed_activities':sum(v.get('failed_activity_count',0) for v in l.values()),'full_panel':sum(bool(v.get('complete')) for v in l.values())},'passed_audit_coverage':{'surface_keys':len(surface_keys),'current_numeric_hash_matches':len(numeric_keys),'coverage_note':'Surface evidence includes all archived completed-surface audits; numerical coverage counts only audited hashes matching the current ledger snapshot'},'external_accuracy':{k:val[k] for k in ['n','MAE','RMSE','bias','slope','intercept','unmatched_reference_count','no_recalibration']},'completed_requirements':{'terminal_campaign':s['counts']['converged']+s['counts']['failed']==5824 and s['counts']['running']==0 and s['counts']['not_yet_run']==0,'surface_coverage':len(surface_keys)==s['counts']['converged']},'remaining_requirements':['Xylene owner identity decision outstanding; all 32 resolved solvent references available','All completed contaminants evaluated against the resolved solvent panel, with explicit unavailable results and failures','Current numerical audits cover every final result hash','Final aggregate tables, plots, manifests and report verified'], 'volume_coverage':vol,'owner_questions':['xylene identity','didecyl phthalate measured-reference discrepancy'],'limits':['Audit evidence applies to recorded snapshots, not unexamined newer returns','Experimental sample is selected by available qualified measurements; it does not establish universal accuracy','31 documented volumes out of 33; missing corrections must remain unavailable','Released 648 and fixed cumulative batch-2 944 packages remain unchanged'],'source_sha256':sources}
p=ROOT/'state/thermodynamics-v1/completion-requirements.json';old=p.read_bytes();backup=p.with_name('completion-requirements-historical-20260914.json')
if not backup.exists():backup.write_bytes(old)
d=json.loads(old);d['historical_requirements_note']='Original per-requirement snapshots below are historical. See current_review for the latest checked scope; no completion is claimed.';d['current_review']=review;d['last_review_utc']=review['utc'];p.write_text(json.dumps(d,indent=2)+'\n')
(ROOT/'state/thermodynamics-v1/completion-review-latest.json').write_text(json.dumps(review,indent=2)+'\n')
print(json.dumps({k:v for k,v in review.items() if k not in ['source_sha256','remaining_requirements','limits']}))
