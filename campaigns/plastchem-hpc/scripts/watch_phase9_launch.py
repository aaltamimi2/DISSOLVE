"""Authorized A-9 gate-to-launch continuation, then serial A/B collection.

The existing diagnostic collector finishes the timing measurement. This watcher
does not change or stop any original campaign collector. Exceeding the 500-hour
gate stops without production or B submission.
"""
import datetime, fcntl, json, os, subprocess, sys, time
from pathlib import Path
import launch_phase9

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase9-v1')
S = R / 'state/phase9-v1'


def main():
    lock = (S / 'launch-watch.lock').open('a'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (S / 'launch-watch-process.json').write_text(json.dumps(dict(pid=os.getpid(), started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))+'\n')
    launched = False
    while True:
        start = time.monotonic()
        try:
            available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
            if available < 2.5*1024**3: raise MemoryError('A-9 collection paused below 2.5 GiB available')
            if not launched:
                if (D/'genoa-gate-submission.json').exists():
                    # If the full-chunk collector has reached its own terminal
                    # condition first, continue collecting the Genoa diagnostic.
                    cost=json.loads((D/'chunk-cost.json').read_text())
                    genoapath=D/'genoa-comparison.json'
                    genoa=json.loads(genoapath.read_text()) if genoapath.exists() else {}
                    if cost['status']!='incomplete' and genoa.get('status','incomplete')=='incomplete':
                        p=subprocess.run([sys.executable,str(R/'scripts/collect_phase9.py')],capture_output=True,text=True)
                        assert p.returncode==0,p.stderr[-4000:]
                    p=subprocess.run([sys.executable,str(R/'scripts/analyze_phase9_genoa.py')],capture_output=True,text=True)
                    assert p.returncode==0,p.stderr[-4000:]
                outcome = launch_phase9.main()
                if outcome['status'] == 'cost_stop_above_500': return
                launched = outcome['status'] == 'A_launched_B_queued'
            if launched:
                for name in ['collect_phase9.py', 'summarize_phase9.py', 'collect_phase9_solvents.py']:
                    p = subprocess.run([sys.executable, str(R/'scripts'/name)], capture_output=True, text=True)
                    assert p.returncode == 0, name + ': ' + p.stderr[-4000:]
                    print(p.stdout[-6000:], flush=True)
                summary = json.loads((D/'production-summary.json').read_text())
                solvents = json.loads((D.parent/'phase9-solvent-library-v1/summary.json').read_text())
                if summary['fully_evaluated_molecules'] == 5830 and solvents['status'] == 'terminal':
                    (S/'production-collection-terminal.json').write_text(json.dumps(summary, indent=2)+'\n'); return
        except Exception as exc:
            error = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), error=str(exc))
            (S/'launch-watch-error.json').write_text(json.dumps(error, indent=2)+'\n')
            print(json.dumps(error), flush=True)
        interval = 600 if launched else 60
        while time.monotonic()-start < interval: time.sleep(min(50, interval-(time.monotonic()-start)))


if __name__ == '__main__': main()
