"""Activate the reviewed v6 allocator after the verified primary handoff.

One shared transport session; no submission, cancellation or global cap raise.
Remote activation and scheduler readback are durable for lost-confirmation
reconciliation. The existing controller cadence remains live.
"""
import datetime
import fcntl
import hashlib
import json
from pathlib import Path

from euler_transport import _run

R=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler');D=B/'phase10-v1'
NAME='phase9_throttle_remote_v6.py'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    v=json.loads((B/'phase9-v1/delivery-verification.json').read_text())
    assert v['status']=='complete_delivery_verified' and v['manifest_sha256']==sha(B/'promotion-v1/manifest.json')
    source=R/'scripts'/NAME;pin=sha(source)
    intent=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),filename=NAME,sha256=pin,
        cap=64,primary_release_id=v['manifest_sha256'],authority='A-10 under owner-authorized shared cap 64; primary release complete',
        purpose='Borrow currently idle capacity; preserve actual running reservations and guard tier-2 dependency transition')
    if not (D/'allocation-v6-intent.json').exists():save(D/'allocation-v6-intent.json',intent)
    else:assert json.loads((D/'allocation-v6-intent.json').read_text())['sha256']==pin
    with (R/'state/ssh-transport.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def call(kind,args,**kwargs):
            p=_run(kind,args,capture_output=True,text=True,timeout=180,**kwargs)
            assert p.returncode==0,p.stderr
            return p.stdout
        def remote(code):
            wrapped='import json,traceback\ntry:\n exec(compile('+repr(code)+",'allocator-activation','exec'),{})\nexcept Exception:\n print(json.dumps(dict(status='application_error',traceback=traceback.format_exc())))\n"
            x=json.loads(call('ssh',['euler','python3 -'],input=wrapped));assert x.get('status')!='application_error',x
            return x
        probe=remote("import json,hashlib\nfrom pathlib import Path\np=Path.home()/'plastchem-euler/phase9-v1'/"+repr(NAME)+"\nprint(json.dumps(dict(exists=p.exists(),sha256=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None)))\n")
        if probe['exists']:assert probe['sha256']==pin,'Never overwrite different controller code'
        else:call('scp',[str(source),'euler:plastchem-euler/phase9-v1/'+NAME])
        code=r'''
import contextlib,datetime,fcntl,hashlib,io,json
from pathlib import Path
D=Path.home()/'plastchem-euler/phase9-v1';E=D.parent/'phase10-v1';intent=INTENT
p=D/intent['filename'];assert hashlib.sha256(p.read_bytes()).hexdigest()==intent['sha256']
receipt=E/'allocation-v6-readback.json'
with (D.parent/'phase83-v1/throttle.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 marker=D/'active-throttle-controller.json';previous=json.loads(marker.read_text())
 assert previous['filename'] in ['phase9_throttle_remote_v5.py',intent['filename']]
 assert hashlib.sha256((D/previous['filename']).read_bytes()).hexdigest()==previous['sha256']
 if previous['filename']=='phase9_throttle_remote_v5.py':
  assert previous['sha256']=='66557d1bd6b34f331533d2fafa936d4fc06323b5026e90ee1c1a37a5700a9884'
 active={**intent,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 if previous['filename']!=intent['filename']:
  tmp=marker.with_suffix('.tmp');tmp.write_text(json.dumps(active,indent=2)+'\n');tmp.replace(marker)
 else:active=previous
if receipt.exists():
 result=json.loads(receipt.read_text());assert result['activation']['sha256']==intent['sha256'];result['reconciled_existing_receipt']=True
else:
 output=io.StringIO();scope={}
 with contextlib.redirect_stdout(output):exec(compile(p.read_text(),str(p),'exec'),scope)
 result=json.loads(output.getvalue());result['activation']=active;result['previous_activation']=previous
 receipt.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
'''.replace('INTENT',repr(intent))
        result=remote(code)
    save(D/'allocation-v6-readback.json',result)
    save(B/'phase9-v1/active-throttle-controller.json',result['activation'])
    (B/'phase9-v1'/NAME).write_bytes(source.read_bytes())
    assert result['status']=='verified',result
    print(json.dumps({k:v for k,v in result.items() if k!='operations'},indent=2))


if __name__=='__main__':main()
