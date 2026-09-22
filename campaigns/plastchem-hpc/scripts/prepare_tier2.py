"""A-4: reuse frozen preparation in an isolated tier, at most eight workers."""
import os
os.environ['OMP_NUM_THREADS']='1'
os.environ['OPENBLAS_NUM_THREADS']='1'
import concurrent.futures,json,time
from pathlib import Path
import prepare_campaign
P=Path(__file__).resolve().parents[1]/'state/tier2-v1'
def prepare(m):
    prepare_campaign.P=P
    return prepare_campaign.prepare(m)
def available():
    return int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])
def rss(pid):
    try:return int(next(x for x in Path(f'/proc/{pid}/status').read_text().splitlines() if x.startswith('VmRSS:')).split()[1])
    except (OSError,StopIteration):return 0
if __name__=='__main__':
    rows=json.loads((P/'tier2/manifest.json').read_text())['molecules']
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
            stats={'epoch':time.time(),'initial_pending':len(pending),'completed_this_pass':completed,'active':len(active),'workers_limit':8,'elapsed_seconds':time.time()-start,'worker_tree_peak_rss_kib':peak,'available_kib':available()}
            tmp=P/'preparation-stats.tmp';tmp.write_text(json.dumps(stats,indent=2)+'\n');tmp.replace(P/'preparation-stats.json')
            if not active and not exhausted:time.sleep(5)
    print('TIER2_PREPARATION_DISPOSED',flush=True)
