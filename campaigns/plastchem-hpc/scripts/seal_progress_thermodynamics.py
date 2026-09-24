"""Pin numeric results for the already frozen ORCA cohort before final plotting."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BULK = Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14'))
REFERENCES = Path('/mnt/r/plastchem-euler/progress-2026-09-14')

def main():
    freeze = BULK / 'freeze'
    manifest_bytes = (freeze / 'manifest.json').read_bytes()
    cohort = json.loads(manifest_bytes)['cohort']
    destination = BULK / 'sealed-thermodynamics'
    assert not destination.exists(), 'Existing numeric snapshot must not be overwritten'
    ledger = json.loads((ROOT / 'state/thermodynamics-v1/processing-ledger.json').read_text())
    destination.mkdir()
    pinned, dispositions, files = {}, [], []

    def save(relative, content):
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        files.append({'path': relative, 'sha256': digest, 'bytes': len(content)})
        return path

    for member in cohort:
        key = member['inchikey']
        entry = ledger.get(key)
        if not entry:
            dispositions.append({'inchikey': key, 'status': 'not_processed_at_numeric_seal'})
            continue
        content = Path(entry['result_path']).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry['result_sha256']:
            dispositions.append({'inchikey': key, 'status': 'changed_during_capture'})
            continue
        result = json.loads(content)
        assert result['solute_surface_sha256'] == member['surface_sha256']
        path = save('results/' + key + '.json', content)
        pinned[key] = {**entry, 'live_result_path': entry['result_path'], 'result_path': str(path)}
        dispositions.append({'inchikey': key, 'status': 'captured',
                             'partition_count': entry['partition_count'],
                             'result_sha256': entry['result_sha256']})
    save('processing-ledger.json', (json.dumps(pinned, indent=2) + '\n').encode())
    save('library-registry.json', (ROOT / 'state/thermodynamics-v1/library-registry.json').read_bytes())
    save('solvent-phase-review.json', (ROOT / 'state/thermodynamics-v1/solvent-phase-review.json').read_bytes())
    save('experimental-reference-candidates.csv', (REFERENCES / 'experimental-reference-candidates.csv').read_bytes())
    save('exclusions.json', (ROOT / 'state/campaign-v1/exclusions.json').read_bytes())
    save('reference-math-and-water-audit.json', (ROOT / 'state/progress-2026-09-14/reference-math-and-water-audit.json').read_bytes())
    reference=(ROOT/'state/opencosmo-verification-v1/reference_cosmo_logp.py').read_bytes()
    math_audit=json.loads((ROOT/'state/progress-2026-09-14/reference-math-and-water-audit.json').read_text())
    assert hashlib.sha256(reference).hexdigest()==math_audit['reference_file_sha256']
    save('reference_cosmo_logp.py',reference)
    software_bytes=(ROOT/'state/progress-2026-09-14/software-provenance.json').read_bytes()
    software=json.loads(software_bytes)
    for item in software['files']:
        assert hashlib.sha256(Path(item['installed_path']).read_bytes()).hexdigest()==item['sha256'], 'Scientific source changed since provenance capture'
        archived=Path(item['archived_path']);content=archived.read_bytes()
        assert hashlib.sha256(content).hexdigest()==item['sha256']
        save('software-sources/'+str(archived.relative_to(REFERENCES/'software-sources')),content)
    save('software-provenance.json',software_bytes)
    for name in ['local-reference-search.json','web-reference-search-review.json',
                 'targeted-pilot-reference-review.json','experimental-reference-coverage.json',
                 'reference-expansion-check.json']:
        save('reference-reviews/'+name,(ROOT/'state/progress-2026-09-14'/name).read_bytes())
    save('water-only-baseline.json', (ROOT / 'state/progress-2026-09-14/water-only-baseline.json').read_bytes())
    save('workstation-anchor-agreement.csv', (REFERENCES / 'workstation-anchor-agreement.csv').read_bytes())
    anchor_keys=['FLKPEMZONWLCSK-UHFFFAOYSA-N','DOIRQSBPFJWKBE-UHFFFAOYSA-N',
                 'IRIAEXORFWYRCZ-UHFFFAOYSA-N','BJQHLKABXJIVAM-UHFFFAOYSA-N']
    for stem in ['DEP-workstation-reference','DBP-workstation-reference',
                 'BBP-workstation-reference','DEHP-workstation-reference',*anchor_keys]:
        name=stem+'.json'
        save('workstation-anchor-sources/'+name,(ROOT/'state/opencosmo-verification-v1/records'/name).read_bytes())
    external_pubchem = []
    for source in sorted((REFERENCES/'reference-sources').iterdir()):
        if source.is_file():
            if source.name.startswith('pubchem-expansion-'):
                external_pubchem.append({'archived_path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'bytes':source.stat().st_size})
                continue
            save('reference-sources/' + source.name, source.read_bytes())
    save('external-pubchem-source-manifest.json',(json.dumps(external_pubchem,indent=2)+'\n').encode())
    octanol = REFERENCES/'octanol-validation'
    if (octanol/'manifest.json').exists():
        for relative in ['provenance.json','manifest.json','reference-summary.json',
                         'experimental-reference-candidates.csv','best-measured-logKow.csv',
                         'per-molecule-best-measured.csv','excluded-reference-observations.csv',
                         'validation/statistics.json','validation/all-predicted-logKow.csv',
                         'validation/best-measured-parity.csv','validation/parity-caption.txt',
                         'validation/predicted-vs-experimental.png']:
            save('octanol-validation/'+relative,(octanol/relative).read_bytes())
        save('octanol-validation/original-archive-root.txt',(str(octanol)+'\n').encode())
    for review_name in ['octanol-sign-review','octanol-hydrophobicity-review']:
        review = REFERENCES/review_name
        if (review/'manifest.json').exists():
            for source in sorted(review.iterdir()):
                if source.is_file():
                    save(review_name+'/'+source.name,source.read_bytes())
    for name in ['plot_progress_20260914.py', 'compare_progress_experiments.py',
                 'extract_progress_references.py', 'seal_progress_thermodynamics.py',
                 'audit_completed_surfaces.py', 'audit_thermodynamic_records.py',
                 'build_frozen_progress_report.py','compare_production_anchors.py',
                 'export_thermodynamic_table.py','audit_progress_table.py',
                 'seal_progress_release.py','octanol_report_section.py',
                 'process_octanol_validation.py','compare_octanol_validation.py',
                 'qualify_pubchem_measured_logkow.py','fetch_pubchem_logkow_candidates.py',
                 'report_octanol_validation.py','check_octanol_sign_20260914.py',
                 'review_octanol_hydrophobicity.py']:
        save('scripts/' + name, (ROOT / 'scripts' / name).read_bytes())
    manifest = {'sealed_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'cohort_snapshot_id': hashlib.sha256(manifest_bytes).hexdigest(),
                'cohort_denominator': len(cohort), 'captured_results': len(pinned),
                'dispositions': dispositions, 'files': files,
                'interpretation': 'Fixed numeric records for the frozen ORCA cohort; later numerical updates are excluded. Missing values remain missing.'}
    payload = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    (destination / 'manifest.json').write_bytes(payload)
    receipt = {'snapshot_id': hashlib.sha256(payload).hexdigest(), 'path': str(destination),
               'cohort_denominator': len(cohort), 'captured_results': len(pinned)}
    receipt_name = 'rehearsal-numeric-seal-receipt.json' if os.environ.get('PLASTCHEM_PROGRESS_ROOT') else 'numeric-seal-receipt.json'
    (ROOT / 'state/progress-2026-09-14' / receipt_name).write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))

if __name__ == '__main__':
    main()
