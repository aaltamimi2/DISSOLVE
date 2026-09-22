"""Four local serial workers; frozen ETKDG/MMFF ranking; durable preparation failures."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import concurrent.futures,hashlib,json,time
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import AllChem
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1'

def prepare(m):
    path=P/'prepared'/m['inchikey'];path.mkdir(parents=True,exist_ok=True)
    record=path/'preparation.json'
    if record.exists():return json.loads(record.read_text())
    start=time.monotonic();r={'input':m,'status':'preparing','recipe':{'n_embed':300,'seed':12345,'prune_rms':0.5,'max_mmff_iters':2000,'dft_conformers':1}}
    try:
        with (path/'attempt.lock').open('x') as f:f.write(str(os.getpid()))
        mol=Chem.MolFromSmiles(m['smiles'])
        assert mol is not None and Chem.MolToInchiKey(mol)==m['inchikey']
        assert not any(a.GetIsotope() for a in mol.GetAtoms())
        h=Chem.AddHs(mol)
        if not AllChem.MMFFHasAllMoleculeParams(h):
            r['failure_mode']='mmff_parameters_unavailable';raise ValueError('No MMFF parameters; frozen recipe permits no force-field substitution')
        params=AllChem.ETKDGv3();params.randomSeed=12345;params.pruneRmsThresh=0.5;params.numThreads=1
        ids=list(AllChem.EmbedMultipleConfs(h,numConfs=300,params=params))
        scores=AllChem.MMFFOptimizeMoleculeConfs(h,numThreads=1,maxIters=2000)
        ranked=sorted((energy,cid) for cid,(status,energy) in zip(ids,scores) if status==0)
        if not ranked:r['failure_mode']='no_converged_mmff_conformer';raise ValueError('No candidate converged under MMFF')
        energy,cid=ranked[0];xyz=Chem.MolToXYZBlock(h,confId=cid)
        (path/'input.xyz').write_text(xyz)
        r.update(status='prepared',embedded_count=len(ids),mmff_converged_count=len(ranked),selected_conformer_id=cid,selected_mmff_energy_kcal=energy,dft_conformer_count=1,xyz_sha256=hashlib.sha256(xyz.encode()).hexdigest())
    except Exception as exc:r.update(status='preparation_failed',failure_mode=r.get('failure_mode',type(exc).__name__),error=str(exc))
    r['wall_seconds']=time.monotonic()-start
    tmp=record.with_suffix('.tmp');tmp.write_text(json.dumps(r,indent=2)+'\n');tmp.replace(record)
    return r

if __name__=='__main__':
    # Finish the separately scheduled extrapolated tail first while the main body prepares.
    molecules=[]
    for group in ['tail_gt80','main_le80']:
        molecules+=json.loads((P/group/'manifest.json').read_text())['molecules']
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(prepare,m):m for m in molecules}
        for i,future in enumerate(concurrent.futures.as_completed(jobs),1):
            r=future.result();print(json.dumps({'completed':i,'total':len(molecules),'key':r['input']['inchikey'],'group':r['input']['group'],'status':r['status'],'seconds':round(r['wall_seconds'],2),'failure_mode':r.get('failure_mode')}),flush=True)
