"""Read-only diagnostic of partial stereo constraints; does not change D-IDENT."""
import argparse
import collections
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
from rdkit import Chem, RDLogger, rdBase
RDLogger.DisableLog('rdApp.warning')
ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unit(request):
    from rdkit.Chem import rdDetermineBonds
    source = Chem.MolFromSmiles(request['smiles'])
    assert Chem.MolToInchiKey(source) == request['input_inchikey'], 'Input SMILES/key mismatch'
    xyz = Path(request['xyz']).read_bytes()
    perceived = Chem.MolFromXYZBlock(xyz.decode())
    rdDetermineBonds.DetermineBonds(perceived, charge=0)
    key = Chem.MolToInchiKey(perceived)
    assert key == request['perceived_inchikey'], 'Fresh perceived key differs from accepted record'
    perceived = Chem.RemoveHs(perceived)
    same_size = (source.GetNumAtoms(), source.GetNumBonds()) == (perceived.GetNumAtoms(), perceived.GetNumBonds())
    match = perceived.GetSubstructMatch(source, useChirality=True) if same_size else ()
    return dict(status='specified_constraints_preserved' if match else 'specified_constraints_not_matched',
                fresh_perceived_key=key, perceived_smiles=Chem.MolToSmiles(perceived),
                xyz_sha256=sha(xyz), same_atom_bond_counts=same_size,
                chirality_aware_atom_mapping=list(match), rdkit_version=rdBase.rdkitVersion)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--unit', action='store_true')
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    if args.unit:
        print(json.dumps(unit(json.load(sys.stdin))))
        return
    out = args.output.resolve()
    assert out.is_relative_to(Path('/mnt/r/plastchem-euler/validation-reviews')) and not out.exists()
    out.mkdir(parents=True)
    cohort, selected, counts, source_pins = [], [], collections.Counter(), {}
    for tier in ['campaign-v1', 'tier2-v1']:
        for path in sorted((ROOT / 'state' / tier / 'records').glob('*.json')):
            raw = path.read_bytes()
            r = json.loads(raw)
            if r.get('status') != 'converged':
                continue
            mol = Chem.MolFromSmiles(r['input']['smiles'])
            assert mol is not None
            specified = sum(str(i.specified) == 'Specified' for i in Chem.FindPotentialStereo(mol))
            key = r.get('inchikey', path.stem)
            same = key == r['perceived_inchikey']
            source_pins[str(path)] = sha(raw)
            item = dict(tier=tier, input_inchikey=key, perceived_inchikey=r['perceived_inchikey'],
                        name=r['input']['name'], cas=r['input'].get('cas', ''),
                        specified_stereo_features=specified, full_key_equal=same,
                        record_sha256=sha(raw), record_path=str(path))
            cohort.append(item)
            counts[f'{tier}:specified={bool(specified)}:full_key_equal={same}'] += 1
            if specified and not same:
                selected.append(dict(item, smiles=r['input']['smiles'],
                                     xyz=str(Path(r['archive_path']) / 'optimized.xyz')))
    with (out / 'accepted-identity-census.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(cohort[0]))
        w.writeheader()
        w.writerows(cohort)
    outcomes = collections.Counter()
    with (out / 'constraint-checks.jsonl').open('w') as stream:
        for request in selected:
            try:
                p = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--unit'],
                                   input=json.dumps(request), text=True, capture_output=True, timeout=30)
                assert p.returncode == 0, p.stderr[-2500:]
                result = json.loads(p.stdout)
            except Exception as exc:
                result = dict(status='constraint_check_unresolved', error=str(exc))
            result.update(request)
            stream.write(json.dumps(result) + '\n')
            stream.flush()
            outcomes[result['status']] += 1
            print(json.dumps(dict(completed=sum(outcomes.values()), denominator=len(selected),
                                  key=request['input_inchikey'], status=result['status'])), flush=True)
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   accepted_captured=len(cohort), identity_counts=dict(counts),
                   specified_with_full_key_difference=len(selected), outcomes=dict(outcomes),
                   rdkit_version=rdBase.rdkitVersion, source_record_sha256=source_pins,
                   interpretation='Read-only structural constraint check using fresh RDKit perception of accepted optimized coordinates. A full key difference can add unspecified stereo without violating specified constraints. D-IDENT acceptance unchanged. Counts are captured per file, not an atomic scheduler census.')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = dict(status='specified_stereo_diagnostic_complete', files={
        p.name: sha(p.read_bytes()) for p in sorted(out.iterdir()) if p.is_file()})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name, pin in manifest['files'].items():
        assert sha((out / name).read_bytes()) == pin
    print(json.dumps(dict(accepted_captured=len(cohort), selected=len(selected), outcomes=dict(outcomes),
                          manifest_sha256=sha((out / 'manifest.json').read_bytes()))))


if __name__ == '__main__':
    main()
