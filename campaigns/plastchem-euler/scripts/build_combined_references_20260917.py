"""Carry forward the 64 selected references; add newly qualified point values by source priority."""
import csv,json,hashlib,datetime
from pathlib import Path
R=Path('/mnt/r/plastchem-euler');D=R/'combined-validation-references-2026-09-17';D.mkdir(exist_ok=True)
oldpath=R/'post1160-octanol-2026-09-15/opera-extension/cumulative-parity.csv'
paths=[R/'measured-expansion-2026-09-17/experimental-reference-candidates.csv',R/'comptox-catchup-2026-09-17/comptox-experimental-reference-candidates.csv',R/'pubchem-catchup-2026-09-17/pubchem-measured-validation/experimental-reference-candidates.csv']
summary=json.loads((paths[-1].parent/'summary.json').read_text());assert summary['retrieval_complete']
fields=next(csv.reader(paths[0].open()));old=list(csv.DictReader(oldpath.open()));assert len(old)==64
candidates=[{f:r.get(f,'') for f in fields} for r in old]
for p in paths:candidates.extend(dict(r) for r in csv.DictReader(p.open()))
selected={r['input_inchikey']:{f:r.get(f,'') for f in fields} for r in old}
for r in sorted(candidates,key=lambda r:(int(r['selection_priority']),r['source_name'],r['raw_reference_string'])):
 if r['observed_operator']!='=':continue
 assert r['qualification'].startswith('qualified') and r['raw_reference_string'] and r['source_sha256']
 selected.setdefault(r['input_inchikey'],r)
for name,rows in [('experimental-reference-candidates.csv',list(selected.values())),('all-qualified-observations.csv',candidates)]:
 with (D/name).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'preserved_prior_choices':64,'selected_point_value_molecules':len(selected),'qualified_observation_rows_with_possible_source_overlap':len(candidates),'selection':'Previous n64 reference choices retained; new entries use lowest selection_priority, then source name and citation as deterministic tie breaks. No choice uses predicted values. Censored observations retained only in observation table. One selected point value per input key; connectivity aliases may share references.','inputs':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [oldpath,*paths]}}
(D/'summary.json').write_text(json.dumps(s,indent=2)+'\n');print(json.dumps(s),flush=True)
s['selected_connectivity_blocks']=len({k.split('-')[0] for k in selected})
(D/'summary.json').write_text(json.dumps(s,indent=2)+'\n')
(D/'REPORT.md').write_text(f"# Combined experimental reference selections\n\nSelected point values cover **{len(selected)} input entries / {s['selected_connectivity_blocks']} connectivity blocks**. The earlier 64 selections are retained. Additional PubChem, OPERA and CompTox observations are ranked using the documented source priority, independently of predictions.\n\nThe candidate file contains one selected point value per input key. all-qualified-observations.csv retains {len(candidates)} observation rows, including overlapping sources; these are not independent experiments. Each row retains citation, retrieval time and source hash. Some sources are curated literature compilations; individual primary papers and measurement conditions have not all been independently inspected. No predicted database field is used as an experimental value.\n\nThis is reference coverage, not an accuracy-validation n. Only available, audited octanol predictions may enter the comparison. Original released and fixed batch-2 packages remain unchanged. No recalibration.\n")
(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
