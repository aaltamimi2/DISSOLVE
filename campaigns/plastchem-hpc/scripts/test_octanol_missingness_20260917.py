"""Exercise missingness audit against an isolated in-memory altered historical record."""
from pathlib import Path
from unittest.mock import patch
import copy,json
P=Path(__file__).with_name('audit_octanol_catchup_20260917.py').resolve()
D=Path('/mnt/r/plastchem-euler/post1160-octanol-2026-09-15')
source=P.read_text().replace('/mnt/r/plastchem-euler/octanol-catchup-2026-09-17',str(D))
manifest=json.loads((D/'manifest.json').read_text());manifest['cohort']=manifest['cohort'][:1]
k=manifest['cohort'][0]['inchikey'];target=D/'octanol'/f'{k}.json';original=json.loads(target.read_text())
read_bytes=Path.read_bytes;read_text=Path.read_text;checks=[]
for illegal_value in [False,True]:
 r=copy.deepcopy(original);a=r['octanol_activity'];a['samples']=[{'solute_fraction':x,'ln_gamma':float(i)} for i,x in enumerate([1e-5,1e-6,1e-7,1e-8])]
 import math
 a.update(status='dilution_not_converged',error='Synthetic test: no plateau',last_log10_dilution_shift=1/math.log(10));a.pop('ln_gamma',None)
 r['prediction']={'status':'not_available','reason':'Synthetic test: failed activity'}
 if illegal_value:r['prediction']['log10_K_concentration']=0.0
 overrides={target:json.dumps(r).encode(),D/'manifest.json':json.dumps(manifest).encode()};outputs=[]
 def rb(p):return overrides[p] if p in overrides else read_bytes(p)
 def rt(p,*args,**kwargs):return overrides[p].decode() if p in overrides else read_text(p,*args,**kwargs)
 try:
  with patch.object(Path,'read_bytes',rb),patch.object(Path,'read_text',rt),patch.object(Path,'write_text',lambda p,s,*a,**kw:outputs.append(s) or len(s)):
   exec(compile(source,str(P),'exec'),{'__file__':str(P),'__name__':'__main__'})
 except AssertionError:
  assert illegal_value
 result=json.loads(outputs[0]);assert result['failed']==int(illegal_value)
 if not illegal_value:assert result['verified_unavailable']==1
 checks.append({'synthetic_case':'unavailable_with_illegal_number' if illegal_value else 'properly_unavailable','expected_audit_failure':illegal_value,'test_passed':True})
(Path(__file__).resolve().parents[1]/'state/octanol-missingness-tests-20260917.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks))
