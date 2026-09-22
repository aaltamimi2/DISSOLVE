"""A-7 memory-bounded serial controller: fresh process per polymer/solute, atomic units."""
from pathlib import Path
import os,sys,json,csv,time,re,subprocess,datetime,hashlib
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7');COUNTS={'evoh':11,'nylon6':20,'nylon66':28,'pe':31,'pp':25,'pvc':27,'pvdf':24};D.mkdir(exist_ok=True)
ENV=dict(os.environ,PYTHONPATH=str(R/'scripts'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
def available():return int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))
def pause(reason,unit,extra=None):
 r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'reason':reason,'unit':unit,'status':'paused_waiting','extra':extra};(R/'state/polymer-v1/a7-phased-status.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r),flush=True);raise SystemExit(75)
def run(cmd,unit,log):
 if available()<2.5*1024*1024:pause('host_available_under_2.5_GiB',unit)
 with log.open('a') as f:
  p=subprocess.Popen(cmd,env=ENV,stdout=f,stderr=subprocess.STDOUT);peak=0
  while p.poll() is None:
   try:rss=int(next(l.split()[1] for l in Path(f'/proc/{p.pid}/status').read_text().splitlines() if l.startswith('VmRSS:')))
   except (OSError,StopIteration):rss=0
   peak=max(peak,rss)
   if rss>1900*1024 or available()<2.5*1024*1024:
    p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
    pause('memory_guard',unit,{'peak_rss_kib':peak,'available_kib':available(),'completed_units_retained':True})
   time.sleep(1)
  if p.returncode==75:pause('unit_memory_guard',unit,{'peak_rss_kib':peak})
  if p.returncode:pause('unit_failed_inspect_log',unit,{'returncode':p.returncode,'log':str(log)})
 return peak
source=(R/'scripts/compare_pe_routes_a6.py').read_text().split('def activity(')[0]
for polymer,n in COUNTS.items():
 folder=D/polymer;folder.mkdir(exist_ok=True)
 if not (folder/'inputs.json').exists():
  s=source.replace("R=Path(__file__).resolve().parents[1]",f"R=Path('{R}')").replace('/route-comparison-a6','/route-comparison-a7/'+polymer).replace("m['polymer']=='pe'",f"m['polymer']=='{polymer}'");s=re.sub(r'\b31\b',str(n),s)
  script=R/'scripts/a7_generated'/(polymer+'_setup.py');script.parent.mkdir(exist_ok=True);script.write_text(s)
  run([sys.executable,str(script)],polymer+':setup',folder/'setup.log')
 for solute in ['dep','dbp','bbp','dehp']:
  u=folder/'units'/solute
  if (u/'complete.json').exists():
   print('SKIP_COMPLETE',polymer,solute,flush=True);peak=0
  else:
   peak=run([sys.executable,str(R/'scripts/a7_phased_unit.py'),polymer,solute],polymer+':'+solute,folder/(solute+'-unit.log'))
  assert (u/'complete.json').exists()
  # Rebuild small per-polymer tables exclusively from durably committed units.
  for unitfile,outfile in [('normalized.csv','route-comparison.csv'),('existing.csv','existing-convention-comparison.csv')]:
   paths=[folder/'units'/s/unitfile for s in ['dep','dbp','bbp','dehp'] if (folder/'units'/s/'complete.json').exists()];headers=[]
   for path in paths:
    with path.open() as h:headers.extend(k for k in next(csv.reader(h)) if k not in headers)
   tmp=folder/(outfile+'.tmp')
   with tmp.open('w',newline='') as h:
    writer=csv.DictWriter(h,fieldnames=headers);writer.writeheader()
    for path in paths:
     with path.open() as f:
      for row in csv.DictReader(f):writer.writerow(row)
    h.flush();os.fsync(h.fileno())
   tmp.replace(folder/outfile)
  status={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'unit_complete','polymer':polymer,'solute':solute,'peak_rss_kib':peak,'units_done':len(list((folder/'units').glob('*/complete.json'))),'available_kib':available()};(R/'state/polymer-v1/a7-phased-status.json').write_text(json.dumps(status,indent=2)+'\n');print(json.dumps(status),flush=True)
 inputs=json.loads((folder/'inputs.json').read_text());(folder/'summary.json').write_text(json.dumps({'paired_predictions':128,'failures':[],'energy_summary':inputs['energy_summary'],'phased_units':4,'conformers':n},indent=2)+'\n')
print('ALL_SEVEN_PHASED_COMPLETE',flush=True)
