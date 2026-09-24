"""Append newly covered experimental point references; preserve prior selections."""
from pathlib import Path
import csv,json,hashlib,datetime,math
R=Path('/mnt/r/plastchem-euler');D=R/'combined-validation-references-2026-09-17-opera-followup'
p=R/'combined-validation-references-2026-09-17-post4579/experimental-reference-candidates.csv'
q=R/'measured-expansion-2026-09-17-opera-followup/opera-experimental-reference-candidates.csv'
old=list(csv.DictReader(p.open()));observations=list(csv.DictReader(q.open()));selected={r['input_inchikey']:r for r in old}
assert len(selected)==len(old)==832
for r in sorted(observations,key=lambda r:(int(r['selection_priority']),r['source_name'],r['raw_reference_string'])):
 assert r['source_class']=='OPERA_curated_observed_LogP_PHYSPROP'
 if r['observed_operator']=='=' and math.isfinite(float(r['measured_logKow'])):selected.setdefault(r['input_inchikey'],r)
rows=list(selected.values());assert rows[:len(old)]==old
D.mkdir(exist_ok=False)
for filename,data in [('experimental-reference-candidates.csv',rows),('new-source-observations.csv',observations)]:
 with (D/filename).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(old[0]));w.writeheader();w.writerows(data)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'retained':len(old),'added':len(rows)-len(old),'selected_entries':len(rows),'connectivity_blocks':len({r['input_inchikey'].split('-')[0] for r in rows}),'source_sha256':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [p,q]},'selection':'Preserve all 832 prior selections. For newly covered keys only, rank qualified experimental point observations by selection_priority, source name, citation. No predicted value is consulted.'}
(D/'summary.json').write_text(json.dumps(s,indent=2)+'\n')
(D/'REPORT.md').write_text('# Extended measured-reference coverage\n\n'+json.dumps(s,indent=2)+'\n\nCoverage is not paired accuracy n. All observations retain source, retrieval time and citation. OPERA observed-field provenance and source citations are preserved; primary papers and measurement conditions have not all been independently inspected. Compilation sources can overlap and are not independent replicates. No recalibration. Xylene identity and didecyl-phthalate reference discrepancy remain owner questions.\n')
(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name+'\n' for f in sorted(D.iterdir()) if f.is_file() and f.name!='artifacts.sha256'))
print(json.dumps(s))
