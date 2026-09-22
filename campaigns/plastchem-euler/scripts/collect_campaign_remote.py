"""Read-only incremental campaign snapshot. Never submits, retries, cancels, or changes caps."""
import json,subprocess,sys,time
from pathlib import Path
root=Path.home()/'plastchem-euler/campaign-v1';since=float(sys.argv[1]) if len(sys.argv)>1 else 0
now=time.time();groups={}
for group in ['main_le80','tail_gt80'] + sorted(p.name for p in root.glob('main_chunk_[0-9][0-9][0-9]')):
    p=root/group/'submission.json'
    if not p.exists():p=root/group/'reconciled-submission.json'
    if p.exists():
        r=json.loads(p.read_text())
        if r['returncode']==0:groups[group]=r['stdout'].strip().split(';')[0]
def run(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout
names=','.join(['contam-p2-main_le80-v1','contam-p2-tail_gt80-v1']+[json.loads((root/g/'manifest.json').read_text())['name'] for g in groups if g.startswith('main_chunk_')])
out={'epoch':now,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(now)),'groups':groups,
     'squeue':run(['squeue','-u','aaltamimi2','--name='+names,'-h','-o','%i|%j|%T|%M|%N|%R']),
     'sacct':'','changed_records':{},'array_indices':{g:json.loads((root/g/'submission-indices.json').read_text()) for g in groups}}
if groups:out['sacct']=run(['sacct','-j',','.join(groups.values()),'--starttime=2026-09-12','--units=K','--format=JobID,JobName%40,Partition,State%32,ExitCode,ElapsedRaw,AllocCPUS,MaxRSS,NodeList,Start,End','-nP'])
for p in (root/'runs').glob('*/result.json'):
    if p.stat().st_mtime>=since-5:
        try:out['changed_records'][p.parent.name]=json.loads(p.read_text())
        except (OSError,json.JSONDecodeError) as exc:out.setdefault('read_errors',{})[str(p)]=str(exc)
print(json.dumps(out))
