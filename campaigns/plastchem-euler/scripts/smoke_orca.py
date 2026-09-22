"""Phase 0 only: serial water optimisation then COSMORS(Water), no scheduler calls."""
import hashlib, json, os, re, socket, subprocess, time
from pathlib import Path
work=Path.home()/'plastchem-euler/phase0/water'
work.mkdir(parents=True,exist_ok=True)
record=work/'result.json'
if record.exists():
    raise SystemExit('Existing result; inspect it rather than silently rerunning')
install=Path.home()/'plastchem-euler/software/orca_6_1_1_linux_x86-64_shared_openmpi418_avx2'
xyz=Path.home()/'plastchem-euler/phase0/water.xyz'
atoms=[l.split() for l in xyz.read_text().splitlines()[2:] if len(l.split())==4]
def body(atoms):
    return ''.join(f'{a[0]:<3}{float(a[1]):14.6f}{float(a[2]):14.6f}{float(a[3]):14.6f}\n' for a in atoms)
env=dict(os.environ); env['PATH']=str(install)+':'+env['PATH']; env['LD_LIBRARY_PATH']=str(install)+':'+env.get('LD_LIBRARY_PATH',''); env['OMP_NUM_THREADS']='1'; env['OPENBLAS_NUM_THREADS']='1'
result={'status':'running','node':socket.gethostname(),'charge':0,'multiplicity':1,'maxcore_mb':1500,'conformers':1,'stages':{},'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
record.write_text(json.dumps(result,indent=2))
try:
    for stage in ['opt','cosmo']:
        deck=('! OPT BP86 def2-TZVP(-f) TightSCF\n' if stage=='opt' else '! COSMORS(Water)\n')+'%maxcore 1500\n* xyz 0 1\n'+body(atoms)+'*\n'
        inp=work/f'water_{stage}.inp'; inp.write_text(deck)
        start=time.monotonic()
        with (work/f'water_{stage}.out').open('w') as out:
            p=subprocess.run([str(install/'orca'),inp.name],cwd=work,stdout=out,stderr=subprocess.STDOUT,env=env,timeout=300)
        output=(work/f'water_{stage}.out').read_text()
        result['stages'][stage]={'exit_code':p.returncode,'wall_seconds':time.monotonic()-start,'input':deck,'energy_hartree':re.findall(r'FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)',output)}
        assert p.returncode==0 and 'ORCA TERMINATED NORMALLY' in output, stage
        assert 'Program Version 6.1.1' in output and '487d211c' in output
        if stage=='opt':
            assert 'THE OPTIMIZATION HAS CONVERGED' in output
            blocks=re.findall(r'CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\n',output,re.S)
            atoms=[l.split() for l in blocks[-1].splitlines() if len(l.split())==4]
            (work/'water.opt.xyz').write_text(str(len(atoms))+'\noptimised water\n'+body(atoms))
    surface=work/'water_cosmo.solute.orcacosmo'; assert surface.stat().st_size>0
    result.update(status='converged_identity_pending',orca_version='6.1.1',orca_git='487d211c',surface_sha256=hashlib.sha256(surface.read_bytes()).hexdigest(),surface_bytes=surface.stat().st_size)
except Exception as e:
    result.update(status='failed',error=repr(e)); raise
finally:
    record.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
