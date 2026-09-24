"""A-1 diagnostic only: allowlisted water/ethanol on research CPU Milan."""
import hashlib,json,os,re,resource,socket,subprocess,sys,time
from pathlib import Path
key=sys.argv[1]
assert key in {'XLYOFNOQVPJJNP-UHFFFAOYSA-N','LFQSCWFLJHTTHZ-UHFFFAOYSA-N'}
assert os.environ['SLURM_JOB_PARTITION']=='research'
assert int(os.environ['SLURM_CPUS_PER_TASK'])==1
root=Path.home()/'plastchem-euler/diagnostics-a1'
work=root/'runs'/key
work.mkdir(parents=True,exist_ok=True)
record=work/'result.json'
with (work/'attempt.lock').open('x') as f: f.write(os.environ['SLURM_JOB_ID']+'\n')
lscpu=subprocess.check_output(['lscpu'],text=True,env=dict(os.environ,LC_ALL='C'))
(work/'lscpu.txt').write_text(lscpu)
cpuinfo=Path('/proc/cpuinfo').read_text(); (work/'cpuinfo.txt').write_text(cpuinfo)
model=re.search(r'^model name\s*:\s*(.+)',cpuinfo,re.M).group(1)
flags=re.search(r'^flags\s*:\s*(.+)',cpuinfo,re.M).group(1).split()
meta=json.loads((root/'inputs'/f'{key}.json').read_text())
result={'status':'running','input':meta,'node':socket.gethostname(),'cpu_model':model,'cpu_flags':flags,'cpu_generation_constraint':'milan','partition':os.environ['SLURM_JOB_PARTITION'],'job_id':os.environ['SLURM_JOB_ID'],'cpus_per_task':1,'memory_request':'4G','walltime_request':'00:15:00','orca_version':'6.1.1','orca_git':'487d211c','stages':{},'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
def save(): record.write_text(json.dumps(result,indent=2)+'\n')
save(); start_all=time.monotonic()
def body(atoms):return ''.join(f'{a[0]:<3}{float(a[1]):14.6f}{float(a[2]):14.6f}{float(a[3]):14.6f}\n' for a in atoms)
try:
 assert 'avx2' in flags
 assert 'AMD EPYC' in model
 raw=(root/'inputs'/f'{key}.xyz').read_bytes();assert hashlib.sha256(raw).hexdigest()==meta['xyz_sha256']
 atoms=[x.split() for x in raw.decode().splitlines()[2:] if len(x.split())==4]
 for stage in ['opt','cosmo']:
  deck=('! OPT BP86 def2-TZVP(-f) TightSCF\n' if stage=='opt' else '! COSMORS(Water)\n')+'%maxcore 1500\n* xyz 0 1\n'+body(atoms)+'*\n'
  inp=work/f'{stage}.inp';inp.write_text(deck)
  begun=time.monotonic()
  with (work/f'{stage}.out').open('w') as out:
   p=subprocess.run([os.environ['ORCA_BIN'],inp.name],cwd=work,stdout=out,stderr=subprocess.STDOUT,timeout=420)
  output=(work/f'{stage}.out').read_text()
  result['stages'][stage]={'exit_code':p.returncode,'wall_seconds':time.monotonic()-begun,'exact_input':deck,'input_sha256':hashlib.sha256(inp.read_bytes()).hexdigest(),'energies_hartree':re.findall(r'FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)',output)}
  save()
  assert p.returncode==0 and 'ORCA TERMINATED NORMALLY' in output,stage
  assert 'Program Version 6.1.1' in output and '487d211c' in output
  if stage=='opt':
   assert 'THE OPTIMIZATION HAS CONVERGED' in output
   blocks=re.findall(r'CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\n',output,re.S)
   atoms=[l.split() for l in blocks[-1].splitlines() if len(l.split())==4]
   (work/'optimized.xyz').write_text(str(len(atoms))+'\noptimised '+key+'\n'+body(atoms))
 sp=(work/'cosmo.solute_cpcm.lastout').read_text()
 assert '!BP86 def2-TZVPD CPCM' in sp and 'ORCA TERMINATED NORMALLY' in sp
 surface=work/'cosmo.solute.orcacosmo';assert surface.stat().st_size>0
 result.update(status='converged_identity_pending',avx2_verified=True,surface_bytes=surface.stat().st_size,surface_sha256=hashlib.sha256(surface.read_bytes()).hexdigest(),cosmo_solute_cpcm_energy_hartree=float(re.findall(r'FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)',sp)[-1]))
 # Accepted compact return shape, staged in home so the same scp route can be used.
 returned=root/'returns'/key;returned.mkdir(parents=True,exist_ok=True)
 for source,target in [('cosmo.solute.orcacosmo','surface.orcacosmo'),('opt.inp','opt.inp'),('cosmo.inp','cosmo.inp'),('optimized.xyz','optimized.xyz')]:
  (returned/target).write_bytes((work/source).read_bytes())
except Exception as exc:
 result.update(status='failed',error=repr(exc));raise
finally:
 result.update(wall_seconds_total=time.monotonic()-start_all,children_maxrss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
 save()
 if result['status']=='converged_identity_pending':(returned/'result.json').write_bytes(record.read_bytes())
print(json.dumps(result,indent=2))
