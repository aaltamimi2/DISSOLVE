"""Exploratory conformer study on CHTC (owner request 2026-10-12; not a release input): one conformer through the
campaign's ORCA recipe inside an HTCondor job sandbox (copied from conformer_runner.py; the decks, the checks of the
ORCA output and the returned files are the same). What differs from Euler: the job runs in the scratch directory
HTCondor gives it, ORCA comes from the bundle shipped with the job, and the machine is whatever CHTC matched (the CPU
model is recorded, AVX2 is required; no machine here has passed the campaign's reproduction check, so these results
are compared with each other and with the release, never mixed into one). An evicted job starts again from the
prepared geometry in a fresh sandbox. Everything returned is packed as <key>.tar.gz, also when the run fails.

    python3 conformer_htc_runner.py <InChIKey-kNN>     (in the sandbox, next to input.xyz and preparation.json)"""
import hashlib,json,os,re,resource,signal,socket,subprocess,tarfile,time,sys
from pathlib import Path
key=sys.argv[1]
assert re.fullmatch(r'[A-Z]{14}-[A-Z]{10}-[A-Z]-k\d\d',key)
work=Path.cwd()/'run';work.mkdir();returned=Path.cwd()/'returns';returned.mkdir()
record=returned/'result.json'
start=time.monotonic();cpuinfo=Path('/proc/cpuinfo').read_text()
def cpu_field(name):
 match=re.search(r'^'+re.escape(name)+r'\s*:\s*(.+)',cpuinfo,re.M);return match.group(1) if match else None
prep=json.loads(Path('preparation.json').read_text());mol=prep['input']
result={'scope':'conformer_study_htc','status':'starting','input':mol,'inchikey':key,'cluster':'CHTC','condor_job':os.environ.get('CONDOR_JOB_ID'),'node':socket.gethostname(),'cpu_model':cpu_field('model name'),'cpu_family':cpu_field('cpu family'),'cpu_model_number':cpu_field('model'),'cpu_flags':cpu_field('flags').split(),'resources':{'cpus':1,'maxcore_mb':1500},'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'orca_bundle_sha256':os.environ.get('ORCA_BUNDLE_SHA256'),'orca_version':'6.1.1','orca_git':'487d211c','stages':{},'dft_ran':False,'preparation':prep}
proc=None
class DiagnosticFailure(RuntimeError):pass
def save():
 result['elapsed_seconds']=time.monotonic()-start
 tmp=returned/'result.json.tmp';tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(record)
def fail(mode,message):result['failure_mode']=mode;raise DiagnosticFailure(message)
def signal_handler(signum,frame):
 result['failure_mode']='scheduler_signal_'+str(signum)
 if proc is not None and proc.poll() is None:
  try:os.killpg(proc.pid,signal.SIGTERM)
  except ProcessLookupError:pass
 raise DiagnosticFailure('Interrupted by signal '+str(signum))
for sig in [signal.SIGTERM,signal.SIGINT,signal.SIGUSR1]:signal.signal(sig,signal_handler)
def body(atoms):return ''.join(f'{a[0]:<3}{float(a[1]):14.6f}{float(a[2]):14.6f}{float(a[3]):14.6f}\n' for a in atoms)
def keep(name):
 source=work/name
 if source.is_file():(returned/name).write_bytes(source.read_bytes())
save()
try:
 if mol['inchikey']!=key:fail('input_key_mismatch','preparation.json is for '+mol['inchikey'])
 if 'avx2' not in result['cpu_flags']:fail('cpu_preflight','Missing AVX2 (the ORCA 6.1.1 build needs it)')
 if prep['status']!='prepared':fail('preparation_failed',prep.get('error','Local embedding/MMFF failure'))
 raw=Path('input.xyz').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=prep['xyz_sha256']:fail('input_digest_mismatch','XYZ digest mismatch')
 atoms=[l.split() for l in raw.decode().splitlines()[2:] if len(l.split())==4]
 if len(atoms)!=mol['atoms']:fail('input_atom_count','XYZ atom count mismatch')
 for stage in ['opt','cosmo']:
  result['status']='running_'+stage;save()
  deck=('! OPT BP86 def2-TZVP(-f) TightSCF\n' if stage=='opt' else '! COSMORS(Water)\n')+'%maxcore 1500\n* xyz 0 1\n'+body(atoms)+'*\n'
  inp=work/f'{stage}.inp';inp.write_text(deck);keep(inp.name)
  begun=time.monotonic()
  with (work/f'{stage}.out').open('w') as out:
   proc=subprocess.Popen([os.environ['ORCA_BIN'],inp.name],cwd=work,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
   result['dft_ran']=True;save()
   code=proc.wait()
  output=(work/f'{stage}.out').read_text(errors='replace')
  result['stages'][stage]={'exit_code':code,'wall_seconds':time.monotonic()-begun,'input_sha256':hashlib.sha256(inp.read_bytes()).hexdigest(),'exact_input':deck,'final_energies_hartree':re.findall(r'FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)',output),'orca_total_run_time_lines':re.findall(r'^TOTAL RUN TIME:.*$',output,re.M),'geometry_cycles':len(re.findall(r'GEOMETRY OPTIMIZATION CYCLE',output))}
  save()
  if code!=0 or 'ORCA TERMINATED NORMALLY' not in output:
   lower=output.lower()
   mode='scf_nonconvergence' if any(s in lower for s in ['scf not converged','scf did not converge','scf convergence failure','the scf has not converged']) else 'orca_nonzero_or_abnormal_termination'
   fail(mode,f'{stage} exit={code}; see {stage}.out')
  if 'Program Version 6.1.1' not in output or '487d211c' not in output:fail('orca_version_mismatch',stage)
  if stage=='opt':
   if 'THE OPTIMIZATION HAS CONVERGED' not in output:fail('geometry_nonconvergence','Optimisation did not converge')
   blocks=re.findall(r'CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\n',output,re.S)
   if not blocks:fail('geometry_parse','No final geometry block')
   atoms=[l.split() for l in blocks[-1].splitlines() if len(l.split())==4]
   (returned/'optimized.xyz').write_text(str(len(atoms))+'\n'+key+' optimised\n'+body(atoms))
 sp=(work/'cosmo.solute_cpcm.lastout').read_text(errors='replace')
 if '!BP86 def2-TZVPD CPCM' not in sp or 'ORCA TERMINATED NORMALLY' not in sp:fail('cosmo_recipe_or_convergence','Bad subsidiary COSMO result')
 surface=work/'cosmo.solute.orcacosmo'
 if not surface.is_file() or not surface.stat().st_size:fail('missing_surface','No solute surface')
 (returned/'surface.orcacosmo').write_bytes(surface.read_bytes())
 result.update(status='converged_identity_pending',surface_bytes=surface.stat().st_size,surface_sha256=hashlib.sha256(surface.read_bytes()).hexdigest(),cosmo_solute_energy_hartree=float(re.findall(r'FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)',sp)[-1]))
except Exception as exc:
 result.update(status='failed',failure_mode=result.get('failure_mode',type(exc).__name__),error=str(exc))
finally:
 result['children_maxrss_kib']=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
 result['finished_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());save()
 for name in ['opt.out','cosmo.out','cosmo.solute_vac.lastout','cosmo.solute_cpcm.lastout']:keep(name)
 with tarfile.open(key+'.tar.gz','w:gz') as archive:archive.add(returned,arcname=key)
print(json.dumps({k:result.get(k) for k in ['inchikey','status','failure_mode','node','cpu_model','elapsed_seconds']},indent=2))
