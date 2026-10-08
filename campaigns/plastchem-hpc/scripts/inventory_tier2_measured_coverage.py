"""Inventory existing measured-reference coverage; no activity calculations.

Reuse the OPERA qualification rules from match_post1111_opera.py. Dataset
membership supplies observed-field provenance, not a per-row experimental flag.
This does not search PubChem/CompTox anew or establish absence of measurements.
"""
import argparse
import collections
import csv
import datetime
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BULK = Path('/mnt/r/plastchem-euler')
SOURCE = BULK / 'measured-expansion-2026-09-15/sources/LogP_QR.sdf'
PIN = 'de50a1eb020ae42f7f89f8cb7dd73987048e80f30ef32eeaf376451cb832e489'
REFS = BULK / 'combined-validation-references-2026-09-17-pubchem-final571/experimental-reference-candidates.csv'
REF_PIN = '504e8135aa7da149dbbe2192d41a9c636c7868732dc7083219243738fceb7d84'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    out = ap.parse_args().output.resolve()
    assert out.is_relative_to(BULK / 'tier2-v1') and not out.exists()
    pins = {}

    def read(path):
        raw = path.read_bytes()
        pins[str(path)] = hashlib.sha256(raw).hexdigest()
        return raw

    raw = read(SOURCE)
    assert pins[str(SOURCE)] == PIN
    source_receipt = json.loads(read(SOURCE.parent / 'OPERA_Data.retrieval.json'))
    byblock = collections.defaultdict(list)
    bycas = collections.defaultdict(list)
    rejected = collections.Counter()
    source_count = 0
    for i, block in enumerate(raw.decode().replace('\r\n', '\n').split('$$$$')):
        if not block.strip():
            continue
        source_count += 1
        r = dict(re.findall(r'>\s*<([^>]+)>[^\n]*\n(.*?)(?=\n\s*\n)', block, re.S))
        reason = None
        if r.get('Tr_1_Tst_0') not in ['0', '1']:
            reason = 'not_flagged_training_or_test'
        elif not r.get('Kow Reference') or r['Kow Reference'].strip() == '?':
            reason = 'no_identifiable_observation_reference'
        elif re.search(r'estimat|comput|predict|calculat|XLogP|KOWWIN|OPERA', r['Kow Reference'], re.I):
            reason = 'model_or_estimate_reference'
        try:
            value = float(r.get('LogP', ''))
            assert math.isfinite(value)
        except (ValueError, AssertionError):
            reason = 'non_numeric_observed_LogP'
        if reason:
            rejected[reason] += 1
            continue
        item = dict(source_record=i + 1, source_key=r['InChI Key_QSARr'],
                    source_cas=r.get('CAS', ''), measured_logKow=value,
                    raw_reference_string=r['Kow Reference'], training_test_flag=r['Tr_1_Tst_0'])
        byblock[item['source_key'].split('-')[0]].append(item)
        if item['source_cas']:
            bycas[item['source_cas']].append(item)
    reference_raw = read(REFS)
    assert pins[str(REFS)] == REF_PIN
    refs = list(csv.DictReader(reference_raw.decode().splitlines()))
    assert len(refs) == 1179
    reference_blocks = {r['input_inchikey'].split('-')[0] for r in refs}
    cohort_path = BULK / 'promotion-v1/provenance/cohort.json'
    released = {r['inchikey'] for r in json.loads(read(cohort_path))['rows']}
    models = json.loads(read(ROOT / 'state/tier2-v1/tier2/manifest.json'))['molecules']
    assert len(models) == len({m['inchikey'] for m in models}) == 270
    rows, observations, conflicts = [], [], []
    for m in models:
        key = m['inchikey']
        path = ROOT / 'state/tier2-v1/records' / (key + '.json')
        record = json.loads(read(path)) if path.exists() else {}
        hits = byblock.get(key.split('-')[0], [])
        cas = m.get('cas', '')
        for hit in bycas.get(cas, []) if cas else []:
            if hit['source_key'].split('-')[0] != key.split('-')[0]:
                conflicts.append(dict(input_inchikey=key, input_cas=cas, **hit))
        for hit in hits:
            observations.append(dict(input_inchikey=key, name=m['name'], **hit))
        rows.append(dict(input_inchikey=key, name=m['name'], input_cas=cas,
                         status=record.get('status', 'not_collected'),
                         in_frozen_release=key in released,
                         qualified_OPERA_observations=len(hits),
                         in_pinned_selected_reference_compilation=key.split('-')[0] in reference_blocks))
    accepted = [r for r in rows if r['status'] == 'converged']
    post = [r for r in accepted if not r['in_frozen_release']]
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        eligible=270, accepted=len(accepted), post_release_accepted=len(post),
        source_records=source_count, qualified_source_records=sum(map(len, byblock.values())),
        rejected_source_records=dict(rejected),
        OPERA_matched_eligible=sum(r['qualified_OPERA_observations'] > 0 for r in rows),
        OPERA_matched_accepted=sum(r['qualified_OPERA_observations'] > 0 for r in accepted),
        OPERA_matched_post_release=sum(r['qualified_OPERA_observations'] > 0 for r in post),
        selected_compilation_matched_eligible=sum(r['in_pinned_selected_reference_compilation'] for r in rows),
        CAS_only_accepted=0, CAS_connectivity_conflicts=len(conflicts),
        source_receipt=source_receipt, source_sha256=pins,
        limitations='Observed LogP and identifiable citations with the prior qualification rules; no per-row experimental boolean is supplied. No fresh PubChem/CompTox search. Zero matches would not establish absence of measurements elsewhere. No accuracy metrics without paired predictions and references. No release or reference-selection mutation.')
    out.mkdir(parents=True)
    for name, data in [('coverage.csv', rows)]:
        with (out / name).open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    for name, data in [('summary.json', summary), ('qualified-observations.json', observations), ('CAS-conflicts.json', conflicts)]:
        (out / name).write_text(json.dumps(data, indent=2) + '\n')
    (out / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps(dict(files=hashes), indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k not in ['source_sha256', 'source_receipt']}))


if __name__ == '__main__':
    main()
