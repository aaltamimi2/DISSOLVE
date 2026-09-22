"""Read-only audit of the pinned post-1151 octanol cohort; no model recalibration."""
from pathlib import Path
import json,hashlib,math,datetime
from decimal import Decimal,localcontext
D=Path('/mnt/r/plastchem-euler/post1151-octanol-2026-09-15')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest=read(D/'manifest.json');provenance=read(D/'provenance.json');rows=[]
for member in manifest['cohort']:
 k=member['inchikey'];p=D/'octanol'/f'{k}.json'
 try:
  v=read(p);panel=D/'panel'/f'{k}.json';q=read(panel);a=v['octanol_activity'];w=v['water_activity'];pred=v['prediction']
  assert sha(panel)==v['water_source_result_sha256']
  assert v['input_inchikey']==k and v['perceived_inchikey'].split('-')[0]==k.split('-')[0]
  assert v['identity_match_basis']=='connectivity_first_block'
  assert v['cpu_model']=='AMD EPYC 7763 64-Core Processor'
  assert v['solute_surface_sha256']==member['surface_sha256']==sha(Path(member['archive_path'])/'surface.orcacosmo')
  assert w==q['activities']['water'] and q['config']==provenance['config']
  assert a['solvent_surface_sha256']==provenance['octanol']['surface_sha256']
  assert pred['status']=='predicted'
  for act in [a,w]:
   assert act['status']=='converged' and math.isfinite(act['ln_gamma'])
   samples=act['samples'];assert len(samples)>=2
   assert [s['solute_fraction'] for s in samples]==[1e-5,1e-6,1e-7,1e-8][:len(samples)]
   assert all(math.isfinite(s['ln_gamma']) for s in samples)
   shift=abs(samples[-1]['ln_gamma']-samples[-2]['ln_gamma'])/math.log(10)
   assert shift<=.005 and abs(shift-act['last_log10_dilution_shift'])<1e-12
   assert act['ln_gamma']==samples[-1]['ln_gamma']
  assert a['last_log10_dilution_shift']+w['last_log10_dilution_shift']<=.01
  assert pred['molar_volume_reference_cm3_mol']==18.07
  assert pred['molar_volume_solvent_cm3_mol']==provenance['volume']['molar_volume_cm3_mol']
  with localcontext() as ctx:
   ctx.prec=40
   x=(Decimal(str(w['ln_gamma']))-Decimal(str(a['ln_gamma'])))/Decimal(10).ln()
   correction=(Decimal(str(pred['molar_volume_reference_cm3_mol']))/Decimal(str(pred['molar_volume_solvent_cm3_mol']))).log10()
   delta=abs(float(x+correction)-pred['log10_K_concentration'])
   assert correction<0 and delta<1e-12
  rows.append({'inchikey':k,'status':'passed','result_sha256':sha(p),'decimal_recalculation_delta':delta})
 except Exception as e:rows.append({'inchikey':k,'status':'failed','error':repr(e)})
result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Identity, bytes, stored activity dilution and independent Decimal standard-state arithmetic; not experimental accuracy validation','denominator':len(rows),'passed':sum(r['status']=='passed' for r in rows),'failed':sum(r['status']=='failed' for r in rows),'manifest_sha256':sha(D/'manifest.json'),'audit_script_sha256':sha(Path(__file__)),'rows':rows}
(D/'numerical-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='rows'}));assert not result['failed'],[r for r in rows if r['status']=='failed']
