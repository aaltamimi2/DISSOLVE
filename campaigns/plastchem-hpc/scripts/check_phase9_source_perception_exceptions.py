"""Separate two-method recheck of unresolved A-9 source pairs; no DFT/edits.

Does not replace the original failure rows or certify product/CAS identity.
All source-derived output stays in the untracked bulk inventory directory.
"""
import datetime, hashlib, json, os, subprocess
from pathlib import Path
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[k]='1'
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds
from opencosmorspy.input_parsers import SigmaProfileParser

D=Path('/mnt/r/plastchem-euler/phase9-solvent-library-v1/full-grid-source-audit')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    original=D/'rows.jsonl'
    rows=[json.loads(line) for line in original.read_text().splitlines()]
    pending=[r for r in rows if r['status']=='source_preflight_unresolved']
    assert len(pending)==7
    results=[]
    for r in pending:
        assert sha(r['source_cosmo'])==r['source_cosmo_sha256']
        assert sha(r['source_energy'])==r['source_energy_sha256']
        parsed=SigmaProfileParser(r['source_cosmo']);n=len(parsed['atm_elmnt'])
        xyz=str(n)+'\nunchanged source coordinates\n'+''.join(f'{e} {p[0]:.10f} {p[1]:.10f} {p[2]:.10f}\n' for e,p in zip(parsed['atm_elmnt'],parsed['atm_pos']))
        lines=Path(r['source_energy']).read_text().splitlines()
        blocks={'cosmo':xyz,'energy':'\n'.join(lines[:int(lines[0])+2])+'\n'}
        evidence=[]
        for source,block in blocks.items():
            for method in ['RDKit extended-Huckel connectivity','OpenBabel bond perception']:
                record=dict(source=source,method=method)
                try:
                    if method.startswith('RDKit'):
                        mol=Chem.MolFromXYZBlock(block);rdDetermineBonds.DetermineBonds(mol,charge=0,useHueckel=True)
                    else:
                        p=subprocess.run(['obabel','-ixyz','-oinchi'],input=block,capture_output=True,text=True,timeout=20)
                        record['stderr']=p.stderr
                        assert p.returncode==0,p.stderr
                        inchi=next(s for s in p.stdout.splitlines() if s.startswith('InChI='))
                        record['inchi']=inchi;mol=Chem.MolFromInchi(inchi)
                    assert mol is not None and Chem.GetFormalCharge(mol)==0 and len(Chem.GetMolFrags(mol))==1
                    assert not any(a.GetNumRadicalElectrons() for a in mol.GetAtoms())
                    record.update(status='perceived',inchikey=Chem.MolToInchiKey(mol))
                except Exception as exc:record.update(status='unresolved',error=str(exc),failure_mode=type(exc).__name__)
                evidence.append(record)
        passed=all(v['status']=='perceived' for v in evidence) and len({v['inchikey'].split('-')[0] for v in evidence})==1
        out=dict(solvent_key=r['solvent_key'],status='both_methods_and_source_geometries_agree' if passed else 'still_unresolved',
                 source_cosmo_sha256=r['source_cosmo_sha256'],source_energy_sha256=r['source_energy_sha256'],evidence=evidence)
        results.append(out);print(r['solvent_key'],out['status'],flush=True)
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),original_rows_sha256=sha(original),script_sha256=sha(__file__),
                openbabel_version=subprocess.run(['obabel','-V'],capture_output=True,text=True,check=True).stdout.strip(),
                denominator=7,agreed=sum(r['status']=='both_methods_and_source_geometries_agree' for r in results),rows=results,
                scope='Separate source consistency evidence only. Original default-perception failures retained. No source coordinates changed, no product/CAS identity decision, no DFT.')
    target=D/'perception-exceptions-recheck.json'
    assert not target.exists(),'Do not overwrite existing evidence'
    target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))


if __name__=='__main__':main()
