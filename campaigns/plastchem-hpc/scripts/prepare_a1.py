import csv,json,hashlib
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import AllChem
root=Path(__file__).resolve().parents[1]
ethanol=next(r for r in csv.DictReader((root/'inputs/plastchem_organics_orca_opencosmo_firstpass.csv').open()) if r['cas']=='64-17-5')
for r in [{'name':'Water','smiles':'O','inchikey':'XLYOFNOQVPJJNP-UHFFFAOYSA-N','role':'diagnostic_water'},dict(ethanol,role='diagnostic_chno')]:
 m=Chem.AddHs(Chem.MolFromSmiles(r['smiles'])); assert Chem.MolToInchiKey(m)==r['inchikey']
 p=AllChem.ETKDGv3();p.randomSeed=20260912
 assert AllChem.EmbedMolecule(m,p)==0
 assert AllChem.MMFFOptimizeMolecule(m)==0
 xyz=Chem.MolToXYZBlock(m)
 r.update(conformers=1,embedding='ETKDGv3',mmff='MMFF94',seed=20260912,atom_count=m.GetNumAtoms(),xyz_sha256=hashlib.sha256(xyz.encode()).hexdigest())
 (root/'state/a1-inputs'/f"{r['inchikey']}.xyz").write_text(xyz)
 (root/'state/a1-inputs'/f"{r['inchikey']}.json").write_text(json.dumps(r,indent=2)+'\n')
 print(r['name'],r['inchikey'],r['atom_count'])
