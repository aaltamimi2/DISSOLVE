"""Qualify the completed raw retrieval; no database predictions enter measured columns."""
from pathlib import Path
import json,time,subprocess,hashlib
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/pubchem-later-2026-09-17');pid=786798
while Path(f'/proc/{pid}').exists():
 assert 'fetch_pubchem_later_20260917.py' in Path(f'/proc/{pid}/cmdline').read_text();time.sleep(30)
r=json.loads((D/'retrieval.json').read_text());assert len(r['rows'])==r['cohort'],'Retrieval terminated before cohort disposition'
subprocess.run(['python',str(R/'scripts/qualify_pubchem_later_20260917.py')],check=True)
s=json.loads((D/'pubchem-measured-validation/summary.json').read_text())
(D/'REPORT.md').write_text(f"# Additional PubChem measured-reference retrieval\n\n{s['retrieval_molecules_completed']}/{s['cohort_denominator']} molecules have retrieval dispositions. {s['qualified_observations']} qualified observations across {s['qualified_molecules']} molecules; {s['best_point_value_molecules']} have a selected point value. Retrieval failures and absent sections remain distinguishable in retrieval.json.\n\nRaw responses, source receipts and citations are retained. Explicit computed, predicted or estimated entries are excluded. HSDB cited literature and Sangster curated values retain distinct provenance classes; qualification is based on database text and citations, not independent inspection of every primary paper. No measured-value imputation or empirical recalibration. These reference counts do not establish prediction accuracy; matching audited predictions is a separate step.\n")
for name in ['fetch_pubchem_later_20260917.py','qualify_pubchem_later_20260917.py','finalize_pubchem_later_20260917.py']:(D/name).write_bytes((R/'scripts'/name).read_bytes())
files=[p for p in D.rglob('*') if p.is_file() and p.name not in ['artifacts.sha256','finalization.log']]
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(D))+'\n' for p in sorted(files)))
print(json.dumps(s),flush=True)
