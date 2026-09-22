from pathlib import Path
from rdkit import Chem
from rdkit.Chem import AllChem
import json
p=Path(__file__).resolve().parents[1]/'state'
m=Chem.AddHs(Chem.MolFromSmiles('O'))
params=AllChem.ETKDGv3(); params.randomSeed=20260912
assert AllChem.EmbedMolecule(m,params)==0
assert AllChem.MMFFOptimizeMolecule(m)==0
(p/'water.xyz').write_text(Chem.MolToXYZBlock(m))
(p/'water-start.json').write_text(json.dumps({'smiles':'O','inchikey':Chem.MolToInchiKey(m),'conformers':1,'method':'ETKDGv3 + MMFF','seed':20260912},indent=2)+'\n')
