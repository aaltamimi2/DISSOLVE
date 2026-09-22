"""Throughput steer: eight serial process workers, main first, bounded dispatch and RSS accounting."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import concurrent.futures,json,time
from pathlib import Path
from prepare_campaign import prepare, P
OUT=P/'throughput-steer'
def rss_kib(pid):
    try:return int(next(l for l in Path(f'/proc/{pid}/status').read_text().splitlines() if l.startswith('VmRSS:')).split()[1])
    except (OSError,StopIteration):return 0
def available_kib():return int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])
if __name__=='__main__':
    assert json.loads((OUT/'reproducibility.json').read_text())['all_pass']
    pending=[]
    for group in ['main_le80','tail_gt80']:
        for m in json.loads((P/group/'manifest.json').read_text())['molecules']:
            d=P/'prepared'/m['inchikey']
            if not (d/'preparation.json').exists():
                assert not (d/'attempt.lock').exists(),f'Undisposed earlier attempt: {d}'
                pending.append(m)
    start=time.time();rows=[];peak=0;minimum=available_kib();todo=iter(pending);exhausted=False
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        active={}
        while active or not exhausted:
            # No more than eight molecules are submitted or executing at a time.
            while len(active)<8 and not exhausted and available_kib()>700*1024:
                m=next(todo,None)
                if m is None:exhausted=True;break
                active[pool.submit(prepare,m)]=m
            sample=sum(rss_kib(pid) for pid in [os.getpid(),*pool._processes])
            peak=max(peak,sample);minimum=min(minimum,available_kib())
            done,_=concurrent.futures.wait(active,timeout=5,return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                m=active.pop(f);r=f.result();entry={'epoch':time.time(),'key':m['inchikey'],'group':m['group'],'atoms':m['atoms'],'status':r['status'],'wall_seconds':r['wall_seconds'],'failure_mode':r.get('failure_mode')};rows.append(entry);print(json.dumps(entry),flush=True)
            stats={'start_epoch':start,'epoch':time.time(),'workers':8,'initial_pending':len(pending),'completed':len(rows),'elapsed_seconds':time.time()-start,'completed_per_hour':len(rows)*3600/(time.time()-start),'worker_tree_peak_rss_kib':peak,'minimum_system_available_kib':minimum,'remaining_order':'main_le80 then tail_gt80','dispatch_paused_for_memory':not exhausted and available_kib()<=700*1024}
            tmp=OUT/'parallel-stats.tmp';tmp.write_text(json.dumps(stats,indent=2)+'\n');tmp.replace(OUT/'parallel-stats.json')
            if not active and not exhausted:time.sleep(5)
    print('PREPARATION_ALL_TARGETS_DISPOSED',flush=True)
