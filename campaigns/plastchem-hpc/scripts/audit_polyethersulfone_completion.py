"""Capture and audit all 20 completed polyethersulfone conformers without changing releases."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/polymer-v1/polyethersulfone-complete-audit-20260925')

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    assert not D.exists(), 'Preserve the existing audit; reconcile rather than overwrite'
    source = R / 'state/polymer-v1/body/manifest.json'
    molecules = [m for m in json.loads(source.read_text())['molecules'] if m['polymer'] == 'polyethersulfone']
    assert len(molecules) == 20 and len({m['entry_id'] for m in molecules}) == 20
    captured = []
    for m in molecules:
        p = R / 'state/polymer-v1/records' / (m['entry_id'] + '.json')
        raw = p.read_bytes()
        r = json.loads(raw)
        assert r['status'] == 'converged' and r['identity_verified']
        assert r['entry_id'] == m['entry_id'] and r['inchikey'] == m['inchikey']
        assert r['input']['atoms'] == m['atoms'] == 98
        assert set(r['stages']) == {'opt', 'cosmo'} and all(s['exit_code'] == 0 for s in r['stages'].values())
        assert r['slurm_accounting']['state'] == 'COMPLETED'
        captured.append((m, p, raw, r))
    folder = D / 'freeze/state/campaign-v1/records'
    folder.mkdir(parents=True)
    rows = []
    for m, p, raw, r in captured:
        # The unchanged auditor checks first-block identity against the filename.
        # A suffix preserves distinct conformers with the same molecular key.
        name = m['inchikey'] + '__' + m['entry_id'] + '.json'
        (folder / name).write_bytes(raw)
        rows.append(dict(entry_id=m['entry_id'], inchikey=m['inchikey'], audit_adapter_filename=name,
            source_record=str(p), source_record_sha256=hashlib.sha256(raw).hexdigest(),
            surface_sha256=r['surface_sha256'], archive_path=r['archive_path'], cpu_model=r['cpu_model'],
            opt_energy_hartree=float(r['stages']['opt']['final_energies_hartree'][-1]),
            cosmo_solute_energy_hartree=r['cosmo_solute_energy_hartree'],
            active_attempt=r.get('active_attempt', 'original'), slurm_accounting=r['slurm_accounting']))
    snapshot = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), polymer='polyethersulfone',
        accepted=20, denominator=20, source_manifest_sha256=sha(source), rows=rows,
        scope='ORCA surface, coordinate and provenance verification; no partition calculations or frozen-release additions')
    (D / 'snapshot.json').write_text(json.dumps(snapshot, indent=2) + '\n')
    auditor = R / 'scripts/audit_completed_surfaces.py'
    subprocess.run([sys.executable, str(auditor), '--frozen'], check=True,
        env=dict(os.environ, PLASTCHEM_PROGRESS_ROOT=str(D), OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1'))
    result = json.loads((D / 'completed-surface-audit.json').read_text())
    assert result['passed'] == 20 and result['failed'] == 0
    for p in [Path(__file__), auditor]:
        shutil.copyfile(p, D / p.name)
    pins = {str(p.relative_to(D)): sha(p) for p in sorted(D.rglob('*')) if p.is_file()}
    (D / 'manifest.json').write_text(json.dumps(dict(status='completed_surface_snapshot_verified',
        files=pins, snapshot=snapshot), indent=2) + '\n')
    for name, digest in pins.items():
        assert sha(D / name) == digest
    print(json.dumps(dict(root=str(D), passed=20, failed=0, manifest_sha256=sha(D / 'manifest.json'))))

if __name__ == '__main__':
    main()
