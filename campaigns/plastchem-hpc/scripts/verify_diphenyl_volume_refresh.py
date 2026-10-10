"""Verify the narrow live-panel conversion change against captured prior records."""
import datetime
import hashlib
import json
import math
from pathlib import Path

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler')
D = B / 'tier2-v1/diphenyl-volume-20260924'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = D / 'refresh-verification.json'
    assert not output.exists()
    application = json.loads((D / 'application.json').read_text())
    assert application['status'] == 'applied_and_source_checked'
    previous = json.loads((D / 'processing-ledger.before.json').read_text())
    current = json.loads((R / 'state/tier2-v1/thermodynamics/processing-ledger.json').read_text())
    volume = json.loads((D / 'qualified-volume.json').read_text())
    assert sha(Path(volume['source_evidence_path'])) == volume['source_evidence_sha256']
    correction = math.log10(18.07 / volume['molar_volume_cm3_mol'])
    allowed = {'log10_K_concentration','concentration_basis_status','volume_correction_log10',
               'molar_volume_solvent_cm3_mol','molar_volume_reference_cm3_mol','volume_source'}
    rows = []
    for key, item in sorted(previous.items()):
        before_path = D / 'before-records' / (key + '.json')
        assert sha(before_path) == item['result_sha256']
        before = json.loads(before_path.read_text())
        after_path = Path(current[key]['result_path'])
        raw = after_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        assert digest == current[key]['result_sha256']
        after = json.loads(raw)
        for name in set(before) | set(after):
            if name not in {'updated_utc','partitions_against_water','validation'}:
                assert before[name] == after[name], (key, name)
        assert before['activities'] == after['activities']
        for name in before['validation']:
            if name != 'concentration_antisymmetry_max_log10':
                assert before['validation'][name] == after['validation'][name]
        assert after['validation']['concentration_antisymmetry_max_log10'] < 1e-10
        a = {p['solvent']: p for p in before['partitions_against_water']}
        b = {p['solvent']: p for p in after['partitions_against_water']}
        assert set(a) == set(b) and len(a) == 32
        for solvent in a:
            if solvent != 'diphenyl ether':
                assert a[solvent] == b[solvent], (key, solvent)
        old, new = a['diphenyl ether'], b['diphenyl ether']
        assert old['log10_K_concentration'] is None
        assert {k:v for k,v in old.items() if k not in allowed} == {k:v for k,v in new.items() if k not in allowed}
        assert new['volume_source']['solvent'] == volume
        assert math.isfinite(new['log10_K_concentration'])
        error = abs(new['log10_K_concentration'] - new['log10_K_mole_fraction'] - correction)
        assert error < 1e-12
        assert new['molar_volume_reference_cm3_mol'] == 18.07
        assert new['molar_volume_solvent_cm3_mol'] == volume['molar_volume_cm3_mol']
        dest = D / 'after-records' / (key + '.json')
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(raw)
        assert sha(dest) == digest
        rows.append(dict(inchikey=key, before_sha256=item['result_sha256'], after_sha256=digest,
                         conversion_error=error, activities_unchanged=True, other_31_pairs_unchanged=True))
    for name, pin in application['release_manifest_pins'].items():
        assert sha(B / name / 'manifest.json') == pin
    result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  status='all_captured_records_changed_only_in_diphenyl_concentration_conversion',
                  records=len(rows), rows=rows, correction_log10=correction,
                  maximum_conversion_error=max(r['conversion_error'] for r in rows),
                  source_qualification=volume['qualification'],
                  source_uncertainty=volume['uncertainty_note'],
                  release_manifest_pins=application['release_manifest_pins'],
                  verifier_sha256=sha(Path(__file__)))
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}, indent=2))


if __name__ == '__main__':
    main()
