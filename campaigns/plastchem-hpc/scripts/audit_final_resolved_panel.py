"""Gate and seal the final resolved-solvent panel; no scientific calculations.

Use --check-only during processing. --output must be a new bulk-output directory.
This audits available references; unresolved owner identities remain explicit.
"""
import os,json,hashlib,datetime,subprocess,sys,shutil,math,argparse
from pathlib import Path
from thermodynamic_prediction import refresh_volumes,CONFIG
from audit_panel_failure_semantics import check as check_failure_semantics
R=Path('/home/aaltamimi2/plastchem-euler')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check-only',action='store_true')
parser.add_argument('--output',type=Path)
args=parser.parse_args()
live=json.loads((R/'state/thermodynamics-v1/processing-ledger.json').read_text())
lib=json.loads((R/'state/thermodynamics-v1/library-registry.json').read_text())
ready_names={s['solvent_key'] for s in lib['solvents'] if s['status']=='ready'}
assert len(ready_names)==32, 'Solvent scope changed: review required'
accepted={}
for p in (R/'state/campaign-v1/records').glob('*.json'):
 record=json.loads(p.read_text())
 if record.get('status')=='converged': accepted[record['inchikey']]=record
assert len(accepted)==5803, 'Accepted cohort changed: review required'
missing=sorted(set(accepted)-set(live))
incomplete=sorted(k for k in accepted if k in live and live[k]['activity_count']+live[k].get('failed_activity_count',0)!=32)
extra=sorted(set(live)-set(accepted))
preflight={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'accepted':len(accepted),'resolved_references':len(ready_names),'missing':len(missing),'incomplete':len(incomplete),'extra':len(extra),'ready_to_seal':not(missing or incomplete or extra),'scope':'Every accepted contaminant, every resolved reference, including explicit failures; not unresolved xylene or future 69-solvent grids'}
print(json.dumps(preflight),flush=True)
if args.check_only: sys.exit(0 if preflight['ready_to_seal'] else 2)
assert preflight['ready_to_seal'], 'Panel processing is incomplete; no final snapshot created'
assert args.output is not None, '--output required'
D=args.output.resolve()
assert D.is_relative_to(Path('/mnt/r/plastchem-euler/audits')), 'Use the bulk audit directory'
D.mkdir(parents=True,exist_ok=False)

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2)+'\n')
l=live;selected={k:v for k,v in l.items() if v['activity_count']+v.get('failed_activity_count',0)==32};sealed={};changed=[]
vol=refresh_volumes();library=json.loads((R/'state/thermodynamics-v1/library-registry.json').read_text());ready={s['solvent_key'] for s in library['solvents'] if s['status']=='ready'};assert len(ready)==32
for k,v in selected.items():
 raw=Path(v['result_path']).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=v['result_sha256']:changed.append(k);continue
 record=json.loads(raw);assert set(record['activities'])==ready and record['config']==CONFIG
 check_failure_semantics(record, [s['solvent_key'] for s in library['solvents']])
 for pair in record['partitions_against_water']:
  if pair['status']=='predicted':
   assert pair['temperature_K']==298.15 and pair['reference']=='water' and pair['parameterization']=='openCOSMORS24a'
   if pair['solvent'] in vol:
    assert pair['molar_volume_solvent_cm3_mol']==vol[pair['solvent']]['molar_volume_cm3_mol'];assert pair['molar_volume_reference_cm3_mol']==18.07
   else:assert pair['log10_K_concentration'] is None and pair['concentration_basis_status']=='missing_documented_molar_volume'
 dest=D/'records'/f'{k}.json';dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw);assert sha(dest)==v['result_sha256'];sealed[k]=dict(v,result_path=str(dest))
 source=R/'state/campaign-v1/records'/f'{k}.json';target=D/'freeze/state/campaign-v1/records'/source.name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
assert not changed and set(sealed)==set(accepted), 'Snapshot changed; final certification refused'
save(D/'sealed-thermodynamics/processing-ledger.json',sealed);save(D/'sealed-thermodynamics/library-registry.json',library)
save(D/'snapshot.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'selected':len(selected),'sealed':len(sealed),'changed_deferred':changed,'scope':'All 5803 accepted molecules at 32 resolved-solvent attempts; explicit activity failures retained','volume_source_checks':'Loaded and verified source hashes using refresh_volumes; each concentration result bound to documented solvent volume'})
env=dict(os.environ,PLASTCHEM_PROGRESS_ROOT=str(D));subprocess.run([sys.executable,str(R/'scripts/audit_thermodynamic_records.py'),'--frozen'],env=env,check=True)
audit=json.loads((D/'sealed-thermodynamics/production-record-audit.json').read_text());assert audit['passed']==len(sealed) and audit['failed']==0
for f in ['audit_final_resolved_panel.py','audit_thermodynamic_records.py','audit_panel_failure_semantics.py']:shutil.copyfile(R/'scripts'/f,D/f)
manifest=''.join(f'{sha(p)}  {p.relative_to(D)}\n' for p in sorted(D.rglob('*')) if p.is_file());(D/'SHA256SUMS').write_text(manifest)
save(R/'state/final-resolved-panel-audit.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(D),'passed':audit['passed'],'failed':audit['failed'],'manifest_sha256':sha(D/'SHA256SUMS'),'scope':'All 5803 accepted contaminants against 32 resolved references; unresolved xylene remains unavailable','file_count':len(manifest.splitlines())})
print('SEALED',len(sealed),'FAILURES',audit['failed'],flush=True)
