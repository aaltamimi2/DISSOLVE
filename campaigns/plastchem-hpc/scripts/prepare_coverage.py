"""A-13: reuse frozen preparation (prepare_campaign.prepare: ETKDGv3 seed 12345, up to 300 conformers, pruneRmsThresh 0.5,
MMFF94 maxIters 2000, the lowest-MMFF-energy conformer to DFT; no force-field substitution) in an isolated tier, one
chunk at a time, at most eight workers (copied from prepare_halogen.py).

    ~/.venvs/cosmo-logp/bin/python scripts/prepare_coverage.py c01"""
import os
os.environ['OMP_NUM_THREADS']='1'
os.environ['OPENBLAS_NUM_THREADS']='1'
import concurrent.futures,json,re,sys,time
from pathlib import Path
import prepare_campaign
P=Path(__file__).resolve().parents[1]/'state/coverage-v1'
def prepare(m):
    prepare_campaign.P=P
    return prepare_campaign.prepare(m)
def available():
    return int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])
def rss(pid):
    try:return int(next(x for x in Path(f'/proc/{pid}/status').read_text().splitlines() if x.startswith('VmRSS:')).split()[1])
    except (OSError,StopIteration):return 0
if __name__=='__main__':
    group=sys.argv[1];assert re.fullmatch(r'c\d\d',group),group
    rows=json.loads((P/group/'manifest.json').read_text())['molecules']
    pending=[]
    for m in rows:
        d=P/'prepared'/m['inchikey']
        if not (d/'preparation.json').exists():
            assert not (d/'attempt.lock').exists(),f'Unconfirmed preparation: {d}'
            pending.append(m)
    todo=iter(pending);exhausted=False;completed=0;peak=0;start=time.time()
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        active={}
        while active or not exhausted:
            while len(active)<8 and not exhausted and available()>2*1024*1024:
                m=next(todo,None)
                if m is None:exhausted=True;break
                active[pool.submit(prepare,m)]=m
            peak=max(peak,sum(rss(pid) for pid in [os.getpid(),*pool._processes]))
            done,_=concurrent.futures.wait(active,timeout=5,return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                m=active.pop(f);r=f.result();completed+=1
                print(json.dumps({'key':m['inchikey'],'status':r['status'],'seconds':r['wall_seconds'],'failure_mode':r.get('failure_mode')}),flush=True)
            stats={'epoch':time.time(),'group':group,'initial_pending':len(pending),'completed_this_pass':completed,'active':len(active),'workers_limit':8,'elapsed_seconds':time.time()-start,'worker_tree_peak_rss_kib':peak,'available_kib':available()}
            tmp=P/'preparation-stats.tmp';tmp.write_text(json.dumps(stats,indent=2)+'\n');tmp.replace(P/'preparation-stats.json')
            if not active and not exhausted:time.sleep(5)
    print('COVERAGE_PREPARATION_DISPOSED',group,flush=True)
