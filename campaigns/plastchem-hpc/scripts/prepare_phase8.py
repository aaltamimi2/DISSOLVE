"""Pin and stage Phase 8 inputs only; no activity-coefficient calculations."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
import csv, json, hashlib, shutil, importlib.util, sys, datetime
sys.dont_write_bytecode=True
from pathlib import Path
import numpy as np
from opencosmorspy.input_parsers import SigmaProfileParser
R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase8-v1'); D.mkdir(exist_ok=True)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2)+'\n')
def finish(manifest):
 package=Path('/home/aaltamimi2/.venvs/cosmo-logp/lib/python3.11/site-packages/opencosmorspy')
 for p in package.glob('*.py'):
  q=D/'opencosmorspy'/p.name;q.parent.mkdir(exist_ok=True);shutil.copyfile(p,q);assert sha(p)==sha(q)
 save(D/'package-pins.json',{str(p.relative_to(D)):sha(p) for p in sorted((D/'opencosmorspy').rglob('*')) if p.is_file()})
 save(R/'state/phase8-v1/preparation.json',{'utc':manifest['utc'],'manifest_sha256':sha(D/'manifest.json'),'complete':manifest['complete'],'excluded':manifest['excluded'],'paired_predictions_per_convention':len(manifest['complete'])*128,'bulk':str(D)})
 print(json.dumps({'complete':manifest['complete'],'excluded':manifest['excluded'],'units':len(manifest['phase81_units']),'surfaces':len(manifest['pins'])},indent=2))
if '--finish-staging' in sys.argv:
 finish(json.loads((D/'manifest.json').read_text()));raise SystemExit(0)
pins={}
def stage(p):
 p=Path(p);h=sha(p);rel='surfaces/'+h+p.suffix;q=D/rel;q.parent.mkdir(exist_ok=True)
 if not q.exists():shutil.copyfile(p,q)
 assert sha(q)==h
 pins[str(p)]={'sha256':h,'staged':rel};return rel
refpath=Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/polymer_cosmo.py')
spec=importlib.util.spec_from_file_location('phase8_reference',refpath);ref=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ref;spec.loader.exec_module(ref)
models=sum([json.loads((R/f'state/polymer-v1/{band}/manifest.json').read_text())['molecules'] for band in ['body','large']],[])
records={m['entry_id']:json.loads((R/'state/polymer-v1/records'/f"{m['entry_id']}.json").read_text()) for m in models}
complete={};excluded={}
for p in sorted({m['polymer'] for m in models}):
 ms=[m for m in models if m['polymer']==p];n=sum(records[m['entry_id']]['status']=='converged' for m in ms)
 (complete if n==len(ms) else excluded)[p]={'converged':n,'total':len(ms)}
base=json.loads(Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7/pe/inputs.json').read_text())
legacy=list(csv.DictReader(Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a6/existing-convention-comparison.csv').open()))
solvents=[]
for s in base['solvents']:
 s=dict(s);lv=next(r for r in legacy if r['solvent']==s['name'])
 for route in ['A','B']:
  original=s[route];assert sha(original)==base['pins'][original];s[route]=stage(original)
  s[route+'_legacy_volume']=float(lv[route+'_solvent_volume_cm3_mol']);s[route+'_legacy_volume_source']=lv[route+'_volume_source']
 solvents.append(s)
solutes={k:{route:stage(p) for route,p in v.items()} for k,v in base['solutes'].items()}
polymers={}
for polymer,count in complete.items():
 rows=[]
 for m in [m for m in models if m['polymer']==polymer]:
  rec=records[m['entry_id']];src=Path('/home/aaltamimi2/polymers_cosmo')/m['source_file'];assert sha(src)==m['source_sha256']
  target=D/'converted'/polymer/(m['conformer_id']+'.cosmo');target.parent.mkdir(parents=True,exist_ok=True);ref.convert_gaussian_cosmo(src,target)
  b=Path(rec['archive_path'])/'surface.orcacosmo';assert sha(b)==rec['surface_sha256']
  rows.append({'entry_id':m['entry_id'],'A':stage(target),'B':stage(b),'A_energy_hartree':ref.split_mcos(src)[0].energy_hartree,'B_energy_hartree':rec['cosmo_solute_energy_hartree'],'B_OPT_energy_hartree':float(rec['stages']['opt']['final_energies_hartree'][-1]),'A_cavity_cm3_mol':float(SigmaProfileParser(str(target))['volume'])*.602214076,'B_cavity_cm3_mol':float(SigmaProfileParser(str(b))['volume'])*.602214076,'source_sha256':sha(src),'optimized_xyz':stage(Path(rec['archive_path'])/'optimized.xyz')})
 polymers[polymer]=rows
manifest={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'complete':complete,'excluded':excluded,'polymers':polymers,'solvents':solvents,'solutes':solutes,'reference_code_sha256':{str(p):sha(p) for p in [refpath,refpath.with_name('cosmo_logp.py')]},'temperature_K':298.15,'phase81_units':[{'polymer':p,'solute':s} for p in polymers for s in solutes],'pins':pins}
save(D/'manifest.json',manifest)
finish(manifest)
