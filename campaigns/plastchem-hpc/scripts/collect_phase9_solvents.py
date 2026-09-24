"""Collect A-9 solvent DFT to bulk storage and verify the owner's identity policy.

Read-only scheduler/remote access; no retries or submissions. TIMEOUT remains
distinct from chemistry failure. Original campaign collectors are independent.
"""
import os
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
import collections, datetime, fcntl, hashlib, json, tarfile
from pathlib import Path
from euler_transport import run

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase9-solvent-library-v1')
S = R / 'state/phase9-v1'


def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''): h.update(chunk)
    return h.hexdigest()


def write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(p.suffix + '.tmp'); temp.write_text(json.dumps(value, indent=2) + '\n'); temp.replace(p)


def terminal_snapshot(known):
    """Reuse a verified final return set after Slurm expires its queue entry.

    Require the same submitted array, exact roster and every recorded digest.
    This is a local recheck, not a new scheduler observation.
    """
    if not (D / 'summary.json').exists(): return None
    summary = json.loads((D / 'summary.json').read_text())
    if summary.get('status') != 'terminal': return None
    assert summary['denominator'] == summary['terminal_verified'] == 69
    assert summary['running'] == summary['pending'] == 0
    submission = json.loads((D / 'submission.json').read_text())
    snapshot = json.loads((D / 'latest-snapshot.json').read_text())
    if snapshot['job_id'] != submission['job_id']: return None
    roster = json.loads((D / 'solvent_library/manifest.json').read_text())['molecules']
    expected = {r['inchikey'] for r in roster}
    assert len(roster) == len(expected) == 69 and set(known) == expected
    for key, pin in known.items():
        path = D / 'results' / key / 'verified-result.json'
        assert sha(path) == pin['result_sha256'], key
        record = json.loads(path.read_text())
        assert record['status'] == pin['status']
        assert record['slurm_state'] in ['COMPLETED', 'FAILED', 'TIMEOUT', 'OUT_OF_MEMORY', 'CANCELLED', 'NODE_FAIL', 'PREEMPTED']
    assert dict(collections.Counter(r['status'] for r in known.values())) == summary['states']
    return summary


def main():
    lock = (S / 'solvent-collect.lock').open('a'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if not (D / 'submission.json').exists(): return
    registry = D / 'retrieved.json'
    known = json.loads(registry.read_text()) if registry.exists() else {}
    terminal = terminal_snapshot(known)
    if terminal is not None:
        write(D / 'terminal-local-recheck.json', dict(
            utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            scheduler_snapshot_utc=terminal['utc'], terminal_records_verified=len(known),
            registry_sha256=sha(registry), scope='Same-array terminal records rechecked locally; no new scheduler query'))
        print(json.dumps(terminal)); return
    code = 'known=' + repr(list(known)) + '\n' + r'''
import datetime,getpass,hashlib,io,json,subprocess,tarfile,uuid
from pathlib import Path
D=Path.home()/'plastchem-euler/phase9-solvent-library-v1'
job=json.loads((D/'submission.json').read_text())['job_id']
def call(args):return subprocess.check_output(args,text=True)
user_queue=call(['squeue','-h','-r','-u',getpass.getuser(),'-o','%i|%T|%C|%R'])
queue='\n'.join(row for row in user_queue.splitlines() if row.split('|')[0].split('_')[0]==job)
out=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),job_id=job,
 queue=queue,
 accounting=call(['sacct','-nP','-j',job,'--units=K','--format=JobID%40,State%30,ElapsedRaw,AllocCPUS,MaxRSS,NodeList']),records={})
acct={s.split('|')[0]:s.split('|') for s in out['accounting'].splitlines() if s}
files={}
for m in json.loads((D/'solvent_library/manifest.json').read_text())['molecules']:
 key=m['inchikey'];task=f"{job}_{m['array_index']}";a=acct.get(task)
 if key in known or not a or a[1].split()[0] not in ['COMPLETED','FAILED','TIMEOUT','OUT_OF_MEMORY','CANCELLED','NODE_FAIL','PREEMPTED']:continue
 p=D/'returns'/key/'result.json'
 record=json.loads(p.read_text()) if p.exists() else dict(input=m,inchikey=key,dft_ran=False)
 out['records'][key]=dict(record=record,accounting=a,batch_accounting=acct.get(task+'.batch'))
 if p.exists():
  for f in p.parent.iterdir():
   if f.is_file():files[str(f.relative_to(D))]=hashlib.sha256(f.read_bytes()).hexdigest()
  for name in ['opt.out','cosmo.out','cosmo.solute_cpcm.lastout']:
   f=D/'runs'/key/name
   if f.exists():files[str(f.relative_to(D))]=hashlib.sha256(f.read_bytes()).hexdigest()
if files:
 folder=D/'return-archives';folder.mkdir(exist_ok=True);archive=folder/(uuid.uuid4().hex+'.tar.gz')
 with tarfile.open(archive,'w:gz') as t:
  b=json.dumps(files).encode();info=tarfile.TarInfo('return-pins.json');info.size=len(b);t.addfile(info,io.BytesIO(b))
  for rel in sorted(files):t.add(D/rel,arcname=rel,recursive=False)
 out.update(archive=str(archive),archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
print(json.dumps(out))
'''
    p = run('ssh', ['euler', 'python3 -'], input=code, capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stderr
    snap = json.loads(p.stdout); write(D / 'latest-snapshot.json', snap)
    if snap.get('archive'):
        folder = D / 'return-archives'; folder.mkdir(exist_ok=True)
        archive = folder / Path(snap['archive']).name
        p = run('scp', ['euler:' + snap['archive'], str(archive)], capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, p.stderr
        assert sha(archive) == snap['archive_sha256']
        with tarfile.open(archive, 'r|gz') as t:
            iterator = iter(t); first = next(iterator); assert first.name == 'return-pins.json'
            pins = json.load(t.extractfile(first)); seen = set()
            for member in iterator:
                assert member.isfile() and member.name in pins and member.name not in seen
                seen.add(member.name); parts = Path(member.name).parts
                assert len(parts) == 3 and parts[0] in ['returns', 'runs'] and '..' not in parts
                raw = t.extractfile(member).read(); assert hashlib.sha256(raw).hexdigest() == pins[member.name]
                target = D / 'results' / parts[1] / parts[2]; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            assert seen == set(pins)
    for key, payload in snap['records'].items():
        rec = payload['record']; acct = payload['accounting']; state = acct[1].split()[0]
        rec['slurm_state'] = state; rec['allocated_cpu_seconds'] = int(acct[2]) * int(acct[3])
        rec['scheduler_node'] = acct[5]; rec['batch_accounting'] = payload['batch_accounting']
        if state in ['TIMEOUT', 'PREEMPTED', 'NODE_FAIL']:
            rec.update(status='execution_interrupted', failure_mode='slurm_' + state.lower(), chemistry_failure=False)
        elif rec.get('status') == 'converged_identity_pending':
            from identity_campaign import verify
            folder = D / 'results' / key
            assert sha(folder / 'surface.orcacosmo') == rec['surface_sha256']
            for stage, evidence in rec['stages'].items(): assert sha(folder / (stage + '.inp')) == evidence['input_sha256']
            rec['dft_status'] = 'converged'
            rec.update(verify(folder / 'optimized.xyz', key, rec['input']['smiles']))
            rec['status'] = 'converged' if rec['identity_verified'] else 'identity_failure'
        else:
            rec.update(status='failed', failure_mode=rec.get('failure_mode', 'slurm_' + state.lower()))
        write(D / 'results' / key / 'verified-result.json', rec)
        known[key] = dict(status=rec['status'], utc=snap['utc'], surface_sha256=rec.get('surface_sha256'),
                          allocated_cpu_seconds=rec['allocated_cpu_seconds'], result_sha256=sha(D / 'results' / key / 'verified-result.json'))
        write(registry, known)
    states = collections.Counter(v['status'] for v in known.values())
    rows = [s.split('|') for s in snap['queue'].splitlines() if s]
    running = sum(r[1] != 'PENDING' for r in rows); pending = sum(r[1] == 'PENDING' for r in rows)
    allocated = sum(int(p[2]) * int(p[3]) for p in (s.split('|') for s in snap['accounting'].splitlines())
                    if len(p) >= 4 and '_' in p[0] and '.' not in p[0] and p[0].split('_')[-1].isdigit())
    summary = dict(utc=snap['utc'], denominator=69, terminal_verified=len(known), states=dict(states),
                   running=running, pending=pending, allocated_cpu_hours=allocated/3600,
                   status='terminal' if len(known) == 69 and not rows else 'in_progress')
    write(D / 'summary.json', summary)
    text = f"# A-9 common-solvent DFT\n\nSnapshot {snap['utc']}. Verified terminal {len(known)}/69; running {running}; pending {pending}. States: {dict(states)}. Allocated cost to date: {allocated/3600:.3f} CPU-hours.\n\n"
    text += 'Identity requires matching InChIKey connectivity and records every perceived full key and agreeing perception engine. TIMEOUT is an execution interruption, not a chemistry failure; no row is removed from the 69 denominator. No remainder-grid DFT or 32-to-69 contaminant-grid extension has been launched.\n\n'
    text += 'Licensed-derived inputs and surfaces are uncommitted bulk data under this directory; immutable return archives contain SHA-256 pins. Collector: `/home/aaltamimi2/plastchem-euler/scripts/collect_phase9_solvents.py`.\n'
    (D / 'REPORT.md').write_text(text); print(json.dumps(summary))


if __name__ == '__main__': main()
