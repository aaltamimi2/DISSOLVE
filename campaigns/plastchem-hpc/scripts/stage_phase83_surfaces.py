"""Stream and verify all accepted existing surfaces into a bulk archive; no DFT."""
import hashlib,io,json,tarfile,time,datetime
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/phase83-v1')
def main():
 c=json.loads((D/'cohort.json').read_text());archive=D/'all-solute-surfaces.tar.gz';tmp=archive.with_suffix('.tmp');start=time.monotonic();pins={};total=0
 with tarfile.open(tmp,'w:gz',compresslevel=3) as tar:
  for r in c['rows']:
   name=r['B'];data=Path(r['source_surface']).read_bytes();h=hashlib.sha256(data).hexdigest();assert h==r['surface_sha256'],r['inchikey']
   if name in pins:continue
   info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600;tar.addfile(info,io.BytesIO(data));pins[name]=h;total+=len(data)
   if len(pins)%250==0:print('SURFACES_VERIFIED',len(pins),round(time.monotonic()-start,1),flush=True)
  data=(json.dumps(pins,indent=2)+'\n').encode();info=tarfile.TarInfo('all-surface-pins.json');info.size=len(data);info.mode=0o600;tar.addfile(info,io.BytesIO(data))
 tmp.replace(archive);digest=hashlib.sha256()
 with archive.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):digest.update(block)
 receipt={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cohort_sha256':hashlib.sha256((D/'cohort.json').read_bytes()).hexdigest(),'surface_count':len(pins),'uncompressed_bytes':total,'archive_bytes':archive.stat().st_size,'archive_sha256':digest.hexdigest(),'wall_seconds':time.monotonic()-start}
 (D/'all-surface-pins.json').write_text(json.dumps(pins,indent=2)+'\n');(D/'surface-staging.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
