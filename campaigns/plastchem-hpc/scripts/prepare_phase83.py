"""Freeze the 8.3 cohort and deterministic stratified calibration; no science compute."""
import collections,csv,datetime,hashlib,json,shutil,tarfile
from pathlib import Path
R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase83-v1')
B=Path('/mnt/r/plastchem-euler/phase8-v1')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def main():
 D.mkdir(exist_ok=True);(D/'surfaces').mkdir(exist_ok=True)
 assert not (D/'cohort.json').exists(),'Frozen cohort already exists; do not replace it'
 rows=[];excluded=[]
 for tier,folder in [('main','campaign-v1'),('tier2','tier2-v1')]:
  for p in sorted((R/'state'/folder/'records').glob('*.json')):
   r=json.loads(p.read_text());i=r['input'];key=i['inchikey']
   if r['status']!='converged':excluded.append({'tier':tier,'input':i,'status':r['status'],'record_sha256':sha(p)});continue
   assert r['input_inchikey'].split('-')[0]==r['perceived_inchikey'].split('-')[0]
   assert '7763' in r['cpu_model']
   n=int(i['atoms']);band='01_le30' if n<=30 else '02_31_50' if n<=50 else '03_51_80' if n<=80 else '04_gt80'
   # Nitrogen-containing chemistry is deliberately represented within every size band.
   stratum='tier2' if tier=='tier2' else band+('_N' if 'N' in i['smiles'].upper() else '_noN')
   rows.append({'index':len(rows),'inchikey':key,'tier':tier,'stratum':stratum,'input':i,
    'B':'surfaces/'+r['surface_sha256']+'.orcacosmo','surface_sha256':r['surface_sha256'],
    'source_surface':str(Path(r['archive_path'])/'surface.orcacosmo'),'source_record':str(p),'record_sha256':sha(p),
    'perceived_inchikey':r['perceived_inchikey'],'identity_match_basis':r['identity_match_basis'],
    'perception_engines_agreeing_on_perceived_key':r.get('perception_engines_agreeing_on_perceived_key',[]),
    'molecular_weight_g_mol':float(i['molecular_weight_g_mol']),'cpu_model':r['cpu_model']})
 assert collections.Counter(r['tier'] for r in rows)=={'main':5803,'tier2':27}
 assert len({r['inchikey'] for r in rows})==len(rows)
 strata=collections.defaultdict(list)
 for r in rows:strata[r['stratum']].append(r)
 calibration=[];counts={}
 for name,group in sorted(strata.items()):
  group.sort(key=lambda r:(int(r['input']['atoms']),r['molecular_weight_g_mol'],r['inchikey']))
  selected=sorted({round((len(group)-1)*q) for q in [.1,.5,.9]})
  ids=[group[j]['index'] for j in selected];calibration+=ids;counts[name]={'population':len(group),'selected_indices':ids}
 cohort={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'A-8 gate 8.2 clearance',
  'phase81_manifest_sha256':sha(B/'manifest.json'),'phase82_validation_sha256':sha(B/'validation-inputs.json'),
  'rows':rows,'not_accepted_records':excluded,'calibration_indices':calibration,'strata':counts,
  'calibration_design':'Nonrandom 10th/50th/90th size-order quantiles within main atom-count × nitrogen-presence strata; tier2 separately. All ten polymers and all 32 solvents, both temperatures per selected molecule.',
  'cost_gate_cpu_hours':6000,'full_grid':{'partition_each_convention':len(rows)*320,'lle':len(rows)*64}}
 write(D/'cohort.json',cohort)
 # Only calibration solutes are staged before the cost gate. Existing phase8 bundle is immutable.
 for idx in calibration:
  r=rows[idx];src=Path(r['source_surface']);assert sha(src)==r['surface_sha256'];shutil.copyfile(src,D/r['B'])
 for name in ['phase83_worker.py','submit_phase83_remote.py','phase83.sbatch']:
  shutil.copyfile(R/'scripts'/name,D/name)
 write(D/'calibration-plan.json',{'cohort_sha256':sha(D/'cohort.json'),'strata':counts,'indices':calibration})
 shutil.copyfile(B/'validation-inputs.json',D/'validation-inputs.json')
 shutil.copyfile(B/'phase8_lle.py',D/'phase8_lle.py')
 files=[D/name for name in ['cohort.json','calibration-plan.json','phase83_worker.py','submit_phase83_remote.py','phase83.sbatch','validation-inputs.json','phase8_lle.py']]+sorted((D/'surfaces').glob('*'))
 write(D/'staging-pins.json',{str(p.relative_to(D)):sha(p) for p in files})
 with tarfile.open(D/'calibration-inputs.tar.gz','w:gz') as tar:
  for p in files+[D/'staging-pins.json']:tar.add(p,arcname=str(p.relative_to(D)))
 print(json.dumps({'cohort':len(rows),'calibration':len(calibration),'strata':counts,'archive_sha256':sha(D/'calibration-inputs.tar.gz')},indent=2))
if __name__=='__main__':main()
