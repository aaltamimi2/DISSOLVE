"""Prepare B-route ensemble inputs from a pinned remaining-coverage inventory.

Parses one surface at a time. No activity calculation, ORCA, submission, A-route
conversion or modification of released manifests. All source conformers remain.
"""
import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
import argparse
import collections
import csv
import datetime
import gc
import hashlib
import json
import math
from pathlib import Path
import re
import resource

import opencosmorspy.input_parsers as parser_module
from opencosmorspy.input_parsers import SigmaProfileParser

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler')
T = 298.15
EH_KJMOL = 2625.4996394799
BOHR_A = 0.52917721092
AVOGADRO_VOLUME = 0.602214076


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def guard():
    memory = dict((line.split(':')[0], int(line.split()[1]))
                  for line in Path('/proc/meminfo').read_text().splitlines())
    rss = int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                   if line.startswith('VmRSS:')))
    assert memory['MemAvailable'] >= 2.5 * 1024**2, 'Pause: host memory below 2.5 GiB'
    assert rss < 1800 * 1024, 'Pause: process memory at guard'


def weights(energies, factor):
    emin = min(energies)
    exponents = [-(e - emin) * factor for e in energies]
    z = math.fsum(math.exp(x) for x in exponents)
    logz = math.log(z)
    normalized = [math.exp(x - logz) for x in exponents]
    assert abs(math.fsum(normalized) - 1) < 1e-12
    return normalized, exponents, logz


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inventory', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    inv = args.inventory.resolve()
    out = args.output.resolve()
    assert inv.is_relative_to(B / 'coverage-plans')
    assert out.is_relative_to(B / 'coverage-plans') and not out.exists()
    manifest = json.loads((inv / 'manifest.json').read_text())
    assert manifest['status'] == 'inventory_only_no_compute_or_submission'
    assert all(sha(inv / name) == digest for name, digest in manifest['files'].items())
    inventory = json.loads((inv / 'summary.json').read_text())
    parser_path = Path(parser_module.__file__)
    frozen_package = B / 'phase8-v1/package-pins.json'
    package = json.loads(frozen_package.read_text())
    assert sha(parser_path) == package['opencosmorspy/input_parsers.py']
    rows = list(csv.DictReader((inv / 'new-polymer-conformers.csv').open()))
    groups = collections.defaultdict(list)
    parent_counts = collections.Counter()
    parent_species = collections.defaultdict(set)
    for row in rows:
        record_path = Path(row['source_record'])
        assert sha(record_path) == row['record_sha256']
        record = json.loads(record_path.read_text())
        species = record['input']['species']
        groups[(row['polymer'], species)].append(row)
        parent_counts[row['polymer']] += 1
        parent_species[row['polymer']].add(species)
    assert dict(parent_counts) == inventory['new_complete_polymers']
    out.mkdir(parents=True)
    summaries = {}
    pins = {str(inv / 'manifest.json'): sha(inv / 'manifest.json'),
            str(parser_path): sha(parser_path), str(frozen_package): sha(frozen_package),
            str(R / 'scripts/phase9_worker_cpu.py'): sha(R / 'scripts/phase9_worker_cpu.py')}
    for (polymer, ensemble_species), members in sorted(groups.items()):
        ensemble_key = ensemble_species if len(parent_species[polymer]) > 1 else polymer
        compositions = set()
        connectivities = set()
        species = set()
        for source in members:
            path = Path(source['source_record'])
            assert sha(path) == source['record_sha256']
            record = json.loads(path.read_text())
            compositions.add(record['input']['formula'])
            connectivities.add(record['input_inchikey'].split('-')[0])
            species.add(record['input']['species'])
        assert len(compositions) == len(connectivities) == len(species) == 1, (
            polymer, 'Do not compare electronic energies across chemical species')
        energies = [float(r['cosmo_solute_energy_hartree']) for r in members]
        wn, exponent_n, logzn = weights(energies, EH_KJMOL / (.00831446261815324 * T))
        wl, exponent_l, logzl = weights(energies, 627.5094740631 / (.0019872041 * T))
        # Analytic constant-activity controls distinguish the two conventions.
        probe = 1.25
        gn = -math.log(math.fsum(math.exp(x - logzn - probe) for x in exponent_n))
        gl = -math.log(math.fsum(math.exp(x - probe) for x in exponent_l))
        assert abs(gn - probe) < 1e-12 and abs(gl - (probe - logzl)) < 1e-12
        volumes = []
        surface_groups = collections.defaultdict(list)
        geometry_groups = collections.defaultdict(list)
        output = out / (ensemble_key + '-conformers.csv')
        with output.open('w', newline='') as handle:
            writer = None
            for i, source in enumerate(members):
                guard()
                record_path = Path(source['source_record'])
                assert sha(record_path) == source['record_sha256']
                record = json.loads(record_path.read_text())
                assert record['status'] == 'converged' and record['connectivity_match']
                assert record['cosmo_solute_energy_hartree'] == energies[i]
                surface = Path(source['source_surface'])
                assert sha(surface) == source['surface_sha256']
                parsed = SigmaProfileParser(str(surface))
                volume_a3 = float(parsed['volume'])
                raw = surface.read_text()
                match = re.search(r'^\s*([0-9.eE+-]+)\s+#\s*Volume', raw, re.M)
                assert match and math.isfinite(volume_a3) and volume_a3 > 0
                assert abs(volume_a3 - float(match.group(1)) * BOHR_A**3) < 1e-8
                xyz = Path(record['archive_path']) / 'optimized.xyz'
                xyz_hash = sha(xyz)
                volume = volume_a3 * AVOGADRO_VOLUME
                volumes.append(volume)
                row = dict(source, species=ensemble_species, ensemble_key=ensemble_key,
                           optimized_xyz=str(xyz), optimized_xyz_sha256=xyz_hash,
                           opt_electronic_energy_hartree=float(record['stages']['opt']['final_energies_hartree'][-1]),
                           relative_cosmo_energy_kJ_mol=(energies[i] - min(energies)) * EH_KJMOL,
                           normalized_weight_298K=wn[i],
                           existing_exponent_298K=exponent_l[i],
                           existing_unnormalized_weight_298K=math.exp(exponent_l[i]),
                           existing_normalized_volume_weight_298K=wl[i],
                           cavity_volume_A3=volume_a3, cavity_volume_cm3_mol=volume)
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                handle.flush()
                pins[str(record_path)] = source['record_sha256']
                pins[str(surface)] = source['surface_sha256']
                pins[str(xyz)] = xyz_hash
                surface_groups[source['surface_sha256']].append(source['entry_id'])
                geometry_groups[xyz_hash].append(source['entry_id'])
                del parsed, raw
                gc.collect()
            os.fsync(handle.fileno())
        cumulative = 0
        for n90, weight in enumerate(sorted(wn, reverse=True), 1):
            cumulative += weight
            if cumulative >= .9:
                break
        summaries[ensemble_key] = dict(
            campaign_polymer=polymer,
            formula=next(iter(compositions)), connectivity=next(iter(connectivities)),
            species=next(iter(species)),
            conformers=len(members), normalized_weight_sum=math.fsum(wn),
            normalized_log_partition_sum=logzn, existing_log_partition_sum=logzl,
            normalized_weighted_cavity_cm3_mol=math.fsum(w * v for w, v in zip(wn, volumes)),
            existing_weighted_cavity_cm3_mol=math.fsum(w * v for w, v in zip(wl, volumes)),
            max_weight=max(wn), conformers_for_90_percent_weight=n90,
            effective_conformer_count=1 / math.fsum(w*w for w in wn),
            exact_duplicate_surfaces=[v for v in surface_groups.values() if len(v) > 1],
            exact_duplicate_xyz_files=[v for v in geometry_groups.values() if len(v) > 1],
            constant_activity_controls=dict(input_ln_gamma=probe, normalized=gn,
                                            existing=gl, expected_existing=probe-logzl))
        print(json.dumps(dict(ensemble_key=ensemble_key, **summaries[ensemble_key])), flush=True)
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   status='B_route_ensemble_inputs_prepared_no_activity_solve',
                   temperature_K=T, energy_basis='COSMO solute electronic energy; no vibrational free energy',
                   conventions='Normalized activities use normalized Boltzmann weights; existing activities retain the unnormalized sum. Both volumes use normalized weights.',
                   species_ensembles=summaries, total_conformers=len(rows),
                   campaign_polymer_species={p: sorted(s) for p, s in parent_species.items()},
                   aggregate_decision_pending={p: sorted(s) for p, s in parent_species.items() if len(s) > 1},
                   merge_policy='All source rows retained. Exact file duplicates only are enumerated; no symmetry/RMSD basin-merge claim and no deduplication.',
                   remaining='PETG aggregate representation is undecided; these are separate-species inputs only. Route-A preparation/comparison and thermodynamic calibration/production remain outstanding.',
                   source_sha256=pins, script_sha256=sha(Path(__file__)),
                   peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    files = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps(dict(status=summary['status'], files=files), indent=2) + '\n')
    assert all(sha(out / name) == digest for name, digest in files.items())
    print(json.dumps(dict(output=str(out), manifest_sha256=sha(out / 'manifest.json'),
                          peak_rss_kib=summary['peak_rss_kib'])))


if __name__ == '__main__':
    main()
