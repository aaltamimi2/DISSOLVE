"""Primary key export, secondary CAS reconciliation, explicit experimental qualification."""
from pathlib import Path
import subprocess,json,collections,hashlib,datetime
import openpyxl
ROOT=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/comptox-catchup-2026-09-17');S=D/'sources'
def run(name):subprocess.run(['python',str(ROOT/'scripts'/name)],check=True)
run('fetch_comptox_catchup_20260917.py')
cohort={r['inchikey']:r for r in json.loads((D/'cohort.json').read_text())['records']}
w=openpyxl.load_workbook(S/'comptox-properties.xlsx',read_only=True,data_only=True);matched=set()
for row in list(w['Main Data'].values)[1:]:
 inp,found,dtx,name,cas,key=row
 if inp in cohort and dtx and key and key.split('-')[0]==inp.split('-')[0]:matched.add(inp)
w.close();casmap=collections.defaultdict(list)
for key,r in cohort.items():
 cas=r['input'].get('cas')
 if key not in matched and cas:casmap[cas].append(key)
(S/'comptox-secondary-CAS-map.json').write_text(json.dumps(casmap,indent=2)+'\n')
if casmap:
 req=json.loads((S/'comptox-export-request.json').read_text());req.update(identifierTypes=['CASRN'],searchItems='\n'.join(sorted(casmap)))
 (S/'comptox-CAS-export-request.json').write_text(json.dumps(req,indent=2)+'\n');run('fetch_comptox_catchup_cas_20260917.py')
run('qualify_comptox_catchup_20260917.py')
s=json.loads((D/'comptox-match-summary.json').read_text());(D/'REPORT.md').write_text(f"# CompTox additional experimental references\n\nCohort: {len(cohort)} newly converged molecules beyond the previously queried 1,157. Primary InChIKey mapping followed by secondary CAS search, always requiring connectivity agreement. {s['qualified_observations']} explicitly experimental LogKow observations matched {s['matched_molecules']} molecules. Predicted property rows are excluded and counts retained in comptox-match-summary.json.\n\nEach candidate retains its export hash, retrieval time, Dashboard URL, experimental type and citation. Sources may overlap PubChem and OPERA/PHYSPROP; alternative observations must not be counted as independent experiments. No accuracy statistics are inferred before joining audited predictions. Frozen packages are unchanged.\n")
for name in ['run_comptox_catchup_20260917.py','fetch_comptox_catchup_20260917.py','fetch_comptox_catchup_cas_20260917.py','qualify_comptox_catchup_20260917.py']:(D/name).write_bytes((ROOT/'scripts'/name).read_bytes())
files=[p for p in D.rglob('*') if p.is_file() and p.name not in ['artifacts.sha256','pipeline.log']]
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(D))+'\n' for p in sorted(files)))
print(json.dumps({'completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cohort':len(cohort),'matched_molecules':s['matched_molecules'],'qualified_observations':s['qualified_observations']}),flush=True)
