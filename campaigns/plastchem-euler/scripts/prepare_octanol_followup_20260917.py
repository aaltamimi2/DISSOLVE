"""Pin the next disjoint cohort without starting a second scientific worker."""
from pathlib import Path
import json,csv,hashlib,datetime
ROOT=Path(__file__).resolve().parents[1];R=Path('/mnt/r/plastchem-euler');D=R/'octanol-followup-2026-09-17'
assert not D.exists()
prior={r['input_inchikey'] for r in csv.DictReader((R/'measured-expansion-2026-09-15/all-predictions.csv').open())}
for p in [*R.glob('post*-octanol-2026-09-15/manifest.json'),R/'octanol-catchup-2026-09-17/manifest.json']:
 prior.update(m['inchikey'] for m in json.loads(p.read_text())['cohort'])
assert len(prior)==2851
raw=(ROOT/'state/thermodynamics-v1/processing-ledger.json').read_bytes();ledger=json.loads(raw)
measured={r['input_inchikey'] for r in csv.DictReader((R/'combined-validation-references-2026-09-17/experimental-reference-candidates.csv').open())}
cohort=[]
for k in sorted(set(ledger)-prior,key=lambda k:(k not in measured,k)):
 r=json.loads((ROOT/f'state/campaign-v1/records/{k}.json').read_text());assert r['status']=='converged' and r['connectivity_match']
 assert r['surface_sha256']==ledger[k]['solute_surface_sha256']
 cohort.append({'inchikey':k,'surface_sha256':r['surface_sha256'],'archive_path':r['archive_path']})
assert len({m['inchikey'] for m in cohort})==len(cohort) and not ({m['inchikey'] for m in cohort}&prior)
D.mkdir();(D/'preparation-ledger.json').write_bytes(raw)
m={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cohort':cohort,'denominator':len(cohort),'prior_octanol_excluded':len(prior),'matched_selected_reference_count':sum(m['inchikey'] in measured for m in cohort),'preparation_ledger_sha256':hashlib.sha256(raw).hexdigest(),'scope':'Disjoint follow-up accepted panel-processed cohort. Validation-only octanol; no panel change; not yet calculated.'}
(D/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes());print(json.dumps({k:v for k,v in m.items() if k!='cohort'}))
(ROOT/'state/octanol-followup-prepared-20260917.json').write_text(json.dumps({'directory':str(D),'manifest_sha256':hashlib.sha256((D/'manifest.json').read_bytes()).hexdigest(),**{k:v for k,v in m.items() if k!='cohort'}},indent=2)+'\n')
