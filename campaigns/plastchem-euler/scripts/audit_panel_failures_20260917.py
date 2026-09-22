"""Check recorded dilution failures and missing partition values, without rerunning chemistry."""
from pathlib import Path
import json,hashlib,math,datetime,csv
ROOT=Path(__file__).resolve().parents[1];stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');D=Path('/mnt/r/plastchem-euler/audits')/('panel-failures-'+stamp);D.mkdir(exist_ok=False)
raw=(ROOT/'state/thermodynamics-v1/processing-ledger.json').read_bytes();ledger=json.loads(raw);(D/'processing-ledger.json').write_bytes(raw);rows=[];count=0
for key,item in ledger.items():
 if not item.get('failed_activity_count'):continue
 payload=Path(item['result_path']).read_bytes();assert hashlib.sha256(payload).hexdigest()==item['result_sha256']
 d=json.loads(payload);assert d['input_inchikey']==key
 bad={s:a for s,a in d['activities'].items() if a['status']!='converged'};assert len(bad)==item['failed_activity_count'];count+=1
 (D/(key+'.json')).write_bytes(payload)
 for solvent,a in bad.items():
  assert a['status']=='dilution_not_converged' and 'ln_gamma' not in a
  assert [s['solute_fraction'] for s in a['samples']]==[1e-5,1e-6,1e-7,1e-8]
  delta=abs(a['samples'][-1]['ln_gamma']-a['samples'][-2]['ln_gamma'])/math.log(10)
  assert delta>.005 and abs(delta-a['last_log10_dilution_shift'])<1e-12
  affected=[p for p in d['partitions_against_water'] if p['solvent']==solvent or solvent=='water']
  assert affected
  for p in affected:assert p['status']=='not_available' and 'log10_K_concentration' not in p and 'log10_K_mole_fraction' not in p
  rows.append({'inchikey':key,'name':d['name'],'solvent':solvent,'mode':a['status'],'last_log10_shift':delta,'tolerance':.005,'affected_partition_count':len(affected),'result_sha256':item['result_sha256']})
assert len(rows)==sum(v.get('failed_activity_count',0) for v in ledger.values())
with (D/'failures.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'panel_molecule_denominator':len(ledger),'recorded_activity_denominator':sum(v.get('activity_count',0)+v.get('failed_activity_count',0) for v in ledger.values()),'failed_activities':len(rows),'affected_molecules':count,'all_failure_modes':'dilution_not_converged','missingness_verified':True,'scope':'Audits recorded failure semantics and missing partition values; no retries, substituted values, or chemistry changes.'}
(D/'summary.json').write_text(json.dumps(s,indent=2)+'\n');(D/'REPORT.md').write_text('# Panel activity failure audit\n\n'+json.dumps(s,indent=2)+'\n\nEach failed activity lacks a final ln_gamma and each affected water-reference partition lacks numeric partition values. The last dilution shift was recalculated from the stored samples and exceeded the frozen 0.005 criterion. Other successfully computed solvent pairs are preserved. These are post-processing dilution failures, separate from ORCA campaign failures.\n')
(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes());(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
(ROOT/'state/thermodynamics-v1/panel-failures-audit-latest.json').write_text(json.dumps({'directory':str(D),**s},indent=2)+'\n');print(json.dumps({'directory':str(D),**s}))
