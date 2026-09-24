"""SCP dated tar snapshots; verify every member against its stable source SHA256.
No campaign processes stopped. Never overwrite a completed milestone archive.
"""
import os,stat,json,hashlib,subprocess,datetime,tarfile,tempfile,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1];A=Path('/mnt/r/plastchem-euler/workspace-archive/2026-09-22');A.mkdir(parents=True,exist_ok=True)
assert not (R/'WORKSPACE_ARCHIVE_RECEIPT.json').exists(),'Choose a new destination for a later milestone'
SOURCE=Path('/home/aaltamimi2/dissolve-v12-builder-1/plastchem-contaminants')
OPTIONS=['-o','ControlMaster=auto','-o',f'ControlPath={R}/state/ssh-%r@%h-%p','-o','ControlPersist=600']
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest_stream(f):
 h=hashlib.sha256()
 for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def sha(p):
 with p.open('rb') as f:return digest_stream(f)
def save(p,x):p.write_text(json.dumps(x,separators=(',',':'))+'\n')
def files(root):
 for base,ds,fs in os.walk(root):
  ds[:]=[d for d in ds if d!='.git']
  for n in sorted(fs):yield Path(base)/n
def copy_verified(p,d):
 h=sha(p);r=subprocess.run(['scp',*OPTIONS,str(p),str(d)],capture_output=True,text=True);assert r.returncode==0,r.stderr;assert sha(d)==h==sha(p);return h
started=now();allrows=[];archives=[];special=[]
with tempfile.TemporaryDirectory(prefix='plastchem-milestone-') as td:
 for directory in ['state','logs']:
  local=Path(td)/(directory+'.tar.gz');entries=[]
  for attempt in range(5):
   entries=[];changed=[]
   with tarfile.open(local,'w:gz',compresslevel=1) as tar:
    for p in files(R/directory):
     rel=str(p.relative_to(R));st=p.lstat()
     if not stat.S_ISREG(st.st_mode):
      if not any(x['path']==rel for x in special):special.append({'path':rel,'type':'socket' if stat.S_ISSOCK(st.st_mode) else 'nonregular','sha256':None,'reason':'No regular-file byte stream'})
      continue
     capture=now();h=sha(p);st2=p.stat()
     if (st.st_size,st.st_mtime_ns,st.st_ino)!=(st2.st_size,st2.st_mtime_ns,st2.st_ino):changed.append(rel);continue
     tar.add(p,arcname=rel,recursive=False);st3=p.stat()
     if (st2.st_size,st2.st_mtime_ns,st2.st_ino)!=(st3.st_size,st3.st_mtime_ns,st3.st_ino):changed.append(rel)
     entries.append({'path':rel,'size':st.st_size,'sha256':h,'capture_utc':capture,'source_mtime_ns':st.st_mtime_ns})
   if not changed:break
   print(json.dumps({'event':'retry_live_capture','directory':directory,'changed_files':changed}),flush=True)
  else:raise RuntimeError('Could not capture stable members')
  expected={r['path']:r for r in entries};target=A/local.name;archive_sha=copy_verified(local,target);seen=set()
  with tarfile.open(target,'r:gz') as tar:
   for member in tar:
    assert member.isfile() and member.name in expected
    with tar.extractfile(member) as f:actual=digest_stream(f)
    row=expected[member.name];assert actual==row['sha256'] and member.size==row['size'],member.name;row.update(archive_member_sha256=actual,verified_utc=now(),archive_file=target.name);seen.add(member.name)
  assert seen==set(expected);allrows.extend(entries);archives.append({'path':str(target),'bytes':target.stat().st_size,'sha256':archive_sha,'member_count':len(entries)})
  print(json.dumps({'event':'archive_verified','directory':directory,'members':len(entries),'compressed_bytes':target.stat().st_size}),flush=True)
manifest={'started_utc':started,'finished_utc':now(),'snapshot_semantics':'Stable per-file captures over an interval, not an atomic snapshot. Every destination tar member independently hashed and matched to the source digest recorded during capture. Whole archive source/destination digest verified after SCP. Live writers left running.','entries':allrows,'nonregular_not_copied':special,'archives':archives}
save(A/'state-logs-digest-manifest.json',manifest)
C=A/'census-sources';C.mkdir(exist_ok=True);census=[]
for p in sorted(SOURCE.iterdir()):
 if not(p.name.startswith('41586_') or p.name in ['s41586-025-09184-8.pdf','PlastChem_State_of_the_Science_on_Plastic_Chemicals_Report.pdf','plastchem_db_v1.0.xlsx']):continue
 capture=now();h=copy_verified(p,C/p.name);census.append({'source_path':str(p),'archive_path':str(C/p.name),'size':p.stat().st_size,'sha256':h,'capture_utc':capture,'verified_utc':now()})
save(C/'digest-manifest.json',{'entries':census,'files':len(census),'total_bytes':sum(x['size'] for x in census)})
# Inventory all current excluded files; archive digests preserve their dated capture.
excluded=[];by={r['path']:r for r in allrows};large=[]
for p in files(R):
 rel=p.relative_to(R);st=p.lstat();regular=stat.S_ISREG(st.st_mode)
 if regular and st.st_size>10000000:large.append(str(rel))
 reason='live_'+rel.parts[0] if rel.parts[0] in ['state','logs'] else 'python_cache' if '__pycache__' in rel.parts or p.suffix in ['.pyc','.pyo','.pyd'] else 'over_10000000_bytes' if st.st_size>10000000 else None
 if not reason:continue
 row={'path':str(rel),'size':st.st_size,'sha256':sha(p) if regular else None,'reason':reason,'type':'regular_file' if regular else 'socket' if stat.S_ISSOCK(st.st_mode) else 'special'}
 if str(rel) in by:row['archive_sha256']=by[str(rel)]['sha256']
 excluded.append(row)
(R/'.gitignore').write_text('# Live campaign data and runtime caches\n/state/\n/logs/\n__pycache__/\n*.py[cod]\n\n# Explicit files greater than 10,000,000 bytes\n'+''.join('/'+p.replace('[','\\[').replace(']','\\]')+'\n' for p in sorted(large)))
e={'capture_started_utc':started,'capture_finished_utc':now(),'scope':'Excluded workspace payload; .git internal database is not scientific payload. Source observations may postdate archived members; both hashes retained. Null SHA256 denotes a runtime socket without regular-file contents.','entries':excluded,'entries_count':len(excluded),'total_regular_bytes':sum(x['size'] for x in excluded if x['type']=='regular_file'),'archive_root':str(A),'archive_manifest_sha256':sha(A/'state-logs-digest-manifest.json')};save(R/'EXCLUSION_MANIFEST.json',e);assert (R/'EXCLUSION_MANIFEST.json').stat().st_size<=10000000
receipt={'started_utc':started,'finished_utc':now(),'archive_root':str(A),'format':'state.tar.gz and logs.tar.gz plus per-member SHA256 manifest','state_logs_verified_files':len(allrows),'state_logs_verified_bytes':sum(x['size'] for x in allrows),'state_logs_manifest_sha256':sha(A/'state-logs-digest-manifest.json'),'archives':archives,'nonregular_not_copied':special,'census_source_files':len(census),'census_source_bytes':sum(x['size'] for x in census),'census_manifest_sha256':sha(C/'digest-manifest.json'),'excluded_workspace_regular_bytes':e['total_regular_bytes'],'every_copied_regular_file_verified':True,'no_push':True};save(R/'WORKSPACE_ARCHIVE_RECEIPT.json',receipt);print(json.dumps(receipt),flush=True)
