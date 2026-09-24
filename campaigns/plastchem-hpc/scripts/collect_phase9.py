"""Serial, incremental, per-file verified A-9 returns; no job mutation.

Raw immutable tar archives remain on R:. Compact metadata drops bulky activity
grids only after they have been archived and verified. No source surfaces copied.
"""
import datetime,fcntl,gzip,hashlib,json,sys,tarfile,time
from pathlib import Path
from euler_transport import run

R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase9-v1')
S=R/'state/phase9-v1'
SCAN_ORDER='all-phase-then-partition-then-lle-v2'


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def publish_registry(regpath, reg):
    """Retain a recovery snapshot before R:'s occasionally denied rename.

    A failed replacement on this mount can remove the destination. Never infer
    that a missing registry means there were no prior collections.
    """
    raw=(json.dumps(reg,indent=2)+'\n').encode()
    digest=hashlib.sha256(raw).hexdigest()
    snapshots=regpath.parent/'collection-snapshots';snapshots.mkdir(exist_ok=True)
    snapshot=snapshots/(digest+'.json.gz')
    if not snapshot.exists():
        with gzip.open(snapshot,'wb') as f:f.write(raw)
    with gzip.open(snapshot,'rb') as f:assert hashlib.sha256(f.read()).hexdigest()==digest
    tmp=regpath.with_suffix('.tmp');tmp.write_bytes(raw)
    last_error=None
    for attempt in range(30):
        try:
            tmp.replace(regpath)
            assert sha(regpath)==digest
            return
        except PermissionError as exc:
            last_error=exc
            # Some mounted filesystems report failure after completing a rename.
            if not tmp.exists() and regpath.exists() and sha(regpath)==digest:return
            time.sleep(1)
    raise RuntimeError(f'Registry publication failed; verified recovery snapshot: {snapshot}') from last_error


def frozen_release_collected(files,plans,manifest):
    """Every required payload AND seal, plus every chunk completion footer.

    This ends transport scanning only. Scientific audit and independent release
    verification still run unchanged. Old diagnostic roots need not delay the
    complete frozen production handoff.
    """
    phases=['solvent-'+s['name'] for s in manifest['solvents']]
    phases+=['polymer-'+r['entry_id'] for rows in manifest['polymers'].values() for r in rows]
    for root,plan in plans.items():
        for i,units in enumerate(plan['chunks']):
            prefix=f'{root}/{i:04d}/'
            if prefix+'complete.json' not in files:return False
            for phase in phases:
                p=prefix+'activities/'+phase+'.json'
                if p not in files or p+'.sha256.json' not in files:return False
            for unit in units:
                p=prefix+'partition/'+unit['id']+'.json'
                if p not in files or p+'.sha256.json' not in files:return False
                for s in manifest['solvents']:
                    for regime in ['RT','high']:
                        p=prefix+'lle/'+unit['id']+'__'+s['name']+'__'+regime+'.json'
                        if p not in files or p+'.sha256.json' not in files:return False
    return bool(plans) and all(plan['chunks'] and all(plan['chunks']) for plan in plans.values())


def main():
    lock=(S/'collect.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    regpath=D/'collection.json'
    if not regpath.exists():
        assert not list((D/'returns').glob('*.tar.gz')) and not regpath.with_suffix('.tmp').exists(), 'Registry missing despite prior returns; recover it before collection'
    reg=json.loads(regpath.read_text()) if regpath.exists() else dict(files={},archives=[])
    # Yield the shared transport lock between bounded archives so cap control,
    # reconciled recovery and other collectors can make progress as well.
    # Production has a large verified-return backlog. Sixteen bounded archives
    # keep the parent's ten-minute cadence from adding idle time as I/O speeds
    # up; preproduction diagnostics retain the shorter pass.
    production=(D/'production-submission.json').exists()
    plans={root:json.loads((D/name).read_text()) for root,name in
           [('chunk-probe-results-v1','chunk-probe-plan.json'),('production-results-v1','production-plan.json')]} if production else {}
    manifest=json.loads((D.parent/'phase8-v1/manifest.json').read_text()) if production else None
    if production:
        units=[u['id'] for plan in plans.values() for chunk in plan['chunks'] for u in chunk]
        assert len(units)==5830 and set(units)=={f'cohort-{i:05d}' for i in range(5830)}
        assert len(manifest['solvents'])==32 and len(manifest['polymers'])==10
    batches=16 if production else 8
    # Amortize transfer/verified-registry publication over larger production
    # archives. Payloads still stream through at most eight pending reads;
    # this does not buffer max_bytes in memory or increase reader concurrency.
    # Release the transport lock after each bounded request/transfer as before.
    limits=dict(max_bytes=256*1024**2,scan_seconds=45) if production else {}
    for _ in range(batches):
        available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
        if available<2.5*1024**3:raise MemoryError('Collection paused between verified archives below 2.5 GiB available')
        code=remote_code(reg['files'],cursor=reg.get('scan_cursor') if reg.get('scan_order')==SCAN_ORDER else None,**limits)
        request_start=time.monotonic()
        response=run('ssh',['euler','python3 -'],input=code,text=True,capture_output=True)
        assert response.returncode==0,response.stderr
        snap=json.loads(response.stdout)
        snap['request_seconds_including_transport_wait']=time.monotonic()-request_start
        collect_response(regpath,reg,snap)
        if production and frozen_release_collected(reg['files'],plans,manifest):
            print(json.dumps(dict(status='frozen_release_files_all_collected',
                utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                scope='Transport completion only; full scientific audit and release verification remain required')),flush=True)
            break
        if not snap.get('scan_more'):break


def remote_code(known,cursor=None,max_bytes=128*1024**2,max_files=4000,scan_seconds=30,archive_readers=4,scan_readers=4):
    # One JSON string literal avoids constructing a huge Python dictionary AST.
    assert 1<=archive_readers<=4 and 1<=scan_readers<=4
    config=dict(cursor=cursor,max_bytes=max_bytes,max_files=max_files,scan_seconds=scan_seconds,archive_readers=archive_readers,scan_readers=scan_readers)
    return 'import json\nknown=json.loads('+repr(json.dumps(known,separators=(',',':')))+')\nconfig='+repr(config)+'\n'+r'''
import collections,concurrent.futures,contextlib,datetime,hashlib,io,json,pathlib,resource,subprocess,tarfile,time,uuid
D=pathlib.Path.home()/'plastchem-euler/phase9-v1'
roots=[D/name for name in ['gate-results-v1','chunk-probe-results-v1','production-results-v1','genoa-gate-results-v1','genoa-gate-results-v2'] if (D/name).exists()]
chunks=[p for root in roots for p in sorted(root.iterdir()) if p.is_dir()]
cursor=config['cursor']
# Completed partition tables need not wait behind a whole chunk's large LLE
# grids. Activities remain first, globally, so every table's prerequisites are
# returned before its dependent rows. The cycle still includes every LLE seal.
buckets=[(chunk,group) for group in ['activities','partition','lle'] for chunk in chunks]
def bucket_key(bucket):return str(bucket[0].relative_to(D))+'/'+bucket[1]
def seals(bucket):return sorted((bucket[0]/bucket[1]).glob('*.sha256.json'))
def scan():
 cursor_bucket='/'.join(cursor.split('/')[:3]) if cursor else None
 keys=[bucket_key(bucket) for bucket in buckets]
 if not cursor or cursor_bucket not in keys:
  for bucket in buckets:yield from seals(bucket)
  return
 pos=keys.index(cursor_bucket)
 current=seals(buckets[pos]);where=next((i+1 for i,p in enumerate(current) if str(p.relative_to(D))==cursor),0)
 yield from current[where:]
 for bucket in buckets[pos+1:]+buckets[:pos]:yield from seals(bucket)
 yield from current[:where]
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
files={};raw_bytes=0;visited=0;last=cursor;more=False;started=time.monotonic();phases=None
with contextlib.closing(bounded_ordered(inspect_seal,scan(),config['scan_readers'])) as inspected:
 for sealraw,s,payload,rel,sealrel,payload_size in inspected:
  if visited and time.monotonic()-started>=config['scan_seconds']:
   more=True;break
  if rel in known and sealrel in known:
   assert known[rel]['sha256']==s['sha256'],rel
   assert hashlib.sha256(sealraw).hexdigest()==known[sealrel]['sha256'],sealrel
   last=sealrel;visited+=1
   continue  # This immutable payload is already hash-verified in a local archive.
  if payload_size is not None:
   size=payload_size+len(sealraw)
   if files and (raw_bytes+size>config['max_bytes'] or len(files)+2>config['max_files']):
    more=True;break
   if payload.parent.name=='partition':
    if phases is None:
     manifest=json.loads((D.parent/'phase8-v1/manifest.json').read_text())
     phases=['solvent-'+r['name'] for r in manifest['solvents']]
     phases+=['polymer-'+r['entry_id'] for rs in manifest['polymers'].values() for r in rs]
    prefix=str(payload.parent.parent.relative_to(D))+'/activities/'
    if not all(prefix+p+'.json'+suffix in known or prefix+p+'.json'+suffix in files for p in phases for suffix in ['', '.sha256.json']):
     last=sealrel;visited+=1;continue  # Collect prerequisites before dependent partition rows.
   # The sealed digest is checked against the exact bytes written into the tar
   # below. Do not read each potentially large LLE payload three times.
   for rel,h in [(rel,s['sha256']),(sealrel,hashlib.sha256(sealraw).hexdigest())]:
    if rel in known:assert known[rel]['sha256']==h,rel
    else:files[rel]=h
   raw_bytes+=size
  last=sealrel;visited+=1
for p in (p for root in roots for p in root.glob('*/complete.json')):
 rel=str(p.relative_to(D));h=hashlib.sha256(p.read_bytes()).hexdigest()
 if rel in known:assert known[rel]['sha256']==h
 else:files[rel]=h
scheduler_start=time.monotonic()
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'new_files':len(files),
 'collection_limits':{k:config[k] for k in ['max_bytes','max_files','scan_seconds']},
 'scan_order':'all-phase-then-partition-then-lle-v2','scan_cursor':last if more else None,'scan_more':more,'scanned_seals':visited,
 'selected_payload_bytes':raw_bytes,'scan_seconds':time.monotonic()-started,'scan_readers':config['scan_readers'],
 'queue':subprocess.check_output(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C|%R'],text=True),
 'complete':{str(p.parent.relative_to(D)):json.loads(p.read_text()) for root in roots for p in root.glob('*/complete.json')},
 'errors':{p.name:p.read_text()[-4000:] for p in (D/'logs').glob('*.err') if p.stat().st_size}}
jobs=sorted({json.loads(p.read_text())['job_id'] for p in D.glob('*-submission.json') if 'job_id' in json.loads(p.read_text())})
out['accounting']=subprocess.check_output(['sacct','-nP','-j',','.join(jobs),'--format=JobID%50,State%30,ElapsedRaw,AllocCPUS,MaxRSS,NodeList,TotalCPU'],text=True) if jobs else ''
out['scheduler_seconds']=time.monotonic()-scheduler_start
if files:
 archive_start=time.monotonic()
 token=uuid.uuid4().hex;folder=D/'return-archives';folder.mkdir(exist_ok=True);archive=folder/(token+'.tar.gz')
 pins=json.dumps(files,sort_keys=True).encode()
 def read_pinned(rel):
  raw=(D/rel).read_bytes();assert hashlib.sha256(raw).hexdigest()==files[rel],rel
  return rel,raw
 with tarfile.open(archive,'w:gz',compresslevel=1) as t:
  info=tarfile.TarInfo('return-pins.json');info.size=len(pins);t.addfile(info,io.BytesIO(pins))
  for rel,raw in bounded_ordered(read_pinned,sorted(files),config['archive_readers']):
   info=tarfile.TarInfo(rel);info.size=len(raw);t.addfile(info,io.BytesIO(raw))
 h=hashlib.sha256()
 with archive.open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 out.update(token=token,archive=str(archive),archive_sha256=h.hexdigest(),archive_bytes=archive.stat().st_size,archive_seconds=time.monotonic()-archive_start,archive_readers=config['archive_readers'],peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
print(json.dumps(out))
'''


def collect_response(regpath,reg,snap):
    (D/'latest-gate-status.json').write_text(json.dumps(snap,indent=2)+'\n')
    reg['scan_order']=snap['scan_order']
    if not snap['new_files']:
        reg['scan_cursor']=snap.get('scan_cursor')
        publish_registry(regpath,reg)
        print(json.dumps({k:v for k,v in snap.items() if k not in ['queue','accounting']}));return
    # Persist an unconfirmed-transfer receipt before scp; no job is resubmitted.
    (S/'collection-pending.json').write_text(json.dumps(snap,indent=2)+'\n')
    archives=D/'returns';archives.mkdir(exist_ok=True);archive=archives/(snap['token']+'.tar.gz')
    transfer_start=time.monotonic()
    response=run('scp',['euler:'+snap['archive'],str(archive)],text=True,capture_output=True)
    assert response.returncode==0,response.stderr
    timings=dict(transfer_seconds=time.monotonic()-transfer_start)
    digest_start=time.monotonic()
    assert sha(archive)==snap['archive_sha256']
    timings['archive_digest_seconds']=time.monotonic()-digest_start
    compact_start=time.monotonic()
    compact=D/'collected';compact.mkdir(exist_ok=True)
    bundle=compact/(snap['token']+'.jsonl.gz');temporary=bundle.with_suffix('.tmp')
    with tarfile.open(archive,'r|gz') as t,gzip.open(temporary,'wt',compresslevel=3) as compressed:
        iterator=iter(t);first=next(iterator);assert first.name=='return-pins.json'
        pins=json.load(t.extractfile(first));seen=set()
        for member in iterator:
            assert member.isfile() and member.name in pins
            assert member.name not in seen;seen.add(member.name)
            raw=t.extractfile(member).read();assert hashlib.sha256(raw).hexdigest()==pins[member.name]
            reg['files'][member.name]=dict(sha256=pins[member.name],archive=archive.name)
            if member.name.endswith('.sha256.json'):continue
            value=json.loads(raw)
            if '/lle/' in member.name:value.pop('activities',None)
            compressed.write(json.dumps(dict(path=member.name,value=value),separators=(',',':'))+'\n')
        assert seen==set(pins)
    temporary.replace(bundle)
    reg.setdefault('compact_bundles',[]).append(dict(path=str(bundle),sha256=sha(bundle)))
    reg['archives'].append(dict(path=str(archive),sha256=snap['archive_sha256'],files=len(pins),utc=snap['utc']))
    reg['scan_cursor']=snap.get('scan_cursor')
    timings['compact_seconds']=time.monotonic()-compact_start
    registry_start=time.monotonic()
    publish_registry(regpath,reg)
    timings['registry_seconds']=time.monotonic()-registry_start
    (S/'collection-pending.json').unlink()
    timing=dict(utc=snap['utc'],archive=archive.name,files=len(pins),archive_bytes=snap['archive_bytes'],
                selected_payload_bytes=snap.get('selected_payload_bytes'),
                **{k:snap[k] for k in ['collection_limits','scan_seconds','scan_readers','scheduler_seconds','archive_seconds','archive_readers','peak_rss_kib','request_seconds_including_transport_wait'] if k in snap},**timings)
    with (D/'collection-timings.jsonl').open('a') as f:f.write(json.dumps(timing)+'\n')
    print(json.dumps(dict(utc=snap['utc'],verified_files=len(pins),total_files=len(reg['files']),archive_bytes=snap['archive_bytes'],complete_chunks=len(snap['complete']),errors=snap['errors'])))


if __name__=='__main__':main()
