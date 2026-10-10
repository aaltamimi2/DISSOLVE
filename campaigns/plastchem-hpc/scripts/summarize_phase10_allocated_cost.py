"""Reconcile terminal task allocations, excluding duplicate Slurm step rows."""
import collections
import csv
import datetime
import hashlib
import json
import re
from pathlib import Path

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')


def main():
    paths=[D/'production-terminal-observation.json', D/'calibration-v2-observation.json',
        R/'reports/phase10-2026-09-24/calibration-v1-control-failure.json',
        R/'reports/phase10-2026-09-24/calibration-submission.json',
        R/'reports/phase10-2026-09-24/calibration-v2-submission.json']
    prod,cal,failed,request1,request2=[json.loads(p.read_text()) for p in paths]
    assert '--cpus-per-task=1' in request1['command'] and '--cpus-per-task=1' in request2['command']
    helpers={json.loads(p.read_text())['job_id'] for p in D.glob('production-retry-tail*-submission.json')}
    expected={prod['job_id']:58,cal['job_id']:9,request1['job_id']:9,**{j:3 for j in helpers}}
    assert len(helpers)==6 and sum(expected.values())==94
    rows=[];seen=set()
    for block,mode in [(prod['accounting'],'production'),(cal['accounting'],'calibration'),(failed['accounting'],'first_attempt')]:
        for line in block.splitlines():
            fields=line.split('|');match=re.fullmatch(r'(\d+)_(\d+)',fields[0])
            if not match:continue
            job,index=match.groups();assert job in expected and fields[0] not in seen
            seen.add(fields[0]);assert int(index)<expected[job]
            cpus=int(fields[3]) if mode=='production' else 1
            assert cpus==1 and fields[1]==('FAILED' if mode=='first_attempt' else 'COMPLETED')
            group='tail_helpers' if job in helpers else mode
            rows.append(dict(job_id=fields[0],group=group,state=fields[1],elapsed_seconds=int(fields[2]),
                allocated_cpus=cpus,allocated_CPU_seconds=int(fields[2])*cpus,
                cpu_evidence='scheduler AllocCPUS' if mode=='production' else 'pinned single-CPU submission request'))
    assert collections.Counter(r['job_id'].split('_')[0] for r in rows)==expected
    seconds=collections.Counter()
    for row in rows:seconds[row['group']]+=row['allocated_CPU_seconds']
    assert seconds['production']+seconds['tail_helpers']==round(prod['allocated_CPU_h']*3600)
    assert seconds['first_attempt']==524 and seconds['calibration']==5117
    out=D/'terminal-cost-accounting';out.mkdir(exist_ok=True)
    with (out/'tasks.csv').open('w',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=list(rows[0]));w.writeheader();w.writerows(sorted(rows,key=lambda r:r['job_id']))
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),tasks=94,completed_tasks=85,
        failed_initial_control_tasks=9,production_or_helper_failures=0,
        allocated_CPU_h_by_group={k:v/3600 for k,v in seconds.items()},
        total_extension_allocated_CPU_h=sum(seconds.values())/3600,
        scope='Extension calibration and production allocations, including initial failed control checks and paused originals. Excludes pre-existing ORCA surfaces, separate solvent-library DFT and local/transport processing. Slurm .batch/.extern rows are not counted again.',
        pins={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        tasks_sha256=hashlib.sha256((out/'tasks.csv').read_bytes()).hexdigest())
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
