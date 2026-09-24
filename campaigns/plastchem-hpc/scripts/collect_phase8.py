"""Archive completed Euler phase output via scp; verify every archived member digest."""
import sys,json,hashlib,tarfile,datetime
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase8-v1');phase=sys.argv[1];assert phase in ['phase81','phase82']
code='''import json,hashlib,tarfile,datetime
from pathlib import Path
D=Path.home()/"plastchem-euler/phase8-v1";phase=PHASE
files=[p for p in (D/phase).rglob("*") if p.is_file() and not p.name.endswith(".tmp")]
pins={str(p.relative_to(D)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
manifest={"utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"phase":phase,"files":pins};mp=D/(phase+"-returns-pins.json");mp.write_text(json.dumps(manifest,indent=2)+"\\n")
with tarfile.open(D/(phase+"-returns.tar.gz"),"w:gz") as tar:
 for p in files+[mp]:tar.add(p,arcname=str(p.relative_to(D)))
p=D/(phase+"-returns.tar.gz");print(json.dumps({"archive":p.name,"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"files":len(files),"bytes":p.stat().st_size,"capture_utc":manifest["utc"]}))
'''.replace('PHASE',repr(phase))
r=run('ssh',['euler','python3 -'],input=code,capture_output=True,text=True);assert r.returncode==0,r.stderr;receipt=json.loads(r.stdout)
r=run('scp',['euler:plastchem-euler/phase8-v1/'+receipt['archive'],str(D/receipt['archive'])],capture_output=True,text=True);assert r.returncode==0,r.stderr
archive=D/receipt['archive'];assert hashlib.sha256(archive.read_bytes()).hexdigest()==receipt['sha256'];lle={};failures={};verified=0
with tarfile.open(archive) as tar:
 pins=json.load(tar.extractfile(phase+'-returns-pins.json'));(D/(phase+'-returns-pins.json')).write_text(json.dumps(pins,indent=2)+'\n')
 for name,h in pins['files'].items():
  data=tar.extractfile(name).read();assert hashlib.sha256(data).hexdigest()==h,name;verified+=1
  p=Path(name)
  if phase=='phase81' and p.name in ['complete.json','predictions.csv','failure.json'] or phase=='phase82' and '/partition/' in name and p.name in ['complete.json','predictions.csv','failure.json']:
   dest=D/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
  if phase=='phase82' and '/lle/' in name and p.name=='result.json':lle[p.parent.name]=json.loads(data)
  if phase=='phase82' and '/lle/' in name and p.name=='failure.json':failures[p.parent.name]=json.loads(data)
if phase=='phase82':
 (D/'phase82/lle-results.json').write_text(json.dumps(lle,indent=2)+'\n');(D/'phase82/lle-failures.json').write_text(json.dumps(failures,indent=2)+'\n')
receipt.update(verified_members=verified,raw_archive=str(archive),local_received_utc=datetime.datetime.now(datetime.timezone.utc).isoformat());p=R/'state/phase8-v1'/(phase+'-collection.json');p.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
