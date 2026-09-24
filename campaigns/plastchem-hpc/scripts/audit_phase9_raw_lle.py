"""Independently reconstruct LLE qualification from sealed raw activity caches.

No COSMOspace call or optimization. Each archive is an atomic resumable unit;
full-cohort completion is asserted only after exact unique-key coverage. This
supplements, rather than replaces, the frozen result and delivery auditors.
"""
import collections
import csv
import datetime
import gzip
import hashlib
import json
import math
import os
import resource
import tarfile
from pathlib import Path

import numpy as np

D = Path('/mnt/r/plastchem-euler/phase9-v1')
OUT = D / 'raw-lle-audit-v2'
QUALIFIED = {'single_liquid_phase', 'two_liquid_phases'}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''): h.update(block)
    return h.hexdigest()


def audit_value(value):
    """Check equilibrium on exact saved coordinates, with no point lookup."""
    answer = dict(status=value['status'], checked_grids=0, checked_ties=0,
                  checked_cache_points=0, maximum_mu_residual=0.,
                  maximum_mu_record_difference=0., maximum_tangent_record_difference=0.,
                  maximum_additional_tangent_lowering=0., maximum_single_hull_gap=0.)
    if value['status'] not in QUALIFIED:
        return answer
    cache = value['activities']
    assert len(cache) == value['n_activity_points']
    points = np.array(sorted(float(k) for k in cache))
    assert len(set(points)) == len(points) and np.all((points > 0) & (points < 1))
    gamma = np.asarray([cache[format(float(x), '.17g')] for x in points])
    assert gamma.shape == (len(points), 2) and np.isfinite(gamma).all()
    assert [g['grid_intervals'] for g in value['grid_checks']] == [1000, 2000]
    for grid in value['grid_checks']:
        assert grid['status'] == value['status']
        n = grid['grid_intervals']
        assert all(format(float(x), '.17g') in cache for x in np.arange(1, n)/n), 'Missing uniform-grid point'
        answer['checked_grids'] += 1
    # The full saved cache contains both grids, logarithmic tails and every
    # refinement/probe evaluation. Use these exact compositions, not recreated
    # geomspace/linspace coordinates whose last bits depend on the environment.
    assert len(points) >= 2087
    assert points[0] <= 1e-14 and points[-1] >= 1-1e-14
    assert sum(points <= .001) >= 45 and sum(points >= .999) >= 45
    xs = np.concatenate(([0.], points, [1.]))
    energy = points*(np.log(points)+gamma[:, 0]) + (1-points)*(np.log1p(-points)+gamma[:, 1])
    energies = np.concatenate(([0.], energy, [0.]))
    answer['checked_cache_points'] = len(points)
    if value['status'] == 'single_liquid_phase':
        vertices = []
        for i in range(len(xs)):
            while len(vertices) > 1:
                a, b = vertices[-2:]
                left = (energies[b]-energies[a])*(xs[i]-xs[b])
                right = (energies[i]-energies[b])*(xs[b]-xs[a])
                if left < right: break
                vertices.pop()
            vertices.append(i)
        lower = np.interp(xs, xs[vertices], energies[vertices])
        gap = float(np.max(energies-lower))
        assert gap <= 1e-9+1e-13, ('single-phase hull gap', gap)
        answer['maximum_single_hull_gap'] = gap
        assert all(not g['tie_lines'] for g in value['grid_checks'])
    else:
        for grid in value['grid_checks']:
            assert grid['tie_lines']
            for tie in grid['tie_lines']:
                a, b = tie['x_solvent_rich'], tie['x_solute_rich']
                assert 0 < a < b < 1 and b-a > 1e-7
                ends = np.array([a, b])
                # Endpoints are recorded numbers with exact cache keys. Do not
                # substitute neighboring samples or interpolate activities.
                end_gamma = np.asarray([cache[format(float(x), '.17g')] for x in ends])
                mu = np.column_stack((np.log(ends), np.log1p(-ends))) + end_gamma
                residual = float(np.max(np.abs(mu[0]-mu[1])))
                assert residual <= 1e-7, ('chemical potential mismatch', residual)
                difference = abs(residual-tie['chemical_potential_residual'])
                assert difference <= 1e-11, ('stored residual differs', difference)
                g0, g1 = ends*(np.log(ends)+end_gamma[:, 0]) + (1-ends)*(np.log1p(-ends)+end_gamma[:, 1])
                slope = (g1-g0)/(b-a)
                intercept = g0-slope*a
                minimum = float(np.min(energies-intercept-slope*xs))
                assert minimum >= -1e-7, ('unstable common tangent on saved cache', minimum)
                reported = tie['minimum_tangent_distance_RT']
                # A superset can have a lower minimum than the solver's subset;
                # it cannot have a materially higher one. Track any extra dip.
                assert minimum <= reported+1e-10, ('saved subset minimum inconsistent', minimum, reported)
                answer['checked_ties'] += 1
                answer['maximum_mu_residual'] = max(answer['maximum_mu_residual'], residual)
                answer['maximum_mu_record_difference'] = max(answer['maximum_mu_record_difference'], difference)
                answer['maximum_tangent_record_difference'] = max(answer['maximum_tangent_record_difference'], abs(minimum-reported))
                answer['maximum_additional_tangent_lowering'] = max(answer['maximum_additional_tangent_lowering'], max(0., reported-minimum))
    return answer


def main():
    OUT.mkdir(exist_ok=True)
    import fcntl
    with (OUT/'audit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code_sha = sha(Path(__file__))
        pin = OUT/'script.sha256'
        if pin.exists(): assert pin.read_text().strip() == code_sha, 'Do not mix auditor versions'
        else: pin.write_text(code_sha+'\n')
        registry = json.loads((D/'collection.json').read_text())
        totals = collections.Counter()
        for item in registry['archives']:
            archive = Path(item['path'])
            receipt = OUT/(archive.name+'.json')
            if receipt.exists():
                old = json.loads(receipt.read_text())
                assert old['archive_sha256'] == item['sha256'] and old['auditor_sha256'] == code_sha
                assert sha(OUT/old['rows_file']) == old['rows_sha256']
                totals.update(old['statuses'])
                continue
            available = int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
            assert available >= 2.5*1024**3, 'Pause cleanly between archives for memory'
            assert sha(archive) == item['sha256']
            records = []
            with tarfile.open(archive, 'r:gz') as stream:
                pins = None
                for member in stream:
                    if member.name == 'return-pins.json':
                        pins = json.load(stream.extractfile(member)); continue
                    if not member.name.startswith(('production-results-v1/', 'chunk-probe-results-v1/')): continue
                    if '/lle/' not in member.name or member.name.endswith('.sha256.json'): continue
                    raw = stream.extractfile(member).read()
                    assert pins and hashlib.sha256(raw).hexdigest() == pins[member.name]
                    assert registry['files'][member.name]['sha256'] == pins[member.name]
                    value = json.loads(raw)
                    checked = audit_value(value)
                    records.append(dict(path=member.name, unit=value['unit'],
                        solvent=value['solvent'], regime=value['regime'], **checked))
            name = archive.name+'.rows.jsonl.gz'
            tmp = OUT/(name+'.tmp')
            with gzip.open(tmp, 'wt') as stream:
                for row in records: stream.write(json.dumps(row)+'\n')
            tmp.replace(OUT/name)
            counts = dict(collections.Counter(r['status'] for r in records))
            result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          archive_sha256=item['sha256'], auditor_sha256=code_sha,
                          rows=len(records), statuses=counts, rows_file=name,
                          rows_sha256=sha(OUT/name), peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            tmp = receipt.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(receipt)
            totals.update(counts)
            print(json.dumps(dict(utc=result['utc'], audited_LLE=sum(totals.values()), last_archive_rows=len(records), peak_rss_kib=result['peak_rss_kib'])), flush=True)
        validation = json.loads((D.parent/'phase8-v1/validation-inputs.json').read_text())
        positions = {key: i for i, key in enumerate(sorted({(r['solvent'], r['regime']) for r in validation['lle_units']}))}
        assert len(positions) == 64
        masks = collections.defaultdict(int)
        aggregate = collections.Counter()
        maxima = collections.defaultdict(float)
        evidence_files = {}
        for item in registry['archives']:
            receipt = OUT/(Path(item['path']).name+'.json')
            checked = json.loads(receipt.read_text())
            evidence_files[receipt.name] = sha(receipt)
            with gzip.open(OUT/checked['rows_file'], 'rt') as stream:
                for line in stream:
                    row = json.loads(line)
                    bit = 1 << positions[row['solvent'], row['regime']]
                    assert not masks[row['unit']] & bit, ('duplicate LLE ownership', row['path'])
                    masks[row['unit']] |= bit
                    for key in ['checked_grids', 'checked_ties', 'checked_cache_points']:
                        aggregate[key] += row[key]
                    for key, value in row.items():
                        if key.startswith('maximum_'): maxima[key] = max(maxima[key], value)
        expected_units = {f'cohort-{i:05d}' for i in range(5830)}
        assert set(masks) <= expected_units
        complete = set(masks) == expected_units and all(m == (1 << 64)-1 for m in masks.values())
        if complete:
            footer_counts = json.loads((D/'compute-completion-v1/summary.json').read_text())['LLE_footer_statuses']
            assert dict(totals) == footer_counts and sum(totals.values()) == 373120
        assert aggregate['checked_grids'] == 2*sum(totals[k] for k in QUALIFIED)
        summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       status='complete' if complete else 'passed_for_collected_subset',
                       systems=sum(totals.values()), denominator=373120,
                       fully_evaluated_contaminants=sum(m == (1 << 64)-1 for m in masks.values()),
                       statuses=dict(totals), reconstructed_counts=dict(aggregate), maximum_errors=dict(maxima),
                       auditor_sha256=code_sha,
                       archive_audit_receipts=evidence_files,
                       registry_snapshot_sha256=hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest(),
                       scope='Independent exact-saved-coordinate reconstruction of full-cache convexity, both-grid endpoint chemical potential equality and full-cache tangent stability for qualified rows; unresolved rows retained without qualification. Does not re-solve COSMOspace or establish experimental accuracy.')
        (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(summary),flush=True)


if __name__ == '__main__': main()
