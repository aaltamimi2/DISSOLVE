"""Describe selected observed identity rejections; never override the frozen policy."""
import argparse,datetime,hashlib,json,math
from pathlib import Path
from rdkit import Chem,rdBase
from rdkit.Chem import rdDetermineBonds,rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize
R=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler')
KEYS=['JBJBSAHNMRBDSH-UHFFFAOYSA-N','GLZGTQYOWXFUNZ-UHFFFAOYSA-N']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def bonds(m,heavy=False):
 return {tuple(sorted([b.GetBeginAtomIdx(),b.GetEndAtomIdx()])):str(b.GetBondType()) for b in m.GetBonds() if not heavy or b.GetBeginAtom().GetAtomicNum()>1 and b.GetEndAtom().GetAtomicNum()>1}
def dist(m,i,j):
 c=m.GetConformer();return (c.GetAtomPosition(i)-c.GetAtomPosition(j)).Length()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',required=True,type=Path);ap.add_argument('--key',action='append',help='Explicit input key; repeat for multiple cases. Default preserves the original two-case cohort.');args=ap.parse_args()
 out=args.output.resolve();assert out.is_relative_to(B) and not out.exists();out.mkdir(parents=True)
 rows=[]
 selected=args.key or KEYS
 assert len(selected)==len(set(selected))
 for key in selected:
  record=R/'state/tier2-v1/records'/f'{key}.json';r=json.loads(record.read_text())
  assert r['status']=='failed' and r['failure_mode']=='return_integrity_or_connectivity'
  assert r['dft_status']=='converged' and r['slurm_accounting']['state']=='COMPLETED'
  folder=B/'tier2-v1/results'/key;start_path=R/'state/tier2-v1/prepared'/key/'input.xyz'
  assert sha(start_path)==r['preparation']['xyz_sha256']
  assert sha(folder/'surface.orcacosmo')==r['surface_sha256']
  for stage in ['opt','cosmo']:
   assert r['stages'][stage]['exit_code']==0
   assert sha(folder/(stage+'.inp'))==r['stages'][stage]['input_sha256']
  original=Chem.AddHs(Chem.MolFromSmiles(r['input']['smiles']))
  start=Chem.MolFromXYZBlock(start_path.read_text())
  final=Chem.MolFromXYZBlock((folder/'optimized.xyz').read_text())
  symbols=lambda m:[a.GetSymbol() for a in m.GetAtoms()]
  assert symbols(original)==symbols(start)==symbols(final)
  rdDetermineBonds.DetermineBonds(final,charge=0)
  perceived=Chem.MolToInchiKey(final)
  assert perceived==r['perceived_keys_by_engine']['rdkit_determine_bonds']
  old,new=bonds(original),bonds(final)
  changes=[dict(atoms_zero_based=list(pair),elements=[original.GetAtomWithIdx(i).GetSymbol() for i in pair],before=old.get(pair),after=new.get(pair),start_distance_A=dist(start,*pair),final_distance_A=dist(final,*pair)) for pair in sorted(set(old)|set(new)) if old.get(pair)!=new.get(pair)]
  canonical=[Chem.MolToInchiKey(rdMolStandardize.TautomerEnumerator().Canonicalize(Chem.RemoveHs(m))) for m in [original,final]]
  assert canonical[0]==canonical[1] and key.split('-')[0]!=perceived.split('-')[0]
  assert set(bonds(original,True))==set(bonds(final,True))
  forms=[rdMolDescriptors.CalcMolFormula(m) for m in [original,final]];assert forms[0]==forms[1]
  row=dict(input_key=key,name=r['input']['name'],cas=r['input']['cas'],task='63873_'+str(r['array_task_id']),perceived_keys_by_engine=r['perceived_keys_by_engine'],formula_before_after=forms,heavy_atom_adjacency_unchanged=True,canonical_tautomer_keys=canonical,bond_changes=changes,elapsed_hours=r['elapsed_seconds']/3600,slurm_accounting=r['slurm_accounting'],policy_disposition='remains_failed_connectivity_first_block; no prediction promoted; no retry or exclusion',interpretation='Endpoint structures support O-to-N proton transfer with azo/hydrazone bond rearrangement. Canonical-tautomer equality is diagnostic only; no reaction path or energetic preference established.',source_pins={str(p):sha(p) for p in [record,start_path,folder/'optimized.xyz',folder/'surface.orcacosmo',folder/'opt.inp',folder/'cosmo.inp']})
  rows.append(row)
 receipt=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),rdkit_version=rdBase.rdkitVersion,script_sha256=sha(Path(__file__)),rows=rows,policy_changed=False,scope=f'{len(rows)} explicitly selected first-block failures only; no reclassification of campaign outcomes')
 (out/'diagnosis.json').write_text(json.dumps(receipt,indent=2)+'\n')
 print(json.dumps({'path':str(out/'diagnosis.json'),'sha256':sha(out/'diagnosis.json'),'cases':len(rows),'policy_changed':False}))
if __name__=='__main__':main()
