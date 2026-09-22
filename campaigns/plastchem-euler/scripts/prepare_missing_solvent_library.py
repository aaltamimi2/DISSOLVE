"""Prepare only missing solvent identities in a separate library namespace; no scheduler access."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import hashlib,json,time
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import Descriptors
import prepare_campaign as frozen
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/solvent-library-v1';P.mkdir(exist_ok=True);frozen.P=P
inventory=json.loads((ROOT/'state/thermodynamics-v1/solvent-inventory.json').read_text());rows=[]
for source in inventory['solvents']:
 if source['status']!='missing_campaign_solvent_calculation':continue
 mol=Chem.MolFromSmiles(source['smiles']);key=Chem.MolToInchiKey(mol);assert key==source['connectivity_inchikey']
 r={'array_index':len(rows),'name':source['solvent_key'],'inchikey':key,'smiles':source['smiles'],'atoms':Chem.AddHs(mol).GetNumAtoms(),'molecular_weight':Descriptors.MolWt(mol),'group':'solvent_library','role':'supporting_solvent_not_campaign_contaminant','input_stereo_specified':False,'identity_source_sha256':source['identity_source_sha256']}
 assert Chem.GetFormalCharge(mol)==0 and not any(a.GetIsotope() for a in mol.GetAtoms());rows.append(r)
manifest={'molecules':rows,'concurrency':4,'walltime':'24:00:00','partition':'research','constraint':'milan&cpu','exclude':['euler09','euler10'],'cpus':1,'memory':'4G','scope':'Six missing solvent-library structures, separate from 5824 eligible campaign contaminants','not_submitted':True,'earliest_execution':'after all main arrays and tail terminate; does not add to active 32 tasks','proposed_dependency':'afterany:54802:54733','recipe':'unchanged frozen serial ORCA 6.1.1 / GIT487d211c / BP86 def2-TZVP(-f) OPT then COSMORS(Water), maxcore1500, 300-candidate ETKDG MMFF lowest one'}
(P/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
for mol in rows:
 r=frozen.prepare(mol);print(json.dumps({'solvent':mol['name'],'status':r['status'],'seconds':r['wall_seconds'],'failure_mode':r.get('failure_mode')}),flush=True)
