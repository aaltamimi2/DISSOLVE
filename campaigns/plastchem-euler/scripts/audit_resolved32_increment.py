"""Seal completed 32-reference records beyond explicitly supplied prior audits."""
import argparse
ap=argparse.ArgumentParser()
ap.add_argument("--output",required=True)
ap.add_argument("--prior",action="append",required=True)
args=ap.parse_args()
import os,json,hashlib,datetime,subprocess,sys,shutil,math
from pathlib import Path
from thermodynamic_prediction import refresh_volumes,CONFIG
from audit_panel_failure_semantics import check as check_failure_semantics
R=Path('/home/aaltamimi2/plastchem-euler');D=Path(args.output).resolve();assert D.parent==Path('/mnt/r/plastchem-euler/audits');D.mkdir(parents=True,exist_ok=False)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2)+'\n')
l=json.loads((R/'state/thermodynamics-v1/processing-ledger.json').read_text());selected={k:v for k,v in l.items() if v['activity_count']+v.get('failed_activity_count',0)==32};sealed={};changed=[]
prior_hashes=set()
for prior_root in args.prior:
 prior_root=Path(prior_root).resolve()
 audit=json.loads((prior_root/'sealed-thermodynamics/production-record-audit.json').read_text())
 prior=json.loads((prior_root/'sealed-thermodynamics/processing-ledger.json').read_text())
 assert audit['failed']==0 and audit['passed']==len(prior)
 for k,v in prior.items():
  assert sha(v['result_path'])==v['result_sha256']
  prior_hashes.add((k,v['result_sha256']))
selected={k:v for k,v in selected.items() if (k,v['result_sha256']) not in prior_hashes}
assert selected, 'No new records to audit'

vol=refresh_volumes();library=json.loads((R/'state/thermodynamics-v1/library-registry.json').read_text());ready={s['solvent_key'] for s in library['solvents'] if s['status']=='ready'};assert len(ready)==32
for k,v in selected.items():
 raw=Path(v['result_path']).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=v['result_sha256']:changed.append(k);continue
 record=json.loads(raw);assert set(record['activities'])==ready and record['config']==CONFIG
 check_failure_semantics(record,[s['solvent_key'] for s in library['solvents']])
 for pair in record['partitions_against_water']:
  if pair['status']=='predicted':
   assert pair['temperature_K']==298.15 and pair['reference']=='water' and pair['parameterization']=='openCOSMORS24a'
   if pair['solvent'] in vol:
    assert pair['molar_volume_solvent_cm3_mol']==vol[pair['solvent']]['molar_volume_cm3_mol'];assert pair['molar_volume_reference_cm3_mol']==18.07
   else:assert pair['log10_K_concentration'] is None and pair['concentration_basis_status']=='missing_documented_molar_volume'
 dest=D/'records'/f'{k}.json';dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw);assert sha(dest)==v['result_sha256'];sealed[k]=dict(v,result_path=str(dest))
 source=R/'state/campaign-v1/records'/f'{k}.json';target=D/'freeze/state/campaign-v1/records'/source.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
save(D/'sealed-thermodynamics/processing-ledger.json',sealed);save(D/'sealed-thermodynamics/library-registry.json',library)
save(D/'snapshot.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'selected':len(selected),'sealed':len(sealed),'changed_deferred':changed,'scope':'New records at 32 attempts beyond specified prior audits; not all5803 campaign molecules','volume_source_checks':'Loaded and verified source hashes using refresh_volumes; each concentration result bound to documented solvent volume'})
env=dict(os.environ,PLASTCHEM_PROGRESS_ROOT=str(D));subprocess.run([sys.executable,str(R/'scripts/audit_thermodynamic_records.py'),'--frozen'],env=env,check=True)
audit=json.loads((D/'sealed-thermodynamics/production-record-audit.json').read_text());assert audit['passed']==len(sealed) and audit['failed']==0
for f in ['audit_resolved32_increment.py','audit_thermodynamic_records.py','audit_panel_failure_semantics.py']:shutil.copyfile(R/'scripts'/f,D/f)
manifest=''.join(f'{sha(p)}  {p.relative_to(D)}\n' for p in sorted(D.rglob('*')) if p.is_file());(D/'SHA256SUMS').write_text(manifest)
save(R/'state'/f'{D.name}-receipt.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(D),'passed':audit['passed'],'failed':audit['failed'],'manifest_sha256':sha(D/'SHA256SUMS'),'scope':'New complete available-32-solvent subset only; unresolved xylene remains unavailable','file_count':len(manifest.splitlines())})
print('SEALED',len(sealed),'FAILURES',audit['failed'],flush=True)
