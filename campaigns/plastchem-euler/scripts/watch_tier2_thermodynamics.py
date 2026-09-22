"""A-4 serial panel worker; acquire original worker lock before any calculations.
Start only after original panel worker is quiesced at a completed-pass boundary.
Uses unchanged activity/pair functions; writes a separate tier-tagged ledger.
"""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import fcntl,gc,hashlib,json,time,resource,subprocess
from pathlib import Path
from thermodynamic_library import registry,ROOT,P as PANEL_STATE
P=ROOT/'state/tier2-v1/thermodynamics';P.mkdir(parents=True,exist_ok=True)
from thermodynamic_prediction import activity,pair,validation,CONFIG,refresh_volumes
DEST=Path('/mnt/r/plastchem-euler/tier2-v1/thermodynamics');DEST.mkdir(exist_ok=True)
lock=(PANEL_STATE/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
ledger_file=P/'processing-ledger.json';ledger=json.loads(ledger_file.read_text()) if ledger_file.exists() else {}
def local_write(path,data):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(path)
def available():return int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])
def input_signature():
 # Local metadata only: do not reread network surfaces while awaiting new inputs.
 paths=list((ROOT/'state/tier2-v1/records').glob('*.json'))+list((ROOT/'state/solvent-library-v1/records').glob('*.json'))
 paths += [PANEL_STATE/name for name in ['solvent-inventory.json','additional-molar-volumes.json','supplier-molar-volumes.json']]
 stamps=[]
 for path in sorted(paths):
  try:
   st=path.stat();stamps.append((str(path),st.st_mtime_ns,st.st_size))
  except FileNotFoundError:stamps.append((str(path),None,None))
 return stamps
while True:
 pass_inputs=input_signature();pass_failed=False
 try:
  volumes=refresh_volumes();lib=registry();ready=[s for s in lib['solvents'] if s['status']=='ready'];requests=[s['solvent_key'] for s in lib['solvents']]
  signature=hashlib.sha256(json.dumps({'solvents':[(s['solvent_key'],s['surface_sha256']) for s in ready],'config':CONFIG,'ready_solvent_volume_data':{s['solvent_key']:volumes.get(s['solvent_key']) for s in ready}},sort_keys=True).encode()).hexdigest()
  records=[]
  for f in sorted((ROOT/'state/tier2-v1/records').glob('*.json')):
   r=json.loads(f.read_text())
   if r.get('status')=='converged':records.append(r)
  for r in records:
   key=r['inchikey'];previous=ledger.get(key,{})
   if previous.get('library_signature')==signature and previous.get('solute_surface_sha256')==r['surface_sha256']:continue
   if available()<1000*1024:print(json.dumps({'event':'memory_pause','available_kib':available()}),flush=True);break
   dest=DEST/(key+'.json')
   if dest.exists():out=json.loads(dest.read_text())
   else:out={'tier':'CHNO_500_700','tier_denominator':270,'input_inchikey':key,'perceived_inchikey':r['perceived_inchikey'],'identity_match_basis':r['identity_match_basis'],'name':r['input']['name'],'cpu_model':r['cpu_model'],'solute_surface_sha256':r['surface_sha256'],'config':CONFIG,'activities':{}}
   assert out['config']==CONFIG,'Changed activity configuration requires explicit review'
   assert out['solute_surface_sha256']==r['surface_sha256'],'Changed completed solute requires explicit review'
   assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor' and r['connectivity_match']
   solute={'surface':str(Path(r['archive_path'])/'surface.orcacosmo'),'surface_sha256':r['surface_sha256']}
   for solvent in ready:
    old=out['activities'].get(solvent['solvent_key'])
    if old:
     assert old['solvent_surface_sha256']==solvent['surface_sha256'],'Changed solvent requires explicit review'
     continue
    out['activities'][solvent['solvent_key']]=activity(solute,solvent)
   out['partitions_against_water']=[pair(name,'water',out['activities']) for name in requests if name!='water']
   out['validation']=validation(out['activities']);out['available_partition_count']=sum(x['status']=='predicted' for x in out['partitions_against_water']);out['requested_partition_count']=32;out['partitioning_complete']=out['available_partition_count']==32
   out['updated_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
   subprocess.run(['df','-h','/'],check=True,stdout=subprocess.DEVNULL)
   content=(json.dumps(out,indent=2)+'\n').encode();dest.write_bytes(content);assert hashlib.sha256(dest.read_bytes()).digest()==hashlib.sha256(content).digest(),'Result write verification'
   ledger[key]={'library_signature':signature,'solute_surface_sha256':r['surface_sha256'],'result_sha256':hashlib.sha256(content).hexdigest(),'activity_count':out['validation']['activity_solvents_converged'],'partition_count':out['available_partition_count'],'complete':out['partitioning_complete'],'result_path':str(dest),'failed_activity_count':sum(a['status']!='converged' for a in out['activities'].values())}
   local_write(ledger_file,ledger)
   print(json.dumps({'key':key,'activity_count':ledger[key]['activity_count'],'partition_count':out['available_partition_count'],'failed_activities':ledger[key]['failed_activity_count'],'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}),flush=True);gc.collect()
  summary={'tier':'CHNO_500_700','tier_denominator':270,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'goal_complete':False,'campaign_converged_snapshot':len(records),'library_ready':len(ready),'library_requested':33,'contaminants_processed':len(ledger),'contaminants_full_partitioning':sum(r['complete'] for r in ledger.values()),'available_nonself_partition_predictions':sum(r['partition_count'] for r in ledger.values()),'failed_activity_calculations':sum(r['failed_activity_count'] for r in ledger.values()),'memory_paused':available()<1000*1024,'result_root':str(DEST)}
  local_write(P/'processing-summary.json',summary);print(json.dumps(summary),flush=True)

 except Exception as exc:
  pass_failed=True;print(json.dumps({'processing_error':str(exc),'epoch':time.time()}),flush=True)
 # Changes arriving during the pass trigger another pass immediately. Otherwise
 # poll local inputs every 10 s; retain a 10-minute unchanged-input heartbeat.
 if pass_failed or available()<1000*1024:time.sleep(30)
 else:
  deadline=time.monotonic()+600
  while input_signature()==pass_inputs and time.monotonic()<deadline:time.sleep(10)
