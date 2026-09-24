"""One fixed-manifest Phase 2 molecule, serial frozen ORCA recipe, durable failure data."""
import hashlib,json,os,re,resource,signal,socket,subprocess,time,sys
from pathlib import Path
ROOT=Path.home()/'plastchem-euler/polymer-v1'
GROUP=sys.argv[1]
assert GROUP in ['body','large']
MANIFEST=ROOT/GROUP/'manifest.json'
manifest=json.loads(MANIFEST.read_text());index=int(os.environ['SLURM_ARRAY_TASK_ID']);mol=manifest['molecules'][index];key=mol['entry_id']
wall_hours=int(manifest['walltime'].split(':')[0])
work=ROOT/'runs'/key;work.mkdir(parents=True,exist_ok=True)
record=work/'result.json';returned=ROOT/'returns'/key;returned.mkdir(parents=True,exist_ok=True)
# Never silently rerun completed work, failures, or interrupted attempts.
with (work/'attempt.lock').open('x') as f:f.write(f"{os.environ['SLURM_ARRAY_JOB_ID']}_{index}\n")
start=time.monotonic();cpuinfo=Path('/proc/cpuinfo').read_text()
def cpu_field(name):
 match=re.search(r'^'+re.escape(name)+r'\s*:\s*(.+)',cpuinfo,re.M);return match.group(1) if match else None
result={'scope':'polymer_conformer_campaign','group':GROUP,'identity_policy':manifest['policy'],'status':'starting','input':mol,'inchikey':mol['inchikey'],'entry_id':key,'array_index':index,'job_id':os.environ['SLURM_JOB_ID'],'array_job_id':os.environ['SLURM_ARRAY_JOB_ID'],'array_task_id':index,'partition':os.environ['SLURM_JOB_PARTITION'],'node':socket.gethostname(),'cpu_model':cpu_field('model name'),'cpu_family':cpu_field('cpu family'),'cpu_model_number':cpu_field('model'),'cpu_generation_constraint':'milan','cpu_flags':cpu_field('flags').split(),'resources':{'cpus':1,'request_memory':'4G','maxcore_mb':1500,'request_walltime':manifest['walltime']},'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'manifest_sha256':hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'orca_version':'6.1.1','orca_git':'487d211c','stages':{},'dft_ran':False}
proc=None
class DiagnosticFailure(RuntimeError):pass
def save():
 result['elapsed_seconds']=time.monotonic()-start
 tmp=work/'result.json.tmp';tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(record)
 (returned/'result.json').write_bytes(record.read_bytes())
def fail(mode,message):result['failure_mode']=mode;raise DiagnosticFailure(message)
def signal_handler(signum,frame):
 result['failure_mode']='scheduler_signal_'+str(signum)
 if proc is not None and proc.poll() is None:
  try:os.killpg(proc.pid,signal.SIGTERM)
  except ProcessLookupError:pass
 raise DiagnosticFailure('Interrupted by signal '+str(signum))
for sig in [signal.SIGTERM,signal.SIGINT,signal.SIGUSR1]:signal.signal(sig,signal_handler)
def body(atoms):return ''.join(f'{a[0]:<3}{float(a[1]):14.6f}{float(a[2]):14.6f}{float(a[3]):14.6f}\n' for a in atoms)
save()
try:
 if result['partition']!='research' or int(os.environ['SLURM_CPUS_PER_TASK'])!=1:fail('resource_preflight','Wrong partition/CPU count')
 if socket.gethostname().split('.')[0] in ['euler09','euler10']:fail('cpu_preflight','Excluded node allocated')
 if 'avx2' not in result['cpu_flags'] or 'AMD EPYC' not in result['cpu_model']:fail('cpu_preflight','Missing AVX2 or wrong CPU family')
 prep=json.loads((ROOT/'prepared'/key/'preparation.json').read_text());result['preparation']=prep
 if prep['status']!='prepared':fail('preparation_failed',prep.get('error','Local embedding/MMFF failure'))
 raw=(ROOT/'prepared'/key/'input.xyz').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=prep['xyz_sha256']:fail('input_digest_mismatch','XYZ digest mismatch')
 atoms=[l.split() for l in raw.decode().splitlines()[2:] if len(l.split())==4]
 if len(atoms)!=mol['atoms']:fail('input_atom_count','XYZ atom count mismatch')
 for stage in ['opt','cosmo']:
  result['status']='running_'+stage;save()
  deck=('! OPT BP86 def2-TZVP(-f) TightSCF\n' if stage=='opt' else '! COSMORS(Water)\n')+'%maxcore 1500\n* xyz 0 1\n'+body(atoms)+'*\n'
  inp=work/f'{stage}.inp';inp.write_text(deck);(returned/inp.name).write_bytes(inp.read_bytes())
  begun=time.monotonic();deadline=(wall_hours*3600-180)-(begun-start)
  if deadline<=0:fail('walltime_censored','Per-job elapsed guard exhausted')
  with (work/f'{stage}.out').open('w') as out:
   proc=subprocess.Popen([os.environ['ORCA_BIN'],inp.name],cwd=work,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
   result['dft_ran']=True;save()
   try:code=proc.wait(timeout=deadline)
   except subprocess.TimeoutExpired:
    os.killpg(proc.pid,signal.SIGTERM)
    try:proc.wait(timeout=20)
    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
    fail('walltime_censored','Reached elapsed guard before requested walltime; time is right-censored')
  output=(work/f'{stage}.out').read_text(errors='replace')
  result['stages'][stage]={'exit_code':code,'wall_seconds':time.monotonic()-begun,'input_sha256':hashlib.sha256(inp.read_bytes()).hexdigest(),'exact_input':deck,'final_energies_hartree':re.findall(r'FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)',output),'orca_total_run_time_lines':re.findall(r'^TOTAL RUN TIME:.*$',output,re.M),'geometry_cycles':len(re.findall(r'GEOMETRY OPTIMIZATION CYCLE',output))}
  save()
  if code!=0 or 'ORCA TERMINATED NORMALLY' not in output:
   lower=output.lower()
   mode='scf_nonconvergence' if any(s in lower for s in ['scf not converged','scf did not converge','scf convergence failure']) else 'orca_nonzero_or_abnormal_termination'
   fail(mode,f'{stage} exit={code}; see {work/stage}.out')
  if 'Program Version 6.1.1' not in output or '487d211c' not in output:fail('orca_version_mismatch',stage)
  if stage=='opt':
   if 'THE OPTIMIZATION HAS CONVERGED' not in output:fail('geometry_nonconvergence','Optimisation did not converge')
   blocks=re.findall(r'CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\n',output,re.S)
   if not blocks:fail('geometry_parse','No final geometry block')
   atoms=[l.split() for l in blocks[-1].splitlines() if len(l.split())==4]
   xyz=str(len(atoms))+'\n'+key+' optimised\n'+body(atoms)
   (work/'optimized.xyz').write_text(xyz);(returned/'optimized.xyz').write_text(xyz)
 sp=(work/'cosmo.solute_cpcm.lastout').read_text(errors='replace')
 if '!BP86 def2-TZVPD CPCM' not in sp or 'ORCA TERMINATED NORMALLY' not in sp:fail('cosmo_recipe_or_convergence','Bad subsidiary COSMO result')
 surface=work/'cosmo.solute.orcacosmo'
 if not surface.is_file() or not surface.stat().st_size:fail('missing_surface','No solute surface')
 (returned/'surface.orcacosmo').write_bytes(surface.read_bytes())
 result.update(status='converged_identity_pending',surface_bytes=surface.stat().st_size,surface_sha256=hashlib.sha256(surface.read_bytes()).hexdigest(),cosmo_solute_energy_hartree=float(re.findall(r'FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)',sp)[-1]))
except Exception as exc:
 result.update(status='failed',failure_mode=result.get('failure_mode',type(exc).__name__),error=str(exc))
 raise
finally:
 result['children_maxrss_kib']=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
 result['finished_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());save()
print(json.dumps({k:result[k] for k in ['inchikey','status','node','cpu_model','elapsed_seconds']},indent=2))
