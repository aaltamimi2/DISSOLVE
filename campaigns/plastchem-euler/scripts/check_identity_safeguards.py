"""Meaningful known-answer checks for identity false acceptance; no ORCA."""
import json
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import AllChem
from geometry_identity import verify
root=Path(__file__).resolve().parents[1]/'state/pilot-v1/identity-method-checks';root.mkdir(exist_ok=True)
def xyz(smiles,name):
 m=Chem.AddHs(Chem.MolFromSmiles(smiles));p=AllChem.ETKDGv3();p.randomSeed=12345
 assert AllChem.EmbedMolecule(m,p)==0;AllChem.MMFFOptimizeMolecule(m,maxIters=2000)
 path=root/(name+'.xyz');path.write_text(Chem.MolToXYZBlock(m));return path
checks=[]
for name,expected_smiles,geometry_smiles,policy,want in [
 ('dep_true','CCOC(=O)c1ccccc1C(=O)OCC','CCOC(=O)c1ccccc1C(=O)OCC','exact_full_inchikey',True),
 ('dep_meta_decoy','CCOC(=O)c1ccccc1C(=O)OCC','CCOC(=O)c1cccc(C(=O)OCC)c1','declared_stereochemistry',False),
 ('declared_enantiomer_inversion','C[C@H](O)C(=O)O','C[C@@H](O)C(=O)O','declared_stereochemistry',False),
 ('undeclared_stereo','CC(O)C(=O)O','C[C@@H](O)C(=O)O','declared_stereochemistry',True),
 ('isotope_loss','[2H]OC','CO','declared_stereochemistry',False)]:
 key=Chem.MolToInchiKey(Chem.MolFromSmiles(expected_smiles));r=verify(xyz(geometry_smiles,name),key,expected_smiles,policy)
 assert r['identity_verified']==want,(name,r)
 checks.append(dict(name=name,expected_accept=want,result=r))
(root/'results.json').write_text(json.dumps(checks,indent=2)+'\n');print('5/5 identity safeguards passed: true DEP, positional-isomer rejection, declared enantiomer rejection, undeclared stereo handling, isotope-loss rejection.')
