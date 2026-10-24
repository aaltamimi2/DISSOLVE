"""Temperature extension of the openCOSMO-RS 24a partition activities (owner, 2026-10-22): one chunk of contaminants,
every product solvent and product-polymer conformer, 28 temperatures (25-160 C in 5 C steps), exact zero solute mole
fraction, pure-component reference state.

No DFT and no new conformers: the contaminants' served surfaces and the polymers' existing conformer ensembles. The
numerical path is the release's (phase9_profiles.ProfileCache and infinite_dilution, the same pinned files), now given the
temperature; the phases' profiles come parsed once from inputs/phase-profiles.pkl (temperature_profiles.py), the
contaminants' are parsed here. Partition coefficients are not formed here: the collector combines these activities later
(polymer ensemble at T, the served normalized convention, concentration basis).

Output, one append-only JSONL per chunk, results/<chunk>.jsonl. Each line is one (temperature, phase) block in a fixed
order (temperature outer, phase inner): the chunk's ln gamma values in key order, the block's signature (temperature
included) and the line's own sha256. A line is written in one write, flushed and fsynced before the next block starts. A
rerun keeps the verified lines, drops a truncated last line and computes only what is missing; any other line that does
not verify stops the chunk (it is never rewritten silently). results/<chunk>.complete.json is written once every block
is present. A chunk id <parent>.a / <parent>.b is the first / second half of its parent's keys (the controller's split).

    python temperature_worker.py --root ROOT --chunk CID [--results DIR] [--local]
"""
import os
for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
import argparse
import datetime
import fcntl
import gzip
import hashlib
import json
import math
import pickle
import resource
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)


def split_point(n, subbatch):
    """Where the controller halves a chunk: at a subbatch boundary, so each half's subbatches (and numbers) are the parent's."""
    groups = -(-n // subbatch)
    return subbatch * (-(-groups // 2)) if groups > 1 else -(-n // 2)


def chunk_keys(chunks, cid, subbatch):
    """Keys and surface digests of a chunk; '.a'/'.b' suffixes take the parent's keys before/after split_point."""
    parts = cid.split('.')
    entry = chunks[parts[0]]
    keys, digests = list(entry['keys']), list(entry['surface_sha256'])
    for half in parts[1:]:
        assert half in ('a', 'b'), cid
        cut = split_point(len(keys), subbatch)
        keys, digests = (keys[:cut], digests[:cut]) if half == 'a' else (keys[cut:], digests[cut:])
    assert keys and len(keys) == len(set(keys)) == len(digests), cid
    return keys, digests


def blocks(campaign):
    phases = [('solvent:' + s['name'], s['sha256']) for s in campaign['solvents']]
    phases += [(f"polymer:{p['name']}:{c['entry_id']}", c['sha256']) for p in campaign['polymers'] for c in p['conformers']]
    return [(t_c, t_k, phase, digest) for t_c, t_k in zip(campaign['temperatures_c'], campaign['temperatures_K'])
            for phase, digest in phases]


def read_surface(index, root, digest, directory):
    """Write one surface from the packs into directory (gzip blob at offset/length) and check its digest."""
    pack, offset, length = index[digest]
    with open(root / 'inputs' / pack, 'rb') as handle:
        handle.seek(offset)
        blob = handle.read(length)
    data = gzip.decompress(blob)
    assert len(blob) == length and sha(data) == digest, f'surface {digest} does not verify'
    path = directory / f'{digest}.orcacosmo'
    path.write_bytes(data)
    return path


class Stop(Exception):
    pass


def stop(signum, frame):
    raise Stop(f'signal {signum}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', required=True, type=Path)
    ap.add_argument('--chunk', required=True)
    ap.add_argument('--results', type=Path, help='default ROOT/results; the pilot control copy writes elsewhere')
    ap.add_argument('--package-root', type=Path, help='local tests only: where the pinned opencosmorspy lives')
    ap.add_argument('--local', action='store_true', help='local test: no Slurm assertions')
    ap.add_argument('--max-blocks', type=int, help='local test: stop after this many new blocks')
    args = ap.parse_args()
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
        signal.signal(s, stop)
    started, cpu0 = time.monotonic(), time.process_time()
    root = args.root.resolve()
    campaign_raw = (root / 'inputs/campaign.json').read_bytes()
    campaign = json.loads(campaign_raw)
    chunks = json.loads((root / 'inputs/chunks.json').read_text())['chunks']
    index = json.loads((root / 'inputs/surfaces-index.json').read_text())['surfaces']
    if not args.local:
        assert os.environ.get('SLURM_JOB_ID') and os.environ['SLURM_JOB_PARTITION'] == 'research'
        assert int(os.environ['SLURM_CPUS_PER_TASK']) == 1
    package_root = (args.package_root if args.local and args.package_root else Path(campaign['package_root'])).resolve()
    for rel, digest in campaign['package_pins'].items():
        assert sha((package_root / rel).read_bytes()) == digest, rel
    profiles_code = HERE / 'phase9_profiles.py'
    assert sha(profiles_code.read_bytes()) == campaign['profiles_sha256']
    sys.path.insert(0, str(package_root))
    sys.path.insert(0, str(HERE))
    from phase9_profiles import ProfileCache, infinite_dilution
    import opencosmorspy
    assert Path(opencosmorspy.__file__).resolve() == (package_root / 'opencosmorspy/__init__.py').resolve()
    model = next(x.split(':', 1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines()
                 if x.startswith('model name'))
    if not args.local:
        assert 'EPYC 9' in model, f'CPU {model} is not the Genoa class the release thermodynamics ran on'

    keys, digests = chunk_keys(chunks, args.chunk, campaign['subbatch'])
    plan = blocks(campaign)
    common = dict(campaign='temperature-v1', campaign_sha256=sha(campaign_raw), worker_sha256=sha(Path(__file__).read_bytes()),
                  profiles_sha256=campaign['profiles_sha256'], parameterization='openCOSMO-RS 24a',
                  reference_state='pure_component', solute_mole_fraction=0, subbatch=campaign['subbatch'],
                  keys_sha256=sha(canonical([keys, digests]).encode()))

    def signature(block):
        t_c, t_k, phase, digest = block
        return dict(common, temperature_c=t_c, temperature_K=t_k, phase=phase, phase_surface_sha256=digest)

    out = (args.results or root / 'results').resolve()
    out.mkdir(parents=True, exist_ok=True)
    path, complete_path = out / f'{args.chunk}.jsonl', out / f'{args.chunk}.complete.json'
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f'{path} is locked by another writer')
    data = path.read_bytes()
    good = data.rfind(b'\n') + 1
    if good < len(data):
        print(json.dumps(dict(event='dropped_truncated_last_line', bytes=len(data) - good)), flush=True)
        os.ftruncate(fd, good)
        os.fsync(fd)
        data = data[:good]
    lines = data.splitlines()
    assert len(lines) <= len(plan), f'{len(lines)} lines for {len(plan)} blocks'
    for i, raw in enumerate(lines):
        row = json.loads(raw)
        claimed = row.pop('sha256')
        assert sha(canonical(row).encode()) == claimed, f'line {i + 1} does not verify'
        assert row['signature'] == signature(plan[i]) and row['chunk'] == args.chunk, f'line {i + 1} is not block {i + 1}'
        assert len(row['values']) == len(keys)
    done = len(lines)
    execution = dict(job_id=os.environ.get('SLURM_JOB_ID'), node=os.uname().nodename, cpu_model=model,
                     started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'))
    print(json.dumps(dict(event='start', chunk=args.chunk, keys=len(keys), blocks_done=done, blocks=len(plan), **execution)),
          flush=True)
    if done == len(plan) and complete_path.exists():
        complete = json.loads(complete_path.read_text())
        assert complete['jsonl_sha256'] == sha(data) and complete['blocks'] == len(plan)
        print(json.dumps(dict(event='complete_verified_no_rerun')), flush=True)
        return
    os.lseek(fd, 0, os.SEEK_END)
    work = Path(tempfile.mkdtemp(prefix=f'tempext-{args.chunk}-', dir=os.environ.get('TMPDIR')))
    new = 0
    try:
        # phases: the profiles ProfileCache.get made of their surfaces once on this CPU class (temperature_profiles.py)
        raw = (root / 'inputs/phase-profiles.pkl').read_bytes()
        assert sha(raw) == campaign['phase_profiles_sha256'], 'phase-profiles.pkl does not match campaign.json'
        phases = pickle.loads(raw)
        cache = ProfileCache()
        units = [cache.get(read_surface(index, root, d, work), d) for d in digests]
        for i in range(done, len(plan)):
            if args.max_blocks is not None and new >= args.max_blocks:
                print(json.dumps(dict(event='stopped_at_max_blocks', blocks_done=i)), flush=True)
                return
            t_c, t_k, phase, digest = plan[i]
            block_wall, block_cpu = time.monotonic(), time.process_time()
            p = phases[digest]
            assert p['sha256'] == digest
            values = []
            for offset in range(0, len(units), campaign['subbatch']):
                values.extend(map(float, infinite_dilution(units[offset:offset + campaign['subbatch']], p, temperature=t_k)))
            assert len(values) == len(keys) and all(math.isfinite(v) for v in values)
            row = dict(chunk=args.chunk, block=i + 1, temperature_c=t_c, phase=phase, values=values, signature=signature(plan[i]))
            row['sha256'] = sha(canonical(row).encode())
            line = (canonical(row) + '\n').encode()
            assert os.write(fd, line) == len(line)
            os.fsync(fd)
            new += 1
            print(json.dumps(dict(event='block', block=i + 1, temperature_c=t_c, phase=phase, units=len(keys),
                                  cpu_s=round(time.process_time() - block_cpu, 3),
                                  wall_s=round(time.monotonic() - block_wall, 3))), flush=True)
        final = path.read_bytes()
        assert final.count(b'\n') == len(plan)
        complete = dict(status='complete', chunk=args.chunk, keys=keys, blocks=len(plan), jsonl_bytes=len(final),
                        jsonl_sha256=sha(final), signature_common=common, last_attempt=execution,
                        last_attempt_new_blocks=new, last_attempt_cpu_seconds=round(time.process_time() - cpu0, 1),
                        last_attempt_wall_seconds=round(time.monotonic() - started, 1),
                        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'))
        tmp = complete_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(complete, indent=1) + '\n')
        os.replace(tmp, complete_path)
        print(json.dumps(dict(event='complete', blocks=len(plan), new_blocks=new)), flush=True)
    except Stop as exc:
        print(json.dumps(dict(event='stopped_by_signal', reason=str(exc), new_blocks=new)), flush=True)
        sys.exit(143)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        os.close(fd)


if __name__ == '__main__':
    main()
