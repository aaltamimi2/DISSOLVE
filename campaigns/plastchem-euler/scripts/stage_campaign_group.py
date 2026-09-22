"""Prepare a bounded staging archive locally; requires all group preparations disposed."""
import hashlib,json,tarfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1';group=sys.argv[1]
assert group in ['main_le80','tail_gt80']
m=json.loads((P/group/'manifest.json').read_text());indices=[];failures=[]
for mol in m['molecules']:
    p=P/'prepared'/mol['inchikey']/'preparation.json'
    if not p.exists():raise SystemExit('Preparation still pending for '+mol['inchikey'])
    prep=json.loads(p.read_text())
    if prep['status']=='prepared':indices.append(mol['array_index'])
    else:
        r={'scope':'phase2_campaign','input':mol,'inchikey':mol['inchikey'],'status':'failed','failure_mode':prep['failure_mode'],'error':prep['error'],'dft_ran':False,'node':None,'cpu_model':None,'preparation':prep}
        (P/'records'/f"{mol['inchikey']}.json").write_text(json.dumps(r,indent=2)+'\n');failures.append(r)
(P/group/'submission-indices.json').write_text(json.dumps(indices)+'\n')
files=[(ROOT/'scripts/campaign_runner.py','campaign_runner.py'),(ROOT/f'scripts/campaign-{group}.sbatch',f'campaign-{group}.sbatch'),(ROOT/'scripts/submit_campaign_remote.py','submit_campaign_remote.py'),(P/group/'manifest.json',f'{group}/manifest.json'),(P/group/'submission-indices.json',f'{group}/submission-indices.json')]
for i in indices:
    key=m['molecules'][i]['inchikey']
    for name in ['input.xyz','preparation.json']:files.append((P/'prepared'/key/name,f'prepared/{key}/{name}'))
sha_path=P/group/'staging.sha256'
sha_path.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}\n' for p,name in files))
files.append((sha_path,f'{group}/staging.sha256'))
archive=P/f'{group}-staging.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for path,name in files:tar.add(path,arcname=name,recursive=False)
record={'group':group,'new_targets':len(m['molecules']),'submission_tasks':len(indices),'preparation_failures':len(failures),'archive':str(archive),'archive_bytes':archive.stat().st_size,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
(P/group/'staging-summary.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
