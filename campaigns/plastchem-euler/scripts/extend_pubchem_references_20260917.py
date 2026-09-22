from pathlib import Path
import csv,json,hashlib,datetime
R=Path('/mnt/r/plastchem-euler');D=R/'combined-validation-references-2026-09-17-pubchem';D.mkdir(exist_ok=False)
p=R/'combined-validation-references-2026-09-17-later/experimental-reference-candidates.csv';q=R/'pubchem-later-2026-09-17/pubchem-measured-validation/best-measured-logKow.csv'
old=list(csv.DictReader(p.open()));keys={r['input_inchikey'] for r in old};new=[r for r in csv.DictReader(q.open()) if r['input_inchikey'] not in keys];rows=old+new;assert len(rows)==len({r['input_inchikey'] for r in rows})
with (D/'experimental-reference-candidates.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(old[0]));w.writeheader();w.writerows(rows)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'retained':len(old),'added':len(new),'selected_entries':len(rows),'connectivity_blocks':len({r['input_inchikey'].split('-')[0] for r in rows}),'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest(),str(q):hashlib.sha256(q.read_bytes()).hexdigest()},'selection':'Keep all prior choices; append only newly covered, qualified PubChem point observations. No prediction-dependent selection.'}
(D/'summary.json').write_text(json.dumps(s,indent=2)+'\n');(D/'REPORT.md').write_text('# Extended measured-reference coverage\n\n'+json.dumps(s,indent=2)+'\n\nReference coverage is not paired accuracy n. Each row retains its source, retrieval time and citation. All observations and exclusions remain in the source packages.\n');(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes());(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'));print(json.dumps(s))
