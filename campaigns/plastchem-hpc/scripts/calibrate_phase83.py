"""Population-weighted actual Slurm CPU cost; no scientific or scheduler mutations."""
import collections,datetime,json,statistics,hashlib
from pathlib import Path
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase83-v1')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 c=json.loads((D/'cohort.json').read_text());s=json.loads((R/'state/phase83-v1/latest.json').read_text());done=s['completed']
 assert not s['task_failures'],s['task_failures']
 accounting={}
 for line in s['accounting'].splitlines():
  parts=line.split('|');job=parts[0]
  if '.' in job or '_' not in job or not job.split('_')[-1].isdigit():continue
  array,task=job.split('_');accounting[(array,int(task))]=dict(state=parts[1],elapsed_seconds=int(parts[2]),cpus=int(parts[3]))
 samples={};strata=[];statuses=collections.Counter();peak=0
 for task,idx in enumerate(c['calibration_indices']):
  key=f'{idx:05d}';assert key in done,('Calibration still incomplete',idx)
  a=accounting[('68169',task)];assert a['state']=='COMPLETED',(task,a)
  row=done[key];assert len(row['lle_statuses'])==64 and len(row['outputs'])==10
  assert row['signature']['cohort_sha256']==sha(D/'cohort.json')
  original=s.get('initial_attempts',{}).get(key,row)
  elapsed=a['elapsed_seconds']*a['cpus'];initial_cpu=elapsed;recovery_cpu=0;assert elapsed>=original['wall_seconds']-1
  repair=s.get('calibration_repair')
  if repair and idx in repair.get('indices',[]):
   recovery=accounting[(repair['job_id'],repair['indices'].index(idx))];assert recovery['state']=='COMPLETED',recovery
   assert key in s['initial_attempts'],'Original calibration must be retained for cost accounting'
   recovery_cpu=recovery['elapsed_seconds']*recovery['cpus'];elapsed+=recovery_cpu
  recovered=bool(recovery_cpu)
  samples[idx]={'cpu_seconds':elapsed,'initial_cpu_seconds':initial_cpu,'recovery_cpu_seconds':recovery_cpu,
   'partition_seconds':original['partition_seconds']+(row['partition_seconds'] if recovered else 0),
   'lle_seconds':original['lle_seconds']+(row['lle_seconds'] if recovered else 0),'inchikey':row['inchikey']}
  statuses.update(row['lle_statuses'].values());peak=max(peak,row['execution']['peak_rss_kib'],original['execution']['peak_rss_kib'])
 point=low=high=0.;by_tier=collections.Counter()
 for name,group in c['strata'].items():
  values=[samples[i]['cpu_seconds'] for i in group['selected_indices']];n=group['population'];estimate=n*statistics.mean(values)/3600
  point+=estimate;low+=n*min(values)/3600;high+=n*max(values)/3600;by_tier['tier2' if name=='tier2' else 'main']+=estimate
  strata.append({'stratum':name,'population':n,'sample_n':len(values),'sample_cpu_seconds':values,'projected_cpu_hours':estimate})
 # Allow 25% above the stratified total for variability/overhead outside this deliberate sample.
 authorized=point*1.25
 result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cohort_sha256':sha(D/'cohort.json'),
  'status_snapshot_sha256':sha(R/'state/phase83-v1/latest.json'),'decision':'continue' if authorized<=6000 else 'stop_cost_gate',
  'measured_calibration_cpu_hours':sum(v['cpu_seconds'] for v in samples.values())/3600,
  'population_weighted_cpu_hours':point,'planning_allowance_fraction':.25,'authorized_cpu_hours':authorized,
  'engineering_range_cpu_hours':[low,high*1.25],'range_note':'Weighted sample minima to weighted maxima plus 25%; engineering sensitivity envelope, not a statistical confidence interval. Deliberate sample; unseen outliers remain possible.',
  'by_tier_cpu_hours_without_allowance':dict(by_tier),'strata':strata,'samples':samples,'lle_status_counts':dict(statuses),'peak_rss_kib':peak,
  'scope_counts':c['full_grid'],'cost_gate_cpu_hours':6000,'staging_recovery_cpu_included':bool(s.get('calibration_repair'))}
 (D/'cost-gate.json').write_text(json.dumps(result,indent=2)+'\n')
 report=R/'reports/phase83-2026-09-23/REPORT.md';text=report.read_text();start=text.index('## Cost gate');end=text.index('## Reproduction',start)
 paragraph=f'''## Cost gate

The measured 27-contaminant calibration consumed **{result['measured_calibration_cpu_hours']:.2f} allocated CPU-hours**, including Slurm task startup overhead. Population weighting within the nine strata projects **{point:.1f} CPU-hours** for the entire requested grid; adding 25% planning headroom gives a calibrated planning total of **{authorized:.1f} CPU-hours**. This is **{'within' if authorized<=6000 else 'above'}** the 6,000 CPU-hour gate. Decision: **{result['decision']}**.

The engineering sensitivity envelope is {low:.1f}–{high*1.25:.1f} CPU-hours (weighted sample minima through weighted sample maxima plus headroom), not a statistical confidence interval. Main-tier estimate before allowance: {by_tier['main']:.1f} CPU-hours; accepted tier2: {by_tier['tier2']:.1f}. Peak observed task RSS: {peak/1024/1024:.3f} GiB. LLE outcomes across the 1,728 calibration systems: `{dict(statuses)}`. All executed costs, including failed scientific statuses, enter the estimate; no incomplete Slurm task was silently omitted.

Exact samples, strata, measurements and pinned evidence are in `/mnt/r/plastchem-euler/phase83-v1/cost-gate.json`.

'''
 report.write_text(text[:start]+paragraph+text[end:]);(D/'REPORT.md').write_text(report.read_text())
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
