"""A-10: new-solvent activities and RT LLE, with sealed A-9 polymer reuse.

No original result is changed. Physical-volume completion is a later arithmetic
step, independent of these costly activity/LLE checkpoints. A missing physical
volume is explicit and cannot become a guessed concentration prediction.
"""
import os
for variable in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[variable] = '1'
import collections
import datetime
import fcntl
import gc
import json
import math
import resource
import sys
import time
from pathlib import Path

from phase9_profiles import ProfileCache, infinite_dilution, sha
from phase9_worker_cpu import checkpoint, guard, lle_result, save
from phase9_failure_policy import wrap, validate_failure

D = Path(__file__).resolve().parent
BASE = D.parent / 'phase8-v1'
safe_lle = wrap(lle_result)


def reused_polymers(unit, manifest):
    path = D / unit['primary_partition']
    seal = json.loads(path.with_suffix('.json.sha256.json').read_text())
    assert sha(path) == seal['sha256'], str(path)
    assert seal['signature']['plan_sha256'] == unit['primary_plan_sha256']
    assert seal['signature']['unit'] == unit['id']
    rows = json.loads(path.read_text())
    assert len(rows) == 640
    coefficients = {}
    solvent_values = {}
    seen = set()
    for r in rows:
        assert r['unit'] == unit['id'] and r['inchikey'] == unit['inchikey']
        assert r['solute_surface_sha256'] == unit['surface_sha256']
        assert r['temperature_K'] == 298.15 and r['status'] == 'predicted'
        k = (r['polymer'], r['convention'])
        assert k[0] in manifest['polymers'] and k[1] in ['normalized', 'existing']
        v = dict(ln_gamma_polymer=r['ln_gamma_polymer'],
                 polymer_volume_cm3_mol=r['polymer_volume_cm3_mol'])
        assert all(math.isfinite(x) for x in v.values()) and v['polymer_volume_cm3_mol'] > 0
        assert k not in coefficients or coefficients[k] == v
        coefficients[k] = v
        s = r['solvent']
        assert (k, s) not in seen
        seen.add((k, s))
        assert s not in solvent_values or solvent_values[s] == r['ln_gamma_solvent']
        solvent_values[s] = r['ln_gamma_solvent']
        x = (v['ln_gamma_polymer'] - r['ln_gamma_solvent']) / math.log(10)
        assert abs(x - r['logP_x']) <= 1e-12
        assert abs(x + math.log10(v['polymer_volume_cm3_mol'] / r['solvent_volume_cm3_mol'])
                   - r['logP_concentration']) <= 1e-12
    assert len(coefficients) == 20 and len(solvent_values) == 32 and len(seen) == 640
    return dict(coefficients=[dict(polymer=p, convention=c, **v) for (p, c), v in sorted(coefficients.items())],
                control_activities={s: solvent_values[s] for s in manifest['controls']},
                source=str(path), source_sha256=seal['sha256'], source_seal=seal,
                original_plan_sha256=unit['primary_plan_sha256'])


def main(planpath, index):
    planpath = Path(planpath)
    plan = json.loads(planpath.read_text())
    assert os.environ['SLURM_JOB_PARTITION'] == 'research'
    for name in ['SLURM_JOB_NUM_NODES', 'SLURM_NTASKS', 'SLURM_CPUS_PER_TASK']:
        assert int(os.environ[name]) == 1
    assert sha(D / 'manifest.json') == plan['manifest_sha256']
    manifest = json.loads((D / 'manifest.json').read_text())
    pins = json.loads((D / 'worker-pins.json').read_text())
    for name, digest in pins.items():
        assert sha(D / name) == digest, name
    for name, digest in json.loads((BASE / 'package-pins.json').read_text()).items():
        assert sha(BASE / name) == digest, name
    assert sha(BASE / 'manifest.json') == manifest['primary_manifest_sha256']
    assert sha(D.parent / 'phase83-v1/cohort.json') == manifest['cohort_sha256']
    genoapath = D.parent / 'phase9-v1/genoa-comparison.json'
    assert sha(genoapath) == manifest['primary_genoa_gate_sha256']
    genoagate = json.loads(genoapath.read_text())
    assert genoagate['status'] == 'passed' and not genoagate['errors']
    units = plan['chunks'][index]
    out = D / plan['output'] / f'{index:04d}'
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / 'worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    execution = dict(job_id=os.environ['SLURM_JOB_ID'],
        array_job_id=os.environ.get('SLURM_ARRAY_JOB_ID'), array_task_id=os.environ.get('SLURM_ARRAY_TASK_ID'),
        node=os.uname().nodename, cpu_model=next(line.split(':', 1)[1].strip()
          for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')))
    assert 'EPYC 7763' in execution['cpu_model'] or 'EPYC 9' in execution['cpu_model']
    signature = dict(plan_sha256=sha(planpath), worker_pins_sha256=sha(D / 'worker-pins.json'),
                     manifest_sha256=plan['manifest_sha256'],
                     package_pins_sha256=sha(BASE / 'package-pins.json'))
    if (out / 'complete.json').exists():
        complete = json.loads((out / 'complete.json').read_text())
        assert complete['signature'] == signature and complete['unit_ids'] == [u['id'] for u in units]
        assert len(complete['lle_statuses']) == 39 * len(units)
        for group, count in [('polymer-reuse', len(units)), ('activities', 41), ('partition', len(units)), ('lle', 39 * len(units))]:
            seals = list((out / group).glob('*.json.sha256.json'))
            assert len(seals) == count
            for p in seals:
                s = json.loads(p.read_text())
                assert all(s['signature'].get(k) == v for k, v in signature.items())
                assert sha(p.with_name(p.name.removesuffix('.sha256.json'))) == s['sha256']
        print(json.dumps(dict(status='complete_verified_no_rerun', original=complete['execution'])), flush=True)
        return
    start = time.monotonic()
    cache = ProfileCache()
    profiles = [cache.get(D / u['surface'], u['surface_sha256']) for u in units]
    primary = json.loads((BASE / 'manifest.json').read_text())
    controls = [dict(name='CONTROL__' + s['name'], source_name=s['name'],
                     surface='../phase8-v1/' + s['B'], surface_sha256=sha(BASE / s['B']))
                for s in primary['solvents'] if s['name'] in manifest['controls']]
    assert len(controls) == 2
    for s in manifest['solvents'] + controls:
        cache.get(D / s['surface'], s['surface_sha256']); guard()
    parse_seconds = time.monotonic() - start
    save(out / ('started-' + execution['job_id'] + '.json'),
         dict(signature=signature, execution=execution, parse_seconds=parse_seconds,
              parse_count=cache.parse_count, utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    polymer = {}
    for u in units:
        value, _ = checkpoint(out / 'polymer-reuse' / (u['id'] + '.json'),
                              dict(signature, unit=u['id']), lambda u=u: reused_polymers(u, manifest))
        polymer[u['id']] = value
    reuse_seconds = time.monotonic() - start - parse_seconds
    phases = {}
    for s in controls + manifest['solvents']:
        t = time.monotonic()
        phase = cache.get(D / s['surface'], s['surface_sha256'])
        def activity():
            values = []
            for offset in range(0, len(units), plan['subbatch']):
                guard()
                values.extend(map(float, infinite_dilution(profiles[offset:offset+plan['subbatch']], phase)))
                gc.collect()
            return dict(values=values, solvent=s['name'], phase_sha256=s['surface_sha256'],
                        temperature_K=298.15, solute_mole_fraction=0., reference_state='pure_component',
                        execution=execution, wall_seconds=time.monotonic()-t)
        value, reused = checkpoint(out / 'activities' / (s['name'] + '.json'),
            dict(signature, solvent_sha256=s['surface_sha256']), activity)
        phases[s['name']] = value
        if s in controls:
            delta = max(abs(v - polymer[u['id']]['control_activities'][s['source_name']])
                        for u, v in zip(units, value['values']))
            assert delta <= 1e-9, ('primary exact-zero control mismatch', s['name'], delta)
            save(out / (s['name'] + '-comparison.json'), dict(max_abs_ln_gamma=delta, n=len(units), passed=True))
        print('PHASE', index, s['name'], 'reused' if reused else 'computed', flush=True)
    for i, u in enumerate(units):
        def partition():
            rows = []
            for e in polymer[u['id']]['coefficients']:
                for s in manifest['solvents']:
                    lng = phases[s['name']]['values'][i]
                    x = (e['ln_gamma_polymer'] - lng) / math.log(10)
                    physical = s['physical_volume']
                    vs = s['cavity_volume_cm3_mol'] if e['convention'] == 'existing' else (physical['value'] if physical else None)
                    rows.append(dict(unit=u['id'], inchikey=u['inchikey'], **e, solvent=s['name'],
                        temperature_K=298.15, logP_x=x,
                        logP_concentration=x+math.log10(e['polymer_volume_cm3_mol']/vs) if vs else None,
                        concentration_status='predicted' if vs else 'missing_documented_molar_volume',
                        ln_gamma_solvent=lng, solvent_volume_cm3_mol=vs,
                        solute_surface_sha256=u['surface_sha256'], solvent_surface_sha256=s['surface_sha256'],
                        primary_partition_sha256=polymer[u['id']]['source_sha256'], status='activity_predicted'))
            return rows
        checkpoint(out / 'partition' / (u['id'] + '.json'), dict(signature, unit=u['id']), partition)
    partition_seconds = time.monotonic() - start - parse_seconds - reuse_seconds
    lle_start = time.monotonic()
    statuses = {}
    for s in manifest['solvents']:
        p = cache.get(D / s['surface'], s['surface_sha256'])
        for i, u in enumerate(units):
            key = u['id'] + '__' + s['name'] + '__RT'
            sig = dict(signature, unit=u['id'], solute_sha256=u['surface_sha256'],
                       solvent_sha256=s['surface_sha256'], temperature_K=298.15)
            def calculate():
                t = time.monotonic()
                r = safe_lle(profiles[i], p, 298.15, u['molecular_weight_g_mol'],
                             s['molecular_weight_g_mol'], plan['grid_batch'])
                if r['status'] == 'activity_nonconvergence':
                    validate_failure(r)
                r.update(unit=u['id'], inchikey=u['inchikey'], solvent=s['name'], regime='RT',
                    temperature_K=298.15, wall_seconds=time.monotonic()-t, execution=execution,
                    signature=sig, value_validated=r['status'] in ['single_liquid_phase', 'two_liquid_phases'])
                return r
            value, reused = checkpoint(out / 'lle' / (key + '.json'), sig, calculate)
            statuses[key] = value['status']
            print('LLE', index, key, value['status'], 'reused' if reused else 'computed', flush=True)
    save(out / 'complete.json', dict(signature=signature, execution=execution,
        unit_ids=[u['id'] for u in units], lle_statuses=statuses, parse_count=cache.parse_count,
        parse_seconds=parse_seconds, polymer_reuse_seconds=reuse_seconds, partition_seconds=partition_seconds,
        lle_seconds=time.monotonic()-lle_start, wall_seconds=time.monotonic()-start,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]))
