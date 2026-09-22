import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import concurrent.futures,hashlib,json,time,resource
from pathlib import Path
import prepare_campaign as original
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1';OUT=P/'throughput-steer';REPRO=OUT/'reproducibility'
def work(m):
    original.P=REPRO
    start=time.time();r=original.prepare(m)
    canonical=json.loads((P/'prepared'/m['inchikey']/'preparation.json').read_text())
    a=(P/'prepared'/m['inchikey']/'input.xyz').read_bytes();b=(REPRO/'prepared'/m['inchikey']/'input.xyz').read_bytes()
    return {'inchikey':m['inchikey'],'group':m['group'],'atoms':m['atoms'],'byte_identical_xyz':a==b,'xyz_sha256':hashlib.sha256(b).hexdigest(),'original_seed':canonical['recipe']['seed'],'repeated_seed':r['recipe']['seed'],'same_recipe':canonical['recipe']==r['recipe'],'same_selected_conformer':canonical['selected_conformer_id']==r['selected_conformer_id'],'wall_seconds':time.time()-start,'original_wall_seconds':canonical['wall_seconds'],'worker_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
if __name__=='__main__':
    candidates=[]
    for group in ['main_le80','tail_gt80']:
        ms=json.loads((P/group/'manifest.json').read_text())['molecules']
        ready=[m for m in ms if (P/'prepared'/m['inchikey']/'input.xyz').exists()]
        candidates += [ready[0],ready[min(20,len(ready)-1)]]
    start=time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:rows=list(pool.map(work,candidates))
    result={'cases':rows,'all_pass':all(r['byte_identical_xyz'] and r['same_recipe'] and r['same_selected_conformer'] and r['original_seed']==r['repeated_seed']==12345 for r in rows),'wall_seconds':time.time()-start,'reproduction_workers':4}
    (OUT/'reproducibility.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)
    assert result['all_pass']
