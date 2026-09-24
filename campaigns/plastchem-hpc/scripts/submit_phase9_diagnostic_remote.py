"""Reconciled A-9 diagnostic/gate submission only; no production or DFT path."""
import datetime,json,re,subprocess,sys
from pathlib import Path
D=Path.home()/'plastchem-euler/phase9-v1'
mode=sys.argv[1];assert mode in ['grid-probe','gate','chunk-probe','dilution-probe','genoa-gate']
name='contam-phase9-'+mode+('-v2' if mode=='genoa-gate' else '-v1')


def call(args):return subprocess.check_output(args,text=True)


q=call(['squeue','-h','-r','-u','aaltamimi2','--name='+name,'-o','%i|%T|%R'])
a=call(['sacct','-nP','-u','aaltamimi2','--starttime=2026-09-23','--name='+name,'--format=JobID,JobName%60,State,Elapsed,NodeList'])
r=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),name=name,squeue=q,sacct=a)
found={l.split('|')[0].split('_')[0].split('.')[0] for l in (q+'\n'+a).splitlines() if re.match(r'^\d',l)}
if found:
    assert len(found)==1;r.update(decision='existing_no_resubmission',job_id=next(iter(found)))
else:
    queue=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%T|%C|%R|%j']);r['queue_before']=queue
    rows=[l.split('|') for l in queue.splitlines()]
    assert all(x[3]=='(JobHeldUser)' for x in rows if x[0].startswith('68234_') and x[1]=='PENDING')
    reserve=0
    for job in {x[0].split('_')[0] for x in rows}:
        group=[x for x in rows if x[0].split('_')[0]==job]
        if job=='68234':reserve+=sum(x[1]!='PENDING' for x in group);continue
        detail=call(['scontrol','show','job',group[0][0],'-o']);caps=re.findall(r'ArrayTaskThrottle=(\d+)',detail)
        assert all(x[2]=='1' for x in group)
        reserve+=max(sum(x[1]!='PENDING' for x in group),min(len(group),int(caps[0]))) if caps else len(group)
    throttle=4 if mode=='gate' else 1
    assert reserve+throttle<=64,(reserve,throttle)
    cmd=['sbatch','--parsable','--job-name='+name,'--partition=research','--constraint=milan&cpu','--exclude=euler09,euler10','--nodes=1','--ntasks=1','--cpus-per-task=1','--mem=4G','--time=08:00:00','--no-requeue','--chdir='+str(D),'--output='+str(D/f'logs/{mode}-%A_%a.out'),'--error='+str(D/f'logs/{mode}-%A_%a.err')]
    if mode in ['gate','chunk-probe','genoa-gate']:
        planfile=mode+'-plan.json'
        plan=json.loads((D/planfile).read_text());assert not plan.get('selected_sizes_provisional_until_euler_sweep')
        assert json.loads((D/'sweep-result.json').read_text())['status']=='complete'
        assert json.loads((D/'grid-probe-result.json').read_text())['status']=='passed'
        cmd+=['--array=0-'+str(len(plan['chunks'])-1)+'%'+str(throttle)]
        worker='phase9_worker_cpu.py' if mode=='genoa-gate' else 'phase9_worker.py'
        entry=worker+' '+planfile+' "$SLURM_ARRAY_TASK_ID"'
        if mode=='genoa-gate':
            cmd[cmd.index('--constraint=milan&cpu')]='--constraint=genoa&cpu'
    else:entry='phase9_'+mode.replace('-','_')+'.py'
    cmd+=['--wrap=export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=../phase8-v1; ../phase8-v1/venv/bin/python -u '+entry]
    r['command']=cmd;(D/(mode+'-submission-unconfirmed.json')).write_text(json.dumps(r,indent=2)+'\n')
    p=subprocess.run(cmd,capture_output=True,text=True);r.update(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
    (D/(mode+'-submission-attempt.json')).write_text(json.dumps(r,indent=2)+'\n');assert p.returncode==0,r
    job=p.stdout.strip().split(';')[0];assert job.isdigit();r.update(decision='submitted',job_id=job,reserved_other_slots=reserve)
r['queue_after']=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%T|%C|%R'])
(D/(mode+'-submission.json')).write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
