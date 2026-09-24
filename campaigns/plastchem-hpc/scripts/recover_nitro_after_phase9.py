"""Resume the authorized nitro retry only after A-9 A and B are queued.

Submit held after deterministic reconciliation; archive original work, register
the attempt, then permit the shared controller to release it strictly last.
No calculation recipe or frozen thermodynamic cohort is changed.
"""
import datetime, fcntl, hashlib, json, os, shutil, subprocess, sys, time
from pathlib import Path
from euler_transport import run

R=Path(__file__).resolve().parents[1];P=R/'state/polymer-v1';S=P/'nitro-retry1'
D=Path('/mnt/r/plastchem-euler/polymer-v1/nitro-retry1')
A=D.parents[1]/'phase9-v1';B=D.parents[1]/'phase9-solvent-library-v1'
PYTHON='/home/aaltamimi2/.venvs/cosmo-logp/bin/python'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()


def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2)+'\n');tmp.replace(path)


def call(kind,args,**kwargs):
    p=run(kind,args,capture_output=True,text=True,timeout=300,**kwargs)
    assert p.returncode==0,p.stderr
    return p.stdout


def ensure_collector():
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:args=(proc/'cmdline').read_bytes().decode().split('\0')
        except (OSError,UnicodeError):continue
        if any(Path(arg).name=='watch_nitro_retry.py' for arg in args if arg):return int(proc.name)
    log=(R/'logs/nitro-retry-collection.jsonl').open('a')
    child=subprocess.Popen([PYTHON,'-u',str(R/'scripts/watch_nitro_retry.py')],cwd=R,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    write(S/'collector-process.json',dict(pid=child.pid,utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    return child.pid


def once():
    if (A/'production-cost-stop.json').exists():return dict(status='deferred_A9_cost_stop')
    if not (A/'production-submission.json').exists() or not (B/'submission.json').exists():
        return dict(status='waiting_for_A9_A_and_B_launch')
    assert json.loads((A/'production-submission.json').read_text())['launch_utc']
    if (S/'ready-for-release.json').exists():
        marker=json.loads((S/'ready-for-release.json').read_text())
        assert (P/'active-retries.json').exists()
        assert str(json.loads((P/'active-retries.json').read_text())['job_id'])==str(marker['job_id'])
        return dict(status='registered_held_until_earlier_arrays_drain',job_id=marker['job_id'],collector_pid=ensure_collector())
    manifest=json.loads((S/'body/manifest.json').read_text());assert len(manifest['molecules'])==7
    for mol in manifest['molecules']:
        v=mol['restart_provenance'];assert v['geometry_identity']['identity_verified']
        assert sha(S/'prepared'/mol['entry_id']/'input.xyz')==v['xyz_sha256']
    # Verify the actual active cap-controller version before introducing an array.
    remote="from pathlib import Path;import hashlib;print(hashlib.sha256((Path.home()/'plastchem-euler/phase9-v1/phase9_throttle_remote.py').read_bytes()).hexdigest())"
    assert call('ssh',['euler','python3 -'],input=remote).strip()==sha(R/'scripts/phase9_throttle_remote.py')
    assert 'nitro_root' in (R/'scripts/phase9_throttle_remote.py').read_text()
    call('ssh',['euler','mkdir -p ~/plastchem-euler/polymer-v1/nitro-retry1'])
    call('scp',['-rq',str(S/'prepared'),str(S/'body'),str(S/'polymer_runner.py'),str(S/'body.sbatch'),'euler:plastchem-euler/polymer-v1/nitro-retry1/'])
    receipt=json.loads(call('ssh',['euler','python3 -'],input=(R/'scripts/submit_nitro_retry_remote.py').read_text()))
    write(S/'submission.json',receipt);write(D/'submission.json',receipt)
    job=receipt['job_id'];entries={};archives={}
    for mol in manifest['molecules']:
        key=mol['entry_id'];v=mol['restart_provenance'];dest=D/'original-attempts'/key;dest.mkdir(parents=True,exist_ok=True)
        names=[f['name'] for f in v['files'] if not (dest/f['name']).exists() or sha(dest/f['name'])!=f['sha256']]
        if names:call('scp',['-q',*[f'euler:plastchem-euler/polymer-v1/runs/{key}/{n}' for n in names],str(dest)])
        pins={}
        for f in v['files']:
            p=dest/f['name'];assert p.stat().st_size==f['bytes'] and sha(p)==f['sha256']
            pins[f['name']]=dict(bytes=f['bytes'],sha256=f['sha256'])
        original=dict(v['original_record'],execution_outcome='time_limit',retry_required=True,accounting_at_retry=v['accounting'],outcome_label='Time-limit interruption, not chemistry failure; retain actual Slurm state')
        write(dest/'record.json',original);write(dest/'archive-manifest.json',pins)
        entries[key]=dict(original_job=v['task'],record_path=str(dest/'record.json'),original_elapsed_seconds=int(v['accounting'][2]),restart_source=v['restart_source'],source_name=v['source_name'],restart_xyz_sha256=v['xyz_sha256'])
        archives[key]=dict(manifest=str(dest/'archive-manifest.json'),sha256=sha(dest/'archive-manifest.json'))
    registry=dict(job_id=job,entries=entries,priority='last_after_A9_A_B_large_polymers_and_tier2',walltime='48:00:00',cap=64,retry_throttle=7,utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    if (P/'active-retries.json').exists():assert json.loads((P/'active-retries.json').read_text())['job_id']==job
    write(P/'active-retries.json',registry);write(D/'restart-provenance.json',registry)
    for mol in manifest['molecules']:
        p=P/'records'/(mol['entry_id']+'.json')
        prior=json.loads(p.read_text()) if p.exists() else {}
        if prior.get('active_attempt')=='nitro-retry1':continue
        write(p,dict(entry_id=mol['entry_id'],input=mol,status='not_yet_run',active_attempt='nitro-retry1',previous_attempt=entries[mol['entry_id']],retry_job_id=job,restart_provenance=mol['restart_provenance']))
    marker=dict(job_id=job,registered_entries=7,original_archives_verified=True,archives=archives,utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    write(S/'ready-for-release.pending.json',marker)
    call('scp',[str(S/'ready-for-release.pending.json'),'euler:plastchem-euler/polymer-v1/nitro-retry1/ready-for-release.pending.json'])
    call('ssh',['euler','python3 -'],input="from pathlib import Path\np=Path.home()/'plastchem-euler/polymer-v1/nitro-retry1';(p/'ready-for-release.pending.json').replace(p/'ready-for-release.json')\n")
    write(S/'ready-for-release.json',marker);write(D/'ready-for-release.json',marker)
    pid=ensure_collector()
    subprocess.run([sys.executable,str(R/'scripts/summarize_polymer.py')],check=True,capture_output=True)
    event=dict(event='nitro_retries_queued_held_registered',job_id=job,utc=marker['utc'],original_archives_verified=True,conformers=7,cap=64,collector_pid=pid)
    with (R/'logs/campaign-events.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
    report=R/'reports/nitro-recovery-2026-09-24';write(report/'submission.json',receipt);write(report/'archive-registration.json',marker)
    text=f"\n## Registered retry submission\n\nAt {marker['utc']}, array **{job}** was reconciled, submitted held and registered for all seven salvaged conformers. Original diagnostic files (including GBW, trajectories and optimisation output where present) were copied with scp and verified file by file. Part A had launched and B was queued before this submission. The shared cap remains 64; each retry requests one Milan CPU, 4 GiB and 48 hours. The controller releases this array only after all earlier arrays drain. Collector PID at registration: {pid}. This is a queued retry, not convergence or inclusion in the frozen thermodynamic release.\n"
    p=report/'REPORT.md'
    if '## Registered retry submission' not in p.read_text():p.write_text(p.read_text()+text)
    shutil.copyfile(p,D/'REPORT.md')
    return dict(status='registered_held_until_earlier_arrays_drain',job_id=job,collector_pid=pid)


def main():
    lock=(S/'recovery.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    write(S/'recovery-process.json',dict(pid=os.getpid(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    while True:
        try:
            status=once();print(json.dumps(status),flush=True)
            if status['status']!='waiting_for_A9_A_and_B_launch':return
        except Exception as exc:
            write(S/'recovery-error.json',dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),error=str(exc)))
            print(json.dumps(dict(error=str(exc))),flush=True)
            if '--watch' not in sys.argv:raise
        if '--watch' not in sys.argv:return
        for _ in range(6):time.sleep(10)


if __name__=='__main__':main()
