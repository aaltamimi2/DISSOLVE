"""Deterministic size-stratified, descriptor-diverse pilot; one MMFF-ranked DFT conformer."""
import csv,hashlib,json,time
from pathlib import Path
import numpy as np
from rdkit import Chem,rdBase
from rdkit.Chem import AllChem,rdMolDescriptors,Descriptors
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'state/pilot-v1';OUT.mkdir(exist_ok=True)
rows=list(csv.DictReader((ROOT/'inputs/plastchem_organics_orca_opencosmo_firstpass.csv').open()))
uniq={}
for row in rows:uniq.setdefault(row['inchikey'],row)
census=[];mols={}
for key,row in sorted(uniq.items()):
 m=Chem.MolFromSmiles(row['smiles']);h=Chem.AddHs(m)
 d=dict(row,atoms=h.GetNumAtoms(),heavy_atoms=m.GetNumAtoms(),rotatable_bonds=rdMolDescriptors.CalcNumRotatableBonds(m),rings=rdMolDescriptors.CalcNumRings(m),aromatic_atoms=sum(a.GetIsAromatic() for a in m.GetAtoms()),nitrogen_atoms=sum(a.GetSymbol()=='N' for a in m.GetAtoms()),oxygen_atoms=sum(a.GetSymbol()=='O' for a in m.GetAtoms()),radical_electrons=sum(a.GetNumRadicalElectrons() for a in m.GetAtoms()),smiles_inchikey=Chem.MolToInchiKey(m),mmff_available=AllChem.MMFFHasAllMoleculeParams(h))
 census.append(d);mols[key]=h
(OUT/'census.json').write_text(json.dumps(census,indent=2)+'\n')
anchors={'84-66-2':'DEP','84-74-2':'DBP','85-68-7':'BBP','117-81-7':'DEHP','523-31-9':'additional_aromatic_ester_dibenzyl_phthalate'}
features=['atoms','heavy_atoms','rotatable_bonds','rings','aromatic_atoms','nitrogen_atoms','oxygen_atoms']
selected=[]
for low,high in [(15,22),(23,30),(31,38),(39,46),(47,54),(55,62),(63,70),(71,80)]:
 pool=[d for d in census if low<=d['atoms']<=high and d['mmff_available'] and d['radical_electrons']==0 and d['inchikey']==d['smiles_inchikey']]
 forced=[d for d in pool if d['cas'] in anchors]
 # Include literal size endpoints as well as the four mandatory timing anchors.
 for endpoint in ([15] if low==15 else [80] if high==80 else []):
  cand=[d for d in pool if d['atoms']==endpoint]
  forced.append(min(cand,key=lambda d:(d['rings']+d['nitrogen_atoms'],d['rotatable_bonds'],d['inchikey'])))
 chosen=list({d['inchikey']:d for d in forced}.values())
 arr=np.array([[d[f] for f in features] for d in pool],float);scale=np.std(arr,axis=0);scale[scale==0]=1
 coords=arr/scale
 if not chosen:
  i=int(np.argmin(np.sum((coords-np.median(coords,axis=0))**2,axis=1)));chosen=[pool[i]]
 while len(chosen)<7:
  used={d['inchikey'] for d in chosen};centers=np.array([[d[f] for f in features] for d in chosen])/scale
  dist=np.min(np.sum((coords[:,None,:]-centers[None,:,:])**2,axis=2),axis=1)
  for i,d in enumerate(pool):
   if d['inchikey'] in used:dist[i]=-1
  chosen.append(pool[int(np.argmax(dist))])
 for d in chosen:d.update(stratum=f'{low}-{high}',selection_role=anchors.get(d['cas'],'descriptor_diversity'))
 selected+=chosen
assert len(selected)==56 and len({d['inchikey'] for d in selected})==56
assert set(anchors)<=set(d['cas'] for d in selected)
selected.sort(key=lambda d:(-d['atoms'],d['inchikey']))
for i,d in enumerate(selected):d['array_index']=i
manifest={'name':'contam-p1-milan-v1','selection_method':'8 fixed atom-count strata, 7 per stratum, deterministic farthest-point descriptor coverage; forced four anchors, dibenzyl phthalate, and 15/80 endpoints','rdkit_version':rdBase.rdkitVersion,'recipe':{'n_embed':300,'seed':12345,'prune_rms':0.5,'max_mmff_iters':2000,'dft_conformers':1},'molecules':selected}
manifest_path=OUT/'manifest.json'
if manifest_path.exists():assert json.loads(manifest_path.read_text())==manifest,'Selection changed; refuse to overwrite'
else:manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
print('SELECTED',len(selected),'atoms',min(d['atoms'] for d in selected),max(d['atoms'] for d in selected),flush=True)
for d in selected:
 key=d['inchikey'];p=OUT/key;p.mkdir(exist_ok=True)
 if (p/'preparation.json').exists():continue
 start=time.monotonic();r=dict(status='preparing',input=d)
 try:
  m=mols[key];params=AllChem.ETKDGv3();params.randomSeed=12345;params.pruneRmsThresh=0.5;params.numThreads=1
  ids=list(AllChem.EmbedMultipleConfs(m,numConfs=300,params=params))
  scores=AllChem.MMFFOptimizeMoleculeConfs(m,numThreads=1,maxIters=2000)
  ranked=sorted((energy,cid) for cid,(status,energy) in zip(ids,scores) if status==0)
  if not ranked:raise RuntimeError('No embedded conformer converged under MMFF')
  energy,cid=ranked[0];xyz=Chem.MolToXYZBlock(m,confId=cid);(p/'input.xyz').write_text(xyz)
  r.update(status='prepared',embedded_count=len(ids),mmff_converged_count=len(ranked),selected_conformer_id=cid,selected_mmff_energy_kcal=energy,dft_conformer_count=1,xyz_sha256=hashlib.sha256(xyz.encode()).hexdigest())
 except Exception as exc:r.update(status='preparation_failed',failure_mode=type(exc).__name__,error=str(exc))
 r['wall_seconds']=time.monotonic()-start;(p/'preparation.json').write_text(json.dumps(r,indent=2)+'\n')
 print(d['array_index'],d['name'],d['atoms'],r['status'],round(r['wall_seconds'],1),flush=True)
