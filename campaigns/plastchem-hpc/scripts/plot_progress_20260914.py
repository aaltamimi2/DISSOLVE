"""Reproducible progress figures; use --frozen only after the cohort freeze."""
import os
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BULK = Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14'))

def write_csv(path, rows, fields):
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields);w.writeheader();w.writerows(rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frozen', action='store_true')
    args = parser.parse_args()
    source = BULK / 'freeze' if args.frozen else ROOT
    out = BULK / ('figures' if args.frozen else 'preview-figures')
    out.mkdir(parents=True, exist_ok=True)
    if args.frozen:
        assert (source / 'manifest.json').exists(), 'No frozen cohort'
    records = {f.stem: json.loads(f.read_text()) for f in
               (source / 'state/campaign-v1/records').glob('*.json')}
    eligible = json.loads((source / 'state/campaign-v1/eligible.json').read_text())
    assert len(eligible) == 5824
    fit = json.loads((source / 'reports/pilot-v1/analysis.json').read_text())['fit']
    main_manifest = json.loads((source / 'state/campaign-v1/main_le80/manifest.json').read_text())
    main_indices = {m['inchikey']: m['array_index'] for m in main_manifest['molecules']}
    chunk_counts = defaultdict(Counter)
    walls = []
    for m in eligible:
        r = records.get(m['inchikey'], {})
        index = main_indices.get(m['inchikey'])
        # Main array task numbers are global preparation indices.
        if m['group'] == 'main_le80' and m['inchikey'] not in main_indices:
            chunk = 'Pilot reuse'
        elif m['group'] == 'tail_gt80':
            chunk = 'Tail >80'
        elif index is not None:
            chunk = f'Main {int(index)//500:02d}'
        else:
            chunk = 'Main pending'
        status = r.get('status', 'not_yet_run')
        category = status if status in ['converged', 'failed', 'not_yet_run'] else 'running'
        chunk_counts[chunk][category] += 1
        stages = r.get('stages', {})
        if status == 'converged' and all(s in stages for s in ['opt', 'cosmo']):
            seconds = sum(stages[s]['wall_seconds'] for s in ['opt', 'cosmo'])
            prediction = math.exp(fit['intercept']) * m['atoms']**fit['exponent'] * fit['residual_smearing']
            walls.append({'inchikey': m['inchikey'], 'name': m['name'], 'atoms': m['atoms'],
                          'group': m['group'], 'orca_hours': seconds/3600,
                          'pilot_mean_hours': prediction/3600,
                          'residual_hours': (seconds-prediction)/3600,
                          'cpu_model': r.get('cpu_model'), 'status': status})
    plt.rcParams.update({'font.size': 14, 'axes.titlesize': 14, 'axes.labelsize': 14,
                         'xtick.labelsize': 14, 'ytick.labelsize': 14, 'legend.fontsize': 14,
                         'text.color': 'black', 'axes.labelcolor': 'black',
                         'xtick.color': 'black', 'ytick.color': 'black', 'savefig.dpi': 300})
    label = 'Frozen' if args.frozen else 'Provisional'
    fig, ax = plt.subplots(figsize=(10, 7), layout='constrained')
    for group, color in [('main_le80', '#3274a1'), ('tail_gt80', '#c47c26')]:
        selected = [r for r in walls if r['group'] == group]
        ax.scatter([r['atoms'] for r in selected], [r['orca_hours'] for r in selected],
                   s=22, alpha=.65, color=color, label='Main ≤80 atoms' if group=='main_le80' else 'Tail >80 atoms')
    x = np.linspace(15, 80, 200)
    y = np.exp(fit['intercept']) * x**fit['exponent'] * fit['residual_smearing']/3600
    ax.plot(x, y, color='black', label='Milan pilot mean fit')
    ax.set(xlabel='Atom count (including hydrogen)', ylabel='OPT + COSMORS wall time (hours)',
           title=f'{label} completed ORCA timings')
    ax.legend();fig.savefig(out/'walltime-vs-atoms.png');plt.close(fig)
    write_csv(out/'walltime-vs-atoms.csv', walls, list(walls[0]))
    # Stable ordering and explicit unknown assignment until pending manifests are joined.
    chunks = sorted(chunk_counts)
    categories = ['converged', 'failed', 'running', 'not_yet_run']
    progress = [{'chunk': c, **{s: chunk_counts[c][s] for s in categories}} for c in chunks]
    assert sum(sum(r[s] for s in categories) for r in progress) == 5824
    fig, ax = plt.subplots(figsize=(11, max(6, len(chunks)*.48)), layout='constrained')
    left = np.zeros(len(chunks))
    for status, color in zip(categories, ['#46906b', '#b95c4b', '#dbb45d', '#b5bbc3']):
        values = np.array([r[status] for r in progress]);ax.barh(chunks, values, left=left, color=color, label=status.replace('_',' '));left += values
    ax.set(xlabel='Structures', title=f'{label} campaign progress / 5,824')
    ax.legend(loc='upper center', bbox_to_anchor=(.5, -0.12), ncol=2)
    fig.savefig(out/'campaign-progress.png');plt.close(fig)
    write_csv(out/'campaign-progress.csv', progress, ['chunk', *categories])
    ledger_path = BULK/'sealed-thermodynamics/processing-ledger.json' if args.frozen else ROOT/'state/thermodynamics-v1/processing-ledger.json'
    ledger = json.loads(ledger_path.read_text())
    partitions = []
    skipped_changing_records = []
    for key, entry in sorted(ledger.items()):
        if records.get(key, {}).get('status') != 'converged':
            continue
        payload = Path(entry['result_path']).read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry['result_sha256']:
            skipped_changing_records.append(key)
            continue
        result = json.loads(payload)
        assert result['solute_surface_sha256'] == records[key]['surface_sha256']
        for pair in result['partitions_against_water']:
            value = pair.get('log10_K_concentration')
            if pair['status'] != 'predicted' or value is None:
                continue
            assert math.isfinite(value)
            partitions.append({'inchikey':key, 'name':result['name'],
                               'input_inchikey':result['input_inchikey'],
                               'perceived_inchikey':result['perceived_inchikey'],
                               'identity_match_basis':result['identity_match_basis'],
                               'perceived_keys_by_engine':json.dumps(records[key]['perceived_keys_by_engine'],sort_keys=True),
                               'perception_engines_agreeing_on_perceived_key':json.dumps(records[key]['perception_engines_agreeing_on_perceived_key']),
                               'cpu_model':result['cpu_model'],
                               'solvent':pair['solvent'], 'reference':'water',
                               'temperature_K':pair['temperature_K'],
                               'log10_K_concentration':value,
                               'log10_K_mole_fraction':pair['log10_K_mole_fraction'],
                               'solute_surface_sha256':result['solute_surface_sha256'],
                               'solvent_surface_sha256':pair['solvent_surface_sha256'],
                               'water_surface_sha256':pair['reference_surface_sha256'],
                               'result_sha256':entry['result_sha256']})
    if partitions:
        write_csv(out/'computed-partition-values.csv', partitions, list(partitions[0]))
        solvents = sorted({r['solvent'] for r in partitions})
        columns = min(2, len(solvents));nrows = math.ceil(len(solvents)/columns)
        fig, axes = plt.subplots(nrows, columns, figsize=(12, 4*nrows),
                                 layout='constrained', squeeze=False)
        for ax, solvent in zip(axes.flat, solvents):
            values = [r['log10_K_concentration'] for r in partitions if r['solvent']==solvent]
            ax.hist(values, bins=min(25, max(5, int(math.sqrt(len(values))))), color='#3274a1', edgecolor='white')
            ax.set(title=f'{solvent}\n(n = {len(values)})', xlabel='log₁₀ K (solvent/water)', ylabel='Structures')
        for ax in list(axes.flat)[len(solvents):]:ax.set_visible(False)
        fig.savefig(out/'computed-value-distribution.png');plt.close(fig)
    manifest = {'mode': label, 'source_root': str(source), 'successful_wall_records': len(walls),
                'partition_rows':len(partitions),
                'partition_molecules':len({r['inchikey'] for r in partitions}),
                'skipped_concurrently_changing_records':skipped_changing_records,
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir()) if f.is_file() and f.name != 'figure-manifest.json'},
                'limitations': 'Only completed OPT+COSMORS timings plotted; failed attempt costs reported separately. Distributions contain actual concentration-basis predictions only and are grouped by solvent; experimental parity requires matched reference values.'}
    (out/'figure-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'output':str(out), 'wall_records':len(walls), 'denominator':5824}))

if __name__ == '__main__':
    main()
