"""A-5 PE report, only after all 31 input conformers have terminal dispositions."""
import json,csv,math,re,hashlib,datetime,argparse
from pathlib import Path
import numpy as np
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds,rdMolAlign
R=Path(__file__).resolve().parents[1];P=R/'state/polymer-v1'
T=298.15;EH_KJMOL=2625.4996394799;RGAS_KJMOL=0.00831446261815324
MERGE_RMSD_A=0.10;MERGE_ENERGY_EH=1e-5

def weights(energies):
 d=np.asarray(energies)-min(energies);a=np.exp(-d*EH_KJMOL/(RGAS_KJMOL*T));return a/a.sum()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
 molecules=[m for m in json.loads((P/'body/manifest.json').read_text())['molecules'] if m['polymer']=='pe'];assert len(molecules)==31
 walltime_receipt=P/'body-walltime-verified.json'
 walltime=json.loads(walltime_receipt.read_text());assert walltime['pe_tasks']==31 and walltime['pe_hours']==3
 rows=[];geometries={}
 for m in molecules:
  f=P/'records'/(m['entry_id']+'.json');r=json.loads(f.read_text()) if f.exists() else {};s=r.get('status','not_yet_run')
  if r.get('failure_mode')=='slurm_timeout':print('PE timeout requires authorised longer-walltime retry: '+m['entry_id']);return 2
  if s not in ['converged','failed']:print('PE pending: '+m['entry_id']);return 2
  row={'entry_id':m['entry_id'],'conformer_id':m['conformer_id'],'species':m['species'],'status':s,'failure_mode':r.get('failure_mode'),'cpu_model':r.get('cpu_model'),'record_sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
  row.update(initial_runner_walltime=r.get('resources',{}).get('request_walltime'),scheduler_walltime_hours=3,scheduler_walltime_evidence_sha256=hashlib.sha256(walltime_receipt.read_bytes()).hexdigest())
  if s=='converged':
   assert r['connectivity_match'] and r['cpu_model']=='AMD EPYC 7763 64-Core Processor'
   d=Path(r['archive_path']);raw=(d/'surface.orcacosmo').read_bytes();assert hashlib.sha256(raw).hexdigest()==r['surface_sha256']
   energy=float(r['stages']['opt']['final_energies_hartree'][-1]);volume=float(re.search(r'^\s*([0-9.eE+-]+)\s+#\s*Volume',raw.decode(),re.M).group(1))
   row.update(opt_electronic_energy_hartree=energy,cosmo_solute_energy_hartree=r['cosmo_solute_energy_hartree'],cavity_volume_bohr3=volume,cavity_volume_angstrom3=volume*0.52917721092**3,surface_sha256=r['surface_sha256'],archive_path=str(d),input_inchikey=m['inchikey'],perceived_inchikey=r['perceived_inchikey'])
   mol=Chem.MolFromXYZFile(str(d/'optimized.xyz'));rdDetermineBonds.DetermineBonds(mol,charge=0);mol=Chem.RemoveHs(mol);geometries[m['entry_id']]=mol
  rows.append(row)
 accepted=sorted([r for r in rows if r['status']=='converged'],key=lambda r:(r['opt_electronic_energy_hartree'],r['entry_id']));assert accepted
 rawweights=weights([r['opt_electronic_energy_hartree'] for r in accepted]);emin=accepted[0]['opt_electronic_energy_hartree'];representatives=[];merges=[]
 for row,w in zip(accepted,rawweights):
  row.update(relative_opt_energy_kj_mol=(row['opt_electronic_energy_hartree']-emin)*EH_KJMOL,raw_row_boltzmann_weight_298K=float(w),merged_into=None)
  for rep in representatives:
   delta=abs(row['opt_electronic_energy_hartree']-rep['opt_electronic_energy_hartree'])
   if delta>MERGE_ENERGY_EH:continue
   rmsd=rdMolAlign.GetBestRMS(Chem.Mol(geometries[row['entry_id']]),Chem.Mol(geometries[rep['entry_id']]),maxMatches=100000)
   if rmsd<=MERGE_RMSD_A:
    row['merged_into']=rep['entry_id'];merges.append({'entry_id':row['entry_id'],'merged_into':rep['entry_id'],'heavy_atom_symmetry_aligned_rmsd_A':rmsd,'absolute_opt_energy_difference_hartree':delta});break
  if row['merged_into'] is None:representatives.append(row)
 repweights=weights([r['opt_electronic_energy_hartree'] for r in representatives]);repw={r['entry_id']:float(w) for r,w in zip(representatives,repweights)}
 for row in accepted:row['unique_representative_boltzmann_weight_298K']=repw.get(row['entry_id'],0.0)
 D=args.output.resolve();assert D.is_relative_to(Path('/mnt/r/plastchem-euler/polymer-v1'));D.mkdir(parents=True,exist_ok=False)
 fields=list(dict.fromkeys(k for r in rows for k in r))
 with (D/'pe-conformers.csv').open('w',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
 summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'denominator':31,'accepted':len(accepted),'failed':31-len(accepted),'unique_representatives':len(representatives),'merge_count':len(merges),'temperature_K':T,'energy_basis':'gas-phase OPT electronic energy, BP86/def2-TZVP(-f); not vibrational free energy','merge_criteria':{'heavy_atom_symmetry_aligned_rmsd_A_max':MERGE_RMSD_A,'absolute_opt_energy_difference_hartree_max':MERGE_ENERGY_EH,'assignment':'Compare directly with lowest-energy accepted representative; no transitive chaining'},'merges':merges,'raw_weight_sum':sum(rawweights),'unique_representative_weight_sum':sum(repweights),'notes':['All 31 rows retained; failed rows carry no weight or invented energy','Raw row weights retain source-library duplication; representative weights count each merged basin once','No polymer-contaminant partition coefficient computed','Runner resource metadata retains the initial 72-hour submission request; authoritative scheduler readback after the owner-directed update confirms PE at 3 hours'],'volume_conversion_source':'opencosmorspy/input_parsers.py ORCA volume parser: bohr^3 times 0.52917721092^3'}
 (D/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(D/'REPORT.md').write_text('# PE conformer results\n\n'+str(len(accepted))+'/31 accepted; '+str(31-len(accepted))+' failed. '+str(len(representatives))+' distinct representatives under the stated merge criteria.\n\nWeights use gas-phase optimized electronic energies at 298.15 K, with no vibrational or configurational entropy correction. The CSV supplies both raw source-row weights and weights over unique representatives; merged source rows remain visible. Criteria and every merge RMSD/energy difference are in summary.json. Cavity volumes and surface hashes are in pe-conformers.csv. No polymer-contaminant partition calculations were performed.\n')
 (D/'report_polymer_pe.py').write_bytes(Path(__file__).read_bytes());(D/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file()))
 print(json.dumps({k:v for k,v in summary.items() if k!='merges'}));return 0
if __name__=='__main__':raise SystemExit(main())
