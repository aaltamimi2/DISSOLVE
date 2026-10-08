"""Pin disjoint post-A10 calculation inputs; no solving or submission.

The frozen cohort needs only new polymer activities. Later contaminants need
the full phase set and binary LLE. Mixed-connectivity PETG is explicitly held.
"""
import argparse
import csv
import datetime
import hashlib
import json
from pathlib import Path

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--audit', type=Path, required=True)
    ap.add_argument('--species', type=Path, required=True)
    ap.add_argument('--cache', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    assert out.is_relative_to(B / 'coverage-plans') and not out.exists()
    pins = {}

    def read(path):
        path = Path(path)
        raw = path.read_bytes()
        pins[str(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    def sealed(root, status):
        m = read(root / 'manifest.json')
        assert m['status'] == status
        for rel, digest in m['files'].items():
            assert sha(root / rel) == digest, str(root / rel)
        return m

    audit = sealed(args.audit, 'completed_snapshot_verified')
    # These preparatory manifests have a data-specific status; pin all payloads.
    for root in (args.species, args.cache):
        m = read(root / 'manifest.json')
        for rel, digest in m['files'].items():
            assert sha(root / rel) == digest
    for name, phase in [('promotion-v1', 'phase9-v1'), ('promotion-ext39-v1', 'phase10-v1')]:
        m = read(B / name / 'manifest.json')
        v = read(B / phase / 'delivery-verification.json')
        assert m['status'] == 'complete' and v['status'] == 'complete_delivery_verified'
        assert pins[str(B / name / 'manifest.json')] == v['manifest_sha256']
    primary = read(B / 'phase8-v1/manifest.json')
    extra = read(B / 'phase10-v1/manifest.json')
    validation = read(B / 'phase8-v1/validation-inputs.json')
    cohort = read(B / 'promotion-v1/provenance/cohort.json')['rows']
    volumes = read(B / 'phase10-v1/qualified-physical-volumes-v4.json')
    volume_by_name = {v['solvent']: v for v in volumes['entries']}
    assert volumes['qualified'] == 39
    polymer_species = read(args.species / 'summary.json')
    cache_summary = read(args.cache / 'summary.json')
    assert cache_summary['rows'] == 5830 * 71
    assert cache_summary['temperature_K'] == 298.15 and cache_summary['solute_mole_fraction'] == 0
    cache_path = args.cache / 'solvent-activities.parquet'
    pins[str(cache_path)] = sha(cache_path)
    polymers = {}
    for name, rows in primary['polymers'].items():
        polymers[name] = []
        for r in rows:
            path = B / 'phase8-v1' / r['B']
            digest = sha(path)
            assert digest in path.name
            polymers[name].append(dict(entry_id=r['entry_id'], surface=str(path), surface_sha256=digest,
                B_energy_hartree=r['B_energy_hartree'], B_cavity_cm3_mol=r['B_cavity_cm3_mol']))
    assert len(polymers) == 10 and sum(map(len, polymers.values())) == 236
    new_polymers = ['polyethersulfone', 'polyurethane']
    for name in new_polymers:
        rows = list(csv.DictReader((args.species / (name + '-conformers.csv')).open()))
        assert len({r['input_inchikey'].split('-')[0] for r in rows}) == 1
        assert {r['ensemble_key'] for r in rows} == {name}
        polymers[name] = []
        for r in rows:
            assert sha(r['source_surface']) == r['surface_sha256']
            polymers[name].append(dict(entry_id=r['entry_id'], surface=r['source_surface'],
                surface_sha256=r['surface_sha256'], B_energy_hartree=float(r['cosmo_solute_energy_hartree']),
                B_cavity_cm3_mol=float(r['cavity_volume_cm3_mol'])))
    solvents = {}
    for s in primary['solvents']:
        name = s['name']
        path = B / 'phase8-v1' / s['B']
        digest = sha(path)
        assert digest in path.name
        regimes = {r['regime']: r['temperature_K'] for r in validation['lle_units'] if r['solvent'] == name}
        assert set(regimes) == {'RT', 'high'} and regimes['RT'] == 298.15
        solvents[name] = dict(surface=str(path), surface_sha256=digest,
            molecular_weight_g_mol=validation['solvent_identities'][name]['molecular_weight_g_mol'],
            normalized_volume_cm3_mol=s['B_volume'], existing_volume_cm3_mol=s['B_legacy_volume'],
            volume_reference=s['B_volume_source'], lle_regimes=regimes, source='panel32')
    for s in extra['solvents']:
        name = s['name']
        assert name not in solvents
        path = (B / 'phase10-v1' / s['surface']).resolve()
        assert sha(path) == s['surface_sha256']
        solvents[name] = dict(surface=str(path), surface_sha256=s['surface_sha256'],
            molecular_weight_g_mol=s['molecular_weight_g_mol'],
            normalized_volume_cm3_mol=volume_by_name[name]['molar_volume_cm3_mol'],
            existing_volume_cm3_mol=s['cavity_volume_cm3_mol'], volume_reference=volume_by_name[name],
            lle_regimes={'RT': 298.15}, source='extension39')
    assert len(solvents) == 71 and sum(len(s['lle_regimes']) for s in solvents.values()) == 103
    frozen = {}
    for r in cohort:
        frozen[r['inchikey']] = dict(inchikey=r['inchikey'], name=r['input']['name'],
            surface=r['source_surface'], surface_sha256=r['surface_sha256'],
            molecular_weight_g_mol=r['molecular_weight_g_mol'],
            atoms=r['input']['atoms'], tier=r['tier'], source='frozen5830')
    assert len(frozen) == 5830
    later = {}
    for key in audit['snapshot']['keys']:
        r = read(args.audit / 'freeze/state/tier2-v1/records' / (key + '.json'))
        assert r['status'] == 'converged' and key not in frozen
        path = Path(r['archive_path']) / 'surface.orcacosmo'
        assert sha(path) == r['surface_sha256']
        later[key] = dict(inchikey=key, name=r['input']['name'], surface=str(path),
            surface_sha256=r['surface_sha256'], atoms=r['input']['atoms'],
            molecular_weight_g_mol=float(r['input']['molecular_weight_g_mol']), tier='tier2', source='later_audited')
    assert len(later) == audit['snapshot']['newly_accepted']
    anchors = {name: validation['solutes'][name]['input']['inchikey'] for name in ['DEP', 'DBP', 'BBP', 'DEHP']}
    assert set(anchors.values()) <= frozen.keys()
    package_path = B / 'phase8-v1/package-pins.json'
    package = read(package_path)
    for rel, digest in package.items():
        assert sha(package_path.parent / rel) == digest
    result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        status='prepared_requires_reproduction_and_measured_cost_gate', temperature_K=298.15,
        solute_mole_fraction=0, reference_state='pure_component', parameterization='openCOSMO-RS 24a',
        polymers=polymers, new_polymers=new_polymers, solvents=solvents, frozen=frozen, later=later,
        anchors=anchors, solvent_cache=str(cache_path), solvent_cache_sha256=pins[str(cache_path)],
        package_root=str(package_path.parent), package_pins=package,
        excluded_polymer_groups=dict(petg='Three distinct connectivities; owner representation pending',
                                     nitrocellulose='Seven conformers not yet accepted'),
        rectangles=dict(frozen_contaminants_new_polymers=len(frozen)*len(new_polymers)*71*2,
                        later_contaminants_all_unambiguous_polymers=len(later)*len(polymers)*71*2,
                        later_contaminants_binary_lle=len(later)*103),
        source_sha256=pins, preparation_script_sha256=sha(__file__),
        policy='No frozen release change, mixed-species averaging, new temperature, interpolated result or product write. Missing activity must be computed or carry an explicit unresolved status.')
    out.mkdir(parents=True)
    (out / 'inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'prepare_supplement_inputs.py').write_bytes(Path(__file__).read_bytes())
    files={p.name:sha(p) for p in out.iterdir() if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps(dict(status=result['status'],files=files),indent=2)+'\n')
    print(json.dumps(dict(path=str(out),frozen=len(frozen),later=len(later),polymer_ensembles=len(polymers),
        conformers=sum(map(len,polymers.values())),rectangles=result['rectangles'],manifest_sha256=sha(out/'manifest.json'))))


if __name__ == '__main__':
    main()
