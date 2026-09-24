"""Read exact terminal failures, recover only observed binary-grid nonconvergence.

Deterministic per-index names and squeue+sacct reconciliation live in the submit
routine. Each original index has at most one registered recovery here. Repeated
or unknown failures are surfaced for inspection, never silently retried.
"""
import datetime,json,re,subprocess
from pathlib import Path
D=Path(__file__).resolve().parent

def call(args):
    p=subprocess.run(args,capture_output=True,text=True)
    assert p.returncode==0,(args,p.stderr)
    return p.stdout


def main():
    gate=json.loads((D/'recovery-gate-fresh-passed.json').read_text())
    assert gate['status']=='passed' and gate['systems']==2240 and gate['mismatch_count']==0
    original=str(json.loads((D/'production-submission.json').read_text())['job_id'])
    receipts=[json.loads(p.read_text()) for p in D.glob('production-retry-*-submission.json')]
    registered={i for r in receipts if r.get('purpose')!='tail_split' for i in r['indices']}
    queue=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%T|%j'])
    live={s.split('|')[0] for s in queue.splitlines() if s}
    accounting=call(['sacct','-nP','-j',','.join([original]+[r['job_id'] for r in receipts]),'--format=JobID%40,State%30,ElapsedRaw,ExitCode'])
    states={s.split('|')[0]:s.split('|')[1] for s in accounting.splitlines() if s and '.' not in s.split('|')[0]}
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),queue=queue,accounting=accounting,submitted=[],unhandled=[],already_registered=sorted(registered))
    for task,state in states.items():
        m=re.fullmatch(original+r'_(\d+)',task)
        if not m or state not in ['FAILED','TIMEOUT','OUT_OF_MEMORY','NODE_FAIL','CANCELLED']:continue
        index=int(m.group(1))
        if index in registered:continue
        if task in live:continue
        error=D/'logs'/f'production-{task}.err'
        if state!='FAILED' or not error.exists() or not error.read_text().rstrip().endswith('ValueError: COSMOspace did not converge for binary grid'):
            result['unhandled'].append(dict(task=task,state=state,reason='Not the authorized exact failure mode'));continue
        attempt=f'{index+10:02d}'
        raw=call(['python3',str(D/'submit_phase9_recovery_v2_remote.py'),attempt,str(index)])
        receipt=json.loads(raw);result['submitted'].append(receipt)
    for receipt in receipts:
        if receipt.get('purpose')=='tail_split':continue
        for i in receipt['indices']:
            task=receipt['job_id']+'_'+str(i);state=states.get(task)
            if state in ['FAILED','TIMEOUT','OUT_OF_MEMORY','NODE_FAIL','CANCELLED']:
                result['unhandled'].append(dict(task=task,state=state,reason='Recovery itself failed; no automatic repeated retry'))
    controller = D / 'phase9_throttle_remote_v4.py'
    active = D / 'active-throttle-controller.json'
    if active.exists():
        import hashlib
        selected = json.loads(active.read_text())
        assert selected['filename'] in ['phase9_throttle_remote_v4.py', 'phase9_throttle_remote_v5.py']
        controller = D / selected['filename']
        assert hashlib.sha256(controller.read_bytes()).hexdigest() == selected['sha256']
    result['controller']=json.loads(call(['python3',str(controller)]))
    assert result['controller']['status']=='verified',result['controller'].get('error')
    print(json.dumps(result))

if __name__=='__main__':main()
