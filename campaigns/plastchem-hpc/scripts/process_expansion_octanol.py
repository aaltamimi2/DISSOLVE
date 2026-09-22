"""Separate post-freeze cohort; reuse verified panel records, calculate validation-only octanol serially."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import csv,datetime as dt,fcntl,gc,hashlib,json,time
from pathlib import Path
from thermodynamic_prediction import activity,pair,CONFIG,VOLUMES,VOLUME_DATA
ROOT=Path(__file__).resolve().parents[1];D=Path(os.environ.get('PLASTCHEM_OCTANOL_FILL_ROOT','/mnt/r/plastchem-euler/measured-expansion-2026-09-15/octanol-fill'));D.mkdir(parents=True,exist_ok=True)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,o):
 p.parent.mkdir(parents=True,exist_ok=True);raw=(json.dumps(o,indent=2)+'\n').encode();p.write_bytes(raw);assert sha(p)==hashlib.sha256(raw).hexdigest()
manifest=D/'manifest.json'
if not manifest.exists():
 frozen=Path('/mnt/r/plastchem-euler/progress-2026-09-14/freeze/manifest.json');old={r['inchikey'] for r in json.loads(frozen.read_text())['cohort']}
 records=[json.loads(p.read_text()) for p in sorted((ROOT/'state/campaign-v1/records').glob('*.json'))]
 cohort=[r for r in records if r.get('status')=='converged' and r['inchikey'] not in old]
 for r in cohort:save(D/'freeze/state/campaign-v1/records'/f"{r['inchikey']}.json",r)
 failures=[{k:r.get(k) for k in ['inchikey','failure_mode','error','dft_status','perceived_inchikey']} for r in records if r.get('status')=='failed']
 save(manifest,{'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'frozen_manifest_sha256':sha(frozen),'released_cohort_excluded':len(old),'cohort':[{'inchikey':r['inchikey'],'surface_sha256':r['surface_sha256'],'archive_path':r['archive_path']} for r in cohort],'campaign_summary':json.loads((ROOT/'state/campaign-v1/summary.json').read_text()),'failures':failures,'scope':'Post-freeze converged cohort only; panel and validation-only dry octanol; no released results modified.'})
m=json.loads(manifest.read_text());cohort=m['cohort']
lock=(ROOT/'state/thermodynamics-v1/worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
ledger=json.loads((ROOT/'state/thermodynamics-v1/processing-ledger.json').read_text())
ref=json.loads((ROOT/'state/campaign-v1/records/KBPLFHHGFOOTCA-UHFFFAOYSA-N.json').read_text());assert ref['status']=='converged' and ref['connectivity_match']
solvent={'solvent_key':'octanol','surface':str(Path(ref['archive_path'])/'surface.orcacosmo'),'surface_sha256':ref['surface_sha256']};assert sha(solvent['surface'])==ref['surface_sha256']
vol=json.loads((ROOT/'state/progress-2026-09-14/octanol-molar-volume.json').read_text());assert sha(vol['source_xml_path'])==vol['source_xml_sha256'];VOLUMES['octanol']=vol['molar_volume_cm3_mol'];VOLUME_DATA['octanol']=vol
save(D/'provenance.json',{'script_sha256':sha(__file__),'config':CONFIG,'octanol':solvent,'volume':vol,'manifest_sha256':sha(manifest)})
for member in cohort:
 k=member['inchikey'];dest=D/'octanol'/f'{k}.json'
 if dest.exists():continue
 while int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])<1024000:
  print(json.dumps({'event':'memory_pause','key':k}),flush=True);time.sleep(30)
 entry=ledger[k];raw=Path(entry['result_path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==entry['result_sha256'];p=json.loads(raw);assert p['solute_surface_sha256']==member['surface_sha256'] and p['config']==CONFIG
 save(D/'panel'/f'{k}.json',p)
 water=p['activities']['water'];assert water['status']=='converged'
 a=activity({'surface':str(Path(member['archive_path'])/'surface.orcacosmo'),'surface_sha256':member['surface_sha256']},solvent);prediction=pair('octanol','water',{'octanol':a,'water':water})
 save(dest,{'input_inchikey':k,'perceived_inchikey':p['perceived_inchikey'],'identity_match_basis':p['identity_match_basis'],'name':p['name'],'cpu_model':p['cpu_model'],'solute_surface_sha256':member['surface_sha256'],'water_source_result_sha256':entry['result_sha256'],'water_activity':water,'octanol_activity':a,'prediction':prediction,'utc':dt.datetime.now(dt.timezone.utc).isoformat()})
 print(json.dumps({'key':k,'status':prediction['status']}),flush=True);gc.collect()
rows=[];panel_count=0
for member in cohort:
 k=member['inchikey'];r=json.loads((D/'octanol'/f'{k}.json').read_text());p=json.loads((D/'panel'/f'{k}.json').read_text());panel_count+=p['available_partition_count'];rows.append({'input_inchikey':k,'perceived_inchikey':r['perceived_inchikey'],'identity_match_basis':r['identity_match_basis'],'name':r['name'],'status':r['prediction']['status'],'logKow':r['prediction'].get('log10_K_concentration')})
with (D/'octanol.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
save(D/'summary.json',{'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'denominator':len(cohort),'panel_molecules':len(rows),'panel_predictions':panel_count,'octanol_predicted':sum(r['status']=='predicted' for r in rows),'octanol_failed':sum(r['status']!='predicted' for r in rows),'full_panel_complete':False})
print((D/'summary.json').read_text(),flush=True)
