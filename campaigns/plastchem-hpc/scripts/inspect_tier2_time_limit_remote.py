"""Read-only salvage inventory for a terminal tier-2 time-limit attempt."""
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

R = Path.home() / 'plastchem-euler/tier2-v1'
index = int(sys.argv[1])
manifest = json.loads((R / 'tier2/manifest.json').read_text())
m = manifest['molecules'][index]
assert m['array_index'] == index
task = '63873_' + str(index)
key = m['inchikey']
work = R / 'runs' / key
record = json.loads((work / 'result.json').read_text())
queue = subprocess.check_output(['squeue', '-u', 'aaltamimi2', '-h', '-r', '-o', '%i|%T'], text=True)
assert task not in {line.split('|')[0] for line in queue.splitlines()}, 'Original attempt still active'
accounting = subprocess.check_output(['sacct', '-j', task, '--starttime=2026-09-21', '-nP',
    '--format=JobID,State,ElapsedRaw,ExitCode'], text=True)
accounts = {v[0]: v for line in accounting.splitlines() if len(v := line.split('|')) >= 4}
account = accounts[task]
assert account[1] in ['TIMEOUT', 'FAILED'], account
assert account[1] == 'TIMEOUT' or record.get('failure_mode') in ['scheduler_signal_10', 'walltime_censored']
assert record['status'] == 'failed' and record['input']['inchikey'] == key

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def frames(path):
    lines = path.read_text(errors='replace').splitlines()
    out = []
    i = 0
    while i < len(lines):
        try:
            n = int(lines[i].strip())
        except ValueError:
            i += 1
            continue
        if n == m['atoms'] and i + 2 + n <= len(lines):
            atoms = [line.split() for line in lines[i + 2:i + 2 + n]]
            try:
                if all(len(a) == 4 and all(math.isfinite(float(x)) for x in a[1:]) for a in atoms):
                    out.append(str(n) + '\nSalvaged from ' + str(path) + ' frame ' + str(len(out)) + '\n' +
                               '\n'.join(' '.join(a) for a in atoms) + '\n')
            except ValueError:
                pass
            i += n + 2
        else:
            i += 1
    return out

files = [dict(name=p.name, bytes=p.stat().st_size, sha256=sha(p)) for p in sorted(work.iterdir())
         if p.is_file() and (p.suffix in ['.xyz', '.gbw', '.inp', '.out', '.json'] or p.name == 'attempt.lock')]
candidates = []
for name in ['opt_trj.xyz', 'opt.xyz', 'optimized.xyz']:
    p = work / name
    if p.exists() and (fs := frames(p)):
        candidates.append((p.stat().st_mtime, name, fs[-1], len(fs)))
if not candidates and (work / 'opt.out').exists():
    text = (work / 'opt.out').read_text(errors='replace')
    blocks = re.findall(r'CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\n', text, re.S)
    for block in reversed(blocks):
        atoms = [line.split() for line in block.splitlines() if len(line.split()) == 4]
        if len(atoms) == m['atoms'] and all(math.isfinite(float(x)) for a in atoms for x in a[1:]):
            candidates.append(((work / 'opt.out').stat().st_mtime, 'opt.out last Cartesian block',
                str(len(atoms)) + '\nLast Cartesian block\n' + '\n'.join(' '.join(a) for a in atoms) + '\n', len(blocks)))
            break
if candidates:
    _, name, xyz, nframes = max(candidates)
    source = dict(restart_source='salvaged_geometry', source_name=name, source_frame_count=nframes)
else:
    xyz = (R / 'prepared' / key / 'input.xyz').read_text()
    source = dict(restart_source='original_no_usable_geometry_found')
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), task=task, input=m,
    accounting=account, squeue=queue, original_record=record, files=files, **source, xyz=xyz,
    xyz_sha256=hashlib.sha256(xyz.encode()).hexdigest(), scope='Inventory only; no mutation or submission')))
