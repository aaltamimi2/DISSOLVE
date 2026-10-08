"""Match literature candidates to immutable predictions; do not qualify accuracy."""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from rdkit import Chem


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output
    assert out.is_dir() and not (out / 'manifest.json').exists()
    assert not (out / 'candidate-matches.csv').exists()
    release = Path('/mnt/r/plastchem-euler/promotion-v1')
    original = Path('/mnt/r/plastchem-euler/tier2-v1/pubchem-measured-20260925-increment46')
    cohort = json.loads((release / 'provenance/cohort.json').read_text())['rows']
    structures = [
        ('4-hydroxybenzoic acid', 'O=C(O)c1ccc(O)cc1'),
        ('methylparaben', 'COC(=O)c1ccc(O)cc1'),
        ('4-methylbenzyl alcohol', 'Cc1ccc(CO)cc1'),
        ('o-toluic acid', 'Cc1ccccc1C(=O)O'),
        ('propylparaben', 'CCCOC(=O)c1ccc(O)cc1'),
        ('diethyl phthalate', 'CCOC(=O)c1ccccc1C(=O)OCC'),
        ('toluene', 'Cc1ccccc1'),
        ('3-(3,5-di-tert-butyl-4-hydroxyphenyl)propionic acid', 'O=C(O)CCc1cc(C(C)(C)C)c(O)c(C(C)(C)C)c1'),
        ('2,4-di-tert-butylphenol', 'Oc1ccc(C(C)(C)C)cc1C(C)(C)C'),
        ('BHT', 'Cc1cc(C(C)(C)C)c(O)c(C(C)(C)C)c1'),
        ('bis(2-ethylhexyl) phthalate', 'CCCCC(CC)COC(=O)c1ccccc1C(=O)OCC(CC)CCCC'),
        ('Irganox 1076', 'CCCCCCCCCCCCCCCCCCOC(=O)CCc1cc(C(C)(C)C)c(O)c(C(C)(C)C)c1'),
    ]
    db = duckdb.connect()
    db.execute("SET memory_limit='256MB'")
    db.execute('SET threads=1')
    db.read_parquet(str(release / 'partition.parquet')).create_view('partition_data')
    candidates = list(csv.DictReader((original / 'polymer-water-table3-candidates.csv').open()))
    assert len(candidates) == len(structures) == 12
    rows = []
    for c, (name, smiles) in zip(candidates, structures):
        key = Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))
        matches = [r for r in cohort if r['inchikey'].split('-')[0] == key.split('-')[0]]
        assert len(matches) <= 1, (name, matches)
        row = dict(c, literature_name=name, reconstructed_smiles=smiles,
                   reconstructed_inchikey=key, input_inchikey='', input_name='',
                   identity_status='absent_from_frozen_cohort', prediction_temperature_K='',
                   normalized_logK_PE_water_x='', normalized_logK_PE_water_concentration='',
                   existing_logK_PE_water_x='', existing_logK_PE_water_concentration='',
                   eligible_for_accuracy_statistics=False,
                   limitation='Original temperature and concentration units unverified; PE density/crystallinity unspecified',
                   source_doi='10.1016/j.ejps.2022.106138', original_doi='10.1016/j.polymer.2007.10.047')
        if matches:
            m = matches[0]
            assert m['input']['cas'] == c['CAS']
            assert Chem.MolToInchiKey(Chem.MolFromSmiles(m['input']['smiles'])).split('-')[0] == key.split('-')[0]
            row.update(input_inchikey=m['inchikey'], input_name=m['input']['name'],
                       identity_status='connectivity_and_CAS_match', prediction_temperature_K=298.15)
            records = db.execute("SELECT convention, logP_x, logP_concentration, status, sign_convention, temperature_K FROM partition_data WHERE input_inchikey=? AND campaign_polymer='pe' AND product_solvent_key='water'", [m['inchikey']]).fetchall()
            assert len(records) == 2 and {r[0] for r in records} == {'normalized', 'existing'}
            for convention, lx, lc, status, sign, temp in records:
                assert status == 'predicted' and temp == 298.15
                assert sign == 'log10 P(solvent/polymer); positive favors solvent'
                row[f'{convention}_logK_PE_water_x'] = -lx
                row[f'{convention}_logK_PE_water_concentration'] = -lc
        rows.append(row)
    with (out / 'candidate-matches.csv').open('x', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(utc=datetime.now(timezone.utc).isoformat(), candidates=len(rows),
                   matched=sum(r['identity_status'] == 'connectivity_and_CAS_match' for r in rows),
                   qualified_for_accuracy=0, no_recalibration=True,
                   direction='Saved water/PE log ratios negated to obtain PE/water on each saved basis',
                   method='Read existing Parquet with DuckDB; no activity calculation',
                   provenance={str(p): digest(p) for p in [release / 'manifest.json', release / 'partition.parquet', release / 'provenance/cohort.json', original / 'manifest.json', original / 'polymer-water-table3-candidates.csv', Path(__file__)]})
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / 'review_pe_water_candidates.py').write_bytes(Path(__file__).read_bytes())
    manifest = dict(utc=summary['utc'], status='provisional_identity_and_direction_review_complete',
                    files={p.name: dict(size=p.stat().st_size, sha256=digest(p)) for p in sorted(out.iterdir()) if p.is_file()})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name, pin in manifest['files'].items():
        assert digest(out / name) == pin['sha256']
    print(json.dumps(summary, indent=2))
    print('manifest_sha256', digest(out / 'manifest.json'))


if __name__ == '__main__':
    main()
