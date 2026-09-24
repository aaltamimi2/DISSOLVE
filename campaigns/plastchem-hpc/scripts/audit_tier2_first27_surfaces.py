"""Freeze the accepted tier-2 cohort and apply the existing surface auditor unchanged."""
from pathlib import Path
import datetime, hashlib, json, os, shutil, subprocess, sys
ROOT = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/audits/tier2-first27-surfaces-20260921')
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
records = []
for p in sorted((ROOT/'state/tier2-v1/records').glob('*.json')):
    r = json.loads(p.read_text())
    if r.get('status') == 'converged':
        records.append((p, r))
assert len(records) == 27
D.mkdir(parents=True, exist_ok=False)
# The existing auditor's frozen-path adapter expects this folder name.
# These are exclusively tier-2 records, never original-campaign additions.
folder = D/'freeze/state/campaign-v1/records'
folder.mkdir(parents=True)
cohort = []
for p, r in records:
    shutil.copyfile(p, folder/p.name)
    cohort.append({'inchikey': p.stem, 'source_record': str(p),
                   'source_record_sha256': sha(p), 'surface_sha256': r['surface_sha256']})
(D/'snapshot.json').write_text(json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'tier': 'CHNO_500_700', 'tier_denominator': 270, 'accepted_subset': 27,
    'cohort': cohort, 'scope': 'Surface integrity, geometry and provenance; not experimental accuracy'}, indent=2)+'\n')
auditor = ROOT/'scripts/audit_completed_surfaces.py'
subprocess.run([sys.executable, str(auditor), '--frozen'],
               env=dict(os.environ, PLASTCHEM_PROGRESS_ROOT=str(D), OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1'), check=True)
a = json.loads((D/'completed-surface-audit.json').read_text())
assert a['completed_snapshot'] == a['passed'] == 27 and a['failed'] == 0
for p in [Path(__file__), auditor]:
    shutil.copyfile(p, D/p.name)
files = sorted(p for p in D.rglob('*') if p.is_file())
manifest = ''.join(f'{sha(p)}  {p.relative_to(D)}\n' for p in files)
(D/'SHA256SUMS').write_text(manifest)
for line in manifest.splitlines():
    digest, name = line.split('  ', 1)
    assert sha(D/name) == digest
receipt = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'root': str(D),
           'passed': 27, 'failed': 0, 'tier_denominator': 270, 'files_verified': len(files),
           'manifest_sha256': sha(D/'SHA256SUMS')}
(ROOT/'state/tier2-first27-surface-audit.json').write_text(json.dumps(receipt, indent=2)+'\n')
print(json.dumps(receipt), flush=True)
