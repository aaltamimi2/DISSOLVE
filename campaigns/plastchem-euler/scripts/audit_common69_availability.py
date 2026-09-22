"""Read-only source inventory; no ORCA or thermodynamic calculation."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import ast,csv,hashlib,json,datetime
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds
from opencosmorspy.input_parsers import SigmaProfileParser
R=Path('/home/aaltamimi2/plastchem-euler'); D=Path('/mnt/r/plastchem-euler/common69-pilot-20260917');D.mkdir(exist_ok=True,parents=True)
source=Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/thermodynamics.py')
node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='COMMON_INTERP_KEYS')
names=ast.literal_eval(node.value.args[0]);assert len(names)==69
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
legacy=Path('/home/aaltamimi2/COSMO-POLYMER-ML/results/oligomers/all-cosmotherm-solvents')
records={}
for p in sorted((R/'state/campaign-v1/records').glob('*.json')):
 r=json.loads(p.read_text())
 if r.get('status')=='converged':records.setdefault(r['inchikey'].split('-')[0],(p,r))
lib=json.loads((R/'state/thermodynamics-v1/library-registry.json').read_text())
ready={s['source_identity'].split('-')[0]:s for s in lib['solvents'] if s['status']=='ready'}
rows=[]
for name in names:
 row={'common_key':name,'legacy_surface':str(legacy/(name+'_c0.cosmo')),'legacy_use':'identity only; not accepted as frozen 24a surface','temperature_K':298.15}
 try:
  p=Path(row['legacy_surface']);row['legacy_sha256']=sha(p);d=SigmaProfileParser(str(p));xyz=str(len(d['atm_elmnt']))+'\nidentity\n'+''.join(f'{a} {v[0]:.10f} {v[1]:.10f} {v[2]:.10f}\n' for a,v in zip(d['atm_elmnt'],d['atm_pos']))
  m=Chem.MolFromXYZBlock(xyz);rdDetermineBonds.DetermineBonds(m,charge=0);row['legacy_perceived_inchikey']=Chem.MolToInchiKey(m);m=Chem.RemoveHs(m);Chem.RemoveStereochemistry(m);row['smiles']=Chem.MolToSmiles(m);key=Chem.MolToInchiKey(Chem.MolFromSmiles(row['smiles']));row['input_inchikey']=key;block=key.split('-')[0]
  s=ready.get(block)
  if s:row.update(status='verified_panel_surface_available',surface=s['surface'],surface_sha256=s['surface_sha256'],source_record=s['source_record'],panel_key=s['solvent_key'])
  elif block in records:
   p,r=records[block];assert r['connectivity_match'] and r['cpu_model']=='AMD EPYC 7763 64-Core Processor';row.update(status='verified_campaign_surface_available',surface=str(Path(r['archive_path'])/'surface.orcacosmo'),surface_sha256=r['surface_sha256'],source_record=str(p))
  else:row['status']='no_verified_frozen_recipe_surface_found'
  if row.get('surface'):
   assert sha(row['surface'])==row['surface_sha256'];parsed=SigmaProfileParser(row['surface']);row['surface_parser_pass']=True
 except Exception as e:
  if name=='gvl':
   evidence=json.loads((D/'gvl-product-identity.json').read_text());assert evidence['rows'] and all(x['cas_number']=='108-29-2' for x in evidence['rows'])
   p,r=records['GAEKPEKOJKCEMS'];assert r['input']['cas']=='108-29-2'
   row.pop('legacy_surface',None);row.update(status='verified_campaign_surface_available',identity_evidence=str(D/'gvl-product-identity.json'),smiles=r['input']['smiles'],input_inchikey=r['inchikey'],surface=str(Path(r['archive_path'])/'surface.orcacosmo'),surface_sha256=r['surface_sha256'],source_record=str(p));assert sha(row['surface'])==row['surface_sha256'];SigmaProfileParser(row['surface']);row['surface_parser_pass']=True
  else:row.update(status='inventory_error',error=str(e))
 if row.get('source_record'):
  rr=json.loads(Path(row['source_record']).read_text());assert rr['cpu_model']=='AMD EPYC 7763 64-Core Processor' and rr['orca_version']=='6.1.1' and rr['orca_git']=='487d211c'
  for stage,info in rr['stages'].items():assert sha(Path(rr['archive_path'])/(stage+'.inp'))==info['input_sha256']
  row['record_sha256']=sha(row['source_record']);row['decks_hash_verified']=True
 rows.append(row)
 print(name,row['status'],flush=True)
from collections import Counter
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':str(source),'source_sha256':sha(source),'scope':'69 product common keys; water separate; no new ORCA or COSMO-RS calculation','counts':dict(Counter(r['status'] for r in rows)),'rows':rows}
(D/'availability.json').write_text(json.dumps(out,indent=2)+'\n')
fields=sorted(set().union(*(r.keys() for r in rows)))
with (D/'availability.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
print(out['counts'])
