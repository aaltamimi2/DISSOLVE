"""Fresh two-phase, two-molecule-order check of the one tier-2 logKow result.

Numerical/implementation verification with the same engine, not independent
experimental validation. The saved finite-dilution result is never replaced.
"""
import os
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
import datetime
import argparse
import fcntl
import gc
import hashlib
import json
import math
from pathlib import Path
import resource
import numpy as np
from opencosmorspy import COSMORS
from opencosmorspy.parameterization import openCOSMORS24a

ROOT = Path(__file__).resolve().parents[1]
BULK = Path('/mnt/r/plastchem-euler')
SAVED = BULK / 'tier2-v1/octanol-validation-20260925'
OUT = BULK / 'tier2-v1/octanol-fresh-check-20260925'
KEY = 'AMFGWXWBFGVCKG-UHFFFAOYSA-N'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    global SAVED, OUT, KEY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saved', type=Path, default=SAVED)
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--key', default=KEY)
    args = parser.parse_args()
    SAVED, OUT, KEY = args.saved.resolve(), args.output.resolve(), args.key
    assert SAVED.is_relative_to(BULK / 'tier2-v1')
    assert OUT.is_relative_to(BULK / 'tier2-v1')
    lock = (ROOT / 'state/thermodynamics-v1/worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not OUT.exists()
    assert int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1]) >= 2500 * 1024
    source = SAVED / (KEY + '.json')
    manifest = json.loads((SAVED / 'manifest.json').read_text())
    assert sha(source) == manifest['files'][source.name]
    saved = json.loads(source.read_text())
    solute = json.loads((ROOT / 'state/tier2-v1/records' / (KEY + '.json')).read_text())
    assert solute['status'] == 'converged' and solute['connectivity_match']
    surface = Path(solute['archive_path']) / 'surface.orcacosmo'
    assert sha(surface) == saved['solute_surface_sha256']
    library = json.loads((ROOT / 'state/thermodynamics-v1/library-registry.json').read_text())
    water = next(r for r in library['solvents'] if r['solvent_key'] == 'water')
    octanol = json.loads((ROOT / 'state/campaign-v1/records/KBPLFHHGFOOTCA-UHFFFAOYSA-N.json').read_text())
    phases = {'water': Path(water['surface']), 'octanol': Path(octanol['archive_path']) / 'surface.orcacosmo'}
    selected = {name: float(saved[name + '_activity']['selected_solute_fraction']) for name in phases}
    fractions_by_phase = {name: sorted({0.0, 1e-6, selected[name]}, reverse=True) for name in phases}
    rows = []
    for name, phase in phases.items():
        assert sha(phase) == saved[name + '_activity']['solvent_surface_sha256']
        for solute_first in [True, False]:
            engine = COSMORS(openCOSMORS24a())
            for path in ([surface, phase] if solute_first else [phase, surface]):
                engine.add_molecule([str(path)])
            fractions = fractions_by_phase[name]
            for x in fractions:
                composition = [x, 1 - x] if solute_first else [1 - x, x]
                engine.add_job(x=np.array(composition), T=298.15, refst='pure_component')
            values = engine.calculate()['tot']['lng']
            solute_index = 0 if solute_first else 1
            for x, values_row in zip(fractions, values):
                value = float(values_row[solute_index])
                assert math.isfinite(value)
                rows.append(dict(solvent=name, solute_first=solute_first, solute_fraction=x, ln_gamma=value))
            del engine
            gc.collect()
    get = lambda phase, order, x: next(r['ln_gamma'] for r in rows if (r['solvent'], r['solute_first'], r['solute_fraction']) == (phase, order, x))
    cached_error = max(abs(get(name, True, selected[name]) - saved[name + '_activity']['ln_gamma']) for name in phases)
    order_error = max(abs(get(name, True, x) - get(name, False, x)) for name in phases for x in fractions_by_phase[name])
    assert cached_error <= 1e-9 and order_error <= 1e-9
    volume = json.loads((SAVED / 'provenance.json').read_text())['volume']['molar_volume_cm3_mol']
    correction = math.log10(18.07 / volume)
    x0 = (get('water', True, 0.0) - get('octanol', True, 0.0)) / math.log(10)
    finite = (get('water', True, selected['water']) - get('octanol', True, selected['octanol'])) / math.log(10)
    assert abs(finite + correction - saved['prediction']['log10_K_concentration']) <= 1e-9
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status='fresh_solve_verified',
        input_inchikey=KEY, saved_result_sha256=sha(source), cached_ln_gamma_max_error=cached_error,
        molecule_order_ln_gamma_max_error=order_error, finite_logK_concentration=finite + correction,
        selected_solute_fractions=selected,
        exact_zero_logK_concentration=x0 + correction, finite_to_zero_logK_difference=x0 - finite,
        volume_correction_log10=correction, measured_reference=float(saved['reference']['measured_logKow']),
        exact_zero_residual=x0 + correction - float(saved['reference']['measured_logKow']),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        interpretation='Same-engine fresh solves and molecule-order invariance. This checks implementation, cached coefficients, dilution and volume sign; it does not establish physical accuracy or explain the reference discrepancy. No recalibration or saved-result replacement.')
    OUT.mkdir()
    for name, data in [('summary.json', summary), ('raw-activities.json', rows)]:
        (OUT / name).write_text(json.dumps(data, indent=2) + '\n')
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    files = {p.name: sha(p) for p in OUT.iterdir() if p.is_file()}
    (OUT / 'manifest.json').write_text(json.dumps(dict(files=files), indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
