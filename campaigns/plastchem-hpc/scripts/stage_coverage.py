"""A-13: a bounded staging archive for one chunk (copied from stage_halogen.py), uploaded to Euler and checked there file
by file. Requires every preparation of the chunk disposed; a preparation failure becomes the molecule's final record
(status failed, its failure mode, dft_ran false) and is not submitted.

    python3 scripts/stage_coverage.py c01"""
import hashlib,json,re,sys,tarfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/coverage-v1';group=sys.argv[1]
REMOTE='plastchem-euler/coverage-v1'
assert re.fullmatch(r'[cr]\d\d',group)
assert not (P/group/'staging-summary.json').exists(),f'{group} is staged; a chunk is staged once'
m=json.loads((P/group/'manifest.json').read_text());indices=[];failures=[]
admitted_path=P/'cpu-checks/admitted.json'
m['admitted']=[{k:a[k] for k in ('cpu_model','partition','constraint','basis')} for a in json.loads(admitted_path.read_text())['admitted']] if admitted_path.exists() else [{'cpu_model':'AMD EPYC 7763 64-Core Processor','partition':'research','constraint':'milan&cpu','basis':'the campaign reference CPU (A-2)'}]
(P/group/'manifest.json').write_text(json.dumps(m,indent=1)+'\n')
(P/'records').mkdir(exist_ok=True)
for mol in m['molecules']:
    p=P/'prepared'/mol['inchikey']/'preparation.json'
    if not p.exists():raise SystemExit('Preparation still pending for '+mol['inchikey'])
    prep=json.loads(p.read_text())
    if prep['status']=='prepared':indices.append(mol['array_index'])
    else:
        r={'scope':'coverage_campaign','group':group,'input':mol,'inchikey':mol['inchikey'],'status':'failed','failure_mode':prep['failure_mode'],'error':prep['error'],'dft_ran':False,'node':None,'cpu_model':None,'preparation':prep}
        (P/'records'/f"{mol['inchikey']}.json").write_text(json.dumps(r,indent=2)+'\n');failures.append(r)
(P/group/'submission-indices.json').write_text(json.dumps(indices)+'\n')
files=[(ROOT/'scripts/coverage_runner.py',f'{group}/coverage_runner.py'),(ROOT/'scripts/coverage.sbatch',f'{group}/coverage.sbatch'),(ROOT/'scripts/submit_coverage_remote.py',f'{group}/submit_coverage_remote.py'),(P/group/'manifest.json',f'{group}/manifest.json'),(P/group/'submission-indices.json',f'{group}/submission-indices.json')]
files += [(ROOT/'scripts/collect_coverage_remote.py','collect_coverage_remote.py')]
for i in indices:
    key=m['molecules'][i]['inchikey']
    for name in ['input.xyz','preparation.json']:files.append((P/'prepared'/key/name,f'prepared/{key}/{name}'))
sha_path=P/group/'staging.sha256'
sha_path.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}\n' for p,name in files))
files.append((sha_path,f'{group}/staging.sha256'))
archive=P/f'{group}-staging.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for path,name in files:tar.add(path,arcname=name,recursive=False)
def check(result,what):
    if result.returncode:raise SystemExit(f'{what} failed: {(result.stderr or "")[:400]}')
    return result
check(run('ssh',['euler',f'mkdir -p ~/{REMOTE}/logs ~/{REMOTE}/runs ~/{REMOTE}/returns ~/{REMOTE}/prepared'],capture_output=True,text=True),'mkdir')
check(run('scp',['-q',str(archive),f'euler:{REMOTE}/']),'scp')
remote=check(run('ssh',['euler',f'cd ~/{REMOTE} && tar -xzf {archive.name} && sha256sum -c --quiet {group}/staging.sha256 && echo STAGED_OK'],capture_output=True,text=True),'extract/verify')
assert 'STAGED_OK' in remote.stdout,remote.stdout[-400:]
record={'group':group,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'new_targets':len(m['molecules']),'submission_tasks':len(indices),'preparation_failures':len(failures),'preparation_failure_keys':[r['inchikey'] for r in failures],'archive':str(archive),'archive_bytes':archive.stat().st_size,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'remote_verified':True}
(P/group/'staging-summary.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
