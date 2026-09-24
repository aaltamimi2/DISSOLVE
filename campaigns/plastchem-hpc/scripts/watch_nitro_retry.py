"""Collect only the registered nitro retry; preserve original attempt and denominator."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import json,time,subprocess,hashlib,shlex
from pathlib import Path
from euler_transport import run
from bounded_geometry_identity import verify
R=Path(__file__).resolve().parents[1];P=R/'state/polymer-v1';S=P/'nitro-retry1';D=Path('/mnt/r/plastchem-euler/polymer-v1/nitro-retry1/results');D.mkdir(parents=True,exist_ok=True)
def write(p,x):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix('.tmp');t.write_text(json.dumps(x,indent=2)+'\n');t.replace(p)
config=json.loads((P/'active-retries.json').read_text());job=config['job_id'];models=json.loads((S/'body/manifest.json').read_text())['molecules'];done=set()
while len(done)<len(models):
 try:
  code="import json,subprocess;from pathlib import Path;r=Path.home()/'plastchem-euler/polymer-v1/nitro-retry1';print(json.dumps({'records':{p.parent.name:json.loads(p.read_text()) for p in (r/'runs').glob('*/result.json')},'sacct':subprocess.check_output(['sacct','-j',"+repr(job)+",'-nP','--units=K','--format=JobID,State,ElapsedRaw,MaxRSS,NodeList'],text=True)}))"
  q=run('ssh',['euler','python3 -c '+shlex.quote(code)],capture_output=True,text=True);assert q.returncode==0,q.stderr;snap=json.loads(q.stdout);write(S/'latest-snapshot.json',snap)
  acct={v[0]:v for line in snap['sacct'].splitlines() if len(v:=line.split('|'))>=5}
  for m in models:
   key=m['entry_id']
   if key in done:continue
   r=snap['records'].get(key);a=acct.get(job+'_'+str(m['array_index']));terminal=a and a[1].split()[0] in ['COMPLETED','FAILED','TIMEOUT','CANCELLED','OUT_OF_MEMORY','NODE_FAIL']
   if not r and not terminal:continue
   r=r or dict(input=m,entry_id=key,status='failed',failure_mode='slurm_'+a[1].lower())
   r.update(active_attempt='nitro-retry1',previous_attempt=config['entries'][key],restart_provenance=m['restart_provenance'])
   if a:r['slurm_accounting']={'state':a[1],'elapsed_seconds':int(a[2]),'node_list':a[4]}
   if (a and a[1]=='TIMEOUT') or r.get('failure_mode') in ['scheduler_signal_10','walltime_censored']:
    r.update(status='failed',execution_outcome='time_limit',failure_mode='slurm_timeout' if a and a[1]=='TIMEOUT' else r['failure_mode'],retry_required=True)
   if terminal:
    if r.get('status') not in ['converged_identity_pending','failed']:
     r.update(status='failed',failure_mode='completed_without_result' if a[1]=='COMPLETED' else 'slurm_'+a[1].lower())
    p=D/key
    if key in snap['records']:
     fetch=run('scp',['-rq','euler:plastchem-euler/polymer-v1/nitro-retry1/returns/'+key,str(D)],capture_output=True,text=True);assert fetch.returncode==0,fetch.stderr
    if r.get('status')=='converged_identity_pending':
     try:
      assert hashlib.sha256((p/'surface.orcacosmo').read_bytes()).hexdigest()==r['surface_sha256']
      for stage,info in r['stages'].items():assert hashlib.sha256((p/(stage+'.inp')).read_bytes()).hexdigest()==info['input_sha256']
      r.update(verify(p/'optimized.xyz',m['inchikey'],''));assert r['identity_verified'];r.update(status='converged',dft_status='converged',archive_path=str(p))
     except Exception as e:r.update(status='failed',failure_mode='return_integrity_or_connectivity',error=str(e))
    write(p/'result.json',r);done.add(key)
   write(P/'records'/(key+'.json'),r)
  subprocess.run(['python3',str(R/'scripts/summarize_polymer.py')],check=True,stdout=subprocess.DEVNULL)
  print(json.dumps({'epoch':time.time(),'job_id':job,'terminal':len(done),'denominator':len(models)}),flush=True)
 except Exception as e:print(json.dumps({'error':str(e),'epoch':time.time()}),flush=True)
 if len(done)<len(models):time.sleep(300)
