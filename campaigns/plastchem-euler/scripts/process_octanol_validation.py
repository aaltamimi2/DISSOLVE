"""Serial validation-only octanol panel for the fixed rehearsal cohort, reusing verified water."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import csv,datetime as dt,fcntl,gc,hashlib,json,time
from pathlib import Path
from thermodynamic_prediction import activity,pair,CONFIG,VOLUMES,VOLUME_DATA
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/progress-2026-09-14';B=Path('/mnt/r/plastchem-euler/progress-2026-09-14');D=B/'octanol-validation';D.mkdir(exist_ok=True)
lock=(ROOT/'state/thermodynamics-v1/worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,obj):
 content=(json.dumps(obj,indent=2)+'\n').encode();p.write_bytes(content);assert sha(p)==hashlib.sha256(content).hexdigest()
key='KBPLFHHGFOOTCA-UHFFFAOYSA-N';source=ROOT/'state/campaign-v1/records'/f'{key}.json';r=json.loads(source.read_text())
assert r['status']=='converged' and r['connectivity_match'] and r['perceived_inchikey'].split('-')[0]==key.split('-')[0]
assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor' and r['orca_version']=='6.1.1' and r['orca_git']=='487d211c'
surface=Path(r['archive_path'])/'surface.orcacosmo';assert sha(surface)==r['surface_sha256']
for stage,item in r['stages'].items():assert sha(Path(r['archive_path'])/(stage+'.inp'))==item['input_sha256']
solvent={'solvent_key':'octanol','surface':str(surface),'surface_sha256':r['surface_sha256']}
volume=json.loads((P/'octanol-molar-volume.json').read_text());assert sha(volume['source_xml_path'])==volume['source_xml_sha256']
VOLUMES['octanol']=volume['molar_volume_cm3_mol'];VOLUME_DATA['octanol']=volume
cohort_file=B/'rehearsal/freeze/manifest.json';cohort=json.loads(cohort_file.read_text())['cohort'];assert len(cohort)==590
ledger=json.loads((B/'rehearsal/sealed-thermodynamics/processing-ledger.json').read_text())
references={x['inchikey'] for x in csv.DictReader((B/'experimental-reference-candidates.csv').open()) if x['solvent']=='octanol' and x.get('observed_operator','=')=='='}
cohort.sort(key=lambda x:(x['inchikey'] not in references,x['inchikey']))
meta={'cohort_snapshot_id':sha(cohort_file),'cohort_denominator':len(cohort),'octanol_surface_sha256':solvent['surface_sha256'],'octanol_source_record_sha256':sha(source),'volume':volume,'config':CONFIG,'script_sha256':sha(__file__),'scope':'Validation-only octanol/water pure-component transfer panel; not a change to the original 32-pair production panel.'}
save(D/'provenance.json',meta)
for member in cohort:
 k=member['inchikey'];dest=D/(k+'.json')
 if dest.exists():
  old=json.loads(dest.read_text());assert old['octanol_surface_sha256']==solvent['surface_sha256'] and old['solute_surface_sha256']==member['surface_sha256'];continue
 available=int(next(x for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')).split()[1])
 if available<1000*1024:print(json.dumps({'event':'memory_pause','available_kib':available}),flush=True);break
 entry=ledger[k];raw=Path(entry['result_path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==entry['result_sha256'];production=json.loads(raw)
 assert production['solute_surface_sha256']==member['surface_sha256'] and production['config']==CONFIG
 water=production['activities']['water'];assert water['status']=='converged'
 solute={'surface':str(Path(member['archive_path'])/'surface.orcacosmo'),'surface_sha256':member['surface_sha256']}
 measured=activity(solute,solvent);prediction=pair('octanol','water',{'octanol':measured,'water':water})
 out={'input_inchikey':k,'perceived_inchikey':production['perceived_inchikey'],'identity_match_basis':production['identity_match_basis'],'name':production['name'],'cpu_model':production['cpu_model'],'solute_surface_sha256':member['surface_sha256'],'octanol_surface_sha256':solvent['surface_sha256'],'water_source_result_sha256':entry['result_sha256'],'water_activity':water,'octanol_activity':measured,'prediction':prediction,'utc':dt.datetime.now(dt.timezone.utc).isoformat()}
 save(dest,out);print(json.dumps({'key':k,'name':production['name'],'status':prediction['status'],'logKow':prediction.get('log10_K_concentration')}),flush=True);gc.collect()
rows=[json.loads((D/(m['inchikey']+'.json')).read_text()) for m in cohort if (D/(m['inchikey']+'.json')).exists()]
save(P/'octanol-processing-summary.json',{'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'denominator':590,'processed':len(rows),'predicted':sum(r['prediction']['status']=='predicted' for r in rows),'failed':sum(r['prediction']['status']!='predicted' for r in rows),'not_processed':590-len(rows),'result_root':str(D)})
