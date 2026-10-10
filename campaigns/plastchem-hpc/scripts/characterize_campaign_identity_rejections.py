"""Read-only structural diagnosis for all original campaign identity rejections.
No chemistry result, source label, identity policy, or disposition is changed.
"""
import argparse,collections,csv,datetime,hashlib,json,subprocess
from pathlib import Path
from rdkit import Chem,rdBase
from rdkit.Chem import rdDetermineBonds,rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize
R=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler')
OBABEL='/home/aaltamimi2/anaconda3/bin/obabel'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def bonds(m,heavy=False):
 return {tuple(sorted((b.GetBeginAtomIdx(),b.GetEndAtomIdx()))):str(b.GetBondType()) for b in m.GetBonds() if not heavy or b.GetBeginAtom().GetAtomicNum()>1 and b.GetEndAtom().GetAtomicNum()>1}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 out=a.output.resolve();assert out.is_relative_to(B) and not out.exists();out.mkdir(parents=True)
 records=[]
 for p in sorted((R/'state/campaign-v1/records').glob('*.json')):
  r=json.loads(p.read_text())
  if r.get('failure_mode')=='return_integrity_or_connectivity':records.append((p,r))
 assert len(records)==16
 rows=[]
 for p,r in records:
  key=r['inchikey'];folder=B/'results'/key;xyz=folder/'optimized.xyz'
  row=dict(input_key=key,name=r['input']['name'],cas=r['input'].get('cas'),stored_perceived_keys=r['perceived_keys_by_engine'],original_outcome=r['status'],source_record_sha256=sha(p),source_record=str(p),warnings=[])
  try:
   assert r['status']=='failed' and r['dft_status']=='converged'
   assert sha(folder/'surface.orcacosmo')==r['surface_sha256']
   for stage in ['opt','cosmo']:assert sha(folder/(stage+'.inp'))==r['stages'][stage]['input_sha256']
   row.update(surface_sha256=r['surface_sha256'],optimized_xyz_sha256=sha(xyz))
   original=Chem.AddHs(Chem.MolFromSmiles(r['input']['smiles']))
   try:
    m=Chem.MolFromXYZBlock(xyz.read_text());rdDetermineBonds.DetermineBonds(m,charge=0);engine='rdkit_determine_bonds'
   except Exception as exc:
    row['warnings'].append('RDKit bond perception: '+str(exc))
    process=subprocess.run([OBABEL,str(xyz),'-osdf'],text=True,capture_output=True,check=True)
    m=Chem.MolFromMolBlock(process.stdout.split('$$$$')[0],removeHs=False)
    assert m is not None,'Open Babel SDF parse failed'
    engine='openbabel_reference';row['warnings'].append(process.stderr.strip())
   observed=Chem.MolToInchiKey(m)
   assert observed.split('-')[0]==r['perceived_keys_by_engine'][engine].split('-')[0], 'Fresh perception differs from recorded first block'
   assert [a.GetSymbol() for a in original.GetAtoms()]==[a.GetSymbol() for a in m.GetAtoms()], 'Atom order mismatch'
   before,after=bonds(original),bonds(m)
   changed=[dict(atom_indices_zero_based=list(pair),elements=[original.GetAtomWithIdx(i).GetSymbol() for i in pair],before=before.get(pair),after=after.get(pair)) for pair in sorted(set(before)|set(after)) if before.get(pair)!=after.get(pair)]
   enum=rdMolStandardize.TautomerEnumerator()
   tautomer=[Chem.MolToInchiKey(enum.Canonicalize(Chem.RemoveHs(x))) for x in [original,m]]
   same_tautomer=tautomer[0].split('-')[0]==tautomer[1].split('-')[0]
   adjacency=set(bonds(original,True))==set(bonds(m,True))
   formula=[rdMolDescriptors.CalcMolFormula(x) for x in [original,m]]
   row.update(diagnostic_status='characterized',engine=engine,fresh_perceived_key=observed,formula_before_after=formula,formula_preserved=formula[0]==formula[1],heavy_atom_adjacency_unchanged=adjacency,canonical_tautomer_keys=tautomer,canonical_tautomer_first_block_match=same_tautomer,canonical_tautomer_full_match=tautomer[0]==tautomer[1],bond_changes=changed,fragment_counts=[len(Chem.GetMolFrags(x)) for x in [original,m]],diagnosis='canonical_tautomer_equivalent_with_unchanged_heavy_adjacency' if same_tautomer and adjacency and formula[0]==formula[1] else 'not_resolved_as_same_canonical_tautomer')
  except Exception as exc:row.update(diagnostic_status='unresolved_diagnostic',error=str(exc))
  rows.append(row);print(json.dumps({k:row.get(k) for k in ['input_key','diagnostic_status','diagnosis','error']}),flush=True)
 summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),denominator=16,original_campaign_denominator=5824,rdkit_version=rdBase.rdkitVersion,script_sha256=sha(Path(__file__)),diagnostic_status_counts=dict(collections.Counter(r['diagnostic_status'] for r in rows)),diagnosis_counts=dict(collections.Counter(r.get('diagnosis','unresolved_diagnostic') for r in rows)),policy_changed=False,interpretation='Endpoint graph and canonical-tautomer diagnostic only. All 16 remain rejected under the owner first-block rule. No reaction path, equilibrium distribution or policy override established.',rows=rows)
 (out/'diagnosis.json').write_text(json.dumps(summary,indent=2)+'\n')
 fields=['input_key','name','cas','diagnostic_status','diagnosis','engine','fresh_perceived_key','formula_preserved','heavy_atom_adjacency_unchanged','canonical_tautomer_first_block_match','error']
 with (out/'diagnosis.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
 print(json.dumps({k:v for k,v in summary.items() if k!='rows'}))
if __name__=='__main__':main()
