"""Pin the A-10 extension and a deliberately stratified calibration, no compute.

All surfaces and licensed-derived metadata stay in the lane's bulk store. The
32 panel identities/surfaces are never replaced. Reused polymer coefficients
remain bound to their original sealed A-9 partition checkpoints.
"""
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

ROOT = Path(__file__).resolve().parents[1]
BULK = Path('/mnt/r/plastchem-euler')
OUT = BULK / 'phase10-v1'


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2) + '\n'
    if p.exists():
        assert p.read_text() == data, f'Refusing to replace pinned input: {p}'
    else:
        p.write_text(data)


def main():
    OUT.mkdir(exist_ok=True)
    a9 = BULK / 'phase9-v1'
    lib = BULK / 'phase9-solvent-library-v1'
    base = BULK / 'phase8-v1'
    cohort_path = BULK / 'phase83-v1/cohort.json'
    cohort = json.loads(cohort_path.read_text())['rows']
    assert len(cohort) == 5830
    features = {}
    for r in cohort:
        m = Chem.MolFromSmiles(r['input']['smiles'])
        assert m is not None
        features[r['index']] = dict(atoms=Chem.AddHs(m).GetNumAtoms(),
            nitrogen_atoms=sum(a.GetAtomicNum() == 7 for a in m.GetAtoms()),
            oxygen_atoms=sum(a.GetAtomicNum() == 8 for a in m.GetAtoms()),
            rings=rdMolDescriptors.CalcNumRings(m),
            aromatic_atoms=sum(a.GetIsAromatic() for a in m.GetAtoms()),
            rotatable_bonds=rdMolDescriptors.CalcNumRotatableBonds(m))
        assert features[r['index']]['atoms'] == r['input']['atoms']
    manifest = json.loads((base / 'manifest.json').read_text())
    overlap = list(csv.DictReader((lib / 'common-panel-connectivity-overlap.csv').open()))
    common = json.loads((lib / 'common-name-inventory.json').read_text())['rows']
    old_keys = {s['name'] for s in manifest['solvents']}
    overlap_keys = {r['common_solvent_key'] for r in overlap}
    assert len(old_keys) == 32 and len(overlap_keys) == 30
    new_keys = {r['solvent_key'].lower() for r in common} - overlap_keys
    assert len(new_keys) == 39 and not new_keys & old_keys
    audit = json.loads((lib / 'latest-surface-audit.json').read_text())
    assert audit['surface_checks_passed'] == audit['profiles_loaded'] == 69
    profiles = {r['inchikey']: r for r in json.loads(
        (Path(audit['path']) / 'profile-loading.json').read_text())}
    volumes = {}
    for name in ['additional-molar-volumes.json', 'supplier-molar-volumes.json']:
        path = ROOT / 'state/thermodynamics-v1' / name
        for r in json.loads(path.read_text())['entries'].values():
            assert r['temperature_K'] == 298.15
            volumes[r['source_inchikey'].split('-')[0]] = dict(
                value=r['molar_volume_cm3_mol'], reference=r,
                source_file=str(path), source_file_sha256=sha(path))
    solvents = []
    for path in sorted((lib / 'results').glob('*/verified-result.json')):
        r = json.loads(path.read_text())
        key = r['input']['name'].lower()
        if key not in new_keys:
            continue
        assert r['status'] == 'converged' and r['connectivity_match']
        surface = path.parent / 'surface.orcacosmo'
        assert sha(surface) == r['surface_sha256'] == profiles[r['inchikey']]['surface_sha256']
        volume = volumes.get(r['inchikey'].split('-')[0])
        solvents.append(dict(name=key, inchikey=r['inchikey'], smiles=r['input']['smiles'],
            molecular_weight_g_mol=r['input']['molecular_weight_g_mol'],
            surface='../phase9-solvent-library-v1/returns/' + r['inchikey'] + '/surface.orcacosmo',
            surface_sha256=r['surface_sha256'], verified_result_sha256=sha(path),
            cavity_volume_cm3_mol=profiles[r['inchikey']]['volume'] * .602214076,
            physical_volume=volume, identity_match_basis=r['identity_match_basis']))
    solvents.sort(key=lambda r: r['name'])
    assert {r['name'] for r in solvents} == new_keys
    unit_sources = {}
    for filename in ['production-plan.json', 'chunk-probe-plan.json']:
        path = a9 / filename
        plan = json.loads(path.read_text())
        plan_digest = sha(path)
        for i, units in enumerate(plan['chunks']):
            for u in units:
                assert u['id'] not in unit_sources
                unit_sources[u['id']] = dict(u, primary_partition='../phase9-v1/' + plan['output'] +
                    f'/{i:04d}/partition/' + u['id'] + '.json',
                    primary_plan_sha256=plan_digest)
    assert len(unit_sources) == 5830
    byunit = {}
    for r in cohort:
        unit = f"cohort-{r['index']:05d}"
        u = unit_sources[unit]
        assert u['inchikey'] == r['inchikey'] and u['surface_sha256'] == r['surface_sha256']
        u.update(stratum=r['stratum'], name=r['input']['name'], atoms=r['input']['atoms'],
                 input_smiles=r['input']['smiles'])
        byunit[unit] = u
    # Original calibration represents all nine size/N strata, three each.
    gate = json.loads((a9 / 'gate-plan.json').read_text())
    reasons = collections.defaultdict(list)
    for u in sum(gate['chunks'], []):
        if u['reference'] == 'calibration':
            reasons[f"cohort-{u['reference_index']:05d}"].append('original three-per-stratum calibration')
    assert len(reasons) == 27
    # Include all four named anchors, numerical difficulties, and chemical extremes.
    for r in cohort:
        name = r['input']['name'].lower()
        if r['inchikey'] in {'FLKPEMZONWLCSK-UHFFFAOYSA-N', 'DOIRQSBPFJWKBE-UHFFFAOYSA-N',
                            'IRIAEXORFWYRCZ-UHFFFAOYSA-N', 'BJQHLKABXJIVAM-UHFFFAOYSA-N'}:
            reasons[f"cohort-{r['index']:05d}"].append('phthalate anchor')
    inventory = ROOT / 'reports/phase9-unresolved-inventory-2026-09-24/unresolved-systems.csv'
    unresolved = list(csv.DictReader(inventory.open()))
    counts = collections.Counter(r.get('unit', r.get('unit_id')) for r in unresolved)
    assert None not in counts
    for unit, n in counts.most_common(5):
        reasons[unit].append(f'prior unresolved systems: {n}')
    for field in ['atoms', 'nitrogen_atoms', 'oxygen_atoms', 'rings', 'aromatic_atoms', 'rotatable_bonds']:
        r = max(cohort, key=lambda r: (features[r['index']][field], -r['index']))
        reasons[f"cohort-{r['index']:05d}"].append('population maximum ' + field)
    population = collections.Counter(r['stratum'] for r in cohort)
    chunks = []
    for stratum in sorted(population):
        selected = [dict(byunit[k], calibration_reasons=v) for k, v in sorted(reasons.items())
                    if byunit[k]['stratum'] == stratum]
        assert len(selected) >= 3
        chunks.append(selected)
    data = dict(scope='A-10 39 common-only solvents; primary panel untouched',
                cohort_sha256=sha(cohort_path), primary_manifest_sha256=sha(base / 'manifest.json'),
                solvents=solvents, polymers=sorted(manifest['polymers']),
                denominator=5830, partition_rows=4547400, lle_rows=227370,
                temperature_K=298.15, solute_mole_fraction=0.,
                primary_genoa_gate_sha256=sha(a9 / 'genoa-comparison.json'),
                population_strata=dict(population), controls=['water', 'hexane'])
    save(OUT / 'manifest.json', data)
    plan = dict(scope='A-10 measured calibration; not production clearance',
                manifest_sha256=sha(OUT / 'manifest.json'), chunks=chunks,
                subbatch=10, grid_batch=256, output='calibration-results-v1',
                cost_limit_CPU_h=510, production_chunk_size=100,
                constraint='(milan|genoa)&cpu')
    save(OUT / 'calibration-plan.json', plan)
    save(OUT / 'cohort-units.json', list(byunit.values()))
    summary = dict(calibration_units=sum(map(len, chunks)), calibration_chunks=len(chunks),
        sample_strata={s: len(c) for s, c in zip(sorted(population), chunks)},
        extension_solvents=len(solvents), available_physical_volumes=sum(s['physical_volume'] is not None for s in solvents),
        missing_physical_volumes=[s['name'] for s in solvents if s['physical_volume'] is None],
        manifest_sha256=sha(OUT / 'manifest.json'), calibration_plan_sha256=sha(OUT / 'calibration-plan.json'))
    save(OUT / 'preparation-summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
