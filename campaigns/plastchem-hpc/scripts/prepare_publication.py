"""A-12: frozen preparation (ETKDGv3 seed 12345, 300 conformers, MMFF94, lowest to DFT) for the publication tier,
four workers. Charged SMILES embed as written; MMFF94 types carboxylate and sulfonate anions."""
import os
os.environ['OMP_NUM_THREADS']='1'
os.environ['OPENBLAS_NUM_THREADS']='1'
import concurrent.futures,json
from pathlib import Path
import prepare_campaign
P=Path(__file__).resolve().parents[1]/'state/publication-v1'
def prepare(m):
    prepare_campaign.P=P
    return prepare_campaign.prepare(m)
if __name__=='__main__':
    import sys
    group=sys.argv[1] if len(sys.argv)>1 else 'publication'
    rows=json.loads((P/group/'manifest.json').read_text())['molecules']
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        for m,r in zip(rows,pool.map(prepare,rows)):
            print(json.dumps({'labels':m['labels'],'key':m['inchikey'],'status':r['status'],'embedded':r.get('embedded_count'),'mmff_converged':r.get('mmff_converged_count'),'seconds':round(r['wall_seconds'],1),'failure_mode':r.get('failure_mode'),'error':r.get('error')}),flush=True)
