"""Planning arithmetic only; no source copies, DFT, activity solve or submission."""
import csv
import datetime
import hashlib
import json
from pathlib import Path

import numpy as np

D = Path('/mnt/r/plastchem-euler/phase9-solvent-library-v1')
R = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    timing = D/'common-solvent-measured-cost.csv'
    source = D/'full-grid-source-audit/rows.jsonl'
    assert sha(timing) == '3ba939a1a3bf5fe7f2d25fe359a5af3d1704963ebc73a61d2e4a81b3ca0445fd'
    assert sha(source) == '36f287759e12b0104db6964f1e2361f57053434f6f7d760f81a95988cfa9bea2'
    measured = list(csv.DictReader(timing.open()))
    names = {r['name'] for r in measured}
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    assert len(measured) == len(names) == 69 and len(rows) == 990
    remaining = [r for r in rows if r['solvent_key'] not in names]
    candidates = [r for r in remaining if r['status'] == 'source_pair_connectivity_agrees']
    assert len(remaining) == 921 and len(candidates) == 914
    x = np.log([float(r['atoms']) for r in measured])
    y = np.log([float(r['allocated_cpu_seconds']) for r in measured])
    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (intercept + slope*x)
    smearing = np.mean(np.exp(residual))
    atoms = np.array([r['atoms'] for r in candidates])
    predicted = np.exp(intercept + slope*np.log(atoms))*smearing
    out = D/'remaining-grid-cost-planning-v1'
    out.mkdir(exist_ok=True)
    # Per-key metadata stays outside git, like the underlying source inventory.
    with (out/'per-key-planning.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['solvent_key', 'atoms', 'support', 'planning_cpu_seconds'])
        writer.writeheader()
        for row, n, value in zip(candidates, atoms, predicted):
            writer.writerow(dict(solvent_key=row['solvent_key'], atoms=int(n),
                support='outside_common_atom_range' if n < 5 or n > 38 else 'inside_common_atom_range_only',
                planning_cpu_seconds=float(value)))
    total = float(predicted.sum()/3600)
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        scope='Uncalibrated remaining-grid planning, not authorization, a measured cost or a runtime guarantee',
        full_grid=990, common_complete=69, remaining_keys=921,
        source_pair_agreement_candidates=914, source_preflight_unresolved=7,
        reference_cpu='AMD EPYC 7763 (Milan)', reference_n=69,
        reference_allocated_cpu_hours=sum(float(r['allocated_cpu_seconds']) for r in measured)/3600,
        reference_atom_range=[5, 38], candidate_atom_range=[int(atoms.min()), int(atoms.max())],
        candidates_below_reference_range=int(sum(atoms < 5)),
        candidates_above_reference_range=int(sum(atoms > 38)),
        candidates_inside_atom_range=int(sum((atoms >= 5) & (atoms <= 38))),
        model='ln(allocated seconds) = intercept + slope * ln(atoms), with residual smearing for arithmetic means',
        slope=float(slope), intercept=float(intercept), smearing=float(smearing),
        log_space_R2=float(1 - sum(residual**2)/sum((y-y.mean())**2)),
        planning_cpu_hours_for_914=total,
        sensitivity_cpu_hours=[total*.5, total*4],
        sensitivity_definition='Illustrative 0.5x to 4x stress range; not a confidence interval or validated bound. Hard SCF/optimization cases can exceed it.',
        exclusions='No numerical cost assigned to the seven unresolved source identities. No alias/stereochemistry collapse.',
        qualifications='Source-pair connectivity agreement is not independent product/CAS identity proof. Atom-range inclusion does not establish chemistry-class coverage. Distinct source keys retained.',
        timing_sha256=sha(timing), source_inventory_sha256=sha(source), script_sha256=sha(Path(__file__)),
        bulk_per_key_path=str(out/'per-key-planning.csv'), bulk_per_key_sha256=sha(out/'per-key-planning.csv'))
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
