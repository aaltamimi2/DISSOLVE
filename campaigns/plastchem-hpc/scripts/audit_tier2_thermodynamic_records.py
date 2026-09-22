"""Independent audit of stored production thermodynamic records and ledger claims."""
import argparse,hashlib,json,math,time
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/thermodynamics-v1'
parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true');args=parser.parse_args()
source=ROOT
if args.frozen:
 P=Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14')) / 'sealed-thermodynamics'
 source=Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14')) / 'freeze'
ledger=json.loads((P/'processing-ledger.json').read_text());library=json.loads((P/'library-registry.json').read_text());solvents={r['solvent_key']:r for r in library['solvents']};rows=[]
for key,item in ledger.items():
 row={'inchikey':key}
 try:
  raw=Path(item['result_path']).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=item['result_sha256'] and not args.frozen:
   current=json.loads((P/'processing-ledger.json').read_text()).get(key, {})
   if current.get('result_sha256')!=item['result_sha256']:
    row.update(status='changed_during_live_audit',error='Ledger entry changed after audit snapshot; no integrity conclusion drawn');rows.append(row);continue
  assert hashlib.sha256(raw).hexdigest()==item['result_sha256'],'Stored result hash'
  r=json.loads(raw);source_record=json.loads((source/'state/tier2-v1/records'/(key+'.json')).read_text())
  assert r['input_inchikey']==key and source_record['status']=='converged' and r['solute_surface_sha256']==source_record['surface_sha256']
  assert r['perceived_inchikey']==source_record['perceived_inchikey'] and r['identity_match_basis']=='connectivity_first_block'
  assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor'
  n=0;fail=0;maximum=0
  for name,a in r['activities'].items():
   assert a['solute_surface_sha256']==source_record['surface_sha256'] and a['solvent_surface_sha256']==solvents[name]['surface_sha256']
   assert a['config']==r['config'] and a['config']['parameterization']=='openCOSMORS24a' and a['config']['temperature_K']==298.15 and a['config']['reference_state']=='pure_component'
   if a['status']=='converged':
    samples=a['samples'];assert len(samples)>=2
    assert all(math.isfinite(v['ln_gamma']) and 0<v['solute_fraction']<=1e-5 for v in samples)
    assert all(x['solute_fraction']>y['solute_fraction'] for x,y in zip(samples,samples[1:]))
    assert a['selected_solute_fraction']==samples[-1]['solute_fraction'] and a['ln_gamma']==samples[-1]['ln_gamma']
    shift=abs(samples[-1]['ln_gamma']-samples[-2]['ln_gamma'])/math.log(10)
    assert abs(shift-a['last_log10_dilution_shift'])<1e-12 and shift<=0.005
    maximum=max(maximum,shift);n+=1
   else:assert 'ln_gamma' not in a;fail+=1
  count=0
  for x in r['partitions_against_water']:
   if x['status']=='predicted':
    a=r['activities'][x['solvent']];b=r['activities']['water'];assert a['status']==b['status']=='converged'
    expected=(b['ln_gamma']-a['ln_gamma'])/math.log(10);assert abs(expected-x['log10_K_mole_fraction'])<1e-12
    if x['log10_K_concentration'] is not None:
     corr=math.log10(x['molar_volume_reference_cm3_mol']/x['molar_volume_solvent_cm3_mol']);assert abs(expected+corr-x['log10_K_concentration'])<1e-12
    count+=1
   else:assert 'log10_K_mole_fraction' not in x and 'log10_K_concentration' not in x
  assert len(r['partitions_against_water'])==32 and count==r['available_partition_count']==item['partition_count']
  assert r['partitioning_complete']==item['complete']==(count==32)
  assert n==item['activity_count'] and fail==item['failed_activity_count']
  row.update(status='passed',converged_activities=n,failed_activities=fail,partition_count=count,max_single_log10_dilution_shift=maximum)
 except Exception as exc:row.update(status='failed',error=str(exc))
 rows.append(row)
result={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'snapshot_ledger_entries':len(ledger),'passed':sum(r['status']=='passed' for r in rows),'failed':sum(r['status']=='failed' for r in rows),'changed_during_live_audit':sum(r['status']=='changed_during_live_audit' for r in rows),'rows':rows,'interpretation':'Stored numerical/provenance consistency audit; not experimental validation or full-solvent completion'}
(P/'production-record-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
for row in rows:
 if row['status']=='failed':print(json.dumps(row))
