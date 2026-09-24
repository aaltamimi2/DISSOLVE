"""Read-only A-10 progress; remote seals are not local release verification."""
import collections
import datetime
import json
import subprocess
from pathlib import Path

D=Path.home()/'plastchem-euler/phase10-v1'


def straggler_candidates(chunks):
    """Retain duration outliers and expose later-start finish-time tails."""
    import statistics
    measured=[c for c in chunks if c['elapsed_seconds'] and c['LLE_seals']]
    mature=[c for c in measured if c['elapsed_seconds']>=3600]
    if not mature:return []
    total=lambda c:c['elapsed_seconds']*c['LLE_expected']/c['LLE_seals']
    duration_median=statistics.median(total(c) for c in mature)
    live=[c for c in measured if c['scheduler_state']=='RUNNING' and not c['complete'] and 'tail_handoff' not in c]
    if not live:return []
    remaining=lambda c:max(0.,total(c)-c['elapsed_seconds'])
    remaining_median=statistics.median(remaining(c) for c in live)
    candidates=[]
    for c in live:
        if c['elapsed_seconds']<3600:continue
        reasons=[]
        if total(c)>1.5*duration_median:reasons.append('total_duration')
        if remaining(c)>1.5*remaining_median:reasons.append('remaining_time_after_late_start')
        if reasons:
            candidates.append(dict(chunk=c['chunk'],projected_seconds=total(c),
                comparison_median_seconds=duration_median,projected_remaining_seconds=remaining(c),
                comparison_median_remaining_seconds=remaining_median,reasons=reasons))
    return candidates


def call(args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=45)
    assert p.returncode==0,(args,p.stderr)
    return p.stdout


def main():
    receipt=json.loads((D/'production-submission.json').read_text());job=receipt['job_id']
    helpers=[json.loads(p.read_text()) for p in sorted(D.glob('production-retry-tail*-submission.json'))]
    jobs=[job]+[r['job_id'] for r in helpers]
    assert len(jobs)==len(set(jobs))
    plan=json.loads((D/'production-plan.json').read_text())
    q=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%T|%C|%j|%R'])
    accounting=call(['sacct','-nP','-j',','.join(jobs),'--format=JobID%40,State%30,ElapsedRaw,AllocCPUS,MaxRSS,NodeList,ExitCode'])
    tasks={line.split('|')[0]:line.split('|') for line in accounting.splitlines()
           if line and '.' not in line.split('|')[0]}
    out=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),job_id=job,
        status='in_progress',queue=q,accounting=accounting,chunks=[],failures=[],
        scope='Remote checkpoint counts and scheduler observation; not collected or independently verified release counts')
    failure_states={'FAILED','TIMEOUT','OUT_OF_MEMORY','CANCELLED','NODE_FAIL','PREEMPTED'}
    for i,units in enumerate(plan['chunks']):
        task=f'{job}_{i}';folder=D/plan['output']/f'{i:04d}';a=tasks.get(task)
        state=a[1] if a else 'ACCOUNTING_NOT_YET_VISIBLE'
        row=dict(chunk=i,units=len(units),scheduler_state=state,elapsed_seconds=int(a[2]) if a else None,
            partition_seals=len(list((folder/'partition').glob('*.sha256.json'))),
            LLE_seals=len(list((folder/'lle').glob('*.sha256.json'))),LLE_expected=39*len(units),
            started=[json.loads(p.read_text()) for p in folder.glob('started-*.json')],complete=(folder/'complete.json').exists())
        if row['complete']:
            f=json.loads((folder/'complete.json').read_text())
            assert f['unit_ids']==[u['id'] for u in units]
            assert len(f['lle_statuses'])==39*len(units)
            row.update(LLE_statuses=dict(collections.Counter(f['lle_statuses'].values())),
                       cpu_model=f['execution']['cpu_model'],peak_rss_kib=f['peak_rss_kib'],wall_seconds=f['wall_seconds'])
        if state.split()[0] in failure_states:
            error=D/'logs'/f'production-{job}_{i}.err'
            out['failures'].append(dict(task=task,state=state,
                error_tail=error.read_text()[-6000:] if error.exists() else None))
        handoff=D/f'tail-{i:04d}-v1/handoff-state.json'
        if handoff.exists():row['tail_handoff']=json.loads(handoff.read_text())
        out['chunks'].append(row)
    for receipt in helpers:
        for i in receipt['indices']:
            task=f"{receipt['job_id']}_{i}";a=tasks.get(task)
            if a and a[1].split()[0] in failure_states:
                error=D/'logs'/f"tail-{receipt['job_id']}_{i}.err"
                out['failures'].append(dict(task=task,state=a[1],purpose='extension_recovery',
                    error_tail=error.read_text()[-6000:] if error.exists() else None))
    out.update(completed_chunks=sum(c['complete'] for c in out['chunks']),
        partition_molecules_remote=40+sum(c['partition_seals'] for c in out['chunks']),
        LLE_systems_remote=1560+sum(c['LLE_seals'] for c in out['chunks']),
        allocated_CPU_h=sum(int(v[2])*int(v[3]) for v in tasks.values() if len(v)>3 and v[3].isdigit())/3600)
    # Report potential stragglers only after one hour. Splitting requires an
    # exact ownership handoff; this observer cannot launch duplicate writers.
    out['potential_stragglers']=straggler_candidates(out['chunks'])
    running=[r.split('|') for r in q.splitlines() if r and r.split('|')[1]!='PENDING']
    out['research_running_CPUs']=sum(int(r[2]) for r in running)
    out['running_by_array']=dict(collections.Counter(r[0].split('_')[0] for r in running))
    if out['failures']:out['status']='inspection_required'
    elif out['completed_chunks']==len(plan['chunks']):out['status']='computation_complete_collection_pending'
    print(json.dumps(out))


if __name__=='__main__':main()
