"""Seal the final disjoint octanol cohort only after its full audit passes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,hashlib,datetime,shutil
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/octanol-final1171-2026-09-17')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
a=json.loads((D/'numerical-audit.json').read_text());s=json.loads((D/'summary.json').read_text());m=json.loads((D/'manifest.json').read_text());prov=json.loads((D/'provenance.json').read_text())
assert a['denominator']==a['passed']==s['processed']==s['denominator']==len(m['cohort'])==1171 and a['failed']==s['not_run']==0
assert s['predicted']+s['failed_or_unavailable']==1171 and s['reused_panel_water']+s['separate_validation_water']==1171
assert sha(R/'scripts/process_final1171_octanol_independent_water.py')==prov['script_sha256']
resume=json.loads((R/'state/octanol-final1171-independent-water-resume.json').read_text());assert resume['processed']==1171
(D/'REPORT.md').write_text(f"""# Final octanol cohort — 2026-09-17

The pinned disjoint cohort is 1,171 accepted contaminants. Predicted: **{s['predicted']}/1,171**; explicitly unavailable: **{s['failed_or_unavailable']}/1,171**; not run: **0/1,171**. Every outcome passed the full provenance, identity, input-surface hash, dilution and partition arithmetic audit (1,171/1,171; zero audit failures).

Previous audited octanol predictions: 4,632. Cumulative available predictions: **{4632+s['predicted']}/5,803 accepted ORCA structures**, with {s['failed_or_unavailable']} explicitly unavailable outcomes. These counts do not assert complete panel coverage.

Water references: {s['reused_panel_water']} reused hash-verified production records; {s['separate_validation_water']} calculated as separate validation-only water records using the same pinned surface, engine configuration and dilution checks. The latter are labelled record_scope and are not represented as completed panel predictions. No production records or ledger entries were changed. The panel process was paused for this serial run and resumed automatically; the handoff/resume receipts are in the lane state directory.

Temperature: 298.15 K; dry pure octanol; pure-component reference state; openCOSMORS24a. Concentration conversion uses the pinned documented octanol/water molar volumes with log10(Vwater/Voctanol). No recalibration, empirical correction, new ORCA calculation, new conformer or full 69-solvent expansion.

Elapsed serial-run wall time including I/O: {s['wall_seconds']:.1f} s. Peak RSS: {s['peak_rss_kib']/1024:.1f} MiB. Failures remain missing rather than interpolated. Numerical validation is not experimental accuracy; the independent measured comparison is a separate package at /mnt/r/plastchem-euler/validation-final5803-2026-09-17/.

Released 648 and batch-2 cumulative 944 packages remain untouched. Xylene identity and the didecyl-phthalate measured-source discrepancy remain owner questions. Full panel calculations and final consolidated reporting remain separate pending work.

Files: octanol.csv (all outcomes), octanol/*.json (raw results), panel/*.json (water-source records, including explicitly scoped validation-only records), numerical-audit.json (all outcome audits), provenance.json, manifest.json, artifacts.sha256, and code/.
""")
code=D/'code';code.mkdir(exist_ok=True)
for name in ['process_final1171_octanol_independent_water.py','audit_octanol_final1171_20260917.py','seal_final1171_octanol_20260917.py','compare_final5803_20260917.py']:shutil.copyfile(R/'scripts'/name,code/name)
files=sorted(p for p in D.rglob('*') if p.is_file() and p.name!='artifacts.sha256' and not p.name.endswith('.log'))
with ThreadPoolExecutor(max_workers=4) as pool:hashes=list(pool.map(sha,files))
(D/'artifacts.sha256').write_text(''.join(h+'  '+str(p.relative_to(D))+'\n' for p,h in zip(files,hashes)))
with ThreadPoolExecutor(max_workers=4) as pool:assert hashes==list(pool.map(sha,files))
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files_verified':len(files),'manifest_sha256':sha(D/'artifacts.sha256'),'audit_passed':1171,'audit_failed':0,'predicted':s['predicted'],'unavailable':s['failed_or_unavailable'],'cumulative_audited_predictions':4632+s['predicted']}
(R/'state/octanol-final1171-sealed-20260917.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
