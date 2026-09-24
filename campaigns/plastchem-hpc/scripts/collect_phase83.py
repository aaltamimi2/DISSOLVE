"""Collect immutable completed units via scp, verify every file, retain raw archives.

Local metadata is compact per-contaminant gzip JSON; released packages are untouched.
"""
import argparse,csv,datetime,gzip,hashlib,io,json,sys,tarfile,collections,fcntl
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase83-v1');S=R/'state/phase83-v1'
def save(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(v,indent=2)+'\n');tmp.replace(p)
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 return h.hexdigest()
def main(limit):
 lock=(S/'collector.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:print('Phase83 collector already active; no duplicate collection');return
 statefile=S/'collection.json';state=json.loads(statefile.read_text()) if statefile.exists() else {'collected':{},'pending':None}
 if not state['pending']:
  snapshot=json.loads((S/'latest.json').read_text());ready=set(snapshot['completed']);repair=snapshot.get('calibration_repair')
  if repair:
   for idx in repair.get('indices',[]):
    key=f'{idx:05d}';initial=snapshot.get('initial_attempts',{}).get(key);current=snapshot['completed'].get(key)
    if not initial or not current or initial['execution']['job_id']==current['execution']['job_id']:ready.discard(key)
  ids=sorted(ready-set(state['collected']))[:limit]
  if not ids:print('No new terminal units in latest status snapshot');return
  token=hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16];state['pending']={'indices':ids,'token':token};save(statefile,state)
 pending=state['pending'];ids=pending['indices'];token=pending['token']
 code='''import pathlib,json,hashlib,tarfile,datetime
D=pathlib.Path.home()/'plastchem-euler/phase83-v1';ids=IDS;token=TOKEN
out=D/'returns';out.mkdir(exist_ok=True);archive=out/(token+'.tar.gz');receipt=out/(token+'.json')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 return h.hexdigest()
if not receipt.exists():
 files=[]
 for key in ids:
  root=D/'results'/key;assert (root/'complete.json').exists(),key
  files.extend(p for p in root.rglob('*') if p.is_file() and not p.name.endswith('.tmp'))
  for n in range(int(key)*64,(int(key)+1)*64):
   folder=D/'phase82/lle'/f'{n:03d}';assert (folder/'complete.json').exists() or (folder/'failure.json').exists(),n
   files.extend(p for p in folder.iterdir() if p.is_file() and not p.name.endswith('.tmp'))
 files=sorted(set(files));pins={str(p.relative_to(D)):sha(p) for p in files};pin=out/(token+'-pins.json');pin.write_text(json.dumps(pins)+'\\n')
 tmp=archive.with_suffix('.tmp')
 with tarfile.open(tmp,'w:gz',compresslevel=3) as tar:
  tar.add(pin,arcname='return-pins.json')
  for p in files:tar.add(p,arcname=str(p.relative_to(D)))
 tmp.replace(archive)
 result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'indices':ids,'token':token,'members':len(files),'archive_sha256':sha(archive),'archive_bytes':archive.stat().st_size}
 receipt.write_text(json.dumps(result,indent=2)+'\\n')
print(receipt.read_text())
'''.replace('IDS',repr(ids)).replace('TOKEN',repr(token))
 r=run('ssh',['euler','python3 -'],input=code,capture_output=True,text=True);assert r.returncode==0,r.stderr;receipt=json.loads(r.stdout)
 dest=D/'returns';dest.mkdir(exist_ok=True);archive=dest/(token+'.tar.gz')
 if not archive.exists() or digest(archive)!=receipt['archive_sha256']:
  tmp=archive.with_suffix('.part');r=run('scp',['euler:plastchem-euler/phase83-v1/returns/'+token+'.tar.gz',str(tmp)],capture_output=True,text=True);assert r.returncode==0,r.stderr
  assert digest(tmp)==receipt['archive_sha256'];tmp.replace(archive)
 metadata={key:{'partition':[],'lle':{},'source_archive':archive.name} for key in ids};verified=0
 with tarfile.open(archive,'r|gz') as tar:
  pins=None
  for member in tar:
   assert member.isfile(),member.name;data=tar.extractfile(member).read()
   if member.name=='return-pins.json':pins=json.loads(data);continue
   assert pins is not None and hashlib.sha256(data).hexdigest()==pins[member.name],member.name;verified+=1
   parts=Path(member.name).parts
   if parts[0]=='results':
    key=parts[1]
    if len(parts)==3 and parts[-1].endswith('.csv'):metadata[key]['partition'].extend(csv.DictReader(io.StringIO(data.decode())))
    if parts[-1]=='complete.json':metadata[key]['complete']=json.loads(data)
   if parts[:2]==('phase82','lle') and parts[-1] in ['result.json','failure.json']:
    n=int(parts[2]);key=f'{n//64:05d}';entry=json.loads(data)
    if parts[-1]=='result.json' or str(n) not in metadata[key]['lle']:metadata[key]['lle'][str(n)]=entry
 assert verified==receipt['members']==len(pins)
 for key,value in metadata.items():
  assert len(value['partition'])==640 and len(value['lle'])==64,(key,len(value['partition']),len(value['lle']))
  assert value['complete']['index']==int(key)
  out=D/'collected'/(key+'.json.gz');out.parent.mkdir(exist_ok=True);tmp=out.with_suffix('.tmp')
  with gzip.open(tmp,'wt') as f:json.dump(value,f)
  tmp.replace(out);state['collected'][key]={'archive':archive.name,'metadata_sha256':digest(out),
   'partition_status_counts':dict(collections.Counter(r['status'] for r in value['partition'])),
   'lle_status_counts':dict(collections.Counter(r['status'] for r in value['lle'].values()))}
 receipt.update(local_verified_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),verified_members=verified)
 save(dest/(token+'.json'),receipt);state['pending']=None;save(statefile,state);print(json.dumps(receipt,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=20);a=p.parse_args();main(a.limit)
