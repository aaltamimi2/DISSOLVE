"""Time-bounded identity perception for isolated restart preparation.

Keeps A-3's existing connectivity decision and records timed-out engines. Does
not replace the verifier used by running collectors or alter any coordinates.
"""
import json, re, subprocess, sys, time
from pathlib import Path


def verify(xyz, expected, smiles='', seconds=30):
    from identity_campaign import decide
    observations=[]
    rdkit_code='''import json,sys
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds
m=Chem.MolFromXYZFile(sys.argv[1])
rdDetermineBonds.DetermineBonds(m,charge=0)
print(json.dumps({'full_inchikey':Chem.MolToInchiKey(m)}))
'''
    commands=[('rdkit_determine_bonds',[sys.executable,'-c',rdkit_code,str(xyz)]),
              ('openbabel_reference',['/home/aaltamimi2/anaconda3/bin/obabel',str(xyz),'-oinchikey'])]
    for method,command in commands:
        row={'method':method};start=time.monotonic()
        try:
            p=subprocess.run(command,capture_output=True,text=True,timeout=seconds)
            assert p.returncode==0,p.stderr[-2000:]
            if method=='rdkit_determine_bonds':key=json.loads(p.stdout)['full_inchikey']
            else:
                keys=re.findall(r'^[A-Z]{14}-[A-Z]{10}-[A-Z]$',p.stdout,re.M)
                assert len(keys)==1,'Expected one geometry InChIKey'
                key=keys[0]
            assert re.fullmatch(r'[A-Z]{14}-[A-Z]{10}-[A-Z]',key)
            row.update(full_inchikey=key,warnings=p.stderr.strip())
        except subprocess.TimeoutExpired:
            row.update(error=f'Perception exceeded {seconds} seconds',execution_outcome='perception_timeout')
        except Exception as exc:row.update(error=str(exc),failure_mode=type(exc).__name__)
        row['wall_seconds']=time.monotonic()-start;observations.append(row)
    result=decide(expected,observations)
    result.update(per_engine_timeout_seconds=seconds,geometry_sha256=__import__('hashlib').sha256(Path(xyz).read_bytes()).hexdigest())
    return result
