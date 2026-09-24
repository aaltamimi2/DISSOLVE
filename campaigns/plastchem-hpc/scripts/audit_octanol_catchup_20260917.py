"""Audit every pinned octanol outcome, including explicitly unavailable predictions."""
import json,hashlib,math,datetime
from pathlib import Path
from decimal import Decimal,localcontext
D=Path('/mnt/r/plastchem-euler/octanol-catchup-2026-09-17')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
m=read(D/'manifest.json');prov=read(D/'provenance.json');rows=[]
for member in m['cohort']:
 k=member['inchikey'];row={'inchikey':k}
 try:
  path=D/'octanol'/f'{k}.json';v=read(path);p=D/'panel'/f'{k}.json';panel=read(p)
  assert sha(p)==v['water_source_result_sha256']
  assert v['input_inchikey']==k and v['perceived_inchikey'].split('-')[0]==k.split('-')[0]
  assert v['identity_match_basis']=='connectivity_first_block' and v['cpu_model']=='AMD EPYC 7763 64-Core Processor'
  assert v['solute_surface_sha256']==member['surface_sha256']==sha(Path(member['archive_path'])/'surface.orcacosmo')
  a=v['octanol_activity'];w=v['water_activity'];pred=v['prediction']
  assert w==panel['activities']['water'] and panel['config']==prov['config']
  assert a['solvent_surface_sha256']==prov['octanol']['surface_sha256']
  for act in [w,a]:
   assert act['config']==prov['config']
   assert act['solute_surface_sha256']==member['surface_sha256']
   samples=act.get('samples',[])
   assert [s['solute_fraction'] for s in samples]==[1e-5,1e-6,1e-7,1e-8][:len(samples)]
   assert all(math.isfinite(s['ln_gamma']) for s in samples)
   if act['status']=='converged':
    assert len(samples)>=2 and act['ln_gamma']==samples[-1]['ln_gamma']
    shift=abs(samples[-1]['ln_gamma']-samples[-2]['ln_gamma'])/math.log(10)
    assert shift<=.005 and abs(shift-act['last_log10_dilution_shift'])<1e-12
   else:
    assert 'ln_gamma' not in act and act.get('error')
    if act['status']=='dilution_not_converged':
     assert len(samples)==4
     shift=abs(samples[-1]['ln_gamma']-samples[-2]['ln_gamma'])/math.log(10)
     assert shift>.005 and abs(shift-act['last_log10_dilution_shift'])<1e-12
  if a['status']!= 'converged' or w['status']!='converged':
   assert pred['status']=='not_available'
   assert 'log10_K_mole_fraction' not in pred and 'log10_K_concentration' not in pred
   row.update(status='passed',prediction_status='not_available',result_sha256=sha(path))
  else:
   assert pred['status']=='predicted' and a['last_log10_dilution_shift']+w['last_log10_dilution_shift']<=.01
   assert pred['molar_volume_reference_cm3_mol']==18.07
   assert pred['molar_volume_solvent_cm3_mol']==prov['volume']['molar_volume_cm3_mol']
   with localcontext() as ctx:
    ctx.prec=40
    x=(Decimal(str(w['ln_gamma']))-Decimal(str(a['ln_gamma'])))/Decimal(10).ln()
    corr=(Decimal(str(pred['molar_volume_reference_cm3_mol']))/Decimal(str(pred['molar_volume_solvent_cm3_mol']))).log10()
    assert corr<0 and abs(float(x)-pred['log10_K_mole_fraction'])<1e-12
    delta=abs(float(x+corr)-pred['log10_K_concentration']);assert delta<1e-12
   row.update(status='passed',prediction_status='predicted',result_sha256=sha(path),decimal_delta=delta)
 except Exception as e:row.update(status='failed',error=repr(e))
 rows.append(row)
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'denominator':len(rows),'passed':sum(r['status']=='passed' for r in rows),'failed':sum(r['status']=='failed' for r in rows),'verified_unavailable':sum(r.get('prediction_status')=='not_available' for r in rows),'rows':rows,'manifest_sha256':sha(D/'manifest.json'),'audit_script_sha256':sha(Path(__file__)),'scope':'Numerical, missingness, identity and provenance audit; not experimental accuracy'}
(D/'numerical-audit.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='rows'}),flush=True)
assert not out['failed'],'Audit failures; inspect rows before using results'
