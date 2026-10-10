"""Seal newly accepted tier-2 panel records outside the immutable promotion cohort.

No new scientific calculation or release modification. Uses existing independent
surface and thermodynamic auditors on a captured, explicitly bounded cohort.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from audit_panel_failure_semantics import check as failure_check
from thermodynamic_prediction import CONFIG, refresh_volumes

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    assert out.is_relative_to(B / 'audits') and not out.exists()
    primary = B / 'promotion-v1'
    verification = json.loads((B / 'phase9-v1/delivery-verification.json').read_text())
    assert verification['status'] == 'complete_delivery_verified'
    assert verification['manifest_sha256'] == sha(primary / 'manifest.json')
    cohort = json.loads((primary / 'provenance/cohort.json').read_text())['rows']
    released = {r['inchikey'] for r in cohort}
    assert len(released) == 5830
    inputs = json.loads((R / 'state/tier2-v1/tier2/manifest.json').read_text())['molecules']
    eligible = {r['inchikey'] for r in inputs}
    assert len(eligible) == 270 and len(eligible & released) == 27
    source_records = {}
    for path in sorted((R / 'state/tier2-v1/records').glob('*.json')):
        raw = path.read_bytes()
        record = json.loads(raw)
        if record.get('status') == 'converged':
            assert record['inchikey'] in eligible
            source_records[record['inchikey']] = (raw, record)
    selected = sorted(set(source_records) - released)
    assert selected, 'No post-release accepted structures'
    ledger = json.loads((R / 'state/tier2-v1/thermodynamics/processing-ledger.json').read_text())
    library = json.loads((R / 'state/thermodynamics-v1/library-registry.json').read_text())
    ready = {r['solvent_key'] for r in library['solvents'] if r['status'] == 'ready'}
    assert len(ready) == 32
    requested = [r['solvent_key'] for r in library['solvents']]
    volumes = refresh_volumes()
    captured = {}
    for key in selected:
        item = ledger[key]
        raw = Path(item['result_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item['result_sha256']
        data = json.loads(raw)
        assert data['config'] == CONFIG and set(data['activities']) == ready
        assert data['solute_surface_sha256'] == source_records[key][1]['surface_sha256']
        failure_check(data, requested)
        for pair in data['partitions_against_water']:
            if pair['status'] != 'predicted':
                continue
            if pair['solvent'] in volumes:
                assert pair['molar_volume_solvent_cm3_mol'] == volumes[pair['solvent']]['molar_volume_cm3_mol']
                assert pair['molar_volume_reference_cm3_mol'] == volumes['water']['molar_volume_cm3_mol']
            else:
                assert pair['log10_K_concentration'] is None
                assert pair['concentration_basis_status'] == 'missing_documented_molar_volume'
        captured[key] = (raw, item)
    out.mkdir()
    sealed = {}
    for key, (raw, item) in captured.items():
        dest = out / 'records' / (key + '.json')
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(raw)
        assert sha(dest) == item['result_sha256']
        sealed[key] = dict(item, result_path=str(dest))
        # The surface auditor uses a historical adapter folder; both are
        # private snapshot metadata, never additions to the live campaign.
        for folder in ['tier2-v1', 'campaign-v1']:
            target = out / 'freeze/state' / folder / 'records' / (key + '.json')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_records[key][0])
    save(out / 'sealed-thermodynamics/processing-ledger.json', sealed)
    save(out / 'sealed-thermodynamics/library-registry.json', library)
    save(out / 'volume-reference-snapshot.json', volumes)
    snapshot = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    tier_denominator=270, accepted_at_capture=len(source_records),
                    already_in_frozen_release=27, newly_accepted=len(selected), keys=selected,
                    primary_manifest_sha256=verification['manifest_sha256'],
                    script_sha256=sha(Path(__file__)),
                    scope='New tier-2 legacy solvent/water panel, finite dilution ladder at 298.15 K. Not an addition to the exact-zero polymer/LLE promotion releases; generic xylene remains unresolved.')
    save(out / 'snapshot.json', snapshot)
    env = dict(os.environ, PLASTCHEM_PROGRESS_ROOT=str(out), OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    for name in ['audit_completed_surfaces.py', 'audit_tier2_thermodynamic_records.py']:
        subprocess.run([sys.executable, str(R / 'scripts' / name), '--frozen'], env=env, check=True)
    surfaces = json.loads((out / 'completed-surface-audit.json').read_text())
    panel = json.loads((out / 'sealed-thermodynamics/production-record-audit.json').read_text())
    assert surfaces['passed'] == panel['passed'] == len(selected)
    assert surfaces['failed'] == panel['failed'] == 0
    for name in [Path(__file__).name, 'audit_completed_surfaces.py', 'audit_tier2_thermodynamic_records.py',
                 'audit_panel_failure_semantics.py', 'thermodynamic_prediction.py']:
        shutil.copyfile(R / 'scripts' / name, out / name)
    pins = {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
    save(out / 'manifest.json', dict(status='completed_snapshot_verified', files=pins, snapshot=snapshot))
    for name, digest in pins.items():
        assert sha(out / name) == digest
    print(json.dumps(dict(root=str(out), new_contaminants=len(selected), surface_passed=surfaces['passed'],
                          panel_passed=panel['passed'], manifest_sha256=sha(out / 'manifest.json'))))


if __name__ == '__main__':
    main()
