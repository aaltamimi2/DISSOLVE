"""Serial A-9 collection and comparison cadence. Never submits or cancels jobs."""
import datetime,fcntl,json,os,subprocess,sys,time
from pathlib import Path
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase9-v1');S=R/'state/phase9-v1'


def main():
    lock=(S/'watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (S/'watch-process.json').write_text(json.dumps(dict(pid=os.getpid(),started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))+'\n')
    while True:
        start=time.monotonic()
        try:
            available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
            if available<2.5*1024**3:raise MemoryError('Collection paused below 2.5 GiB available')
            for script in ['collect_phase9.py','analyze_phase9_gate.py','analyze_phase9_cost.py']:
                r=subprocess.run([sys.executable,str(R/'scripts'/script)],capture_output=True,text=True)
                if r.returncode:raise RuntimeError(script+': '+r.stderr[-4000:])
            gate=json.loads((D/'gate-comparison.json').read_text());status={k:v for k,v in gate.items() if k not in ['errors','cost_screen']};status['errors']=len(gate['errors'])
            print(json.dumps(status),flush=True)
            cost=json.loads((D/'chunk-cost.json').read_text());status['chunk_cost_status']=cost['status'];status['chunk_lle_completed']=cost['lle_systems']
            if gate['complete_chunks']==gate['chunk_denominator'] and cost['status']!='incomplete':
                (S/'watch-terminal.json').write_text(json.dumps(gate,indent=2)+'\n');return
        except Exception as exc:
            error=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),error=str(exc));(S/'watch-error.json').write_text(json.dumps(error,indent=2)+'\n');print(json.dumps(error),flush=True)
        while time.monotonic()-start<300:time.sleep(min(55,300-(time.monotonic()-start)))


if __name__=='__main__':main()
