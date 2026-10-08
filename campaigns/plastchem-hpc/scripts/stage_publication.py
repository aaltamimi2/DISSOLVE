"""A-12: staging archive for the publication tier (every molecule prepared; all indices submitted)."""
import hashlib,json,tarfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/publication-v1';group=sys.argv[1] if len(sys.argv)>1 else 'publication'
assert group in ['publication','neutral']
m=json.loads((P/group/'manifest.json').read_text());indices=[]
for mol in m['molecules']:
    prep=json.loads((P/'prepared'/mol['inchikey']/'preparation.json').read_text())
    assert prep['status']=='prepared',mol['inchikey']
    indices.append(mol['array_index'])
(P/group/'submission-indices.json').write_text(json.dumps(indices)+'\n')
files=[(ROOT/'scripts/publication_runner.py','publication_runner.py'),(ROOT/f'scripts/{group}.sbatch',f'{group}.sbatch'),(ROOT/'scripts/submit_publication_remote.py','submit_publication_remote.py'),(P/group/'manifest.json',f'{group}/manifest.json'),(P/group/'submission-indices.json',f'{group}/submission-indices.json')]
for mol in m['molecules']:
    for name in ['input.xyz','preparation.json']:files.append((P/'prepared'/mol['inchikey']/name,f"prepared/{mol['inchikey']}/{name}"))
sha_path=P/group/'staging.sha256'
sha_path.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}\n' for p,name in files))
files.append((sha_path,f'{group}/staging.sha256'))
archive=P/f'{group}-staging.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for path,name in files:tar.add(path,arcname=name,recursive=False)
print(json.dumps({'tasks':len(indices),'files':len(files),'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()},indent=1))
