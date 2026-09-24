"""Collect immutable A-10 checkpoints only after the primary release is verified.

Reuses the primary's streamed archive verification, never its registry or paths.
Every SSH/scp operation yields to an already-held shared transport lock.
"""
import datetime
import fcntl
import json
from pathlib import Path

import collect_phase9 as archive_io
from euler_transport import _run

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase10-v1')


class PrimaryBusy(Exception): pass


def require_primary():
    p = D.parent/'phase9-v1/delivery-verification.json'
    target = D.parent/'promotion-v1/manifest.json'
    if not p.exists() or not target.exists(): raise PrimaryBusy('primary_release_not_yet_verified')
    verification = json.loads(p.read_text())
    assert verification['status'] == 'complete_delivery_verified'
    assert verification['manifest_sha256'] == archive_io.sha(target)
    available = int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines()
                         if s.startswith('MemAvailable:')))*1024
    if available < 2.5*1024**3: raise PrimaryBusy('host_memory_guard')


def transport(kind, args, **kwargs):
    require_primary()
    with (R/'state/ssh-transport.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise PrimaryBusy('primary_transport_busy')
        try:
            return _run(kind, args, **kwargs)
        except RuntimeError as exc:
            # A refusal before attempting transport is an expected wait, not
            # an archive failure. Keep every data/remote error fail-closed.
            if str(exc).startswith(('Backoff active:', 'Wait at least 120s before another connection attempt')):
                raise PrimaryBusy(str(exc)) from exc
            raise


def remote_code(known, cursor, scan_readers=4):
    assert 1 <= scan_readers <= 4
    prefix = 'known=json.loads('+repr(json.dumps(known, separators=(',', ':')))+')\ncursor='+repr(cursor)+'\n'
    return 'import json\n'+prefix+'scan_readers='+str(scan_readers)+'\n'+r'''
import collections,concurrent.futures,contextlib,datetime,hashlib,io,pathlib,subprocess,tarfile,time,uuid
D=pathlib.Path.home()/'plastchem-euler/phase10-v1'
roots=[D/name for name in ['calibration-results-v2','production-results-v1'] if (D/name).exists()]
chunks=[p for root in roots for p in sorted(root.iterdir()) if p.is_dir()]
groups=['polymer-reuse','activities','partition','lle']
buckets=[(p,group) for group in groups for p in chunks]
def key(bucket):return str(bucket[0].relative_to(D))+'/'+bucket[1]
def seals(bucket):return sorted((bucket[0]/bucket[1]).glob('*.sha256.json'))
def scan():
 keys=[key(b) for b in buckets];bucket='/'.join(cursor.split('/')[:3]) if cursor else None
 if bucket not in keys:
  for b in buckets:yield from seals(b)
  return
 pos=keys.index(bucket);items=seals(buckets[pos]);where=next((i+1 for i,p in enumerate(items) if str(p.relative_to(D))==cursor),0)
 yield from items[where:]
 for b in buckets[pos+1:]+buckets[:pos]:yield from seals(b)
 yield from items[:where]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def bounded_ordered(function,items,workers):
 # At most eight reads/results are outstanding; preserve input order.
 if workers==1:
  for item in items:yield function(item)
  return
 pending=collections.deque();items=iter(items)
 with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
  for _ in range(2*workers):
   item=next(items,None)
   if item is None:break
   pending.append(pool.submit(function,item))
  while pending:
   yield pending.popleft().result()
   item=next(items,None)
   if item is not None:pending.append(pool.submit(function,item))
def inspect_seal(seal):
 sealraw=seal.read_bytes();record=json.loads(sealraw);payload=seal.with_name(seal.name[:-len('.sha256.json')])
 rel=str(payload.relative_to(D));sealrel=str(seal.relative_to(D));size=None
 if not (rel in known and sealrel in known):
  try:size=payload.stat().st_size
  except FileNotFoundError:pass
 return sealraw,record,payload,rel,sealrel,size
files={};raw_bytes=0;visited=0;last=cursor;more=False;start=time.monotonic()
manifest=json.loads((D/'manifest.json').read_text())
phases=[s['name'] for s in manifest['solvents']]+['CONTROL__water','CONTROL__hexane']
with contextlib.closing(bounded_ordered(inspect_seal,scan(),scan_readers)) as inspected:
 for raw,stamp,payload,rel,sr,payload_size in inspected:
  if visited and time.monotonic()-start>=45:more=True;break
  if rel in known and sr in known:
   assert known[rel]['sha256']==stamp['sha256'] and known[sr]['sha256']==hashlib.sha256(raw).hexdigest()
   visited+=1;last=sr;continue
  if payload_size is None:continue
  size=payload_size+len(raw)
  if files and (raw_bytes+size>256*1024**2 or len(files)+2>16000):more=True;break
  if payload.parent.name=='partition':
   prefix=str(payload.parent.parent.relative_to(D))
   prerequisites=[prefix+'/activities/'+p+'.json'+suffix for p in phases for suffix in ['', '.sha256.json']]
   prerequisites += [prefix+'/polymer-reuse/'+payload.name+suffix for suffix in ['', '.sha256.json']]
   if not all(p in known or p in files for p in prerequisites):last=sr;visited+=1;continue
  for name,digest in [(rel,stamp['sha256']),(sr,hashlib.sha256(raw).hexdigest())]:
   if name in known:assert known[name]['sha256']==digest
   else:files[name]=digest
  raw_bytes+=size;last=sr;visited+=1
for chunk in chunks:
 for p in [chunk/'complete.json',*chunk.glob('CONTROL__*-comparison.json')]:
  if not p.exists():continue
  rel=str(p.relative_to(D));digest=sha(p)
  if rel in known:assert known[rel]['sha256']==digest
  else:files[rel]=digest
out=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),new_files=len(files),
 scan_order='phase10-reuse-activities-partition-lle-v1',scan_cursor=last if more else None,scan_more=more,
 scan_readers=scan_readers,collection_limits=dict(max_bytes=256*1024**2,max_files=16000,scan_seconds=45),scanned_seals=visited,selected_payload_bytes=raw_bytes,scan_seconds=time.monotonic()-start,
 complete={str(p.parent.relative_to(D)):json.loads(p.read_text()) for root in roots for p in root.glob('*/complete.json')},
 errors={p.name:p.read_text()[-4000:] for p in (D/'logs').glob('*.err') if p.stat().st_size and not p.name.startswith('calibration-68833')})
if files:
 token=uuid.uuid4().hex;folder=D/'return-archives';folder.mkdir(exist_ok=True);archive=folder/(token+'.tar.gz')
 def read(name):
  raw=(D/name).read_bytes();assert hashlib.sha256(raw).hexdigest()==files[name],name
  return name,raw
 with tarfile.open(archive,'w:gz',compresslevel=1) as t:
  raw=json.dumps(files,sort_keys=True).encode();m=tarfile.TarInfo('return-pins.json');m.size=len(raw);t.addfile(m,io.BytesIO(raw))
  # At most eight pending reads: bounded memory, deterministic archive order.
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
   names=iter(sorted(files));pending=collections.deque()
   for _ in range(8):
    name=next(names,None)
    if name is not None:pending.append(pool.submit(read,name))
   while pending:
    name,raw=pending.popleft().result();m=tarfile.TarInfo(name);m.size=len(raw);t.addfile(m,io.BytesIO(raw))
    name=next(names,None)
    if name is not None:pending.append(pool.submit(read,name))
 out.update(token=token,archive=str(archive),archive_sha256=sha(archive),archive_bytes=archive.stat().st_size)
print(json.dumps(out))
'''


def main():
    require_primary()
    lock=(D/'collect.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    # These module globals are private to this process. The primary source,
    # running process, registry and state are untouched.
    archive_io.D=D;archive_io.S=D;archive_io.run=transport
    regpath=D/'collection.json'
    if not regpath.exists():
        assert not list((D/'returns').glob('*.tar.gz')), 'Recover existing registry first'
    reg=json.loads(regpath.read_text()) if regpath.exists() else dict(files={},archives=[])
    pending=D/'collection-pending.json'
    if pending.exists():
        snap=json.loads(pending.read_text())
        match=[a for a in reg['archives'] if Path(a['path']).name==snap['token']+'.tar.gz']
        if match:
            assert len(match)==1 and match[0]['sha256']==snap['archive_sha256'];pending.unlink()
        else:
            archive_io.collect_response(regpath,reg,snap)
        return
    source=remote_code(reg['files'],reg.get('scan_cursor'))
    wrapped='import traceback,json\ntry:\n exec(compile('+repr(source)+",'phase10-collector','exec'),{})\nexcept Exception:\n print(json.dumps(dict(status='collector_error',traceback=traceback.format_exc())))\n"
    response=transport('ssh',['euler','python3 -'],input=wrapped,text=True,capture_output=True,timeout=240)
    assert response.returncode==0,response.stderr
    snap=json.loads(response.stdout);assert snap.get('status')!='collector_error',snap
    reg['scan_order']=snap['scan_order']
    archive_io.collect_response(regpath,reg,snap)
    (D/'collection-status.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        verified_files=len(reg['files']),archives=len(reg['archives']),scan_more=snap['scan_more']),indent=2)+'\n')


if __name__=='__main__':
    try:main()
    except PrimaryBusy as exc:print(json.dumps(dict(status='deferred',reason=str(exc))))
