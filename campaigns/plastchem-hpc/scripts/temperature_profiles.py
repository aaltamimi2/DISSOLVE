"""Phase profiles of the temperature extension, parsed once on the campaign's CPU class (Genoa) and shared by every chunk.

ProfileCache.get parses a surface into the compact profile that infinite_dilution consumes (area, volume, segment-type
descriptors and areas). For the 268 phases (32 solvents, 236 polymer conformers) that parse costs about half an hour of
CPU per pass; every chunk attempt would repeat it. Here each array task parses its share with the same pinned
phase9_profiles code, and --merge joins the parts into inputs/phase-profiles.pkl (digest -> profile, without the
temporary file path, which nothing downstream reads). The worker loads that file, checked against its sha256 in
campaign.json, instead of parsing.

    python temperature_profiles.py --root ROOT --part I --parts N      (one Slurm array task)
    python temperature_profiles.py --root ROOT --merge --parts N       (login node, seconds)
"""
import os
for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
import argparse
import hashlib
import json
import pickle
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', required=True, type=Path)
    ap.add_argument('--part', type=int)
    ap.add_argument('--parts', type=int, required=True)
    ap.add_argument('--merge', action='store_true')
    args = ap.parse_args()
    root = args.root.resolve()
    campaign = json.loads((root / 'inputs/campaign.json').read_text())
    digests = [s['sha256'] for s in campaign['solvents']] + [c['sha256'] for p in campaign['polymers'] for c in p['conformers']]
    out = root / 'profiles'
    if args.merge:
        merged = {}
        for i in range(args.parts):
            merged.update(pickle.loads((out / f'part-{i:02d}.pkl').read_bytes()))
        assert sorted(merged) == sorted(set(digests)), 'a phase profile is missing'
        data = pickle.dumps({d: merged[d] for d in sorted(merged)}, protocol=4)
        (root / 'inputs/phase-profiles.pkl').write_bytes(data)
        print(json.dumps(dict(profiles=len(merged), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))))
        return
    package_root = Path(campaign['package_root'])
    for rel, digest in campaign['package_pins'].items():
        assert hashlib.sha256((package_root / rel).read_bytes()).hexdigest() == digest, rel
    assert hashlib.sha256((HERE / 'phase9_profiles.py').read_bytes()).hexdigest() == campaign['profiles_sha256']
    sys.path.insert(0, str(package_root))
    sys.path.insert(0, str(HERE))
    from phase9_profiles import ProfileCache
    from temperature_worker import read_surface
    model = next(x.split(':', 1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines()
                 if x.startswith('model name'))
    assert 'EPYC 9' in model, model
    index = json.loads((root / 'inputs/surfaces-index.json').read_text())['surfaces']
    work = Path(tempfile.mkdtemp(prefix='tempext-profiles-', dir=os.environ.get('TMPDIR')))
    try:
        cache, part = ProfileCache(), {}
        for d in sorted(set(digests))[args.part::args.parts]:
            p = dict(cache.get(read_surface(index, root, d, work), d))
            del p['path']
            part[d] = p
        out.mkdir(exist_ok=True)
        tmp = out / f'part-{args.part:02d}.tmp'
        tmp.write_bytes(pickle.dumps(part, protocol=4))
        os.replace(tmp, out / f'part-{args.part:02d}.pkl')
        print(json.dumps(dict(part=args.part, profiles=len(part), cpu_model=model)))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
