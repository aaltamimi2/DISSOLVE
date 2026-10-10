"""Read-only check of input-specified stereochemistry against accepted coordinates.

This supplements the owner's connectivity policy; it does not change acceptance.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path

from rdkit import Chem, rdBase
from rdkit.Chem import rdDetermineBonds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    record_path = root / 'state/tier2-v1/records' / (args.key + '.json')
    record = json.loads(record_path.read_text())
    assert record['status'] == 'converged' and record['inchikey'] == args.key
    xyz_path = Path(record['archive_path']) / 'optimized.xyz'
    original = Chem.MolFromSmiles(record['input']['smiles'])
    perceived = Chem.MolFromXYZBlock(xyz_path.read_text())
    rdDetermineBonds.DetermineBonds(perceived, charge=0,
                                  allowChargedFragments=True, embedChiral=True)
    perceived = Chem.RemoveHs(perceived)
    key = Chem.MolToInchiKey(perceived)
    assert key == record['perceived_inchikey']
    assert Chem.MolToInchiKey(original) == args.key
    assert key.split('-')[0] == args.key.split('-')[0]
    mapping = perceived.GetSubstructMatch(original, useChirality=True)
    source_centres = Chem.FindMolChiralCenters(original, includeUnassigned=True)
    output_centres = dict(Chem.FindMolChiralCenters(perceived, includeUnassigned=True))
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    result = dict(
        utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        input_inchikey=args.key, perceived_inchikey=key,
        name=record['input']['name'], rdkit_version=rdBase.rdkitVersion,
        input_smiles=record['input']['smiles'],
        perceived_smiles=Chem.MolToSmiles(perceived, isomericSmiles=True),
        same_atom_and_bond_counts=(original.GetNumAtoms() == perceived.GetNumAtoms()
                                  and original.GetNumBonds() == perceived.GetNumBonds()),
        input_specified_stereochemistry_matches=bool(mapping),
        atom_mapping_input_to_output=list(mapping),
        centres=[dict(input_atom=i, input_CIP=label,
                      output_atom=mapping[i] if mapping else None,
                      output_CIP=output_centres.get(mapping[i]) if mapping else None)
                 for i, label in source_centres],
        source_sha256={str(path): sha(path) for path in
                       [record_path, xyz_path, Path(__file__)]},
        scope='RDKit coordinate perception and chirality-aware full-structure substructure match. '
              'Reports whether the specified input constraints match the output; '
              'not experimental stereochemical validation or a change to D-IDENT.')
    assert result['same_atom_and_bond_counts']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
