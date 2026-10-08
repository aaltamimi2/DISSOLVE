"""A-13: read-only scheduler monitoring, scp returns, local owner-policy verification (D-IDENT). No submission capability.
Copied from watch_halogen.py, over every submitted chunk; --once runs one pass and exits (the coverage lane's loop calls
it each wake), without it the watcher polls every 180 s like the A-11 one.

    ~/.venvs/cosmo-logp/bin/python scripts/watch_coverage.py [--once]"""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import fcntl,hashlib,json,re,subprocess,sys,time
from pathlib import Path
from euler_transport import run
from identity_campaign import verify
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/coverage-v1';REMOTE='~/plastchem-euler/coverage-v1'
ONCE='--once' in sys.argv
collector_lock=(P/'collector.lock').open('a');fcntl.flock(collector_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
DEST=Path('/mnt/r/plastchem-euler/coverage-v1/results');DEST.mkdir(parents=True,exist_ok=True)
TERMINAL={'COMPLETED','FAILED','TIMEOUT','OUT_OF_MEMORY','CANCELLED','NODE_FAIL','PREEMPTED','BOOT_FAIL','DEADLINE'}
def chunk_models():
    return {d.name:json.loads((d/'manifest.json').read_text())['molecules'] for d in sorted(P.glob('[cr][0-9][0-9]')) if (d/'manifest.json').exists()}
ledger_path=P/'retrieved.json';ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
remote_records_path=P/'remote-records.json';remote_records=json.loads(remote_records_path.read_text()) if remote_records_path.exists() else {}
since=0
if (P/'latest-snapshot.json').exists():since=json.loads((P/'latest-snapshot.json').read_text())['epoch']
if os.environ.get('PLASTCHEM_COLLECTOR_FULL_RECONCILE')=='1':since=0
def write(path,obj):
    if str(path).startswith('/mnt/r/'):
        path.write_text(json.dumps(obj,indent=2)+'\n');return
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2)+'\n');tmp.replace(path)
while True:
    try:
        models=chunk_models()
        response=run('ssh',['euler',f'python3 {REMOTE}/collect_coverage_remote.py {since}'],capture_output=True,text=True)
        if response.returncode:raise RuntimeError(response.stderr[:500])
        snap=json.loads(response.stdout)
        remote_records.update(snap['changed_records']);write(remote_records_path,remote_records)
        # Commit the cursor only after its records are durable. A failed write or
        # temporarily unreadable remote record must be replayed next pass.
        if snap.get('read_errors'):
            write(P/'remote-read-errors.json',{'epoch':snap['epoch'],'errors':snap['read_errors']})
            raise RuntimeError('Remote record read errors; polling cursor retained for replay')
        write(P/'latest-snapshot.json',snap);since=snap['epoch']
        accounting={line.split('|')[0]:line.split('|') for line in snap['sacct'].splitlines() if len(line.split('|'))>=11}
        known={}
        for group,array in snap['groups'].items():
            selected=set(snap.get('array_indices',{}).get(group,[m['array_index'] for m in models[group]]))
            for mol in models[group]:
                if mol['array_index'] in selected:known[f"{array}_{mol['array_index']}"]=mol
        fresh=[]
        for task,mol in known.items():
            key=mol['inchikey']
            retry_registry=P/'active-retries.json'
            if retry_registry.exists() and key in json.loads(retry_registry.read_text()).get('entries',{}):continue
            r=remote_records.get(key);acct=accounting.get(task)
            terminal=acct and acct[3].split()[0] in TERMINAL
            if not r and not terminal:continue
            r=dict(r or {'input':mol,'inchikey':key,'scope':'coverage_campaign','group':mol['group'],'cpu_model':None,'node':acct[8] if acct else None,'dft_ran':False})
            if terminal and acct[3].split()[0]!='COMPLETED' and r.get('status') not in ['failed','converged_identity_pending']:
                r.update(status='failed',failure_mode='slurm_'+acct[3].lower().replace(' ','_'),error='Scheduler terminal failure; retain elapsed cost and available diagnostics')
            if terminal and acct[3].split()[0]=='COMPLETED' and r.get('status') not in ['converged_identity_pending','converged','failed']:
                r.update(status='failed',failure_mode='completed_without_result',error='Scheduler completion without a normal ORCA terminal record')
            if terminal and acct[3].split()[0]=='TIMEOUT':
                r['original_failure_mode']=r.get('failure_mode')
                r.update(status='failed',failure_mode='slurm_timeout',execution_outcome='time_limit',retry_required=True)
            elif r.get('failure_mode') in ['walltime_censored','scheduler_signal_10']:
                r.update(execution_outcome='time_limit',retry_required=True)
                r['time_limit_note']='Runner elapsed guard or configured Slurm USR1 pre-time-limit warning; preserve actual Slurm state separately'
            if acct:
                r['slurm_accounting']={'state':acct[3],'exit_code':acct[4],'elapsed_seconds':int(acct[5]) if acct[5] else None,'node_list':acct[8]}
                batch=accounting.get(task+'.batch')
                if batch and batch[7]:r['slurm_accounting']['maxrss_kib']=float(batch[7].rstrip('K'));r['slurm_accounting']['maxrss_source']='sacct batch step'
            # Already verified records keep their owner-policy assessment; accounting may improve later.
            local=P/'records'/f'{key}.json'
            if key in ledger:
                saved=json.loads(local.read_text())
                if saved.get('retry_round'):continue  # coverage_retry.py owns retried records
                if r.get('execution_outcome')=='time_limit' and saved.get('status')!='converged':
                    saved.update(execution_outcome='time_limit',retry_required=True,failure_mode=r.get('failure_mode'),original_failure_mode=r.get('original_failure_mode'),slurm_accounting=r.get('slurm_accounting'));write(local,saved)
                if r.get('slurm_accounting')!=saved.get('slurm_accounting'):
                    saved['slurm_accounting']=r.get('slurm_accounting');write(local,saved)
                continue
            if r.get('status') in ['converged_identity_pending','failed']:
                fresh.append((key,r,key in remote_records))
            else:write(local,r)
        if fresh:
            subprocess.run(['df','-h','/','/mnt/r'],check=True,stdout=subprocess.DEVNULL)
            fetch=[key for key,r,exists in fresh if exists]
            for start in range(0,len(fetch),50):  # bounded argument lists
                result=run('scp',['-rq',*[f'euler:plastchem-euler/coverage-v1/returns/{key}' for key in fetch[start:start+50]],str(DEST)],capture_output=True,text=True)
                if result.returncode:raise RuntimeError('scp failed: '+result.stderr[:500])
            for key,r,exists in fresh:
                p=DEST/key;p.mkdir(exist_ok=True)
                if r.get('status')=='converged_identity_pending':
                    r['dft_status']='converged'
                    try:
                        for name,sha in [('surface.orcacosmo',r['surface_sha256']),*[(stage+'.inp',info['input_sha256']) for stage,info in r['stages'].items()]]:
                            assert hashlib.sha256((p/name).read_bytes()).hexdigest()==sha,name+' digest mismatch'
                        r.update(verify(p/'optimized.xyz',key,r['input']['smiles']))
                        r['identity_authority']=json.loads((P/'policy.json').read_text())
                        if not r['identity_verified']:raise ValueError('No perception engine produced the required first-block match')
                        r.update(status='converged',archive_path=str(p))
                    except Exception as exc:r.update(status='failed',failure_mode='return_integrity_or_connectivity',error=str(exc),identity_verified=False)
                write(p/'result.json',r)
                r['returned_bytes']=sum(f.stat().st_size for f in p.iterdir() if f.is_file());write(P/'records'/f'{key}.json',r)
                ledger[key]={'status':r['status'],'surface_sha256':r.get('surface_sha256'),'snapshot_utc':snap['utc']};write(ledger_path,ledger)
        subprocess.run([sys.executable,str(ROOT/'scripts/summarize_coverage.py')],check=True,stdout=subprocess.DEVNULL)
        summary=json.loads((P/'summary.json').read_text())
        brief={'utc':snap['utc'],'counts':summary['counts'],'array_ids':snap['groups'],'new_returns':len(fresh)}
        with (P/'monitor-history.jsonl').open('a') as f:f.write(json.dumps(brief)+'\n')
        print(json.dumps(brief),flush=True)
        if not snap['squeue'].strip() and all(summary['counts'][s]==0 for s in ['running','not_yet_run','awaiting_verification','retry_pending']):
            print('COVERAGE_ALL_SUBMITTED_TARGETS_DISPOSED',flush=True)
            if not ONCE:break
    except Exception as exc:
        print(json.dumps({'monitor_error':str(exc),'epoch':time.time()}),flush=True)
        if ONCE:sys.exit(1)
    if ONCE:break
    # run() reads/enforces persistent backoff under the shared transport lock.
    # Do not read its live state without that lock during the idle interval.
    time.sleep(180)
