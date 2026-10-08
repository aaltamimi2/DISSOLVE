"""Pin disjoint remaining coverage after A-10; no calculation or submission.

The frozen packages stay immutable. This inventory deliberately distinguishes
the later legacy solvent/water panel from the exact-zero polymer grid.
"""
import argparse
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    out = ap.parse_args().output.resolve()
    assert out.is_relative_to(B / 'coverage-plans') and not out.exists()
    pins = {}

    def read(path):
        path = Path(path)
        raw = path.read_bytes()
        pins[str(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    for release, phase in [('promotion-v1', 'phase9-v1'),
                           ('promotion-ext39-v1', 'phase10-v1')]:
        path = B / release / 'manifest.json'
        manifest = read(path)
        proof = read(B / phase / 'delivery-verification.json')
        assert manifest['status'] == 'complete'
        assert proof['status'] == 'complete_delivery_verified'
        assert pins[str(path)] == proof['manifest_sha256']
    cohort = read(B / 'promotion-v1/provenance/cohort.json')['rows']
    frozen = {r['inchikey'] for r in cohort}
    assert len(cohort) == len(frozen) == 5830
    primary = read(B / 'phase8-v1/manifest.json')
    extension = read(B / 'phase10-v1/manifest.json')
    frozen_polymers = set(primary['polymers'])
    panel = {s['name'] for s in primary['solvents']}
    extra = {s['name'] for s in extension['solvents']}
    assert len(frozen_polymers) == 10 and len(panel) == 32 and len(extra) == 39
    assert not panel & extra
    solvents = panel | extra
    ledger = read(R / 'state/tier2-v1/thermodynamics/processing-ledger.json')
    later = []

    def surface_row(path, record):
        assert record['status'] == 'converged' and record['connectivity_match']
        assert all(record['stages'][s]['exit_code'] == 0 for s in ['opt', 'cosmo'])
        assert 'EPYC 7763' in record['cpu_model']
        surface = Path(record['archive_path']) / 'surface.orcacosmo'
        assert sha(surface) == record['surface_sha256'], surface
        return dict(source_record=str(path), record_sha256=pins[str(path)],
                    source_surface=str(surface), surface_sha256=record['surface_sha256'],
                    input_inchikey=record['input_inchikey'],
                    perceived_inchikey=record['perceived_inchikey'],
                    identity_match_basis=record['identity_match_basis'],
                    cpu_model=record['cpu_model'])

    for path in sorted((R / 'state/tier2-v1/records').glob('*.json')):
        record = read(path)
        key = record['inchikey']
        if record['status'] != 'converged' or key in frozen:
            continue
        row = surface_row(path, record)
        row.update(inchikey=key, name=record['input']['name'],
                   atoms=record['input']['atoms'],
                   legacy_panel_processed=key in ledger)
        later.append(row)
    assert len({r['inchikey'] for r in later}) == len(later)
    assert not frozen & {r['inchikey'] for r in later}
    groups = collections.defaultdict(list)
    for path in sorted((R / 'state/polymer-v1/records').glob('*.json')):
        record = read(path)
        groups[record['input']['polymer']].append((path, record))
    assert sum(map(len, groups.values())) == 284
    complete = {p for p, rows in groups.items()
                if all(r['status'] == 'converged' for _, r in rows)}
    assert frozen_polymers <= complete
    new_polymers = complete - frozen_polymers
    species = {}
    mixed_groups = set()
    for polymer, records in sorted(groups.items()):
        named = collections.defaultdict(list)
        for _, record in records:
            named[record['input']['species']].append(record)
        species[polymer] = {}
        for name, members in sorted(named.items()):
            blocks = {r['input']['inchikey'].split('-')[0] for r in members}
            formulas = {r['input']['formula'] for r in members}
            assert len(blocks) == len(formulas) == 1
            species[polymer][name] = dict(conformers=len(members),
                connectivity=next(iter(blocks)), formula=next(iter(formulas)))
        if len({r['input']['inchikey'].split('-')[0] for _, r in records}) > 1:
            mixed_groups.add(polymer)
    assert not mixed_groups & frozen_polymers, 'Frozen ensemble species needs review'
    unambiguous_complete = complete - mixed_groups
    unambiguous_new = new_polymers - mixed_groups
    conformers = []
    for polymer in sorted(new_polymers):
        for path, record in groups[polymer]:
            row = surface_row(path, record)
            row.update(polymer=polymer, entry_id=record['entry_id'],
                       atoms=record['input']['atoms'],
                       cosmo_solute_energy_hartree=record['cosmo_solute_energy_hartree'])
            conformers.append(row)
    n, s, c = len(later), len(solvents), 2
    # Base rectangles exclude multi-connectivity campaign groups. Their final
    # representation needs a separate decision; source energies alone cannot
    # supply the material's sequence/isomer mixture proportions.
    # They are disjoint by contaminant key; no existing release row is repeated.
    a = n * s * len(unambiguous_complete) * c
    b = len(frozen) * s * len(unambiguous_new) * c
    frozen_rows = len(frozen) * s * len(frozen_polymers) * c
    assert frozen_rows + a + b == (len(frozen) + n) * s * len(unambiguous_complete) * c
    distinct_species_count = sum(len(species[p]) for p in complete)
    summary = dict(
        utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        status='inventory_only_no_compute_or_submission',
        frozen_contaminants=len(frozen), later_accepted_contaminants=n,
        accepted_contaminants_in_planned_union=len(frozen) + n,
        later_legacy_panel_processed=sum(r['legacy_panel_processed'] for r in later),
        solvent_keys=sorted(solvents), frozen_polymers=sorted(frozen_polymers),
        new_complete_polymers={p: len(groups[p]) for p in sorted(new_polymers)},
        complete_campaign_polymer_groups=len(complete),
        complete_single_connectivity_groups=len(unambiguous_complete),
        species_by_campaign_polymer=species,
        aggregate_representation_pending=sorted(complete & mixed_groups),
        complete_polymer_conformers=sum(len(groups[p]) for p in complete),
        incomplete_polymers={p: dict(collections.Counter(r['status'] for _, r in rows))
                             for p, rows in sorted(groups.items()) if p not in complete},
        partition_rectangles=[
            dict(cohort='later_accepted', polymers='all_complete_single_connectivity', rows=a),
            dict(cohort='frozen_5830', polymers='new_complete_single_connectivity', rows=b)],
        additional_partition_rows=a + b,
        complete_union_partition_rows=frozen_rows + a + b,
        base_partition_scope='Excludes campaign groups with multiple connectivities pending representation decision',
        conditional_partition_scenarios={
            'each_distinct_species_separately': dict(ensembles=distinct_species_count,
                total_rows=(len(frozen)+n)*s*distinct_species_count*c,
                additional_rows=(len(frozen)+n)*s*distinct_species_count*c-frozen_rows),
            'one_result_per_campaign_group_requires_aggregate_model': dict(groups=len(complete),
                total_rows=(len(frozen)+n)*s*len(complete)*c,
                additional_rows=(len(frozen)+n)*s*len(complete)*c-frozen_rows)},
        additional_binary_LLE_status_rows=n * (len(panel) * 2 + len(extra)),
        convention_count=c, temperature_K=298.15, solute_mole_fraction=0,
        old_LLE_recomputed_for_new_polymers=False,
        assumptions=[
            'Two partition convention rows; mole-fraction and concentration values are columns.',
            'Existing 32 panel solvents keep RT and literal workbook high-temperature LLE; 39 additions keep RT only.',
            'Binary LLE has no polymer dimension, so adding polymers does not add LLE rows for frozen contaminants.',
            'No time or cost estimate follows from row counts. Measure a stratified calibration before production.',
            'Reuse requires exact source/surface/temperature/package provenance; never relabel an old checkpoint.',
            'The original extension worker assumes ten frozen polymers and is not a launcher for this inventory.',
            'New polymer ensembles still need cavity-volume/weight assembly and the corresponding route comparison.',
            'Campaign polymer labels do not imply a single conformer ensemble. PETG contains three distinct connectivities; no aggregate or species mixture weights are selected here.',
            'This snapshot is not a thermodynamic result, release, cost-gate pass or scheduling instruction.'],
        source_sha256=pins, script_sha256=sha(Path(__file__)))
    out.mkdir(parents=True)
    for name, rows in [('later-contaminants.csv', later), ('new-polymer-conformers.csv', conformers)]:
        assert rows, 'Expected nonempty supplementary scope'
        with (out / name).open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    files = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps(dict(status=summary['status'], files=files), indent=2) + '\n')
    assert all(sha(out / name) == digest for name, digest in files.items())
    print(json.dumps({k: v for k, v in summary.items() if k not in ['source_sha256', 'solvent_keys', 'assumptions']}))
    print('manifest_sha256', sha(out / 'manifest.json'))


if __name__ == '__main__':
    main()
