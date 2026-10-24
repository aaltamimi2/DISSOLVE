"""Temperature extension of the served openCOSMO-RS partition coefficients (owner, 2026-10-22: "go ahead with the pilot
and run the full job"): local staging, pilot checks and collection. Euler runs temperature_worker.py per chunk under
temperature_controller.py (scrontab, every 15 minutes).

Scope: every computed contaminant of the v14 set (v14 is not built: promotion-v13's converged structures, minus the
withdrawn Acid fuchsine FB, plus the coverage structures converged since, as logkow_opera_parity.served() builds it), the
32 product solvents and the 10 product polymers' conformer ensembles of the served partition table, 25-160 C in 5 C
steps. No DFT: the served surfaces. openCOSMO-RS 24a through the release's pinned phase9_profiles.infinite_dilution.

    python3 scripts/temperature_campaign.py stage-phases         campaign.json, phase pack, code, config (nothing released)
    python3 scripts/temperature_campaign.py profiles             Genoa array parsing the 268 phase profiles once
    python3 scripts/temperature_campaign.py stage-pilot          chunks.json and the pilot's pack -> Euler (index extended)
    python3 scripts/temperature_campaign.py finalize             merged profiles' digest into campaign.json; release p00
    python3 scripts/temperature_campaign.py stage-rest           the other contaminants' packs -> Euler (index extended)
    python3 scripts/temperature_campaign.py check-pilot          (a) 25 C vs the served table, (b) resumed vs control, (c) cost
    python3 scripts/temperature_campaign.py status               the controller's last pass

Local staging: /mnt/r/plastchem-euler/temperature-v1 (the packs and inputs as uploaded). Outputs stay outside git.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import numpy as np
from scipy.special import logsumexp

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from euler_transport import run  # noqa: E402

R = HERE.parent
B = Path('/mnt/r/plastchem-euler')
STAGE = B / 'temperature-v1'
REMOTE = '/srv/home/aaltamimi2/plastchem-euler/temperature-v1'
HOST = 'aaltamimi2@euler.engr.wisc.edu'
PHASE_BATCH = B / 'coverage-thermo-v1/t16'          # its inputs.json carries the served panel's 32 solvents, 10 polymers
PACKAGE_ROOT = '/srv/home/aaltamimi2/plastchem-euler/phase8-v1'
TEMPERATURES_C = list(range(25, 161, 5))
SUBBATCH = 10
CHUNK = 50
PILOT = 'p00'
PROFILE_PARTS = 16
CODE = ('temperature_worker.py', 'temperature_controller.py', 'temperature_profiles.py')
STRUCTURE_SET = 'the v14 structure set (promotion-v13 plus the structures converged since, less those withdrawn)'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def ensemble(rows, activities, temperature):
    """phase9_worker_cpu.ensemble with the temperature as an argument (both conventions); at 298.15 K it is the
    original expression for expression."""
    es = np.array([r['B_energy_hartree'] for r in rows]); de = es - es.min()
    norm = -de * 2625.4996394799 / (.00831446261815324 * temperature); norm -= logsumexp(norm)
    old = -de * 627.5094740631 / (.0019872041 * temperature)
    vols = np.array([r['B_cavity_cm3_mol'] for r in rows])
    vals = np.array(activities)
    return dict(normalized_gamma=-float(logsumexp(norm - vals)), existing_gamma=-float(logsumexp(old - vals)),
                normalized_volume=float(np.exp(norm) @ vols), existing_volume=float(np.exp(old - logsumexp(old)) @ vols))


def campaign_inputs():
    spec = json.loads((PHASE_BATCH / 'inputs.json').read_text())
    batch = json.loads((PHASE_BATCH / 'batch.json').read_text())
    solvents = [dict(name=n, sha256=spec['solvents'][n]['surface_sha256'],
                     normalized_volume_cm3_mol=spec['solvents'][n]['normalized_volume_cm3_mol']) for n in batch['solvents']]
    polymers = [dict(name=n, conformers=[dict(entry_id=c['entry_id'], sha256=c['surface_sha256'],
                                              B_energy_hartree=c['B_energy_hartree'], B_cavity_cm3_mol=c['B_cavity_cm3_mol'])
                                         for c in spec['polymers'][n]]) for n in batch['polymers']]
    assert len(solvents) == 32 and len(polymers) == 10
    profiles = (PHASE_BATCH / 'phase9_profiles.py').read_bytes()
    temperatures_k = [float(Decimal('273.15') + t) for t in TEMPERATURES_C]
    assert temperatures_k[0] == 298.15 and temperatures_k[-1] == 433.15
    return dict(scope='temperature extension of the served partition activities (owner 2026-10-22)',
                temperatures_c=TEMPERATURES_C, temperatures_K=temperatures_k, subbatch=SUBBATCH,
                parameterization='openCOSMO-RS 24a', reference_state='pure_component', solute_mole_fraction=0,
                package_root=PACKAGE_ROOT, package_pins=spec['package_pins'], profiles_sha256=sha(profiles),
                phase_profiles_sha256=None,
                phase_source=str(PHASE_BATCH / 'inputs.json'), phase_source_sha256=sha((PHASE_BATCH / 'inputs.json').read_bytes()),
                solvents=solvents, polymers=polymers), profiles


def contaminants():
    """InChIKey -> (surface path, digest, atoms) of every computed v14 contaminant (the located list)."""
    located = json.loads((STAGE / 'staging/surfaces.json').read_text())

    def atoms(v):
        if v['atoms'] not in (None, ''):
            return int(v['atoms'])
        lines = Path(v['path']).read_text(errors='replace').splitlines()   # coverage rows: the surface's #XYZ_FILE count
        return int(lines[lines.index('#XYZ_FILE') + 1])
    return located['label'], {k: (Path(v['path']), v['sha256'], atoms(v)) for k, v in located['surfaces'].items()}


def write_pack(name, digests_paths, index, folder=None):
    """Append gzip blobs (mtime 0) to one pack; index digest -> [pack, offset, length]. R: answers one file at a time
    slowly, so the surfaces are read by a thread pool; the pack is written in the given order."""
    from concurrent.futures import ThreadPoolExecutor
    pack = (folder or STAGE / 'inputs') / name
    todo = [(d, p) for d, p in digests_paths if d not in index]

    def blob(item):
        digest, path = item
        data = Path(path).read_bytes()
        assert sha(data) == digest, path
        return digest, gzip.compress(data, compresslevel=6, mtime=0)
    offset = 0
    with pack.open('wb') as handle, ThreadPoolExecutor(12) as pool:
        for digest, data in pool.map(blob, todo):
            handle.write(data)
            index[digest] = [name, offset, len(data)]
            offset += len(data)
    return pack


def chunking(keys_atoms):
    """Production chunks of CHUNK contaminants with like cost: keys by size (atoms) dealt in snake order."""
    order = sorted(keys_atoms, key=lambda ka: (-ka[1], ka[0]))
    n = math.ceil(len(order) / CHUNK)
    chunks = [[] for _ in range(n)]
    for i, (key, _) in enumerate(order):
        lap, pos = divmod(i, n)
        chunks[pos if lap % 2 == 0 else n - 1 - pos].append(key)
    return {f'c{i:03d}': sorted(c) for i, c in enumerate(chunks)}


def upload(paths, remote_dir):
    run('ssh', [HOST, f'mkdir -p {remote_dir}'], check=True)
    for p in paths:
        r = run('scp', ['-q', str(p), f'{HOST}:{remote_dir}/'], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f'scp {p}: {r.stderr[:300]}')


def stage_phases():
    """campaign.json (phase-profile digest still empty), the phases' pack and index, the code, a config that releases
    nothing; enough for the phase-profile array to start."""
    (STAGE / 'inputs').mkdir(parents=True, exist_ok=True)
    (STAGE / 'code').mkdir(exist_ok=True)
    campaign, profiles = campaign_inputs()
    index = {}
    phase_surfaces = [(s['sha256'], PHASE_BATCH / 'surfaces' / f"{s['sha256']}.orcacosmo") for s in campaign['solvents']]
    phase_surfaces += [(c['sha256'], PHASE_BATCH / 'surfaces' / f"{c['sha256']}.orcacosmo")
                       for p in campaign['polymers'] for c in p['conformers']]
    pack = write_pack('pack-phases.bin', phase_surfaces, index)
    files = {'campaign.json': json.dumps(campaign, indent=1) + '\n', 'surfaces-index.json': json.dumps(dict(surfaces=index)) + '\n'}
    for name, text in files.items():
        (STAGE / 'inputs' / name).write_text(text)
    (STAGE / 'code/phase9_profiles.py').write_bytes(profiles)
    for name in CODE:
        (STAGE / 'code' / name).write_bytes((HERE / name).read_bytes())
    (STAGE / 'code/run_chunk.sh').write_text(
        '#!/bin/bash\nset -euo pipefail\n'
        'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 MALLOC_ARENA_MAX=1 MALLOC_TRIM_THRESHOLD_=65536\n'
        f'ROOT={REMOTE}\n'
        f'exec {PACKAGE_ROOT}/venv/bin/python -u "$ROOT/code/temperature_worker.py" --root "$ROOT" --chunk "$1" '
        '${2:+--results "$2"}\n')
    # nothing is released until the phase profiles exist and campaign.json carries their digest (finalize)
    config = dict(cap=64, release=[], mem_mb=4000, oom_mem_mb=16000, time='24:00:00', partition='research',
                  constraint='genoa&cpu', exclude_nodes=['euler09', 'euler10'], max_attempts=8, max_submit_per_pass=64,
                  submit=True)
    (STAGE / 'controller-config.json').write_text(json.dumps(config, indent=1) + '\n')
    upload([STAGE / 'inputs' / n for n in files] + [pack], f'{REMOTE}/inputs')
    upload([STAGE / 'code' / n for n in (*CODE, 'phase9_profiles.py', 'run_chunk.sh')], f'{REMOTE}/code')
    upload([STAGE / 'controller-config.json'], REMOTE)
    run('ssh', [HOST, f'chmod +x {REMOTE}/code/run_chunk.sh; mkdir -p {REMOTE}/results {REMOTE}/logs {REMOTE}/control '
                      f'{REMOTE}/profiles; cd {REMOTE} && sha256sum inputs/* code/* controller-config.json'], check=True)
    print(json.dumps(dict(phases=len(index), pack_bytes=pack.stat().st_size)))


def stage_pilot():
    """chunks.json (the pilot and every production chunk) and the pilot's pack; the index is extended last."""
    label, units = contaminants()
    # pilot: 50 v13-served structures evenly spread over size, so its cost and its 25 C check stand for the whole set
    with gzip.open(B / 'promotion-v13/contaminants.csv.gz', 'rt') as handle:
        v13 = {r['input_inchikey'] for r in csv.DictReader(handle) if r['campaign_status_at_snapshot'] == 'converged'}
    eligible = sorted((k for k in units if k in v13), key=lambda k: (units[k][2], k))
    pilot = [eligible[round(i * (len(eligible) - 1) / (CHUNK - 1))] for i in range(CHUNK)]
    assert len(set(pilot)) == CHUNK
    rest = chunking([(k, units[k][2]) for k in units if k not in set(pilot)])
    chunks = {PILOT: pilot, **rest}
    assert sorted(k for c in chunks.values() for k in c) == sorted(units)
    chunk_file = dict(source=label, chunk_size=CHUNK, subbatch=SUBBATCH,
                      chunks={c: dict(keys=keys, surface_sha256=[units[k][1] for k in keys]) for c, keys in chunks.items()})
    index = json.loads((STAGE / 'inputs/surfaces-index.json').read_text())['surfaces']
    pack = write_pack('pack-00.bin', [(units[k][1], units[k][0]) for k in pilot], index)
    (STAGE / 'inputs/chunks.json').write_text(json.dumps(chunk_file) + '\n')
    (STAGE / 'inputs/surfaces-index.json').write_text(json.dumps(dict(surfaces=index)) + '\n')
    upload([STAGE / 'inputs/chunks.json', pack], f'{REMOTE}/inputs')
    upload([STAGE / 'inputs/surfaces-index.json'], f'{REMOTE}/inputs')
    print(json.dumps(dict(pilot=PILOT, production_chunks=len(rest), contaminants=len(units), source=label,
                          pack_bytes=pack.stat().st_size)))


def profiles():
    """The phase-profile array: 16 one-CPU Genoa tasks, a few minutes each."""
    r = run('ssh', [HOST, f'sbatch --parsable --job-name=tempctl-profiles --partition=research --constraint="genoa&cpu" '
                          f'--exclude=euler09,euler10 --array=0-{PROFILE_PARTS - 1} --cpus-per-task=1 --mem=6G --time=02:00:00 '
                          f'--no-requeue --output={REMOTE}/logs/profiles-%a.log --wrap "{PACKAGE_ROOT}/venv/bin/python -u '
                          f'{REMOTE}/code/temperature_profiles.py --root {REMOTE} --part \\$SLURM_ARRAY_TASK_ID '
                          f'--parts {PROFILE_PARTS}"'], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr[-500:])


def finalize():
    """Merge the profile parts, put their digest into campaign.json, release the pilot."""
    r = run('ssh', [HOST, f'{PACKAGE_ROOT}/venv/bin/python {REMOTE}/code/temperature_profiles.py --root {REMOTE} --merge '
                          f'--parts {PROFILE_PARTS}'], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f'merge failed: {r.stderr[-500:]}')
    merged = json.loads(r.stdout.strip().splitlines()[-1])
    path = STAGE / 'inputs/campaign.json'
    campaign = json.loads(path.read_text())
    assert campaign['phase_profiles_sha256'] in (None, merged['sha256'])
    campaign['phase_profiles_sha256'] = merged['sha256']
    path.write_text(json.dumps(campaign, indent=1) + '\n')
    config = json.loads((STAGE / 'controller-config.json').read_text())
    config['release'] = [PILOT]
    (STAGE / 'controller-config.json').write_text(json.dumps(config, indent=1) + '\n')
    upload([path], f'{REMOTE}/inputs')
    upload([STAGE / 'controller-config.json'], REMOTE)
    r = run('scp', ['-q', f'{HOST}:{REMOTE}/inputs/phase-profiles.pkl', str(STAGE / 'inputs/')], capture_output=True, text=True)
    assert not r.returncode and sha((STAGE / 'inputs/phase-profiles.pkl').read_bytes()) == merged['sha256']
    print(json.dumps(dict(merged, campaign_sha256=sha(path.read_bytes()), released=[PILOT])))


def stage_rest():
    label, units = contaminants()
    index = json.loads((STAGE / 'inputs/surfaces-index.json').read_text())['surfaces']
    chunks = json.loads((STAGE / 'inputs/chunks.json').read_text())['chunks']
    todo = [(c['surface_sha256'][i], units[k][0]) for cid, c in sorted(chunks.items()) if cid != PILOT
            for i, k in enumerate(c['keys'])]
    packs = []
    scratch = Path(os.environ.get('TEMPEXT_SCRATCH', STAGE / 'inputs'))       # a local disk is faster than R:
    scratch.mkdir(parents=True, exist_ok=True)
    for n, start in enumerate(range(0, len(todo), 1000), 1):
        packs.append(write_pack(f'pack-{n:02d}.bin', todo[start:start + 1000], index, scratch))
        print(json.dumps(dict(pack=packs[-1].name, bytes=packs[-1].stat().st_size)), flush=True)
    assert all(d in index for d, _ in todo)
    upload(packs, f'{REMOTE}/inputs')
    # the index goes last: a worker only ever sees digests whose pack is already in place
    (STAGE / 'inputs/surfaces-index.json').write_text(json.dumps(dict(surfaces=index)) + '\n')
    upload([STAGE / 'inputs/surfaces-index.json'], f'{REMOTE}/inputs')
    for p in packs:                                                  # the archive copy on R:
        if p.parent != STAGE / 'inputs':
            (STAGE / 'inputs' / p.name).write_bytes(p.read_bytes())
    print(json.dumps(dict(packs=len(packs), bytes=sum(p.stat().st_size for p in packs), surfaces=len(index))))


def read_jsonl(path):
    rows = []
    for raw in path.read_bytes().splitlines():
        row = json.loads(raw)
        claimed = row.pop('sha256')
        assert sha(json.dumps(row, sort_keys=True, separators=(',', ':')).encode()) == claimed
        rows.append(row)
    return rows


def check_pilot():
    local = STAGE / 'pilot-check'
    local.mkdir(exist_ok=True)
    for name, remote in (('p00.jsonl', f'{REMOTE}/results/p00.jsonl'), ('control.jsonl', f'{REMOTE}/control/p00.jsonl'),
                         ('p00.log', f'{REMOTE}/logs/p00.log'), ('control.log', f'{REMOTE}/logs/control-p00.log')):
        r = run('scp', ['-q', f'{HOST}:{remote}', str(local / name)], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f'scp {remote}: {r.stderr[:200]}')
    campaign = json.loads((STAGE / 'inputs/campaign.json').read_text())
    keys = json.loads((STAGE / 'inputs/chunks.json').read_text())['chunks'][PILOT]['keys']
    resumed, control = (local / 'p00.jsonl').read_bytes(), (local / 'control.jsonl').read_bytes()
    n = min(resumed.count(b'\n'), control.count(b'\n'))
    prefix = b''.join(resumed.splitlines(keepends=True)[:n]) == b''.join(control.splitlines(keepends=True)[:n])
    log = [json.loads(line) for line in (local / 'p00.log').read_text().splitlines() if line.startswith('{')]
    starts = [e for e in log if e.get('event') == 'start']
    # (a) 25 C partition from the stored activities vs the served table
    rows = read_jsonl(local / 'p00.jsonl')
    phases = len(campaign['solvents']) + sum(len(p['conformers']) for p in campaign['polymers'])
    at25 = {r['phase']: r['values'] for r in rows if r['temperature_c'] == 25}
    result = dict(lines_resumed=resumed.count(b'\n'), lines_control=control.count(b'\n'), compared_lines=n,
                  prefix_identical=prefix, attempts=len(starts), resumed_from_blocks=[e['blocks_done'] for e in starts])
    if len(at25) == phases:
        import duckdb
        con = duckdb.connect()
        served = con.execute(f"""SELECT input_inchikey, campaign_polymer, product_solvent_key, logP_concentration
            FROM read_parquet('{B}/promotion-v13/partition.parquet') WHERE convention='normalized'
            AND input_inchikey IN (SELECT unnest(?::VARCHAR[]))""", [keys]).fetchall()
        served = {(k, p, s): v for k, p, s, v in served}
        diffs = []
        for poly in campaign['polymers']:
            conformers = poly['conformers']
            for i, key in enumerate(keys):
                e = ensemble(conformers, [at25[f"polymer:{poly['name']}:{c['entry_id']}"][i] for c in conformers], 298.15)
                for s in campaign['solvents']:
                    kx = (e['normalized_gamma'] - at25['solvent:' + s['name']][i]) / math.log(10)
                    value = kx + math.log10(e['normalized_volume'] / s['normalized_volume_cm3_mol'])
                    diffs.append(abs(value - served[(key, poly['name'], s['name'])]))
        result.update(a_pairs=len(diffs), a_max_abs_logP_difference=max(diffs), a_median=float(np.median(diffs)))
    blocks = [e for e in log if e.get('event') == 'block']
    if blocks:
        cpu = sum(e['cpu_s'] for e in blocks)
        result.update(c_blocks_timed=len(blocks), c_cpu_s_per_block=cpu / len(blocks),
                      c_cpu_s_per_evaluation=cpu / (len(blocks) * len(keys)),
                      c_by_temperature={t: round(sum(e['cpu_s'] for e in blocks if e['temperature_c'] == t) /
                                                 max(1, sum(e['temperature_c'] == t for e in blocks)), 3)
                                        for t in sorted({e['temperature_c'] for e in blocks})})
    print(json.dumps(result, indent=1))


SERVED_DB = Path.home() / 'dissolve-main-cleanup/src/dissolve/data/plastchem_opencosmo.duckdb'
BOILING = R / 'reports/phase8-0-inventory/workbook-solvent-temperature-rows.csv'
RELEASES = [B / 'releases/temperature', R / 'releases/temperature']
RELEASE_NAME = 'partition_by_temperature.duckdb'


def elapsed_hours(text):
    days, _, clock = text.rpartition('-')
    h, m, s = (int(x) for x in clock.split(':'))
    return (int(days or 0) * 24 * 3600 + h * 3600 + m * 60 + s) / 3600


def boiling_points():
    """Normal boiling points (C) of the 32 solvents: the paper workbook's Phthalates_Miscibility sheet (column B), the
    source the served LLE 'high' regime already uses."""
    out = {}
    for row in csv.DictReader(BOILING.open()):
        if row['sheet'] == 'Phthalates_Miscibility' and row['boiling_point_C']:
            out[row['solvent_raw'].strip().lower()] = float(row['boiling_point_C'])
    return out


def release(force=False, every=None):
    """Cut a cumulative release when the completed chunks' CPU-hours grew by `every` since the last one (or forced, or
    at 100%). Whole contaminants only: a chunk's contaminants enter once its every (temperature, phase) block is in."""
    import duckdb
    campaign_raw = (STAGE / 'inputs/campaign.json').read_bytes()
    campaign = json.loads(campaign_raw)
    chunks = json.loads((STAGE / 'inputs/chunks.json').read_text())['chunks']
    config = json.loads((STAGE / 'controller-config.json').read_text())
    every = every or config.get('release_every_cpu_hours', 250)
    r = run('scp', ['-q', f'{HOST}:{REMOTE}/ledger.jsonl', str(STAGE / 'ledger.jsonl')], capture_output=True, text=True)
    assert not r.returncode, r.stderr
    events = [json.loads(line) for line in (STAGE / 'ledger.jsonl').read_text().splitlines()]
    complete = sorted({e['chunk'] for e in events if e['event'] == 'complete'})
    cpu = sum(elapsed_hours(e['elapsed']) for e in events if e['event'] == 'end' and e['chunk'] in set(complete))
    manifest_path = RELEASES[0] / 'manifest.json'
    last = json.loads(manifest_path.read_text()) if manifest_path.exists() else dict(release=0, cpu_hours=0.0)
    split = {e['chunk']: e['children'] for e in events if e['event'] == 'split'}
    todo, stack = [], list(chunks)
    while stack:
        c = stack.pop(0)
        stack = list(split[c]) + stack if c in split else stack
        if c not in split:
            todo.append(c)
    final = set(todo) <= set(complete)
    if not (force or final or cpu - last['cpu_hours'] >= every):
        print(json.dumps(dict(status='no release yet', complete_chunks=len(complete), cpu_hours=round(cpu, 1),
                              since_last=round(cpu - last['cpu_hours'], 1), every=every)))
        return
    sys.path.insert(0, str(HERE))
    from temperature_worker import blocks as block_plan, chunk_keys
    plan = block_plan(campaign)
    local = STAGE / 'results'
    local.mkdir(exist_ok=True)
    con_served = duckdb.connect(str(SERVED_DB), read_only=True)
    ids = dict(con_served.execute('SELECT inchikey, id FROM contaminants WHERE computed').fetchall())
    # served contaminants outside the structure set staged for the run never get a value above 25 C: the release names
    # them and the set, so an answer can say why rather than "not yet"
    located = json.loads((STAGE / 'staging/surfaces.json').read_text())
    served_not_in_run = sorted(set(ids) - set(located['surfaces']))
    # owner, 2026-10-24: "every contaminant released should have 25-160C": the final cut holds exactly the served asset's
    # computed contaminants (DISSOLVE's packer refuses anything else), so it is cut against the release serving the
    # run's structure set
    without_id = sorted(set(located['surfaces']) - set(ids))
    if final and (served_not_in_run or without_id):
        sys.exit(f'a final cut must hold every served contaminant and no other: {len(served_not_in_run)} served outside '
                 f'the run, {len(without_id)} of the run without a served id ({SERVED_DB})')
    served = {}
    bp = boiling_points()
    solvents = campaign['solvents']
    assert all(s['name'] in bp for s in solvents), 'a solvent without a boiling point'
    # built on the local disk (git-ignored state/): a working database takes the chunks, the release file gets only the
    # ordered copy, so no blocks freed during the build stay in it
    build = R / 'state/temperature-release-build'
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True)
    out = build / RELEASE_NAME
    con = duckdb.connect(str(build / 'work.duckdb'))
    con.execute(f"SET temp_directory = '{build / 'spill'}'")
    con.execute("SET memory_limit = '3GB'")
    con.execute('CREATE TABLE partition_by_temperature (id INTEGER, solvent VARCHAR, polymer VARCHAR, temperature_c SMALLINT, '
                'logp_x100 SMALLINT, above_boiling_point BOOLEAN)')
    released, withheld, worst, flips, cells = [], [], 0.0, 0, 0
    for c in complete:
        jsonl, done = local / f'{c}.jsonl', local / f'{c}.complete.json'
        for name in (f'{c}.complete.json', f'{c}.jsonl'):
            if not (local / name).exists() or name.endswith('.jsonl') and \
                    sha((local / name).read_bytes()) != json.loads(done.read_text())['jsonl_sha256']:
                r = run('scp', ['-q', f'{HOST}:{REMOTE}/results/{name}', str(local / name)], capture_output=True, text=True)
                assert not r.returncode, r.stderr
        info = json.loads(done.read_text())
        assert sha(jsonl.read_bytes()) == info['jsonl_sha256']
        keys, digests = chunk_keys(chunks, c, campaign['subbatch'])
        rows = read_jsonl(jsonl)
        assert len(rows) == len(plan)
        values = {}
        for row, (t_c, t_k, phase, digest) in zip(rows, plan):
            s = row['signature']
            assert (row['temperature_c'], row['phase'], s['phase_surface_sha256'], s['campaign_sha256']) == \
                   (t_c, phase, digest, sha(campaign_raw))
            values[(t_c, phase)] = np.array(row['values'])
        keep = [i for i, k in enumerate(keys) if k in ids]
        withheld += [k for k in keys if k not in ids]
        released += [keys[i] for i in keep]
        if not keep:
            continue
        missing = [k for k in (keys[i] for i in keep) if k not in served]
        if missing:
            served.update({(k, p, s): v for k, p, s, v in con_served.execute(
                'SELECT c.inchikey, p.polymer, p.solvent, p.logp FROM partition p JOIN contaminants c USING (id) '
                'WHERE c.inchikey IN (SELECT unnest(?::VARCHAR[]))', [missing]).fetchall()})
        frames = []
        for t_c, t_k in zip(campaign['temperatures_c'], campaign['temperatures_K']):
            for poly in campaign['polymers']:
                conf = poly['conformers']
                ens = [ensemble(conf, [values[(t_c, f"polymer:{poly['name']}:{x['entry_id']}")][i] for x in conf], t_k)
                       for i in keep]
                gp = np.array([e['normalized_gamma'] for e in ens]); vp = np.array([e['normalized_volume'] for e in ens])
                for s in solvents:
                    gs = values[(t_c, 'solvent:' + s['name'])][keep]
                    logp = (gp - gs) / math.log(10) + np.log10(vp / s['normalized_volume_cm3_mol'])
                    x100 = np.rint(logp * 100)
                    assert np.all(np.abs(x100) < 32767)
                    if t_c == 25:
                        ref = np.array([served[(keys[i], poly['name'], s['name'])] for i in keep])
                        worst = max(worst, float(np.max(np.abs(logp - ref))))
                        flips += int(np.sum(np.rint(ref * 100) != x100))
                        cells += len(keep)
                    frames.append(dict(id=np.array([ids[keys[i]] for i in keep], dtype=np.int32), solvent=s['name'],
                                       polymer=poly['name'], temperature_c=np.int16(t_c), logp_x100=x100.astype(np.int16),
                                       above_boiling_point=bool(t_c > bp[s['name']])))
        import pandas as pd
        df = pd.concat([pd.DataFrame(f) for f in frames], ignore_index=True)
        df = df.astype({'solvent': object, 'polymer': object})   # duckdb 1.4.3 does not know pandas 3's 'str' dtype
        con.register('chunk_frame', df)
        con.execute('INSERT INTO partition_by_temperature SELECT * FROM chunk_frame')
        con.unregister('chunk_frame')
    # temperature second: it then compresses to runs, about a fifth smaller than with temperature last
    con.execute(f"ATTACH '{out}' AS rel")
    con.execute('CREATE TABLE rel.partition_by_temperature AS SELECT * FROM partition_by_temperature '
                'ORDER BY id, temperature_c, polymer, solvent')
    con.execute('CREATE TABLE rel.solvents (solvent VARCHAR, normal_boiling_point_c DOUBLE, molar_volume_cm3_mol DOUBLE)')
    con.executemany('INSERT INTO rel.solvents VALUES (?, ?, ?)', [(s['name'], bp[s['name']], s['normalized_volume_cm3_mol'])
                                                                  for s in solvents])
    number = last['release'] + 1
    meta = dict(release=number, status='final' if final else 'partial', contaminants=len(released),
                withheld_without_served_id=len(withheld), cpu_hours=round(cpu, 1), chunks_complete=len(complete),
                chunks_total=len(todo), temperatures_c=campaign['temperatures_c'], convention='normalized',
                basis='concentration', sign_convention='log10 P(solvent/polymer); positive favors solvent',
                logp_x100='round(100 * log10 P), half to even', served_db=str(SERVED_DB),
                served_db_sha256=sha(SERVED_DB.read_bytes()), check_25c_max_abs_logp_difference=worst,
                check_25c_cells=cells, check_25c_cells_rounding_differently=flips, campaign_sha256=sha(campaign_raw),
                profiles_sha256=campaign['profiles_sha256'], phase_profiles_sha256=campaign['phase_profiles_sha256'],
                package_pins=campaign['package_pins'], subbatch=campaign['subbatch'], parameterization=campaign['parameterization'],
                boiling_points='Zhou et al., Green Chem. 2026 ESI workbook, Phthalates_Miscibility column B '
                               '(reports/phase8-0-inventory/workbook-solvent-temperature-rows.csv)',
                above_boiling_point='temperature_c > normal boiling point of the solvent (flagged, not dropped)',
                row_order='id, temperature_c, polymer, solvent',
                structure_set=STRUCTURE_SET, structure_set_label=located['label'], served_not_in_run=served_not_in_run,
                created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    con.execute('CREATE TABLE rel.metadata (key VARCHAR, value VARCHAR)')
    con.executemany('INSERT INTO rel.metadata VALUES (?, ?)',
                    [(k, v if isinstance(v, str) else json.dumps(v)) for k, v in meta.items()])
    rows_written = con.execute('SELECT count(*) FROM rel.partition_by_temperature').fetchone()[0]
    assert rows_written == len(released) * len(solvents) * len(campaign['polymers']) * len(campaign['temperatures_c'])
    con.execute('DETACH rel')
    con.close()
    digest = sha(out.read_bytes())
    manifest = dict(meta, file=RELEASE_NAME, sha256=digest, bytes=out.stat().st_size, rows=rows_written)
    for folder in RELEASES:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / (RELEASE_NAME + '.tmp')
        tmp.write_bytes(out.read_bytes())
        tmp.replace(folder / RELEASE_NAME)
        (folder / (RELEASE_NAME + '.sha256')).write_text(f'{digest}  {RELEASE_NAME}\n')
        (folder / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    shutil.rmtree(build)
    print(json.dumps({k: manifest[k] for k in ('release', 'status', 'contaminants', 'withheld_without_served_id', 'cpu_hours',
                                               'check_25c_max_abs_logp_difference', 'check_25c_cells_rounding_differently',
                                               'sha256', 'bytes', 'rows')}))


def status():
    r = run('ssh', [HOST, f'cat {REMOTE}/controller-latest.json; tail -3 {REMOTE}/ledger.jsonl'], capture_output=True, text=True)
    print(r.stdout[-6000:] or r.stderr)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = {'stage-phases': stage_phases, 'stage-pilot': stage_pilot, 'profiles': profiles, 'finalize': finalize,
                'stage-rest': stage_rest, 'check-pilot': check_pilot, 'release': release, 'status': status}
    ap.add_argument('command', choices=list(commands))
    ap.add_argument('--force', action='store_true', help='release: cut one now (the first, early release)')
    a = ap.parse_args()
    commands[a.command](force=a.force) if a.command == 'release' else commands[a.command]()
