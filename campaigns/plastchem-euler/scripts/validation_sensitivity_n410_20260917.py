"""Descriptive validation sensitivity; no prediction or reference changes."""
from pathlib import Path
import csv,json,hashlib,datetime,collections,math
SOURCE=Path('/mnt/r/plastchem-euler/octanol-post3883-2026-09-17/cumulative-validation')
R=Path('/mnt/r/plastchem-euler/validation-sensitivity-20260917-n410');R.mkdir(exist_ok=False)
raw=(SOURCE/'parity.csv').read_bytes();rows=list(csv.DictReader(raw.decode().splitlines()))
blocks=collections.Counter(r['input_inchikey'].split('-')[0] for r in rows)
def stats(rs,weighted=False):
 if not rs:return {'n':0}
 w=[1/blocks[r['input_inchikey'].split('-')[0]] if weighted else 1 for r in rs];sw=sum(w)
 x=[float(r['measured_logKow']) for r in rs];y=[float(r['predicted_logKow']) for r in rs];e=[b-a for a,b in zip(x,y)]
 mx=sum(a*b for a,b in zip(w,x))/sw;my=sum(a*b for a,b in zip(w,y))/sw
 var=sum(a*(b-mx)**2 for a,b in zip(w,x));slope=sum(a*(b-mx)*(c-my) for a,b,c in zip(w,x,y))/var if var else None
 return {'n':len(rs),'connectivity_blocks':len({r['input_inchikey'].split('-')[0] for r in rs}),'MAE':sum(a*abs(b) for a,b in zip(w,e))/sw,'RMSE':math.sqrt(sum(a*b*b for a,b in zip(w,e))/sw),'bias':sum(a*b for a,b in zip(w,e))/sw,'slope':slope,'intercept':my-slope*mx if slope is not None else None}
o={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'parity_sha256':hashlib.sha256(raw).hexdigest(),'entry_weighted':stats(rows),'equal_connectivity_weight':stats(rows,True),'measured_ranges':{label:stats([r for r in rows if lo<=float(r['measured_logKow'])<hi]) for label,lo,hi in [('below_4',-math.inf,4),('4_to_below_7',4,7),('7_and_above',7,math.inf)]},'interpretation':'Descriptive sensitivity only. Connectivity aliases are correlated observations; equal-connectivity weighting gives each first block total weight one without selecting or changing an entry. Range summaries do not establish causes or experimental speciation equivalence. No recalibration.'}
(R/'sensitivity.json').write_text(json.dumps(o,indent=2)+'\n')
(R/'SENSITIVITY.md').write_text('# Validation sensitivity\n\n'+json.dumps(o,indent=2)+'\n\nThese comparisons do not establish accuracy for all campaign molecules or every solvent. References may differ in measurement conditions and species; the calculations represent the specified neutral structure in dry octanol. The source-specific limitations and unresolved didecyl reference are retained in the primary report.\n')
(R/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
(R/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(R.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
print(json.dumps(o,indent=2))
