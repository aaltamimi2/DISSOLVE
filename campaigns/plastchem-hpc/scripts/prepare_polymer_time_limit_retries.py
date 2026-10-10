"""Prepare isolated geometry restarts; no transport, submission or record changes."""
import copy
import hashlib
import json
from pathlib import Path

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler/polymer-v1')
SOURCE = B / 'time-limit-recovery-20260924'
OUT = B / 'time-limit-retry1/staging'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not OUT.exists(), 'Reconcile any existing staged retry before rebuilding'
    inventory = json.loads((SOURCE / 'salvage-inventory.json').read_text())
    receipt = json.loads((SOURCE / 'receipt.json').read_text())
    identities = json.loads((SOURCE / 'identity-summary.json').read_text())
    assert sha(SOURCE / 'salvage-inventory.json') == receipt['inventory_sha256'] == identities['inventory_sha256']
    rows = {row['entry_id']: row for row in inventory['rows']}
    verified = {row['entry_id']: row for row in identities['rows']}
    assert len(rows) == len(verified) == 2 and set(rows) == set(verified)
    OUT.mkdir(parents=True)
    specifications = [('body', 'polyethersulfone__conf_5180', 48),
                      ('large', 'polyurethane__PU_conf_39', 96)]
    staged = []
    for group, key, hours in specifications:
        source = rows[key]
        assert source['disposition'] == 'time_limit_retry_candidate'
        assert source['restart_source'] == 'salvaged_geometry'
        original_manifest = R / 'state/polymer-v1' / group / 'manifest.json'
        original = json.loads(original_manifest.read_text())
        molecule = copy.deepcopy(next(m for m in original['molecules'] if m['entry_id'] == key))
        check = verified[key]['geometry_identity']
        xyz = SOURCE / key / 'restart.xyz'
        assert sha(xyz) == source['xyz_sha256'] == check['geometry_sha256']
        assert check['input_inchikey'] == molecule['inchikey'] and check['identity_verified'] and check['connectivity_match']
        assert int(xyz.read_text().splitlines()[0]) == molecule['atoms']
        provenance = {k: v for k, v in source.items() if k not in ['xyz', 'original_record']}
        provenance.update(geometry_identity=check, original_manifest_sha256=sha(original_manifest),
                          inventory_sha256=receipt['inventory_sha256'],
                          original_array_index=molecule['array_index'], retry_walltime_hours=hours,
                          method='Same frozen recipe restarted from last salvaged geometry; no GBW or recipe changes')
        molecule.update(array_index=0, restart_provenance=provenance)
        target = OUT / 'prepared' / key
        target.mkdir(parents=True)
        (target / 'input.xyz').write_bytes(xyz.read_bytes())
        (target / 'preparation.json').write_text(json.dumps(dict(status='prepared', xyz_sha256=sha(xyz),
            restart_provenance=provenance), indent=2) + '\n')
        original.update(molecules=[molecule], name='contam-polymer24a-timelimit-retry1-' + group,
                        walltime=f'{hours}:00:00',
                        retry_authority='Owner instruction: retain time-limit interruptions and resubmit with longer limits',
                        retry_limit_basis=f'{hours} hours from salvaged geometry; previous interruption at {source["accounting"][2]} seconds, not an atom-count-fit prediction')
        (OUT / group).mkdir()
        (OUT / group / 'manifest.json').write_text(json.dumps(original, indent=2) + '\n')
        sbatch = (R / 'state/polymer-v1/body.sbatch').read_text()
        sbatch = sbatch.replace('contam-polymer24a-body-v1', original['name']).replace('72:00:00', original['walltime'])
        sbatch = sbatch.replace('/polymer-v1/polymer_runner.py" body', f'/polymer-v1/time-limit-retry1/polymer_runner.py" {group}')
        (OUT / f'{group}.sbatch').write_text(sbatch)
        staged.append(dict(group=group, entry_id=key, original_task=source['task'], hours=hours,
                           geometry_sha256=sha(xyz), manifest_sha256=sha(OUT / group / 'manifest.json')))
    runner = (R / 'scripts/polymer_runner.py').read_text()
    needle = "ROOT=Path.home()/'plastchem-euler/polymer-v1'"
    assert runner.count(needle) == 1
    (OUT / 'polymer_runner.py').write_text(runner.replace(needle, "ROOT=Path.home()/'plastchem-euler/polymer-v1/time-limit-retry1'"))
    files = {str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*')) if p.is_file()}
    result = dict(status='prepared_not_submitted', entries=staged, file_pins=files,
                  source_runner_sha256=sha(R / 'scripts/polymer_runner.py'),
                  remaining_gates=['Current queue/accounting reconciliation', 'Current partition limits',
                                   'Original-attempt preservation', 'Shared-cap controller registration',
                                   'Remote staging digest verification', 'Held submission and scheduler readback'])
    (OUT.parent / 'preparation-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
