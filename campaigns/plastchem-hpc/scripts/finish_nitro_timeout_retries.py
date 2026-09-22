"""Wait for originals naturally, inventory, stage verified geometry, reconcile, submit last."""
import sys,json,time,shlex,subprocess,datetime
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];P=R/'state/polymer-v1';S=P/'nitro-retry1';D=Path('/mnt/r/plastchem-euler/polymer-v1/nitro-retry1');D.mkdir(parents=True,exist_ok=True)
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix('.tmp');t.write_text(json.dumps(x,indent=2)+'\n');t.replace(p)
def call(kind,args):
 r=run(kind,args,capture_output=True,text=True);assert r.returncode==0,r.stderr;return r.stdout
while True:
 try:
  inv=json.loads(call('ssh',['euler','python3 -c '+shlex.quote((R/'scripts/inspect_nitrocellulose_restart_remote.py').read_text())]));write(P/'nitro-salvage-inventory.json',inv)
  active=sum(x['disposition']=='still_active_do_not_touch' for x in inv['rows']);print(json.dumps({'utc':inv['utc'],'active_originals':active,'dispositions':[x['disposition'] for x in inv['rows']]}),flush=True)
  if not active:break
 except Exception as e:print(json.dumps({'inventory_error':str(e)}),flush=True)
 time.sleep(120)
rows=[x for x in inv['rows'] if x['disposition']=='time_limit_retry_candidate']
assert all(x['disposition'] in ['time_limit_retry_candidate','completed_do_not_retry'] for x in inv['rows']),'Unexpected outcome requires review, no automatic resubmission'
if not rows:print('ALL_ORIGINALS_COMPLETED_NO_RETRY',flush=True);sys.exit()
subprocess.run([sys.executable,str(R/'scripts/stage_nitro_retry.py')],check=True)
call('ssh',['euler','mkdir -p ~/plastchem-euler/polymer-v1/nitro-retry1'])
call('scp',['-rq',str(S/'prepared'),str(S/'body'),str(S/'polymer_runner.py'),str(S/'body.sbatch'),'euler:plastchem-euler/polymer-v1/nitro-retry1/'])
receipt=json.loads(call('ssh',['euler','python3 -c '+shlex.quote((R/'scripts/submit_nitro_retry_remote.py').read_text())]));write(S/'submission.json',receipt)
# Submission is delayed behind every preceding array, so archive/register before it can execute.
entries={}
for row in rows:
 key=row['entry_id'];a=D/'original-attempts'/key;a.mkdir(parents=True,exist_ok=True)
 original=dict(row['original_record']);original.update(status='failed',execution_outcome='time_limit',retry_required=True,accounting_at_retry=row['accounting'],outcome_label='TIMEOUT (time-limit interruption, not chemistry failure)');write(a/'record.json',original)
 call('scp',['-rq','euler:plastchem-euler/polymer-v1/returns/'+key,str(D/'original-attempts')])
 entries[key]={'original_job':row['task'],'record_path':str(a/'record.json'),'original_elapsed_seconds':int(row['accounting'][2]),'restart_source':row['restart_source'],'source_name':row.get('source_name'),'restart_xyz_sha256':row['xyz_sha256']}
registry={'job_id':receipt['job_id'],'entries':entries,'dependency':'afterany:65676:65677:63873','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'walltime':'48:00:00','cap':64,'retry_throttle':7};write(P/'active-retries.json',registry)
for row in rows:
 key=row['entry_id'];m=next(x for x in json.loads((S/'body/manifest.json').read_text())['molecules'] if x['entry_id']==key)
 write(P/'records'/(key+'.json'),{'entry_id':key,'input':m,'status':'not_yet_run','active_attempt':'nitro-retry1','previous_attempt':entries[key],'retry_job_id':receipt['job_id'],'restart_provenance':m['restart_provenance']})
write(D/'submission.json',receipt);write(D/'restart-provenance.json',registry)
with (R/'logs/campaign-events.jsonl').open('a') as f:f.write(json.dumps({'event':'nitro_time_limit_retries_submitted',**registry})+'\n')
subprocess.run(['python3',str(R/'scripts/summarize_polymer.py')],check=True)
print(json.dumps({'event':'NITRO_RETRIES_REGISTERED',**registry}),flush=True)
subprocess.run([sys.executable,'-u',str(R/'scripts/watch_nitro_retry.py')],check=True)
