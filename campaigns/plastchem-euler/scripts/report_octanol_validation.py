"""Publish the completed octanol supplement while retaining the sealed panel rehearsal."""
import datetime as dt,hashlib,json,shutil
from pathlib import Path
from octanol_report_section import section
ROOT=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler/progress-2026-09-14');D=B/'octanol-validation';R=B/'pubchem-measured-validation'
s=json.loads((D/'validation/statistics.json').read_text());refs=json.loads((R/'summary.json').read_text())
assert s['predicted_molecules']+s['failed_molecules']==590 and s['unprocessed_molecules']==0
assert refs['retrieval_complete'] and refs['retrieval_molecules_completed']==590
for name in ['experimental-reference-candidates.csv','best-measured-logKow.csv','per-molecule-best-measured.csv','excluded-reference-observations.csv']:shutil.copyfile(R/name,D/name)
shutil.copyfile(R/'summary.json',D/'reference-summary.json')
base=(B/'rehearsal/REPORT.md').read_text();intro=section(D)
report=base.replace('## Partitioning and validation',intro+'\n## Original product-panel partitioning and validation',1)
report+='\n## Reproduce the octanol supplement\n\n```bash\npython3 scripts/fetch_pubchem_logkow_candidates.py --all-cohort\npython3 scripts/qualify_pubchem_measured_logkow.py\n# Run the following only with exclusive ownership of the numerical-worker lock:\n/home/aaltamimi2/.venvs/cosmo-logp/bin/python scripts/process_octanol_validation.py\npython3 scripts/compare_octanol_validation.py\npython3 scripts/report_octanol_validation.py\n```\n'
(D/'REPORT.md').write_text(report);(ROOT/'reports/progress-2026-09-14/REPORT.md').write_text(report)
files=[]
for p in sorted(D.rglob('*')):
 if p.is_file() and p.name!='manifest.json':files.append({'path':str(p.relative_to(D)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
manifest={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'scope':'590 initial cohort, validation-only octanol supplement','files':files,'predicted':s['predicted_molecules'],'matched_n':s['matched_molecules']}
(D/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps({'report':str(D/'REPORT.md'),'matched_n':s['matched_molecules'],'predicted':s['predicted_molecules']}))
