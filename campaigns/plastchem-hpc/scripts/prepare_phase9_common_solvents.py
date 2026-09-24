"""Prepare A-9 common-solvent geometries from licensed lowest-energy conformers.

Bulk inputs stay outside git. Only GVL, absent from the source, uses the existing
frozen ETKDG/MMFF preparation. No scientific DFT or COSMO-RS solve is run locally.
"""
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
import hashlib
import json
import time
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import Descriptors, rdDetermineBonds
from opencosmorspy.input_parsers import SigmaProfileParser
import prepare_campaign

D=Path('/mnt/r/plastchem-euler/phase9-solvent-library-v1')


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    inventory=json.loads((D/'common-name-inventory.json').read_text())
    rows=[];failures=[]
    for source in inventory['rows']:
        started=time.monotonic()
        try:
            if source['solvent_key']=='gvl':
                smiles=source['structure_proposal_smiles'];m=Chem.MolFromSmiles(smiles)
                key=Chem.MolToInchiKey(m)
                row=dict(name='gvl',inchikey=key,smiles=smiles,atoms=Chem.AddHs(m).GetNumAtoms(),molecular_weight_g_mol=Descriptors.MolWt(m),group='solvent_library',role='A-9 common solvent')
                prepare_campaign.P=D;prep=prepare_campaign.prepare(row)
                assert prep['status']=='prepared',prep
                row.update(source='structure; absent from COSMObase-1901',preparation_sha256=sha(D/'prepared'/key/'preparation.json'))
            else:
                surface=Path(source['source_cosmo']);assert sha(surface)==source['source_cosmo_sha256']
                energy=Path(source['source_energy']);assert sha(energy)==source['source_energy_sha256']
                parsed=SigmaProfileParser(str(surface))
                xyz=str(len(parsed['atm_elmnt']))+'\nA-9 lowest-energy COSMObase conformer; coordinates in angstrom\n'+''.join(f'{e} {p[0]:.10f} {p[1]:.10f} {p[2]:.10f}\n' for e,p in zip(parsed['atm_elmnt'],parsed['atm_pos']))
                m=Chem.MolFromXYZBlock(xyz);rdDetermineBonds.DetermineBonds(m,charge=0)
                assert Chem.GetFormalCharge(m)==0 and len(Chem.GetMolFrags(m))==1
                assert not any(a.GetNumRadicalElectrons() or a.GetIsotope() for a in m.GetAtoms())
                perceived=Chem.MolToInchiKey(m);heavy=Chem.RemoveHs(m);Chem.RemoveStereochemistry(heavy)
                smiles=Chem.MolToSmiles(heavy,isomericSmiles=False);parent=Chem.MolFromSmiles(smiles);key=Chem.MolToInchiKey(parent)
                assert perceived.split('-')[0]==key.split('-')[0]
                # Independent geometry in the matched .energy file must give the same connectivity.
                energy_lines=energy.read_text().splitlines()
                energy_xyz='\n'.join(energy_lines[:int(energy_lines[0])+2])+'\n'
                e_m=Chem.MolFromXYZBlock(energy_xyz);assert e_m is not None
                rdDetermineBonds.DetermineBonds(e_m,charge=0)
                assert Chem.MolToInchiKey(e_m).split('-')[0]==key.split('-')[0]
                assert e_m.GetNumAtoms()==m.GetNumAtoms()
                row=dict(name=source['solvent_key'],inchikey=key,smiles=smiles,atoms=m.GetNumAtoms(),molecular_weight_g_mol=Descriptors.MolWt(parent),group='solvent_library',role='A-9 common solvent',source_cosmo=str(surface),source_cosmo_sha256=source['source_cosmo_sha256'],source_energy=str(energy),source_energy_sha256=source['source_energy_sha256'],source_energy_hartree=source['lowest_energy_hartree'],source_perceived_inchikey=perceived,source_identity_engines=['RDKit COSMO coordinates','RDKit energy-file coordinates'])
                path=D/'prepared'/key;path.mkdir(parents=True,exist_ok=True)
                if (path/'input.xyz').exists():assert (path/'input.xyz').read_text()==xyz
                else:(path/'input.xyz').write_text(xyz)
                prep=dict(input=row,status='prepared',recipe='A-9 COSMObase lowest-energy conformer; no new embedding',dft_conformer_count=1,xyz_sha256=sha(path/'input.xyz'),wall_seconds=time.monotonic()-started)
                save(path/'preparation.json',prep)
                row['preparation_sha256']=sha(path/'preparation.json')
            row.update(array_index=len(rows),input_stereo_specified=False)
            rows.append(row);print(row['name'],key,'prepared',row['atoms'],flush=True)
        except Exception as e:
            failures.append(dict(solvent_key=source['solvent_key'],failure_mode=type(e).__name__,error=str(e)))
            print('PREFLIGHT_FAILURE',source['solvent_key'],repr(e),flush=True)
    assert len(rows)+len(failures)==69
    assert len({r['inchikey'] for r in rows})==len(rows),'Duplicate common-solvent connectivity requires explicit mapping'
    policy=dict(mode='connectivity_first_block_with_computed_stereochemistry',authority='A-3 D-IDENT and A-9',require_stereo_layer_match=False,campaign_concurrency_limit=64)
    save(D/'policy.json',policy)
    save(D/'solvent_library/manifest.json',dict(molecules=rows,preflight_failures=failures,denominator=69,policy=policy,concurrency=4,walltime='08:00:00',partition='research',constraint='milan&cpu',exclude=['euler09','euler10'],cpus=1,memory='4G',recipe='unchanged serial ORCA 6.1.1 BP86 def2-TZVP(-f) OPT then COSMORS(Water), maxcore1500',inventory_sha256=sha(D/'common-name-inventory.json')))
    save(D/'preparation-summary.json',dict(prepared=len(rows),preflight_failures=failures,denominator=69))


if __name__=='__main__':main()
