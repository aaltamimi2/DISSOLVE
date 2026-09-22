"""Stage one prepared main-body block; preserve the original global array indices."""
import hashlib,json,re,sys,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1';label=sys.argv[1]
assert re.fullmatch(r'main_chunk_\d{3}',label)
folder=P/label;chunk=json.loads((folder/'manifest.json').read_text());main=json.loads((P/'main_le80/manifest.json').read_text())
indices=[];failed=[]
for i in chunk['indices']:
    mol=main['molecules'][i];f=P/'prepared'/mol['inchikey']/'preparation.json'
    if not f.exists():raise SystemExit('Preparation pending: '+mol['inchikey'])
    prep=json.loads(f.read_text())
    if prep['status']=='prepared':indices.append(i)
    else:
        key=mol['inchikey'];r={'scope':'phase2_campaign','group':'main_le80','input':mol,'inchikey':key,'status':'failed','failure_mode':prep['failure_mode'],'error':prep['error'],'dft_ran':False,'node':None,'cpu_model':None,'preparation':prep}
        dest=P/'records'/f'{key}.json';tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(r,indent=2)+'\n');tmp.replace(dest);failed.append(key)
(folder/'submission-indices.json').write_text(json.dumps(indices)+'\n')
files=[(ROOT/'scripts/campaign_runner.py','campaign_runner.py'),(ROOT/'scripts/campaign-main_le80.sbatch','campaign-main_le80.sbatch'),(ROOT/'scripts/submit_campaign_chunk_remote.py','submit_campaign_chunk_remote.py'),(P/'main_le80/manifest.json','main_le80/manifest.json'),(folder/'manifest.json',f'{label}/manifest.json'),(folder/'submission-indices.json',f'{label}/submission-indices.json')]
for i in indices:
    key=main['molecules'][i]['inchikey']
    for name in ['input.xyz','preparation.json']:files.append((P/'prepared'/key/name,f'prepared/{key}/{name}'))
sha=folder/'staging.sha256';sha.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}\n' for p,name in files));files.append((sha,f'{label}/staging.sha256'))
archive=P/f'{label}-staging.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for p,name in files:tar.add(p,arcname=name,recursive=False)
s={'group':'main_le80','chunk':label,'new_targets':len(chunk['indices']),'submission_tasks':len(indices),'preparation_failures':len(failed),'archive':str(archive),'archive_bytes':archive.stat().st_size,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
(folder/'staging-summary.json').write_text(json.dumps(s,indent=2)+'\n');print(json.dumps(s))
