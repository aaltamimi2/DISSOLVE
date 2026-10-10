"""Run one validation pass between idle panel passes, always restarting panel.

Collectors and scheduler jobs are never signalled. A busy panel is left alone.
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

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'state/tier2-v1/thermodynamics'


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--panel-pid', type=int, required=True)
    ap.add_argument('--check', action='store_true', help='Run the separate fresh-solve verification')
    ap.add_argument('--reference-batch', type=Path)
    ap.add_argument('--validation-output', type=Path)
    ap.add_argument('--fresh-output', type=Path)
    ap.add_argument('--key')
    ap.add_argument('--receipt', type=Path)
    args = ap.parse_args()
    custom = any([args.reference_batch, args.validation_output, args.fresh_output, args.key, args.receipt])
    if custom:
        assert all([args.validation_output, args.fresh_output, args.key, args.receipt])
        for path in [p for p in [args.reference_batch, args.validation_output, args.fresh_output] if p]:
            assert path.resolve().is_relative_to(Path('/mnt/r/plastchem-euler/tier2-v1'))
        assert args.receipt.resolve().is_relative_to(STATE.resolve())
        assert not args.fresh_output.exists()
        if args.check:
            assert args.reference_batch is None and (args.validation_output / 'manifest.json').is_file()
        else:
            assert args.reference_batch and not args.validation_output.exists()
            assert (args.reference_batch / 'manifest.json').is_file()
    pid = args.panel_pid
    proc = Path('/proc') / str(pid)
    argv = [s.decode() for s in (proc / 'cmdline').read_bytes().split(b'\0') if s]
    assert Path(argv[0]) == Path('/home/aaltamimi2/.venvs/cosmo-logp/bin/python')
    assert argv[1:] == ['-u', 'scripts/watch_tier2_thermodynamics.py']
    assert (proc / 'cwd').resolve() == ROOT
    assert (proc / 'fd/1').resolve() == ROOT / 'logs/tier2-thermodynamics.jsonl'
    receipt = dict(started_utc=stamp(), old_pid=pid, panel_argv=argv, events=[])
    receipt_path = args.receipt or STATE / ('octanol-check-serial-slot.json' if args.check else 'octanol-validation-serial-slot.json')
    assert not receipt_path.exists(), 'Inspect prior attempt before rerunning'

    def record(event, **fields):
        receipt['events'].append(dict(utc=stamp(), event=event, **fields))
        tmp = receipt_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(receipt, indent=2) + '\n')
        tmp.replace(receipt_path)
        print(json.dumps(receipt['events'][-1]), flush=True)

    def idle():
        if (proc / 'wchan').read_text().strip() != 'hrtimer_nanosleep':
            return False
        if (proc / 'task' / str(pid) / 'children').read_text().strip():
            return False
        accepted = {json.loads(p.read_text())['inchikey'] for p in (ROOT / 'state/tier2-v1/records').glob('*.json')
                    if json.loads(p.read_text()).get('status') == 'converged'}
        ledger = json.loads((STATE / 'processing-ledger.json').read_text())
        return accepted == set(ledger)

    record('preflight', wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    deadline = time.monotonic() + 120
    while True:
        if idle():
            before = (STATE / 'processing-ledger.json').read_bytes()
            time.sleep(2)
            if idle() and before == (STATE / 'processing-ledger.json').read_bytes():
                break
        if time.monotonic() >= deadline:
            record('deferred_panel_busy_no_signal')
            return
        time.sleep(5)
    record('idle_boundary_confirmed', processed=len(json.loads(before)), panel_ledger_sha256=hashlib.sha256(before).hexdigest())
    os.kill(pid, signal.SIGTERM)
    stopped = False
    for _ in range(100):
        if not proc.exists() or '\nState:\tZ ' in (proc / 'status').read_text():
            stopped = True
            break
        time.sleep(.1)
    assert stopped, 'Old worker has not exited; do not start another worker'
    record('idle_panel_stopped')
    try:
        script = 'verify_tier2_octanol_fresh_solve.py' if args.check else 'process_tier2_octanol_validation.py'
        command = [argv[0], '-u', str(ROOT / 'scripts' / script)]
        if custom and args.check:
            command += ['--saved', str(args.validation_output), '--output', str(args.fresh_output), '--key', args.key]
        elif custom:
            command += ['--sources', str(args.reference_batch), '--output', str(args.validation_output), '--key', args.key]
        result = subprocess.run(command, cwd=ROOT, timeout=300)
        record('validation_terminal', returncode=result.returncode)
        if custom and not args.check and result.returncode == 0:
            check = subprocess.run([argv[0], '-u', str(ROOT / 'scripts/verify_tier2_octanol_fresh_solve.py'),
                '--saved', str(args.validation_output), '--output', str(args.fresh_output), '--key', args.key],
                cwd=ROOT, timeout=300)
            record('fresh_check_terminal', returncode=check.returncode)
    except Exception as exc:
        record('validation_exception', error=repr(exc))
        raise
    finally:
        with (ROOT / 'logs/tier2-thermodynamics.jsonl').open('ab') as log:
            worker = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(2)
        assert worker.poll() is None, 'Restart failed; inspect panel log'
        assert (Path('/proc') / str(worker.pid) / 'cmdline').read_bytes().split(b'\0')[:-1] == [a.encode() for a in argv]
        record('panel_restarted', new_pid=worker.pid)
        (STATE / 'worker-process.json').write_text(json.dumps(dict(pid=worker.pid, utc=stamp(), argv=argv,
            reason='Resume original serial panel worker after one validation-only octanol pass'), indent=2) + '\n')


if __name__ == '__main__':
    main()
