"""A-7 serial expansion using unchanged A-6 numerical routines, isolated outputs."""
from pathlib import Path
import json,hashlib,subprocess,sys,re,shutil,datetime,os,signal
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7');D.mkdir(parents=True,exist_ok=True)
counts={'evoh':11,'nylon6':20,'nylon66':28,'pe':31,'pp':25,'pvc':27,'pvdf':24}
A6=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a6')
# Release the single calculation slot only from a verified idle completed worker.
pids=subprocess.run(['pgrep','-f','^/home/aaltamimi2/.venvs/cosmo-logp/bin/python -u scripts/watch_tier2_thermodynamics.py'],capture_output=True,text=True).stdout.split()
for pid in pids:
 assert Path('/proc/'+pid+'/wchan').read_text().strip()=='hrtimer_nanosleep'
 ledger=json.loads((R/'state/tier2-v1/thermodynamics/processing-ledger.json').read_text());assert len(ledger)==27 and all(v['activity_count']+v['failed_activity_count']==32 for v in ledger.values())
 os.kill(int(pid),signal.SIGTERM)
source=(R/'scripts/compare_pe_routes_a6.py').read_text();legacy=(R/'scripts/pe_a6_existing_convention.py').read_text();generated=R/'scripts/a7_generated';generated.mkdir(exist_ok=True)
manifest={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'counts':counts,'denominator_conformers':166,'denominator_predictions':896,'source_scripts':{name:hashlib.sha256((R/'scripts'/name).read_bytes()).hexdigest() for name in ['compare_pe_routes_a6.py','pe_a6_existing_convention.py']},'excluded':['PET','PS','polyethersulfone','nitrocellulose','polyurethane','PC','PETG'],'idle_worker_stopped':pids}
(D/'dispatch.json').write_text(json.dumps(manifest,indent=2)+'\n')
for polymer,n in counts.items():
 dest=D/polymer;dest.mkdir(exist_ok=True)
 acts=dest/'activities';acts.mkdir(exist_ok=True)
 # Solvent gamma does not depend on which polymer is compared.
 for p in (A6/'activities').glob('*.json'):
  if '-solvent-' in p.name or polymer=='pe':
   target=acts/p.name
   if not target.exists():shutil.copyfile(p,target)
 # Each adapted worker imports the original thermodynamic helper via PYTHONPATH.
 s=source.replace('/route-comparison-a6','/route-comparison-a7/'+polymer).replace("m['polymer']=='pe'",f"m['polymer']=='{polymer}'")
 s=re.sub(r'\b31\b',str(n),s)
 # Per-polymer provisional reports are replaced by A-7 analysis before delivery.
 f=generated/(polymer+'_calculate.py');f.write_text(s)
 t=legacy.replace('/route-comparison-a6','/route-comparison-a7/'+polymer).replace("m['polymer']=='pe'",f"m['polymer']=='{polymer}'").replace("polymer_name='pe'",f"polymer_name='{polymer}'")
 t=re.sub(r'\b31\b',str(n),t);g=generated/(polymer+'_existing.py');g.write_text(t)
 env=dict(os.environ,PYTHONPATH=str(R/'scripts'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
 # Generated files are nested: explicitly fix the workspace root.
 for q in [f,g]:q.write_text(q.read_text().replace('R=Path(__file__).resolve().parents[1]',"R=Path('/home/aaltamimi2/plastchem-euler')"))
 if not (dest/'summary.json').exists():
  with (dest/'calculation.log').open('a') as log:subprocess.run([sys.executable,str(f)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
 if not (dest/'existing-convention-summary.json').exists():
  with (dest/'existing.log').open('a') as log:subprocess.run([sys.executable,str(g)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
 print(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'polymer_complete':polymer,'conformers':n}),flush=True)
print('ALL_SEVEN_CALCULATED',flush=True)
