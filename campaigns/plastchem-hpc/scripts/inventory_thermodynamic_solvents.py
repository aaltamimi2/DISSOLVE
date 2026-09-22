"""Inventory exact legacy solvent identities; do not reuse their 2002 surfaces for 24a."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import hashlib,importlib.util,json,sys
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds, rdMolDescriptors
from opencosmorspy.input_parsers import SigmaProfileParser
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/thermodynamics-v1';P.mkdir(exist_ok=True)
spec=importlib.util.spec_from_file_location('reference_cosmo',ROOT/'state/opencosmo-verification-v1/reference_cosmo_logp.py');ref=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ref;spec.loader.exec_module(ref)
eligible=json.loads((ROOT/'state/campaign-v1/eligible.json').read_text());byblock={}
for m in eligible:byblock.setdefault(m['inchikey'].split('-')[0],[]).append(m)
rows=[]
for name in sorted(ref.TABLE_SOLVENT_KEYS):
 r={'solvent_key':name,'source_role':'identity only; COSMObase 2002 surface is not a 24a input'}
 f=ref.cosmotherm_file_for(name)
 if f is None:r.update(status='identity_unresolved',reason='No exact source-library identity file; do not substitute another name');rows.append(r);continue
 r.update(identity_source=str(f),identity_source_sha256=hashlib.sha256(f.read_bytes()).hexdigest())
 try:
  parsed=SigmaProfileParser(str(f));atoms=list(zip(parsed['atm_elmnt'],parsed['atm_pos']));xyz=str(len(atoms))+'\nlegacy solvent identity\n'+''.join(f'{a} {v[0]:.9f} {v[1]:.9f} {v[2]:.9f}\n' for a,v in atoms);r['coordinate_parser']='opencosmorspy SigmaProfileParser, explicit Turbomole bohr conversion'
  mol=Chem.MolFromXYZBlock(xyz);rdDetermineBonds.DetermineBonds(mol,charge=0);key=Chem.MolToInchiKey(mol)
  # Preserve perceived identity, but use a stereo-unspecified parent solely for locating an existing campaign structure.
  heavy=Chem.RemoveHs(mol);perceived_smiles=Chem.MolToSmiles(heavy);Chem.RemoveStereochemistry(heavy);smiles=Chem.MolToSmiles(heavy);target=Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))  # Rebuild without 3D conformer stereo perception.
  matches=byblock.get(target.split('-')[0],[])
  r.update(perceived_source_inchikey=key,perceived_source_smiles=perceived_smiles,connectivity_inchikey=target,smiles=smiles,formula=rdMolDescriptors.CalcMolFormula(heavy),atoms=len(atoms),elements=sorted({a.GetSymbol() for a in mol.GetAtoms()}),campaign_matches=[{'inchikey':m['inchikey'],'name':m['name'],'input_smiles':m['smiles']} for m in matches])
  if matches:
   r['status']='reuse_campaign_when_verified';r['campaign_states']=[]
   for m in matches:
    record=ROOT/'state/campaign-v1/records'/(m['inchikey']+'.json');rec=json.loads(record.read_text()) if record.exists() else {};r['campaign_states'].append({'inchikey':m['inchikey'],'status':rec.get('status','not_yet_run')})
  elif name=='water':r['status']='existing_Milan_diagnostic_requires_provenance_check'
  else:r['status']='missing_campaign_solvent_calculation'
 except Exception as exc:r.update(status='identity_perception_failed',error=str(exc))
 rows.append(r)
out={'scope':'All 33 exact shared table solvent keys; final production uses homogeneous Milan and frozen 24a recipe','solvents':rows,'count':len(rows)}
(P/'solvent-inventory.json').write_text(json.dumps(out,indent=2)+'\n')
for r in rows:print(r['solvent_key'],r['status'],r.get('smiles'),r.get('campaign_states'))
