import requests,datetime as dt,hashlib,json,zipfile
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/measured-expansion-2026-09-15');S=D/'sources';commit='078b2a301e1d70db771b002d44fe73afde06e59f';url=f'https://raw.githubusercontent.com/kmansouri/OPERA/{commit}/OPERA_Data.zip';p=S/'OPERA_Data.zip'
if not p.exists():
 r=requests.get(url,timeout=120);r.raise_for_status();p.write_bytes(r.content)
 (S/'OPERA_Data.retrieval.json').write_text(json.dumps({'url':url,'commit':commit,'retrieved_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'sha256':hashlib.sha256(r.content).hexdigest(),'bytes':len(r.content)},indent=2)+'\n')
with zipfile.ZipFile(p) as z:
 for info in z.infolist():
  if any(x in info.filename.lower() for x in ['logp','readme']):
   print(info.filename,info.file_size)
   if not info.is_dir():
    dest=S/Path(info.filename).name;dest.write_bytes(z.read(info))
