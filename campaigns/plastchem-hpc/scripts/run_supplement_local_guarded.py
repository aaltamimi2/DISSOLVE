"""Run one local reproduction process with an external memory stop.

No automatic retry. A fresh interpreter can resume its sealed checkpoints.
Allocator trimming changes memory reclamation only, not numerical settings.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

R=Path(__file__).resolve().parents[1]
B=Path('/mnt/r/plastchem-euler')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan',required=True,type=Path)
    ap.add_argument('--kind',choices=['partition','lle'],required=True)
    ap.add_argument('--receipt',required=True,type=Path)
    args=ap.parse_args();plan=json.loads(args.plan.read_text())
    assert plan['purpose']=='reproduction_only_not_production'
    receipt=args.receipt.resolve();assert receipt.is_relative_to(B/'supplement-v1') and not receipt.exists()
    script=R/'scripts'/('supplement_'+args.kind+'_worker.py')
    assert sha(script)==plan['code_pins'][str(script)]
    env=dict(os.environ,MALLOC_ARENA_MAX='1',MALLOC_TRIM_THRESHOLD_='65536')
    command=['/home/aaltamimi2/.venvs/cosmo-logp/bin/python','-u',str(script),'--plan',str(args.plan.resolve()),'--local-reproduction']
    evidence=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),plan_sha256=sha(args.plan),
        launcher_sha256=sha(__file__),command=command,allocator_environment={k:env[k] for k in ('MALLOC_ARENA_MAX','MALLOC_TRIM_THRESHOLD_')},
        rss_stop_kib=1750*1024,host_available_stop_kib=int(2.5*1024**2),automatic_retry=False)
    def save():
        tmp=receipt.with_suffix('.tmp');tmp.write_text(json.dumps(evidence,indent=2)+'\n');tmp.replace(receipt)
    start=time.monotonic();peak=0;minimum=None;stopped=False
    log=receipt.with_suffix('.log')
    with log.open('x') as stream:
        p=subprocess.Popen(command,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        evidence.update(pid=p.pid,log=str(log),status='running');save()
        while p.poll() is None:
            try:
                rss=int(next(x.split()[1] for x in (Path('/proc')/str(p.pid)/'status').read_text().splitlines() if x.startswith('VmRSS:')))
                available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
            except (FileNotFoundError,ProcessLookupError,StopIteration):
                time.sleep(.1);continue
            peak=max(peak,rss);minimum=available if minimum is None else min(minimum,available)
            if not stopped and (rss>=evidence['rss_stop_kib'] or available<evidence['host_available_stop_kib']):
                p.send_signal(signal.SIGINT);stopped=True
                evidence.update(stop_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),stop_rss_kib=rss,stop_host_available_kib=available)
                save()
            time.sleep(.1)
        code=p.wait()
    evidence.update(status='memory_stopped' if stopped else ('completed' if code==0 else 'failed'),exit_code=code,
        completed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),elapsed_seconds=time.monotonic()-start,
        peak_sampled_rss_kib=peak,minimum_host_available_kib=minimum,log_sha256=sha(log));save();print(json.dumps(evidence))
    raise SystemExit(code if code>=0 else 128-code)

if __name__=='__main__':main()
