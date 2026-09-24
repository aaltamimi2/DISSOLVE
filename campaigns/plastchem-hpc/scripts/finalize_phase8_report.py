"""Seal completed phase reports and mirror small review artifacts into Git workspace."""
import json,csv,hashlib,shutil,datetime,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase8-v1');phase=sys.argv[1];assert phase in ['phase81','phase82'];O=D/'reports'/phase;summary=json.loads((O/'summary.json').read_text())
if phase=='phase81':assert summary['paired_per_convention']=={'normalized':1280,'existing':1280}
else:assert sum(summary['lle_status_counts'].values())==512 and not summary['lle_status_counts'].get('missing',0),'Still missing LLE outcomes; do not seal'
receipt=json.loads((R/'state/phase8-v1'/(phase+'-collection.json')).read_text());assert receipt['verified_members']==receipt['files']
account=json.loads((R/'state/phase8-v1'/(phase+'-accounting.json')).read_text());rows=[l.split('|') for l in account['sacct'].splitlines() if '_' in l.split('|')[0] and '.' not in l.split('|')[0]];cpu_hours=sum(int(r[2]) for r in rows)/3600
report=(O/'REPORT.md').read_text().split('<!-- EXECUTION SEAL -->')[0].rstrip();report=report.replace('total unit wall time','calculation-only wall time (excluding interpreter startup)').replace('CPU-h, peak RSS','CPU-h equivalent, peak RSS')
report+='\n\n<!-- EXECUTION SEAL -->\n## Execution seal\n\n'
report+=f"Slurm accounting covers {len(rows)} tasks; allocated single-CPU elapsed time **{cpu_hours:.4f} CPU-hours**, including interpreter startup. States: {sorted({r[1] for r in rows})}. Full accounting and environment versions are in execution-provenance.json. Peak process RSS is recorded per unit; the Euler allocation is 4 GB.\n\n"
if phase=='phase82':
 n=json.loads((O/'lle-numerical-verification.json').read_text());report+=f"Numerical verification: all {n['coverage']} LLE systems pass. Maximum chemical-potential residual {n['max_chemical_potential_residual']:.3g} RT; largest solubility grid change {max(n['max_grid_mol_percentage_point_change'],n['max_grid_wt_percentage_point_change']):.3g} percentage point. No indeterminate threshold verdicts. {n['total_activity_points']:,} finite-composition activity points, all on {', '.join(n['cpu_models'])}; maximum LLE process RSS {n['max_lle_rss_mib']:.1f} MiB. Details: lle-numerical-verification.json.\n\n"
report+=f"Raw return archive: `{receipt['raw_archive']}`; sha256 `{receipt['sha256']}`. All **{receipt['verified_members']}** archived members passed digest verification. Raw activities are retained inside this archive, avoiding thousands of loose files on the network drive. Surface/input pins are in ../phase8-execution/input-manifest.json in the workspace review copy and `{D}/manifest.json` in bulk storage.\n\n"
figures=['route-parity.png'] if phase=='phase81' else ['workbook-logP-parity.png','miscibility-confusion.png']
for name in figures:
 if (O/name).exists():report+=f'![{name.removesuffix(".png").replace("-"," ")}]({name})\n\n'
report+='Figures: PNG at 300 dpi, black text, same text size throughout, with their CSV source tables alongside. Parity dashed lines are 1:1; neither route comparison nor workbook comparison is experimental truth.\n\nPhase 8.3 remains held. Background collectors remain untouched.\n'
(O/'REPORT.md').write_text(report)
provenance={'sealed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'archive_receipt':receipt,'accounting':account,'staging':json.loads((R/'state/phase8-v1/staging.json').read_text()),'code_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (R/'scripts').glob('*phase8*.py')}}
for p in (R/'state/phase8-v1').glob('*.json'):
 if p.name.startswith(('submission','release','validation-code','time-limit')) and not p.name.endswith('transport.json'):
  val=json.loads(p.read_text());provenance[p.name]={k:v for k,v in val.items() if k not in ['queue_after','queue_before','squeue','sacct','stdout','stderr']}
(O/'execution-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
pins={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in O.iterdir() if p.is_file() and p.name!='report-manifest.json'};(O/'report-manifest.json').write_text(json.dumps({'utc':provenance['sealed_utc'],'files':pins},indent=2)+'\n')
local=R/'reports'/('phase8-1-route-comparison' if phase=='phase81' else 'phase8-2-workbook-validation');local.mkdir(exist_ok=True)
for p in O.iterdir():
 if p.is_file():assert p.stat().st_size<10_000_000;shutil.copyfile(p,local/p.name);assert hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256((local/p.name).read_bytes()).digest()
print(json.dumps({'phase':phase,'bulk_report':str(O/'REPORT.md'),'review_report':str(local/'REPORT.md'),'files':len(pins)+1,'allocated_cpu_hours':cpu_hours,'manifest_sha256':hashlib.sha256((O/'report-manifest.json').read_bytes()).hexdigest()},indent=2))
