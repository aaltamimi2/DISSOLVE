"""Independent 3D bond perception, retaining disagreements and original stereo declarations."""
import re,subprocess
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds
OBABEL='/home/aaltamimi2/anaconda3/bin/obabel'
def declared_key(molecule,smiles):
 original=Chem.AddHs(Chem.MolFromSmiles(smiles));reduced=Chem.Mol(molecule)
 if original.GetNumAtoms()!=reduced.GetNumAtoms():raise ValueError('Atom count changed')
 if any(a.GetAtomicNum()!=b.GetAtomicNum() for a,b in zip(original.GetAtoms(),reduced.GetAtoms())):raise ValueError('Atom ordering/element mapping changed')
 for a,b in zip(original.GetAtoms(),reduced.GetAtoms()):
  if a.GetChiralTag()==Chem.ChiralType.CHI_UNSPECIFIED:b.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
 for b in reduced.GetBonds():
  old=original.GetBondBetweenAtoms(b.GetBeginAtomIdx(),b.GetEndAtomIdx())
  if old is None:raise ValueError('Connectivity changed at atom-mapped bond')
  if old.GetStereo()==Chem.BondStereo.STEREONONE:b.SetStereo(Chem.BondStereo.STEREONONE);b.SetBondDir(Chem.BondDir.NONE)
 reduced.RemoveAllConformers()
 return Chem.MolToInchiKey(reduced)
def verify(xyz,expected,smiles,policy='exact_full_inchikey'):
 observations=[]
 try:
  m=Chem.MolFromXYZFile(str(xyz));rdDetermineBonds.DetermineBonds(m,charge=0)
  row={'method':'rdkit_determine_bonds','full_inchikey':Chem.MolToInchiKey(m)}
  if policy=='declared_stereochemistry' and row['full_inchikey']!=expected:row['declared_stereo_inchikey']=declared_key(m,smiles)
  observations.append(row)
 except Exception as e:observations.append({'method':'rdkit_determine_bonds','error':str(e)})
 try:
  p=subprocess.run([OBABEL,str(xyz),'-oinchikey'],capture_output=True,text=True,check=True)
  keys=re.findall(r'^[A-Z]{14}-[A-Z]{10}-[A-Z]$',p.stdout,re.M)
  if not keys:raise ValueError('No InChIKey produced')
  row={'method':'openbabel_reference','full_inchikey':keys[0],'warnings':p.stderr.strip()}
  if policy=='declared_stereochemistry' and row['full_inchikey']!=expected:
   sdf=subprocess.run([OBABEL,str(xyz),'-osdf'],capture_output=True,text=True,check=True)
   m=Chem.MolFromMolBlock(sdf.stdout.split('$$$$')[0],removeHs=False)
   if m is None:raise ValueError('Open Babel SDF could not be parsed')
   row['declared_stereo_inchikey']=declared_key(m,smiles)
  observations.append(row)
 except Exception as e:observations.append({'method':'openbabel_reference','error':str(e)})
 exact=[r for r in observations if r.get('full_inchikey')==expected]
 declared=[r for r in observations if r.get('declared_stereo_inchikey')==expected]
 accepted=exact or (declared if policy=='declared_stereochemistry' else [])
 return {'identity_verified':bool(accepted),'identity_policy':policy,'identity_observations':observations,'inchikey_after_optimization':accepted[0]['full_inchikey'] if accepted else next((r['full_inchikey'] for r in observations if r.get('full_inchikey')),None),'identity_match_mode':'exact' if exact else 'declared_stereochemistry' if accepted else 'unresolved'}
