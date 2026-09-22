"""Read-only checks of bounded dilution outcomes and missing-result semantics."""
import math


def check(record, requested):
    activities = record['activities']
    grid = [1e-5, 1e-6, 1e-7, 1e-8]
    for name, activity in activities.items():
        samples = activity['samples']
        assert [s['solute_fraction'] for s in samples] == grid[:len(samples)], name
        assert len(samples) <= len(grid), name
        assert all(math.isfinite(s['ln_gamma']) for s in samples), name
        shifts = [abs(b['ln_gamma'] - a['ln_gamma']) / math.log(10)
                  for a, b in zip(samples, samples[1:])]
        status = activity['status']
        assert status in {'converged', 'dilution_not_converged', 'failed'}, name
        if status == 'converged':
            assert shifts and shifts[-1] <= .005, name
            assert all(s > .005 for s in shifts[:-1]), name
        else:
            assert 'ln_gamma' not in activity and 'selected_solute_fraction' not in activity, name
            assert activity.get('error'), name
            if status == 'dilution_not_converged':
                assert len(samples) == 4 and all(s > .005 for s in shifts), name
                assert abs(shifts[-1] - activity['last_log10_dilution_shift']) < 1e-12, name
    pairs = record['partitions_against_water']
    names = [p['solvent'] for p in pairs]
    assert len(names) == len(set(names)) and set(names) == set(requested) - {'water'}
    for pair in pairs:
        solvent = activities.get(pair['solvent'], {})
        water = activities.get('water', {})
        available = solvent.get('status') == water.get('status') == 'converged'
        assert (pair['status'] == 'predicted') == available, pair['solvent']
        if available:
            shift = solvent['last_log10_dilution_shift'] + water['last_log10_dilution_shift']
            assert shift <= .01, pair['solvent']
            assert abs(pair['observed_dilution_shift_sum_log10'] - shift) < 1e-12
        else:
            assert pair['status'] == 'not_available' and pair.get('reason')
            assert 'log10_K_mole_fraction' not in pair and 'log10_K_concentration' not in pair
