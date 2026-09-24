"""Reconstruct both ensemble conventions from archived raw activity coefficients.

Pure arithmetic on existing results; no COSMO engine, new jobs, or recalibration.
Processes one return archive at a time and does not import the execution worker.
"""
import collections,datetime,gzip,hashlib,json,math,tarfile
from pathlib import Path
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase83-v1');B=D.parent/'phase8-v1'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def logsum(values):
 peak=max(values);return peak+math.log(math.fsum(math.exp(v-peak) for v in values))
def main():
 manifest=json.loads((B/'manifest.json').read_text());cohort=json.loads((D/'cohort.json').read_text())
 registry=json.loads((R/'state/phase83-v1/collection.json').read_text())['collected'];archives=collections.defaultdict(list)
 for key,entry in registry.items():archives[entry['archive']].append(key)
 phases={r['entry_id']:(sha(B/r['B']),r) for ensemble in manifest['polymers'].values() for r in ensemble}
 solvents={s['name']:(sha(B/s['B']),s) for s in manifest['solvents']}
 counters=collections.Counter();errors=collections.defaultdict(float);units=[]
 for archive,keys in sorted(archives.items()):
  activities={key:{} for key in keys};receipt=json.loads((D/'returns'/archive.replace('.tar.gz','.json')).read_text())
  assert sha(D/'returns'/archive)==receipt['archive_sha256']
  with tarfile.open(D/'returns'/archive,'r|gz') as tar:
   pins=None
   for member in tar:
    if member.name=='return-pins.json':pins=json.load(tar.extractfile(member));continue
    parts=Path(member.name).parts
    if len(parts)!=4 or parts[0]!='results' or parts[1] not in activities or parts[2]!='activities' or not parts[3].endswith('.json'):continue
    raw=tar.extractfile(member).read();assert pins and hashlib.sha256(raw).hexdigest()==pins[member.name]
    activities[parts[1]][Path(parts[3]).stem]=json.loads(raw)
  for key in keys:
   c=cohort['rows'][int(key)];p=D/'collected'/(key+'.json.gz');assert sha(p)==registry[key]['metadata_sha256']
   data=json.load(gzip.open(p,'rt'));raw=activities[key];checked=set();gamma={};volumes={}
   def activity(name,phase_sha):
    a=raw[name];assert a['status']=='converged' and a['reference_state']=='pure_component'
    sig=a['signature'];assert sig['solute_sha256']==c['surface_sha256'] and sig['phase_sha256']==phase_sha and sig['temperature_K']==298.15 and sig['route']=='B'
    samples=a['samples'];assert 2<=len(samples)<=4 and [s['x'] for s in samples]==[1e-5,1e-6,1e-7,1e-8][:len(samples)]
    assert all(math.isfinite(s['ln_gamma']) for s in samples)
    delta=abs(samples[-1]['ln_gamma']-samples[-2]['ln_gamma'])/math.log(10)
    assert delta<=.005 and a['ln_gamma']==samples[-1]['ln_gamma']
    errors['largest_raw_dilution_delta_log10']=max(errors['largest_raw_dilution_delta_log10'],delta)
    checked.add(name);return a
   for polymer,ensemble in manifest['polymers'].items():
    rows=[r for r in data['partition'] if r['polymer']==polymer and r['status']=='predicted']
    if not rows:continue
    acts=[activity('polymer-'+r['entry_id'],phases[r['entry_id']][0]) for r in ensemble]
    energies=[r['B_energy_hartree'] for r in ensemble];minimum=min(energies);delta=[e-minimum for e in energies]
    for convention in ['normalized','existing']:
     exponent=[-e*(2625.4996394799/(.00831446261815324*298.15) if convention=='normalized' else 627.5094740631/(.0019872041*298.15)) for e in delta]
     z=logsum(exponent);weights=[math.exp(v-z) for v in exponent]
     assert abs(math.fsum(weights)-1)<1e-12
     ln_gamma=[a['ln_gamma'] if convention=='normalized' else a['samples'][0]['ln_gamma'] for a in acts]
     gp=-logsum([x-y for x,y in zip(exponent,ln_gamma)])+(z if convention=='normalized' else 0)
     vp=math.fsum(w*r['B_cavity_cm3_mol'] for w,r in zip(weights,ensemble))
     gamma[(polymer,convention)]=gp;volumes[(polymer,convention)]=vp
    counters['polymer_ensembles_reconstructed']+=1
   for row in data['partition']:
    if row['status']!='predicted':counters['failed_partition_rows_retained']+=1;continue
    name=row['solvent'];phase_sha,solvent=solvents[name];a=activity('solvent-'+name,phase_sha);convention=row['convention'];k=(row['polymer'],convention)
    gs=a['ln_gamma'] if convention=='normalized' else a['samples'][0]['ln_gamma'];gp=gamma[k];vp=volumes[k]
    vs=solvent['B_volume' if convention=='normalized' else 'B_legacy_volume'];lp=(gp-gs)/math.log(10);lc=lp+math.log10(vp/vs)
    expected={'ln_gamma_polymer':gp,'ln_gamma_solvent':gs,'polymer_volume_cm3_mol':vp,'solvent_volume_cm3_mol':vs,'logP_x':lp,'logP_concentration':lc}
    for field,value in expected.items():
     error=abs(float(row[field])-value);errors[field]=max(errors[field],error);assert error<1e-9,(key,field,error)
    counters['partition_rows_reconstructed']+=1
   counters['raw_activities_checked']+=len(checked);counters['contaminants_checked']+=1
   units.append({'index':int(key),'input_inchikey':c['inchikey'],'raw_activities_checked':len(checked),'metadata_sha256':registry[key]['metadata_sha256'],'archive':archive})
  print('ENSEMBLE_ARCHIVE_CHECKED',archive,len(keys),flush=True)
 result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cohort_sha256':sha(D/'cohort.json'),'auditor_sha256':sha(Path(__file__)),
  'denominator':len(cohort['rows']),'status':'passed_for_collected_subset','counts':dict(counters),'maximum_errors':dict(errors),'units':units,
  'scope':'Independently reconstructed stored partition values from archived conformer/solvent activities and pinned energies/volumes. Both historical and normalized conventions retained. Verifies source surface linkage and dilution convergence. Does not validate experimental accuracy or independently solve COSMOspace.'}
 p=D/'ensemble-audit.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(p)
 print(json.dumps({k:v for k,v in result.items() if k!='units'},indent=2))
 return result
if __name__=='__main__':main()
