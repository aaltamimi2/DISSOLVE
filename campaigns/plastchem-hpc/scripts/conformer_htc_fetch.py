"""Exploratory conformer study on CHTC (owner request 2026-10-12; not a release input): pull the result archives that
have come back to the access point and report the queue. Run every hour, so that a closed login costs at most the
results of the last hour (the jobs themselves keep running on CHTC and their archives wait there).

The access point needs the owner's Duo login; this uses the master connection the owner opened and nothing else. When
that connection is gone or does not answer, the script says so and exits 2 without touching anything. Every remote
command has a time limit. An archive is kept only when it reads completely and holds one folder named after its
conformer with a result.json; anything else is reported, left out of fetched.json and fetched again next time. Nothing
is deleted on CHTC.

    python3 scripts/conformer_htc_fetch.py"""
import io
import json
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'state' / 'conformers-htc-v1'
SOCKET = Path.home() / '.ssh' / 'sockets' / 'htc'
REMOTE = 'plastchem-conformers'
KEY = re.compile(r'[A-Z]{14}-[A-Z]{10}-[A-Z]-k\d\d')
BATCH = 300


def ssh(command, timeout, stdin=None):
    return subprocess.run(['ssh', '-S', str(SOCKET), '-o', 'BatchMode=yes', 'htc', command], input=stdin,
                          capture_output=True, timeout=timeout)


def down(reason):
    print(json.dumps(dict(connection='down', reason=reason,
                          reopen='ssh -O exit -S ~/.ssh/sockets/htc htc; ssh -M -S ~/.ssh/sockets/htc -o ControlPersist=24h '
                                 '-o ServerAliveInterval=60 -o ServerAliveCountMax=5 htc')))
    sys.exit(2)


def main():
    if not SOCKET.exists():
        down('no master connection')
    try:
        probe = ssh(f'cd {REMOTE} && ls -l results && echo ==QUEUE== && condor_q -totals | grep jobs && echo ==HELD== && '
                    'condor_q -hold -af:j Args HoldReason | head -20', 60)
    except subprocess.TimeoutExpired:
        down('the master connection does not answer')
    if probe.returncode:
        down(probe.stderr.decode(errors='replace').strip()[-300:])
    listing, rest = probe.stdout.decode().split('==QUEUE==')
    queue, held = rest.split('==HELD==')
    remote = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 9 and parts[-1].endswith('.tar.gz') and KEY.fullmatch(parts[-1][:-7]):
            remote[parts[-1][:-7]] = int(parts[4])
    ledger_path = STATE / 'fetched.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    tars, returned = STATE / 'returned' / 'tar', STATE / 'returned'
    tars.mkdir(parents=True, exist_ok=True)
    todo = sorted(k for k, size in remote.items() if size and ledger.get(k, {}).get('bytes') != size)
    new, bad = [], []
    for at in range(0, len(todo), BATCH):
        batch = todo[at:at + BATCH]
        try:
            pull = ssh(f'cd {REMOTE}/results && tar -cf - -T -', 900, ''.join(k + '.tar.gz\n' for k in batch).encode())
        except subprocess.TimeoutExpired:
            down('transfer timed out')
        if pull.returncode:
            down('transfer failed: ' + pull.stderr.decode(errors='replace').strip()[-300:])
        with tarfile.open(fileobj=io.BytesIO(pull.stdout)) as outer:
            for member in outer:
                key = member.name[:-7]
                if key not in batch or not member.isfile():
                    continue
                raw = outer.extractfile(member).read()
                try:
                    with tarfile.open(fileobj=io.BytesIO(raw)) as inner:
                        members = inner.getmembers()
                        if any(m.name != key and not re.fullmatch(re.escape(key) + r'/[A-Za-z0-9._]+', m.name) or
                               not (m.isfile() or m.isdir()) for m in members):
                            raise ValueError('unexpected member')
                        result = json.loads(inner.extractfile(key + '/result.json').read())
                        if result['inchikey'] != key:
                            raise ValueError('result.json is for ' + str(result['inchikey']))
                        inner.extractall(returned, members=members)
                except Exception as exc:  # incomplete while HTCondor is still writing it, or damaged: fetched again
                    bad.append((key, str(exc)[:120]))
                    continue
                (tars / (key + '.tar.gz')).write_bytes(raw)
                ledger[key] = dict(bytes=len(raw), status=result['status'], failure_mode=result.get('failure_mode'),
                                   elapsed_seconds=result.get('elapsed_seconds'), cpu_model=result.get('cpu_model'),
                                   fetched_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
                new.append(key)
        tmp = ledger_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(ledger, indent=1, sort_keys=True) + '\n')
        tmp.replace(ledger_path)
    counts = {}
    for row in ledger.values():
        label = row['status'] if row['status'] != 'failed' else 'failed:' + str(row['failure_mode'])
        counts[label] = counts.get(label, 0) + 1
    complete = {}
    for key, row in ledger.items():
        if row['status'] == 'converged_identity_pending':
            complete[key[:-4]] = complete.get(key[:-4], 0) + 1
    print(json.dumps(dict(connection='up', utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), on_htc=len(remote),
                          fetched_now=len(new), fetched_total=len(ledger), not_readable_yet=bad, by_status=counts,
                          compounds_with_10_or_more=sum(1 for n in complete.values() if n >= 10), queue=queue.strip(),
                          held=[h for h in held.strip().splitlines() if h]), indent=1))


if __name__ == '__main__':
    main()
