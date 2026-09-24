"""Uncensored first-production-wave cost reassessment; no scheduler mutation.

Preserves the original calibration. Uses current measured costs in represented
strata and exposes unchanged prior estimates in unrepresented strata explicitly.
"""
import collections,datetime,hashlib,json,statistics
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/phase83-v1')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 c=json.loads((D/'cohort.json').read_text());original=json.loads((D/'cost-gate.json').read_text());snapshot=json.loads((D/'latest-status.json').read_text())
 indices=[i for i in range(len(c['rows'])) if i not in c['calibration_indices']][:27]
 accounting={}
 for line in snapshot['accounting'].splitlines():
  f=line.split('|');job=f[0]
  if job.startswith('68234_') and '.' not in job and job.split('_')[1].isdigit():accounting[int(job.split('_')[1])]=f
 groups=collections.defaultdict(list);measured=[]
 for task,index in enumerate(indices):
  f=accounting[task];assert f[1]=='COMPLETED',(task,f[1])
  complete=snapshot['completed'][f'{index:05d}'];assert len(complete['outputs'])==10 and len(complete['lle_statuses'])==64
  assert complete['signature']['cohort_sha256']==sha(D/'cohort.json')
  row={'task':task,'index':index,'input_inchikey':c['rows'][index]['inchikey'],'name':c['rows'][index]['input']['name'],'stratum':c['rows'][index]['stratum'],
   'allocated_cpu_seconds':int(f[2])*int(f[3]),'partition_seconds':complete['partition_seconds'],'lle_seconds':complete['lle_seconds'],'wall_seconds':complete['wall_seconds']}
  measured.append(row);groups[row['stratum']].append(row['allocated_cpu_seconds'])
 strata=[];point=low=high=0.;covered=0
 for prior in original['strata']:
  values=groups.get(prior['stratum']);source='first_production_wave' if values else 'original_calibration_unrepresented_in_first_wave'
  if values:covered+=prior['population']
  else:values=prior['sample_cpu_seconds']
  n=prior['population'];estimate=n*statistics.mean(values)/3600;point+=estimate;low+=n*min(values)/3600;high+=n*max(values)/3600
  strata.append({'stratum':prior['stratum'],'population':n,'source':source,'sample_n':len(values),'sample_allocated_cpu_seconds':values,'projected_cpu_hours':estimate})
 allowance=original['planning_allowance_fraction'];planning=point*(1+allowance)
 result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'snapshot_utc':snapshot['utc'],'cohort_sha256':sha(D/'cohort.json'),
  'original_calibration_sha256':sha(D/'cost-gate.json'),'snapshot_sha256':sha(D/'latest-status.json'),'decision':'stop_cost_gate' if planning>6000 else 'continue',
  'measured_first_wave_n':27,'all_first_wave_terminal':True,'first_wave_allocated_cpu_hours':sum(r['allocated_cpu_seconds'] for r in measured)/3600,
  'population_with_current_stratum_measurement':covered,'denominator':len(c['rows']),'population_weighted_cpu_hours':point,'planning_allowance_fraction':allowance,
  'planning_total_cpu_hours':planning,'engineering_sensitivity_range_cpu_hours':[low,high*(1+allowance)],'cost_gate_cpu_hours':6000,'strata':strata,'measurements':measured,
  'limitations':'Not a statistical confidence interval. First 27 production tasks are a fixed nonrandom cohort. Five represented strata use complete current allocated runtimes; four unrepresented strata retain original calibration values and may understate current I/O waiting. Timing is conditional on this observed execution regime. All 27 tasks are included, including the slowest; no incomplete task is discarded. This is a cost reassessment, not a correction to thermodynamic predictions.'}
 p=D/'cost-reassessment-first-wave.json';assert not p.exists(),'Never overwrite a recorded cost decision'
 p.write_text(json.dumps(result,indent=2)+'\n')
 (D/'first-wave-terminal-status.json').write_text(json.dumps(snapshot,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k not in ['strata','measurements']},indent=2))
 return result
if __name__=='__main__':main()
