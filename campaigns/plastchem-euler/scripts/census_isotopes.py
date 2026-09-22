"""Local, read-only pinned-input isotope census and candidate parent mapping; no policy applied."""
import csv, hashlib, json, re
from collections import Counter, defaultdict
from pathlib import Path
from rdkit import Chem, rdBase

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/isotope-census'
STATE = ROOT / 'state/isotope-census'
TIERS = {'CHNO': 'firstpass', 'heteroatoms': 'flagged_heteroatoms', 'Si_B': 'flagged_si_b'}
inputs = {}
digests = {}
for line in (ROOT / 'state/INPUTS.sha256').read_text().splitlines():
    digest, name = line.split()
    path = ROOT / 'inputs' / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    inputs[name] = list(csv.DictReader(path.open()))
    digests[name] = digest

rows = {tier: inputs[f'plastchem_organics_orca_opencosmo_{suffix}.csv'] for tier, suffix in TIERS.items()}
groups = {}
all_keys = {}
for tier, data in rows.items():
    groups[tier] = defaultdict(list)
    for row in data:
        groups[tier][row['inchikey']].append(row)
        assert row['inchikey'] not in all_keys or all_keys[row['inchikey']] == tier
        all_keys[row['inchikey']] = tier
assert [len(groups[t]) for t in TIERS] == [5833, 1721, 238]
assert set(all_keys) == {r['inchikey'] for r in inputs['plastchem_organics_orca_opencosmo_firstpass_audited_8065.csv']}

records = []
summary = {}
for tier, by_key in groups.items():
    tier_records = []
    for key, aliases in sorted(by_key.items()):
        derived = []
        for row in aliases:
            mol = Chem.MolFromSmiles(row['smiles'])
            assert mol is not None and Chem.MolToInchiKey(mol) == key, key
            labelled = [{'atom_index': a.GetIdx(), 'element': a.GetSymbol(), 'mass_number': a.GetIsotope()}
                        for a in mol.GetAtoms() if a.GetIsotope() != 0]
            bracket_labels = re.findall(r'\[(\d+)([A-Z][a-z]?|[bcnops])', row['smiles'])
            assert len(bracket_labels) == len(labelled), (key, bracket_labels, labelled)
            parent = Chem.Mol(mol)
            for atom in parent.GetAtoms():
                atom.SetIsotope(0)
            parent = Chem.RemoveHs(parent)
            Chem.AssignStereochemistry(parent, cleanIt=True, force=True)
            parent_smiles = Chem.MolToSmiles(parent, isomericSmiles=True)
            parent_key = Chem.MolToInchiKey(parent)
            assert Chem.MolToInchiKey(Chem.MolFromSmiles(parent_smiles)) == parent_key
            assert parent_key.split('-')[0] == key.split('-')[0], (key, parent_key)
            assert not any(a.GetIsotope() for a in parent.GetAtoms())
            derived.append((bool(labelled), parent_key))
        assert len(set(derived)) == 1, (key, derived)
        if not labelled:
            assert parent_key == key
            continue
        assert parent_key != key
        existing_tier = all_keys.get(parent_key)
        existing = groups[existing_tier][parent_key] if existing_tier else []
        record = {
            'tier': tier, 'inchikey': key,
            'name': ' | '.join(sorted({r['name'] for r in aliases})),
            'source_rows': len(aliases),
            'cas': ' | '.join(sorted({r['cas'] for r in aliases})),
            'smiles': aliases[-1]['smiles'],
            'isotope_atoms': labelled,
            'isotope_composition': dict(Counter(f"{a['mass_number']}{a['element']}" for a in labelled)),
            'unlabelled_parent_inchikey': parent_key,
            'unlabelled_parent_smiles': parent_smiles,
            'parent_present_in_same_tier': parent_key in by_key,
            'parent_present_in_any_tier': bool(existing),
            'parent_tier': existing_tier,
            'parent_names': ' | '.join(sorted({r['name'] for r in existing})),
            'parent_source_rows': len(existing),
            'aliases': aliases,
        }
        records.append(record)
        tier_records.append(record)
    labelled_keys = {r['inchikey'] for r in tier_records}
    parent_keys = {r['unlabelled_parent_inchikey'] for r in tier_records}
    unlabelled_keys = set(by_key) - labelled_keys
    summary[tier] = {
        'rows': len(rows[tier]), 'unique_structures': len(by_key),
        'labelled_unique_structures': len(labelled_keys),
        'labelled_source_rows': sum(r['source_rows'] for r in tier_records),
        'distinct_unlabelled_parents': len(parent_keys),
        'labelled_entries_with_parent_in_same_tier': sum(r['parent_present_in_same_tier'] for r in tier_records),
        'parents_missing_from_same_tier': sorted(parent_keys - unlabelled_keys),
        'unlabelled_unique_if_excluding_isotopologues': len(unlabelled_keys),
        'distinct_electronic_targets_if_mapping_all': len(unlabelled_keys | parent_keys),
        'isotope_entry_counts': dict(Counter(label for r in tier_records for label in r['isotope_composition'])),
    }

summary['total'] = {field: sum(s[field] for t, s in summary.items()) for field in [
    'rows', 'unique_structures', 'labelled_unique_structures', 'labelled_source_rows',
    'distinct_unlabelled_parents', 'labelled_entries_with_parent_in_same_tier',
    'unlabelled_unique_if_excluding_isotopologues', 'distinct_electronic_targets_if_mapping_all']}
report = {'policy_selected': None, 'cluster_work_performed': False, 'rdkit_version': rdBase.rdkitVersion,
          'input_digests': digests, 'summary': summary, 'records': records,
          'method': 'Every row parsed; nonzero RDKit atom isotope flags cross-checked against bracketed isotope tokens. Parent clears isotope flags only, removes ordinary explicit H and cleans stereochemistry; canonical SMILES roundtrip and connectivity-key checks required. No parent calculation or result substitution performed.',
          'audit_gap_attribution': 'Orchestrator statement: the 2026-09-12 8,065-row audit checked parsing, net charge, fragment count and molecular weight, but did not check isotope labels. Labelled species can pass every check because RDKit reports their labelled molecular weight. The orchestrator owns this gap; it is not attributed to the census lane.'}
(STATE / 'census.json').write_text(json.dumps(report, indent=2) + '\n')
columns = [k for k in records[0] if k not in ['aliases', 'isotope_atoms']] if records else []
with (OUT / 'isotopologue-parent-mapping.csv').open('w', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for record in records:
        writer.writerow({k: json.dumps(record[k], sort_keys=True) if isinstance(record[k], dict) else record[k] for k in columns})
print(json.dumps(summary, indent=2))
