"""A-13: read-only incremental campaign snapshot (copied from collect_halogen_remote.py, over every submitted chunk). Never
submits, retries, cancels, or changes caps.

    python3 ~/plastchem-euler/coverage-v1/collect_coverage_remote.py [since_epoch]"""
import json,subprocess,sys,time
from pathlib import Path
root=Path.home()/'plastchem-euler/coverage-v1';since=float(sys.argv[1]) if len(sys.argv)>1 else 0
now=time.time();groups={}
for folder in sorted(root.glob('[cr][0-9][0-9]')):
    group=folder.name
    p=folder/'submission.json'
    if not p.exists():p=folder/'reconciled-submission.json'
    if p.exists():
        r=json.loads(p.read_text())
        if r['returncode']==0:groups[group]=r['stdout'].strip().split(';')[0]
def run(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout
names=','.join(f'contam-coverage-{g}' for g in groups) or 'contam-coverage-none'
out={'epoch':now,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(now)),'groups':groups,
     'squeue':run(['squeue','-u','aaltamimi2','--name='+names,'-h','-o','%i|%j|%T|%M|%N|%R']),
     'sacct':'','changed_records':{},'array_indices':{g:json.loads((root/g/'submission-indices.json').read_text()) for g in groups}}
if groups:out['sacct']=run(['sacct','-j',','.join(groups.values()),'--starttime=2026-10-08','--units=K','--format=JobID,JobName%40,Partition,State%32,ExitCode,ElapsedRaw,AllocCPUS,MaxRSS,NodeList,Start,End','-nP'])
for p in (root/'runs').glob('*/result.json'):
    if p.stat().st_mtime>=since-5:
        try:out['changed_records'][p.parent.name]=json.loads(p.read_text())
        except (OSError,json.JSONDecodeError) as exc:out.setdefault('read_errors',{})[str(p)]=str(exc)
print(json.dumps(out))
