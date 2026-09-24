"""Planning-only common-solvent extension costs from existing measurements.

No COSMO engine, DFT, scheduler action, new prediction or product write.
The observed solvent range is not a confidence interval for new chemistry.
"""
import collections
import csv
import datetime
import gzip
import hashlib
import json
import math
import resource
import statistics
from pathlib import Path

from audit_phase9_results import records, sha, ROOTS

D = Path('/mnt/r/plastchem-euler/phase9-v1')
OUT = D / 'common-extension-planning-v1'


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def main():
    OUT.mkdir(exist_ok=False)
    captures = {}
    for name in ['collection.json', 'production-progress-ledger.json']:
        raw = (D / name).read_bytes()
        captures[name] = hashlib.sha256(raw).hexdigest()
        with gzip.open(OUT / (name + '.gz'), 'wb') as f:
            f.write(raw)
        if name == 'collection.json': registry = json.loads(raw)
        else: ledger = json.loads(raw)
    cohort = json.loads((D.parent / 'phase83-v1/cohort.json').read_text())['rows']
    strata = {f"cohort-{r['index']:05d}": r['stratum'] for r in cohort}
    population = collections.Counter(strata.values())
    complete = {u for u, r in ledger['units'].items()
                if r.get('partition_rows') == 640 and r['lle_mask'] == (1 << 64) - 1}
    # This original in-flight unit includes the documented 4005-second pause.
    # Exclude its timings, not its scientific results or allocation accounting.
    excluded = complete & {'cohort-00287'}
    selected = complete - excluded
    sample = collections.Counter(strata[u] for u in selected)
    assert set(sample) == set(population) and sum(population.values()) == 5830
    plans = {root: json.loads((D / name).read_text()) for root, name in ROOTS.items()}
    sizes = {f'{root}/{i:04d}': len(units) for root, plan in plans.items()
             for i, units in enumerate(plan['chunks'])}
    manifest = json.loads((D.parent / 'phase8-v1/manifest.json').read_text())
    solvents = {s['name'] for s in manifest['solvents']}
    phases = []; times = collections.defaultdict(list)
    seen_phases = set(); seen_lle = set(); statuses = collections.Counter()
    for path, value in records(registry):
        if '/activities/solvent-' in path:
            assert path not in seen_phases
            seen_phases.add(path)
            chunk = '/'.join(path.split('/')[:2])
            solvent = value['phase'].removeprefix('solvent-')
            assert solvent in solvents and len(value['values']) == sizes[chunk]
            seconds = float(value['wall_seconds'])
            assert math.isfinite(seconds) and seconds > 0
            phases.append(dict(chunk=chunk, solvent=solvent, contaminants=sizes[chunk],
                               wall_seconds=seconds, cpu_model=value['execution']['cpu_model']))
        elif '/lle/' in path and value['unit'] in selected:
            key = (value['unit'], value['solvent'], value['regime'])
            assert key not in seen_lle
            seen_lle.add(key)
            seconds = float(value['wall_seconds'])
            assert math.isfinite(seconds) and seconds >= 0
            times[(strata[key[0]], key[1], key[2])].append(seconds)
            statuses[value['status']] += 1
    assert len(phases) == len(sizes) * 32 == 1888
    assert len(seen_lle) == len(selected) * 64
    assert {r['solvent'] for r in phases} == solvents
    by_solvent = []
    for solvent in sorted(solvents):
        phase_rows = [r for r in phases if r['solvent'] == solvent]
        assert sum(r['contaminants'] for r in phase_rows) == 5830
        row = dict(solvent=solvent,
                   measured_full_cohort_phase_CPU_h=sum(r['wall_seconds'] for r in phase_rows)/3600)
        for regime in ['RT', 'high']:
            total = 0
            for stratum, n in population.items():
                observations = times[stratum, solvent, regime]
                assert len(observations) == sample[stratum]
                total += n * statistics.mean(observations)
            row[regime + '_stratum_weighted_full_cohort_CPU_h'] = total / 3600
        by_solvent.append(row)
    write_csv(OUT / 'existing-solvent-timing-reference.csv', by_solvent)
    write_csv(OUT / 'phase-timing-observations.csv', phases)
    write_csv(OUT / 'sample-strata.csv', [dict(stratum=s, population=population[s],
               fully_collected_timing_sample=sample[s]) for s in sorted(population)])
    costs = []
    for n, option in [(39, 'add_common_only_identities_keep_existing_panel'),
                      (69, 'separately_keyed_full_new_common_surface_library')]:
        for include_lle in [False, True]:
            per_solvent = [r['measured_full_cohort_phase_CPU_h'] +
                           (r['RT_stratum_weighted_full_cohort_CPU_h'] if include_lle else 0)
                           for r in by_solvent]
            costs.append(dict(option=option, new_solvent_surface_conditions=n,
              quantities='partition_and_RT_LLE' if include_lle else 'partition_only',
              new_partition_rows=5830*n*10*2, new_RT_LLE_rows=5830*n if include_lle else 0,
              baseline_CPU_h=n*statistics.mean(per_solvent),
              planning_CPU_h_with_25_percent_allowance=1.25*n*statistics.mean(per_solvent),
              observed_solvent_min_max_range_with_allowance_CPU_h=[
                  1.25*n*min(per_solvent), 1.25*n*max(per_solvent)]))
    result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
       status='proposal_costs_only_not_clearance', captures=captures,
       cohort_sha256=sha(D.parent / 'phase83-v1/cohort.json'),
       phase_manifest_sha256=sha(D.parent / 'phase8-v1/manifest.json'),
       script_sha256=sha(Path(__file__)), population=5830,
       fully_collected_molecules_in_snapshot=len(complete), timing_molecules=len(selected),
       excluded_timing_units=sorted(excluded), timing_LLE_systems=len(seen_lle),
       timing_LLE_statuses=dict(statuses), measured_solvent_phase_checkpoints=len(phases),
       observed_phase_cpu_models=sorted({r['cpu_model'] for r in phases}), costs=costs,
       peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
       limitations=[
        'Fully collected selection is not random; unfinished/uncollected cases may be slower.',
        'LLE means are stratified by the existing cohort strata; unresolved outcomes retain their measured cost.',
        'Range substitutes the fastest/slowest observed panel-solvent mean for every new solvent; not a statistical confidence interval or guarantee for new chemistry.',
        '25 percent allowance for parsing, staging, checkpointing and export is a planning assumption, not measured extension overhead.',
        'Existing phase activities can be reused only under identical phase, solute, temperature, package and convention provenance.',
        'Future end-to-end calibration on the actual new solvents is required after extension clearance; the original production estimate underestimated actual cost.',
        'No new high-temperature LLE cost or output is authorized or assumed; the new solvents lack literal workbook high-temperature specifications.',
        'No new thermodynamic predictions, DFT, scheduler mutations, interpolation or product writes.'])
    result['files'] = {p.name: sha(p) for p in sorted(OUT.glob('*.csv'))}
    (OUT / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
