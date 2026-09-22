"""Measured polymer/tier2 regressions and proposed pending-only limits."""
from pathlib import Path
import json,math,datetime
import numpy as np
from review_tier2_cost import project
R=Path(__file__).resolve().parents[1]
result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'fits':{},'plans':{}}
for kind,folder,target in [('polymer','polymer-v1','large'),('tier2','tier2-v1','tier2')]:
 rows=[]
 for p in (R/'state'/folder/'records').glob('*.json'):
  r=json.loads(p.read_text())
  if r.get('status')!='converged':continue
  assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor'
  ts=[r['stages'][s]['wall_seconds'] for s in ['opt','cosmo']];rows.append({'id':r.get('entry_id',r['inchikey']),'atoms':r['input']['atoms'],'hours':sum(ts)/3600})
 if kind=='tier2':assert len(rows)==27
 else:assert len(rows)>200
 models=json.loads((R/'state'/folder/target/'manifest.json').read_text())['molecules']
 estimate,a,b,res=project([r['atoms'] for r in rows],[r['hours'] for r in rows],[m['atoms'] for m in models]);smear=float(np.mean(np.exp(res)));q95=float(np.quantile(np.exp(res),.95))
 rng=np.random.default_rng(12345);boot=[]
 for _ in range(2000):
  ids=rng.integers(0,len(rows),len(rows));rs=[rows[i] for i in ids]
  if len({r['atoms'] for r in rs})>1:boot.append(project([r['atoms'] for r in rs],[r['hours'] for r in rs],[m['atoms'] for m in models])[0])
 fit={'n':len(rows),'measured_atom_range':[min(r['atoms'] for r in rows),max(r['atoms'] for r in rows)],'intercept':a,'exponent':b,'residual_smearing':smear,'residual_factor_q95':q95,'projection_for_target_manifest_CPU_h':estimate,'bootstrap95_CPU_h':np.quantile(boot,[.025,.975]).tolist(),'measured_CPU_h':sum(r['hours'] for r in rows),'max_measured_hours':max(r['hours'] for r in rows),'qualification':'Conditional complete-case fit; ongoing/failed jobs not zero cost; polymer155–167 extrapolated. No polymer speedup transferred to tier2.','observations':rows}
 plan=[]
 for m in models:
  atom=m['atoms'];upper=atom if kind=='polymer' else int(math.ceil(atom/10)*10);mean=math.exp(a+b*math.log(upper))*smear;limit=max(3,int(math.ceil(max(2*mean,1.25*math.exp(a+b*math.log(upper))*q95))))
  plan.append({'index':m['array_index'],'atoms':atom,'band_upper_atoms':upper,'mean_predicted_hours_at_band_upper':mean,'limit_hours':limit})
 result['fits'][kind]=fit;result['plans'][kind]=plan
p=R/'state/polymer-v1/owner-release-measured-refits.json';p.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'fits':{k:{x:y for x,y in f.items() if x!='observations'} for k,f in result['fits'].items()},'bands':{k:sorted({(r['band_upper_atoms'],round(r['mean_predicted_hours_at_band_upper'],2),r['limit_hours']) for r in rs}) for k,rs in result['plans'].items()}},indent=2))
