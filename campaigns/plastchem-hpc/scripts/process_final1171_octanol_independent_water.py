"""Finish pinned octanol cohort serially; explicit separate water references when panel is absent."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import json,hashlib,csv,datetime,time,signal,fcntl,gc,resource
from pathlib import Path
from thermodynamic_prediction import activity,pair,CONFIG,VOLUMES,VOLUME_DATA
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/octanol-final1171-2026-09-17');PID=1147795
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(p,o):
 p.parent.mkdir(parents=True,exist_ok=True);raw=(json.dumps(o,indent=2)+'\n').encode();p.write_bytes(raw);assert sha(p)==hashlib.sha256(raw).hexdigest()
lock=(R/'state/octanol-final1171-coordinator.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert sha(D/'manifest.json')=='46ac13d8bc7a57881845db96af41c3e6ec9dfb5467a6bed788e0456b0611c432'
m=json.loads((D/'manifest.json').read_text());assert len(m['cohort'])==1171
assert b'scripts/watch_thermodynamics.py' in Path(f'/proc/{PID}/cmdline').read_bytes()
ref=json.loads((R/'state/campaign-v1/records/KBPLFHHGFOOTCA-UHFFFAOYSA-N.json').read_text());assert ref['status']=='converged' and ref['connectivity_match']
octanol={'solvent_key':'octanol','surface':str(Path(ref['archive_path'])/'surface.orcacosmo'),'surface_sha256':ref['surface_sha256']};assert sha(octanol['surface'])==octanol['surface_sha256']
water_ref=next(x for x in json.loads((R/'state/thermodynamics-v1/library-registry.json').read_text())['solvents'] if x['solvent_key']=='water');assert water_ref['status']=='ready' and sha(water_ref['surface'])==water_ref['surface_sha256']
vol=json.loads((R/'state/progress-2026-09-14/octanol-molar-volume.json').read_text());assert sha(vol['source_xml_path'])==vol['source_xml_sha256'];VOLUMES['octanol']=vol['molar_volume_cm3_mol'];VOLUME_DATA['octanol']=vol
prov={'utc':utc(),'script_sha256':sha(__file__),'config':CONFIG,'octanol':octanol,'volume':vol,'water_reference':water_ref,'manifest_sha256':sha(D/'manifest.json'),'scope':'1171 final accepted contaminants; validation-only dry octanol. Reuse hash-verified panel water where available; otherwise separately calculate water with unchanged recipe. No production panel records or ledgers modified.'}
if (D/'provenance.json').exists():
 old=json.loads((D/'provenance.json').read_text());assert old['script_sha256']==prov['script_sha256'] and old['manifest_sha256']==prov['manifest_sha256']
else:save(D/'provenance.json',prov)
paused=False;start=time.monotonic();rows=[]
try:
 os.kill(PID,signal.SIGSTOP);paused=True
 for _ in range(600):
  state=next(x for x in Path(f'/proc/{PID}/status').read_text().splitlines() if x.startswith('State:'))
  if '\tT' in state:break
  time.sleep(.1)
 else:raise RuntimeError('Panel process did not stop; no calculations started')
 save(R/'state/octanol-final1171-independent-water-handoff.json',{'utc':utc(),'panel_pid':PID,'coordinator_pid':os.getpid(),'panel_state':state,'script_sha256':sha(__file__),'action':'Temporarily pause panel for serial final octanol; automatically resume in finally'})
 ledger=json.loads((R/'state/thermodynamics-v1/processing-ledger.json').read_text())
 for member in m['cohort']:
  key=member['inchikey'];dest=D/'octanol'/f'{key}.json'
  if dest.exists():
   out=json.loads(dest.read_text());assert out['input_inchikey']==key and out['solute_surface_sha256']==member['surface_sha256']
  else:
   while int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])<1024000:
    print(json.dumps({'event':'memory_pause','utc':utc()}),flush=True);time.sleep(30)
   rr=json.loads((R/'state/campaign-v1/records'/f'{key}.json').read_text());assert rr['status']=='converged' and rr['surface_sha256']==member['surface_sha256'] and rr['connectivity_match'] and rr['cpu_model']=='AMD EPYC 7763 64-Core Processor'
   solute={'surface':str(Path(member['archive_path'])/'surface.orcacosmo'),'surface_sha256':member['surface_sha256']};assert sha(solute['surface'])==solute['surface_sha256']
   panel=None;basis='validation_only_water_no_panel_record'
   if key in ledger:
    entry=ledger[key];raw=Path(entry['result_path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()==entry['result_sha256']:
     panel=json.loads(raw);assert panel['config']==CONFIG and panel['solute_surface_sha256']==member['surface_sha256'];basis='reused_hash_verified_production_panel_water'
    else:basis='validation_only_water_panel_write_not_yet_committed'
   if panel is None:
    water=activity(solute,water_ref)
    panel={'input_inchikey':key,'perceived_inchikey':rr['perceived_inchikey'],'identity_match_basis':rr['identity_match_basis'],'name':rr['input']['name'],'cpu_model':rr['cpu_model'],'solute_surface_sha256':member['surface_sha256'],'config':CONFIG,'activities':{'water':water},'available_partition_count':0,'partitioning_complete':False,'record_scope':basis,'note':'Separate validation water record; does not claim full panel coverage'}
   p=D/'panel'/f'{key}.json';save(p,panel);water=panel['activities']['water'];assert water['solvent_surface_sha256']==water_ref['surface_sha256']
   a=activity(solute,octanol);prediction=pair('octanol','water',{'octanol':a,'water':water})
   out={'input_inchikey':key,'perceived_inchikey':panel['perceived_inchikey'],'identity_match_basis':panel['identity_match_basis'],'name':panel['name'],'cpu_model':panel['cpu_model'],'solute_surface_sha256':member['surface_sha256'],'water_source_result_sha256':sha(p),'water_source_basis':basis,'water_activity':water,'octanol_activity':a,'prediction':prediction,'utc':utc()};save(dest,out)
  rows.append({'input_inchikey':key,'perceived_inchikey':out['perceived_inchikey'],'identity_match_basis':out['identity_match_basis'],'name':out['name'],'status':out['prediction']['status'],'logKow':out['prediction'].get('log10_K_concentration'),'water_source_basis':out['water_source_basis']})
  summary={'utc':utc(),'denominator':1171,'processed':len(rows),'predicted':sum(r['status']=='predicted' for r in rows),'failed_or_unavailable':sum(r['status']!='predicted' for r in rows),'not_run':1171-len(rows),'reused_panel_water':sum(r['water_source_basis']=='reused_hash_verified_production_panel_water' for r in rows),'separate_validation_water':sum(r['water_source_basis']!='reused_hash_verified_production_panel_water' for r in rows),'wall_seconds':time.monotonic()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
  if len(rows)%20==0 or len(rows)==1171:save(D/'processing-summary.json',summary)
  print(json.dumps(dict(summary,last_key=key,last_status=rows[-1]['status'])),flush=True);gc.collect()
 with (D/'octanol.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 save(D/'summary.json',summary)
finally:
 if paused:
  os.kill(PID,signal.SIGCONT);save(R/'state/octanol-final1171-independent-water-resume.json',{'utc':utc(),'panel_pid':PID,'coordinator_pid':os.getpid(),'action':'SIGCONT','processed':len(rows),'wall_seconds':time.monotonic()-start})
