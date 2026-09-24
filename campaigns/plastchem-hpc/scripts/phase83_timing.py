"""Read-only timing drift diagnostic; includes unfinished first-wave tasks.

Never replaces the authorized calibration or silently excludes slow tasks.
"""
import json,statistics
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/phase83-v1')
def main():
 c=json.loads((D/'cohort.json').read_text());g=json.loads((D/'cost-gate.json').read_text());s=json.loads((D/'latest-status.json').read_text())
 indices=[i for i in range(len(c['rows'])) if i not in c['calibration_indices']][:27]
 means={r['stratum']:statistics.mean(r['sample_cpu_seconds']) for r in g['strata']};rows=[]
 for line in s['accounting'].splitlines():
  fields=line.split('|')
  if not fields[0].startswith('68234_') or '.' in fields[0]:continue
  task=fields[0].split('_')[1]
  if not task.isdigit() or int(task)>=27:continue
  index=indices[int(task)]
  rows.append({'task':int(task),'index':index,'state':fields[1],'allocated_seconds_so_far':int(fields[2])*int(fields[3]),'calibration_stratum_mean_seconds':means[c['rows'][index]['stratum']]})
 assert len(rows)==27 and len({r['task'] for r in rows})==27
 ratio=sum(r['allocated_seconds_so_far'] for r in rows)/sum(r['calibration_stratum_mean_seconds'] for r in rows)
 out={'utc':s['utc'],'cohort':'First 27 production tasks fixed at submission, including all still-running tasks','completed':sum(r['state']=='COMPLETED' for r in rows),'denominator':27,
  'allocated_cpu_hours_so_far':sum(r['allocated_seconds_so_far'] for r in rows)/3600,'ratio_to_matched_calibration_strata_lower_bound':ratio,
  'projected_total_if_uniform_slowdown_persists_without_allowance':g['population_weighted_cpu_hours']*ratio,'with_original_25_percent_allowance':g['authorized_cpu_hours']*ratio,
  'interpretation':'Timing diagnostic, not a replacement population calibration: incomplete tasks are lower bounds, first 27 are not a representative population sample, and a uniform slowdown may not persist. Original calibration gate remains recorded separately. Reassess with all 27 terminal, not just fastest completions. Interruptions require separate reconciliation.',
  'tasks':rows}
 if out['with_original_25_percent_allowance']>6000:out['alert']='Persistent slowdown sensitivity exceeds the original cost gate; operator reassessment required'
 p=D/'production-timing-diagnostic.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(out,indent=2)+'\n');tmp.replace(p)
 print(json.dumps({k:v for k,v in out.items() if k!='tasks'},indent=2))
if __name__=='__main__':main()
