"""Stage the first tier-2 time-limit recovery; no transport or submission."""
import copy
import hashlib
import json
from pathlib import Path

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler/tier2-v1')
SOURCE = B / 'time-limit-recovery-20260925'
OUT = B / 'time-limit-retry1/staging'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    assert not OUT.exists(), 'Existing staging requires reconciliation'
    source = json.loads((SOURCE / 'salvage-inventory.json').read_text())
    receipt = json.loads((SOURCE / 'archive-receipt.json').read_text())
    identity = json.loads((SOURCE / 'identity-summary.json').read_text())
    assert receipt['inventory_sha256'] == identity['inventory_sha256'] == sha(SOURCE / 'salvage-inventory.json')
    assert source['task'] == '63873_15' and source['restart_source'] == 'salvaged_geometry'
    assert source['original_record']['failure_mode'] == 'scheduler_signal_10'
    key = source['input']['inchikey']
    assert key == 'VYYKGEAVUMSSBR-UHFFFAOYSA-N'
    for item in source['files']:
        path = SOURCE / key / 'original' / item['name']
        assert path.stat().st_size == item['bytes'] and sha(path) == item['sha256']
    check = identity['geometry_identity']
    xyz = SOURCE / key / 'restart.xyz'
    assert check['identity_verified'] and check['connectivity_match'] and check['input_inchikey'] == key
    assert sha(xyz) == check['geometry_sha256'] == source['xyz_sha256']
    manifest = json.loads((R / 'state/tier2-v1/tier2/manifest.json').read_text())
    molecule = copy.deepcopy(manifest['molecules'][15])
    assert molecule['inchikey'] == key and int(xyz.read_text().splitlines()[0]) == molecule['atoms']
    provenance = dict(original_task=source['task'], original_array_index=15,
        original_failure_mode=source['original_record']['failure_mode'], original_accounting=source['accounting'],
        inventory_sha256=receipt['inventory_sha256'], archive_receipt_sha256=sha(SOURCE / 'archive-receipt.json'),
        geometry_identity=check, restart_source=source['restart_source'], source_name=source['source_name'],
        xyz_sha256=source['xyz_sha256'], retry_walltime_hours=48,
        method='Same frozen OPT + COSMORS recipe, later starting geometry; archived GBW is not injected')
    molecule.update(array_index=0, restart_provenance=provenance)
    manifest.update(molecules=[molecule], name='contam-tier2-timelimit-retry1', walltime='48:00:00', concurrency=1,
        retry_authority='Owner: record time-limit outcomes separately and resubmit with longer limits',
        retry_limit_basis='Four times the interrupted 12-hour request; geometry optimization did not finish. Not a refit prediction.')
    target = OUT / 'prepared' / key
    target.mkdir(parents=True)
    (target / 'input.xyz').write_bytes(xyz.read_bytes())
    (target / 'preparation.json').write_text(json.dumps(dict(status='prepared', input=molecule,
        xyz_sha256=sha(xyz), restart_provenance=provenance), indent=2) + '\n')
    (OUT / 'tier2').mkdir()
    (OUT / 'tier2/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (OUT / 'tier2/submission-indices.json').write_text('[0]\n')
    runner = (R / 'scripts/tier2_runner.py').read_text()
    needle = "ROOT=Path.home()/'plastchem-euler/tier2-v1'"
    assert runner.count(needle) == 1
    (OUT / 'tier2_runner.py').write_text(runner.replace(needle, "ROOT=Path.home()/'plastchem-euler/tier2-v1/time-limit-retry1'"))
    sbatch = (R / 'scripts/tier2.sbatch').read_text()
    sbatch = sbatch.replace('contam-tier2-chno500700-v1', manifest['name']).replace('72:00:00', '48:00:00')
    sbatch = sbatch.replace('/tier2-v1/tier2_runner.py', '/tier2-v1/time-limit-retry1/tier2_runner.py')
    (OUT / 'tier2.sbatch').write_text(sbatch)
    pins = {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*')) if p.is_file()}
    result = dict(status='prepared_not_submitted', entry=key, original_task=source['task'],
        new_array_index=0, walltime='48:00:00', cpus=1, memory='4G', partition='research',
        constraint='milan&cpu', file_pins=pins, source_runner_sha256=sha(R / 'scripts/tier2_runner.py'),
        remaining_gates=['Current queue/accounting reconciliation', 'Shared-cap controller registration',
            'Collector attempt registration preserving the original', 'Remote staging digest verification',
            'Held submission and scheduler readback before release'])
    (OUT.parent / 'preparation-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
