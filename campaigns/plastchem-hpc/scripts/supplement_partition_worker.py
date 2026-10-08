"""Checkpointed exact-zero supplemental partition calculations.

No frozen release is changed. Local mode is restricted to a small reproduction
plan; production requires a one-CPU research allocation. LLE is a separate
calculation and is never inferred from these partition rows.
"""
import os
for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
import argparse
import datetime
import fcntl
import gc
import hashlib
import json
import math
from pathlib import Path
import resource
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def guard(local):
    rss = int(next(x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('VmRSS:')))
    assert rss < 1800 * 1024, 'Pause at memory guard; sealed phases retained'
    if local:
        available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
        assert available >= 2.5 * 1024**2, 'Pause: host available memory below 2.5 GiB'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan', required=True, type=Path)
    ap.add_argument('--local-reproduction', action='store_true')
    args = ap.parse_args()
    plan = json.loads(args.plan.read_text())
    inputs_path = Path(plan['inputs'])
    assert sha(inputs_path) == plan['inputs_sha256']
    spec = json.loads(inputs_path.read_text())
    assert spec['temperature_K'] == 298.15 and spec['solute_mole_fraction'] == 0
    assert spec['reference_state'] == 'pure_component'
    if 'original_batch_source' in plan:
        assert sha(plan['original_batch_source']) == plan['original_batch_source_sha256']
    local = args.local_reproduction
    if local:
        assert plan['purpose'] == 'reproduction_only_not_production'
        assert 0 < len(plan['keys']) <= 8
    else:
        assert os.environ.get('SLURM_JOB_ID') and os.environ['SLURM_JOB_PARTITION'] == 'research'
        assert all(int(os.environ[k]) == 1 for k in ('SLURM_JOB_NUM_NODES','SLURM_NTASKS','SLURM_CPUS_PER_TASK'))
    for path, digest in plan['code_pins'].items():
        assert sha(path) == digest, path
    assert plan['code_pins'][str(Path(__file__).resolve())] == sha(__file__)
    package_root = Path(spec['package_root'])
    for rel, digest in spec['package_pins'].items():
        assert sha(package_root / rel) == digest, rel
    # Bind the numerical package before importing the existing verified helpers.
    sys.path.insert(0, str(package_root))
    from phase9_profiles import ProfileCache, infinite_dilution
    from phase9_worker_cpu import checkpoint, ensemble, save
    import opencosmorspy
    assert Path(opencosmorspy.__file__).resolve() == (package_root / 'opencosmorspy/__init__.py').resolve()
    import duckdb
    out = Path(plan['output'])
    assert out.resolve().is_relative_to(Path('/mnt/r/plastchem-euler')) or not local
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / 'worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    signature = dict(plan_sha256=sha(args.plan), inputs_sha256=sha(inputs_path), code_pins=plan['code_pins'])
    complete_path = out / 'complete.json'
    if complete_path.exists():
        complete = json.loads(complete_path.read_text())
        assert complete['signature'] == signature
        for seal_path in out.rglob('*.json.sha256.json'):
            seal = json.loads(seal_path.read_text())
            assert all(seal['signature'].get(k) == v for k,v in signature.items())
            assert sha(seal_path.with_name(seal_path.name.removesuffix('.sha256.json'))) == seal['sha256']
        expected = {out/'partition'/polymer/(key+'.json.sha256.json') for polymer in plan['polymers'] for key in plan['keys']}
        assert set((out/'partition').rglob('*.json.sha256.json')) == expected
        print(json.dumps(dict(status='complete_verified_no_rerun',original_execution=complete['execution'])),flush=True)
        return
    model = next(x.split(':',1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name'))
    if not local:
        assert 'EPYC 7763' in model or 'EPYC 9' in model
    execution = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),cpu_model=model,
        node=os.uname().nodename, job_id=os.environ.get('SLURM_JOB_ID'), local_reproduction=local)
    keys = plan['keys']
    assert len(set(keys)) == len(keys)
    units = [spec['frozen'].get(k) or spec['later'][k] for k in keys]
    solvents = plan['solvents']
    polymers = plan['polymers']
    assert len(set(solvents)) == len(solvents) and set(solvents) <= spec['solvents'].keys()
    assert len(set(polymers)) == len(polymers) and set(polymers) <= spec['polymers'].keys()
    guard(local)
    start = time.monotonic()
    cache = ProfileCache()
    profiles = []
    for u in units:
        profiles.append(cache.get(u['surface'], u['surface_sha256']))
        guard(local)
    activities = {}

    def phase_values(identifier, phase, original_batches=False):
        sig = dict(signature, phase_sha256=phase['surface_sha256'], keys=keys)
        def calculate():
            guard(local)
            t = time.monotonic()
            p = cache.get(phase['surface'], phase['surface_sha256'])
            values = []
            if original_batches:
                batches = {}
                for key in keys:
                    control = plan['original_control_batches'][key]
                    members = control['members']
                    ids = tuple(m['id'] for m in members)
                    if ids not in batches:
                        ps = []
                        for member in members:
                            ps.append(cache.get(member['surface'],member['surface_sha256']))
                            guard(local)
                        batches[ids] = list(map(float,infinite_dilution(ps,p)))
                    values.append(batches[ids][ids.index(control['id'])])
                    gc.collect()
            else:
                for offset in range(0, len(units), plan['subbatch']):
                    guard(local)
                    values.extend(map(float,infinite_dilution(profiles[offset:offset+plan['subbatch']],p)))
                    gc.collect()
            assert len(values) == len(keys) and all(math.isfinite(x) for x in values)
            return dict(values=values,phase_sha256=phase['surface_sha256'],keys=keys,
                wall_seconds=time.monotonic()-t, execution=execution,solute_mole_fraction=0,
                original_batch_control=original_batches)
        value,reused=checkpoint(out/'activities'/(identifier+'.json'),sig,calculate)
        print(json.dumps(dict(phase=identifier,reused=reused)),flush=True)
        return value['values']

    if plan['solvent_mode'] == 'frozen_cache':
        assert set(keys) <= spec['frozen'].keys()
        assert sha(spec['solvent_cache']) == spec['solvent_cache_sha256']
        con = duckdb.connect()
        con.execute("SET threads=1")
        con.execute("SET memory_limit='128MB'")
        con.from_parquet(spec['solvent_cache']).create_view('cached')
        # Identifiers are data parameters, never interpolated SQL fragments.
        rows = con.execute('SELECT * FROM cached WHERE input_inchikey IN (SELECT unnest(?))',[keys]).fetchdf()
        con.close()
        for name in solvents:
            selected = rows[rows['product_solvent_key']==name]
            by_key = {r['input_inchikey']:r for r in selected.to_dict('records')}
            assert len(by_key) == len(keys)
            values=[]
            for u in units:
                r=by_key[u['inchikey']]
                assert r['solute_surface_sha256']==u['surface_sha256']
                assert r['solvent_surface_sha256']==spec['solvents'][name]['surface_sha256']
                # Temperature/x/reference state are bound by the cache manifest
                # and preparation pins, not repeated as Parquet columns.
                assert math.isfinite(float(r['ln_gamma_solvent']))
                values.append(float(r['ln_gamma_solvent']))
            activities[name]=values
        del rows
        for name in plan['direct_solvent_controls']:
            fresh=phase_values('control-solvent-'+name,spec['solvents'][name],original_batches=True)
            assert max(abs(x-y) for x,y in zip(fresh,activities[name]))<=1e-9
            save(out/'controls'/(name+'.json'),dict(keys=keys,fresh=fresh,cached=activities[name],
                max_difference=max(abs(x-y) for x,y in zip(fresh,activities[name])),signature=signature))
    elif plan['solvent_mode'] == 'fresh':
        for name in solvents:
            activities[name]=phase_values('solvent-'+name,spec['solvents'][name])
    else:
        raise ValueError('Unknown solvent activity source')
    for polymer in polymers:
        conformers=spec['polymers'][polymer]
        values=[phase_values('polymer-'+r['entry_id'],r,
            original_batches=polymer in plan.get('original_batch_polymer_controls',[])) for r in conformers]
        for i,u in enumerate(units):
            def partition():
                e=ensemble(conformers,[v[i] for v in values])
                rows=[]
                for solvent in solvents:
                    s=spec['solvents'][solvent]
                    for convention in ('normalized','existing'):
                        gamma=e[convention+'_gamma']; vp=e[convention+'_volume']; vs=s[convention+'_volume_cm3_mol']
                        kx=(gamma-activities[solvent][i])/math.log(10)
                        assert all(math.isfinite(x) for x in (gamma,vp,vs,kx)) and vp>0 and vs>0
                        rows.append(dict(input_inchikey=u['inchikey'],polymer=polymer,product_solvent_key=solvent,
                            convention=convention,temperature_K=298.15,solute_mole_fraction=0,status='predicted',
                            ln_gamma_polymer=gamma,ln_gamma_solvent=activities[solvent][i],logP_x=kx,
                            logP_concentration=kx+math.log10(vp/vs),polymer_volume_cm3_mol=vp,solvent_volume_cm3_mol=vs,
                            solute_surface_sha256=u['surface_sha256'],solvent_surface_sha256=s['surface_sha256'],
                            polymer_surface_sha256=[r['surface_sha256'] for r in conformers],parameterization='openCOSMO-RS 24a'))
                return rows
            checkpoint(out/'partition'/polymer/(u['inchikey']+'.json'),dict(signature,polymer=polymer,key=u['inchikey']),partition)
        del values
        # Keep the small solute profiles; discard completed phase profiles.
        keep={str(Path(u['surface']).resolve()) for u in units}
        cache.profiles={k:v for k,v in cache.profiles.items() if k in keep}
        gc.collect();guard(local)
    expected=len(keys)*len(polymers)*len(solvents)*2
    save(out/'complete.json',dict(status='partition_complete_lle_not_computed',signature=signature,execution=execution,
        rows=expected,units=len(keys),polymer_ensembles=len(polymers),solvents=len(solvents),
        wall_seconds=time.monotonic()-start,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        limitations='Local results are reproduction/control work only. No production cost estimate, LLE verdict, experimental accuracy or product promotion is implied.'))
    print(json.dumps(dict(status='complete',rows=expected,output=str(out))),flush=True)


if __name__=='__main__':
    main()
