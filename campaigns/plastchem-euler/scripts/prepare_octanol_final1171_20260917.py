"""Pin the next disjoint cohort without starting a second scientific worker."""
from pathlib import Path
import json,csv,hashlib,datetime
ROOT=Path(__file__).resolve().parents[1];R=Path('/mnt/r/plastchem-euler');D=R/'octanol-final1171-2026-09-17'
assert not D.exists()
prior={r['input_inchikey'] for r in csv.DictReader((R/'measured-expansion-2026-09-15/all-predictions.csv').open())}
for p in [*R.glob('post*-octanol-2026-09-15/manifest.json'),R/'octanol-catchup-2026-09-17/manifest.json',R/'octanol-followup-2026-09-17/manifest.json',R/'octanol-post3883-2026-09-17/manifest.json',R/'octanol-post4060-2026-09-17/manifest.json',R/'octanol-post4172-2026-09-17/manifest.json',R/'octanol-post4234-2026-09-17/manifest.json',R/'octanol-post4244-2026-09-17/manifest.json',R/'octanol-post4261-2026-09-17/manifest.json',R/'octanol-post4353-2026-09-17/manifest.json',R/'octanol-post4455-2026-09-17/manifest.json']:
 prior.update(m['inchikey'] for m in json.loads(p.read_text())['cohort'])
assert len(prior)==4632
raw=(ROOT/'state/thermodynamics-v1/processing-ledger.json').read_bytes();ledger=json.loads(raw)
measured={r['input_inchikey'] for r in csv.DictReader((R/'combined-validation-references-2026-09-17-comptox-final1631/experimental-reference-candidates.csv').open())}
cohort=[]
accepted={p.stem for p in (ROOT/'state/campaign-v1/records').glob('*.json') if json.loads(p.read_text()).get('status')=='converged'}
assert len(accepted)==5803
for k in sorted(accepted-prior,key=lambda k:(k not in measured,k)):
 r=json.loads((ROOT/f'state/campaign-v1/records/{k}.json').read_text());assert r['status']=='converged' and r['connectivity_match']
 if k in ledger:assert r['surface_sha256']==ledger[k]['solute_surface_sha256']
 cohort.append({'inchikey':k,'surface_sha256':r['surface_sha256'],'archive_path':r['archive_path']})
assert len({m['inchikey'] for m in cohort})==len(cohort) and not ({m['inchikey'] for m in cohort}&prior)
assert len(cohort)==1171
D.mkdir();(D/'preparation-ledger.json').write_bytes(raw)
m={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cohort':cohort,'denominator':len(cohort),'prior_octanol_excluded':len(prior),'matched_selected_reference_count':sum(m['inchikey'] in measured for m in cohort),'preparation_ledger_sha256':hashlib.sha256(raw).hexdigest(),'scope':'Final disjoint accepted cohort. Every member must have a verified panel water record before serial execution; some remain pending at preparation. Validation-only octanol; no panel change; not yet calculated.'}
(D/'manifest.json').write_text(json.dumps(m,indent=2)+'\n');(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes());print(json.dumps({k:v for k,v in m.items() if k!='cohort'}))
(ROOT/'state/octanol-final1171-prepared-20260917.json').write_text(json.dumps({'directory':str(D),'manifest_sha256':hashlib.sha256((D/'manifest.json').read_bytes()).hexdigest(),**{k:v for k,v in m.items() if k!='cohort'}},indent=2)+'\n')
