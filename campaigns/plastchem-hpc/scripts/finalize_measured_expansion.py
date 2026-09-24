"""Refresh source aggregation using the completed, read-only 960-record numerical audit."""
from pathlib import Path
import json,hashlib,csv
D=Path('/mnt/r/plastchem-euler/measured-expansion-2026-09-15');script=Path(__file__).with_name('build_measured_expansion.py');s=script.read_text();ns={}
a=json.loads((D/'numerical-audit.json').read_text());assert a['passed']==960 and a['failed']==0
exec(compile(s[:s.index('cohort={')],str(script),'exec'),ns)
cohort={r['inchikey']:r for r in ns['read'](D/'cohort.json')['records']};preds={r['input_inchikey']:{**r,'predicted_logKow':float(r['predicted_logKow'])} for r in ns['rows'](D/'all-predictions.csv')};assert set(cohort)==set(preds)=={r['input_inchikey'] for r in a['rows']}
assert all(preds[r['input_inchikey']]['result_sha256']==r['result_sha256'] for r in a['rows'])
ns.update(cohort=cohort,preds=preds,best={r['input_inchikey']:r for p in [ns['F'],ns['B'],D] for r in ns['rows'](p/'pubchem-measured-validation/best-measured-logKow.csv')})
exec(compile(s[s.index('# Public export'):],str(script),'exec'),ns)
