"""Read-only post-termination salvage inventory. Refuses to inspect active runs as retry candidates."""
import json,subprocess,hashlib,re,datetime,math
from pathlib import Path
R=Path.home()/'plastchem-euler/polymer-v1';models=json.loads((R/'body/manifest.json').read_text())['molecules'];target=models[166:173];assert len(target)==7 and all(m['polymer']=='nitrocellulose' for m in target)
a=subprocess.run(['sacct','-j','65676','--starttime=2026-09-21','-nP','--format=JobID,State,ElapsedRaw,ExitCode'],capture_output=True,text=True,check=True).stdout;states={v[0]:v for line in a.splitlines() if len(v:=line.split('|'))>=4}
# Completed arrays can leave squeue's job-ID lookup before sacct expires them.
# Query the user's queue; absence is combined with terminal accounting below.
q=subprocess.run(['squeue','-h','-r','-u','aaltamimi2','-o','%i|%T'],capture_output=True,text=True,check=True).stdout;active={line.split('|')[0] for line in q.splitlines()};rows=[]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def frames(path,n):
 lines=path.read_text(errors='replace').splitlines();out=[];i=0
 while i<len(lines):
  try:count=int(lines[i].strip())
  except ValueError:i+=1;continue
  if count==n and i+2+n<=len(lines):
   atoms=[l.split() for l in lines[i+2:i+2+n]]
   try:
    if all(len(a)==4 and all(math.isfinite(float(x)) for x in a[1:]) for a in atoms):out.append(str(n)+'\nSalvaged from '+str(path)+' frame '+str(len(out))+'\n'+'\n'.join(' '.join(a) for a in atoms)+'\n')
   except ValueError:pass
   i+=n+2
  else:i+=1
 return out
for m in target:
 task='65676_'+str(m['array_index']);key=m['entry_id'];work=R/'runs'/key;record=json.loads((work/'result.json').read_text()) if (work/'result.json').exists() else {};account=states.get(task);row={'entry_id':key,'task':task,'accounting':account,'source_failure_mode':record.get('failure_mode'),'retry_hours':48,'original_record':record}
 if task in active:row['disposition']='still_active_do_not_touch';rows.append(row);continue
 if record.get('status')=='converged_identity_pending':row['disposition']='completed_do_not_retry';rows.append(row);continue
 if not account or account[1].split()[0] not in ['TIMEOUT','FAILED','CANCELLED']:
  row['disposition']='not_qualified_for_retry';rows.append(row);continue
 if account[1].split()[0]!='TIMEOUT' and record.get('failure_mode') not in ['scheduler_signal_10','walltime_censored']:
  row['disposition']='not_a_time_limit_failure';rows.append(row);continue
 row['disposition']='time_limit_retry_candidate';row['files']=[{'name':p.name,'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(work.iterdir()) if p.is_file() and (p.suffix in ['.xyz','.gbw'] or p.name=='opt.out')]
 candidates=[]
 for name in ['opt_trj.xyz','opt.xyz','optimized.xyz']:
  p=work/name
  if p.exists():
   fs=frames(p,m['atoms'])
   if fs:candidates.append((p.stat().st_mtime,name,fs[-1],len(fs)))
 if not candidates and (work/'opt.out').exists():
  text=(work/'opt.out').read_text(errors='replace');blocks=re.findall(r'CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\n',text,re.S)
  for b in reversed(blocks):
   atoms=[l.split() for l in b.splitlines() if len(l.split())==4]
   if len(atoms)==m['atoms']:candidates.append(((work/'opt.out').stat().st_mtime,'opt.out last Cartesian block',str(len(atoms))+'\nLast Cartesian block\n'+'\n'.join(' '.join(a) for a in atoms)+'\n',len(blocks)));break
 if candidates:
  _,name,xyz,nframes=max(candidates);row.update(restart_source='salvaged_geometry',source_name=name,source_frame_count=nframes,xyz=xyz,xyz_sha256=hashlib.sha256(xyz.encode()).hexdigest())
 else:
  original=R/'prepared'/key/'input.xyz';row.update(restart_source='original_no_usable_geometry_found',xyz=original.read_text(),xyz_sha256=sha(original),gbw_requires_further_inspection=any(f['name'].endswith('.gbw') for f in row['files']))
 rows.append(row)
print(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'rows':rows,'denominator':7,'scope':'Inventory only; no cancellation, modification or submission'}))
