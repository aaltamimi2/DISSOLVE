import json,re,time
from pathlib import Path
root=Path.home()/'plastchem-euler/pilot-v1/runs';rows=[]
for p in root.glob('*/result.json'):
 r=json.loads(p.read_text());f=p.parent/'opt.out';text=f.read_text(errors='replace') if f.exists() else ''
 rows.append({'inchikey':r['inchikey'],'name':r['input']['name'],'role':r['input']['selection_role'],'cpu_model':r['cpu_model'],'status':r['status'],'geometry_cycles':len(re.findall('GEOMETRY OPTIMIZATION CYCLE',text)),'scf_convergences':len(re.findall('SCF CONVERGED AFTER',text)),'last_energy':re.findall(r'FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)',text)[-1:],'last_lines':text.splitlines()[-4:]})
print(json.dumps({'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'rows':rows},indent=2))
