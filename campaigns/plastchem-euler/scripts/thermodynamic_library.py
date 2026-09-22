"""Resolve only verified Milan/24a surfaces; keep every missing solvent explicit."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/thermodynamics-v1'
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def registry():
 inventory=json.loads((P/'solvent-inventory.json').read_text());entries=[]
 for item in inventory['solvents']:
  name=item['solvent_key'];row={'solvent_key':name,'status':'pending','source_identity':item.get('connectivity_inchikey')}
  try:
   if name=='water':f=Path('/mnt/r/plastchem-euler/results/XLYOFNOQVPJJNP-UHFFFAOYSA-N/result.json')
   elif item['status']=='reuse_campaign_when_verified':
    assert len(item['campaign_matches'])==1,'Ambiguous campaign connectivity matches'
    f=ROOT/'state/campaign-v1/records'/(item['campaign_matches'][0]['inchikey']+'.json')
   elif item['status']=='missing_campaign_solvent_calculation':f=ROOT/'state/solvent-library-v1/records'/(item['connectivity_inchikey']+'.json')
   else:row.update(status='identity_unresolved',reason=item.get('reason'));entries.append(row);continue
   if not f.exists():entries.append(row);continue
   r=json.loads(f.read_text())
   if r['status']!='converged':row.update(status='failed' if r['status']=='failed' else 'pending',reason=r.get('failure_mode'));entries.append(row);continue
   assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor' and r['orca_version']=='6.1.1' and r['orca_git']=='487d211c'
   perceived=r.get('perceived_inchikey',r.get('inchikey_after_optimization'));assert r['identity_verified'] and perceived.split('-')[0]==item['connectivity_inchikey'].split('-')[0]
   d=Path(r['archive_path']);surface=d/'surface.orcacosmo';assert sha(surface)==r['surface_sha256']
   for stage,info in r['stages'].items():assert sha(d/(stage+'.inp'))==info['input_sha256']
   row.update(status='ready',surface=str(surface),surface_sha256=r['surface_sha256'],cpu_model=r['cpu_model'],perceived_inchikey=perceived,source_record=str(f),source_record_sha256=sha(f),orca_version=r['orca_version'],orca_git=r['orca_git'])
   if name=='water':row['reuse_note']='Single water geometry from verified Milan A-1 diagnostic; water has no conformational torsions. Same frozen OPT/COSMORS levels; original diagnostic seed and preparation metadata retained.'
  except Exception as exc:row.update(status='verification_failed',error=str(exc))
  entries.append(row)
 result={'requested_count':33,'ready_count':sum(r['status']=='ready' for r in entries),'solvents':entries,'parameterization':'24a','cpu_model':'AMD EPYC 7763 64-Core Processor'}
 tmp=P/'library-registry.tmp';tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(P/'library-registry.json');return result
if __name__=='__main__':print(json.dumps(registry(),indent=2))
