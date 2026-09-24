"""Recover any live-snapshot races explicitly before rendering; no new calculation."""
import json,csv,hashlib,datetime
from pathlib import Path
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/progress-2026-09-17/contaminant-overview-draft-v1');s=json.loads((D/'summary.json').read_text());pred=list(csv.DictReader((D/'predictions.csv').open()));ledger=json.loads((D/'frozen-ledger.json').read_text());live=json.loads((R/'state/thermodynamics-v1/processing-ledger.json').read_text());availability=json.loads((D/'availability.json').read_text());mapping={r['panel_key']:r['common_key'] for r in availability['rows'] if r.get('panel_key')};mapping['acetic acid']='acetic acid';recoveries=[]
for issue in s['snapshot_issues']:
 k=issue['inchikey'];entry=live[k];raw=Path(entry['result_path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==entry['result_sha256'];r=json.loads(raw);dst=D/'snapshot/panel'/f'{k}.json';dst.write_bytes(raw);assert hashlib.sha256(dst.read_bytes()).hexdigest()==entry['result_sha256'];ledger[k]=entry
 for x in r['partitions_against_water']:
  if x['status']=='predicted':pred.append({'input_inchikey':k,'solvent':mapping[x['solvent']],'temperature_K':x['temperature_K'],'log10_K_mole_fraction':x['log10_K_mole_fraction'],'log10_K_concentration':x.get('log10_K_concentration'),'source':'production_panel','audit_status':'solver_and_dilution_passed; batch_audit_coverage_varies','source_path':str(dst),'source_sha256':entry['result_sha256']})
 recoveries.append({'inchikey':k,'action':'Adopted newer hash-verified complete record within explicit snapshot interval','result_sha256':entry['result_sha256']})
assert len({(r['input_inchikey'],r['solvent']) for r in pred})==len(pred)
with (D/'predictions.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(pred[0]));w.writeheader();w.writerows(pred)
(D/'frozen-ledger.json').write_text(json.dumps(ledger,indent=2)+'\n')
s.update(snapshot_finalized_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),snapshot_race_recoveries=recoveries,displayed_predictions=len(pred),production_panel_predictions=sum(r['source']=='production_panel' for r in pred),octanol_predictions=sum(r['source']=='validation_only_octanol' for r in pred),molecules_with_predictions=len({r['input_inchikey'] for r in pred}),missing_cells=5803*70-len(pred));(D/'summary.json').write_text(json.dumps(s,indent=2)+'\n');print(json.dumps(s),flush=True)
