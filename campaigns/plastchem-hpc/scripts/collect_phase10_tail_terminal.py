"""SCP and hash-verify terminal tail controls without altering scientific outputs."""
import datetime
import hashlib
import json
import shutil
from pathlib import Path

from euler_transport import run

D=Path('/mnt/r/plastchem-euler/phase10-v1')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def verify_capture(folder,assignment,receipt,pins,initial):
    for name,pin in pins.items():
        assert Path(name).name==name and name.endswith('.json')
        path=folder/name
        assert path.stat().st_size==pin['bytes'] and sha(path)==pin['sha256'],name
    state=json.loads((folder/'handoff-state.json').read_text())
    assert state['status']=='original_resumed'
    assert state['outcome']=='all_helper_units_verified','Reconcile helper failures separately'
    assert state['original_process']==initial['original_process']
    assert state['resumed_process']['state']!='T'
    for key in ['pid','start_ticks','uid','command','cwd']:
        assert state['resumed_process'][key]==state['original_process'][key]
    assert state['assignment_sha256']==receipt['assignment_sha256']==sha(folder/'assignment.json')
    assert json.loads((folder/'assignment.json').read_text())==assignment
    accounting={line.split('|')[0]:line.split('|')[1] for line in state['helper_accounting'].splitlines() if line}
    for group in range(3):
        assert accounting[receipt['job_id']+'_'+str(group)]=='COMPLETED'
        done=json.loads((folder/f'helper-{group}-complete.json').read_text())
        assert done['assignment_sha256']==state['assignment_sha256']
        assert done['units']==assignment['groups'][group]
        assert len(done['files'])==39*len(done['units'])
        assert done['counts']['computed']<=assignment['helper_systems'][group]
        assert done['counts']['computed']+done['counts']['reused']==len(done['files'])
    return state

def main():
    receipts={int(p.name.split('tail')[1].split('-')[0]):json.loads(p.read_text())
              for p in D.glob('production-retry-tail*-submission.json')}
    assert receipts
    chunks=sorted(receipts)
    code='chunks='+repr(chunks)+'\n'+r'''
import hashlib,json,datetime
from pathlib import Path
D=Path.home()/'plastchem-euler/phase10-v1'
rows=[]
for chunk in chunks:
 root=D/f'tail-{chunk:04d}-v1'
 state=json.loads((root/'handoff-state.json').read_text())
 row=dict(chunk=chunk,status=state['status'],root=str(root))
 if state['status']=='original_resumed':
  names=['assignment.json','handoff-state.json','supervisor-start.json']
  names += [f'helper-{i}-complete.json' for i in range(3)]
  row['files']={}
  for name in names:
   p=root/name
   if not p.exists():continue
   raw=p.read_bytes();row['files'][name]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
 rows.append(row)
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),rows=rows)))
'''
    wrapped='import traceback,json\ntry:\n exec(compile('+repr(code)+",'terminal-tail-inventory','exec'),{})\nexcept Exception:\n print(json.dumps(dict(error=traceback.format_exc())))\n"
    reply=run('ssh',['euler','python3 -'],input=wrapped,capture_output=True,text=True,timeout=180)
    assert reply.returncode==0,reply.stderr
    inventory=json.loads(reply.stdout);assert 'error' not in inventory,inventory
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    result=[]
    for row in inventory['rows']:
        chunk=row['chunk'];root=D/f'tail-{chunk:04d}-v1'
        if row['status']!='original_resumed':
            result.append(dict(chunk=chunk,status='waiting',remote_status=row['status']));continue
        pins=row['files'];record=root/'terminal-evidence.json'
        if record.exists():
            previous=json.loads(record.read_text());assert previous['file_pins']==pins
            for name,pin in pins.items():assert sha(root/name)==pin['sha256']
            result.append(dict(chunk=chunk,status='already_verified'));continue
        names=['assignment.json','handoff-state.json','supervisor-start.json']+[f'helper-{i}-complete.json' for i in range(3)]
        assert set(pins)==set(names),'Terminal helper evidence missing; inspect rather than infer completion'
        assert sum(p['bytes'] for p in pins.values())<10*1024**2
        incoming=root/('terminal-capture-'+stamp);incoming.mkdir(exist_ok=False)
        sources=['euler:'+row['root']+'/'+name for name in names]
        copied=run('scp',[*sources,str(incoming)+'/'],capture_output=True,text=True,timeout=180)
        assert copied.returncode==0,copied.stderr
        assignment=json.loads((root/'assignment.json').read_text())
        initial=json.loads((root/'handoff-state.json').read_text())
        state=verify_capture(incoming,assignment,receipts[chunk],pins,initial)
        # Retain the complete capture before replacing only the initial control
        # snapshot. Scientific checkpoint files are never opened for writing.
        for name in names:shutil.copyfile(incoming/name,root/name)
        proof=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='terminal_handoff_verified',
            chunk=chunk,job_id=receipts[chunk]['job_id'],file_pins=pins,capture=str(incoming),
            resume_utc=state['resume_utc'],paused_seconds=state['paused_seconds'],
            scope='Terminal scheduler/ownership/process evidence; full scientific row and raw-grid audits remain separate')
        record.write_text(json.dumps(proof,indent=2)+'\n');result.append(proof)
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),inventory=inventory,results=result)
    (D/'tail-terminal-evidence-latest.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps([dict(chunk=r['chunk'],status=r['status']) for r in result]))

if __name__=='__main__':main()
