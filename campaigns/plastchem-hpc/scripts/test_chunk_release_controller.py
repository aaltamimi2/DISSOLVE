"""Offline scheduler transition checks. No SSH, submission or filesystem mutations."""
import contextlib,io,json,subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
CODE=Path(__file__).with_name('chunk_release_remote.py').read_text()
IDS=['54755','54786','54787','54789','54795','54796','54797','54798','54799','54800','54801','54802']
def check(label,older,cap,next_active=False,terminal_race=False,tail_count=4,limit=64):
 state={'54786':{'n':older,'cap':cap,'dep':'(null)'},'54787':{'n':500,'cap':limit-10 if next_active else limit-4,'dep':'(null)' if next_active else 'afterany:54786'},'54789':{'n':500,'cap':limit-4,'dep':'afterany:54787'}};commands=[]
 def run(args,**kw):
  commands.append(args);text=''
  if args[0]=='squeue':
   rows=[f'54733_{i}|contam-p2-tail_gt80-v1|'+('RUNNING' if i<4 else 'PENDING')+'|1|None' for i in range(tail_count)]
   for j,s in state.items():
    for i in range(s['n']):
     active=s['dep']=='(null)';running=active and i<min(s['n'],s['cap']);rows.append(f'{j}_{i}|contam-p2-main-c{IDS.index(j):03d}-v1|'+('RUNNING' if running else 'PENDING')+'|1|'+('None' if running else 'JobArrayTaskLimit' if active else 'Dependency'))
   text='\n'.join(rows)
  elif args[1]=='show':
   j=args[3];s=state[j];text=f"JobName=contam-p2-main-c{IDS.index(j):03d}-v1 Partition=research ArrayTaskThrottle={s['cap']} Dependency={s['dep']} "
  else:
   j=args[2].split('=')[1];field,val=args[3].split('=',1)
   if field=='ArrayTaskThrottle':state[j]['cap']=int(val)
   else:state[j]['dep']=val or '(null)'
   possible=sum(min(s['n'],s['cap']) for s in state.values() if s['dep']=='(null)')+min(4,tail_count)
   assert possible<=limit,(label,args,possible)
  if terminal_race and args[:2]==['scontrol','update'] and args[2]=='JobId=54786':
   return SimpleNamespace(returncode=1,stdout='',stderr='54786_951: Job has already finished\n')
  return SimpleNamespace(returncode=0,stdout=text,stderr='')
 out=io.StringIO()
 with patch('subprocess.run',run),patch.object(Path,'write_text',return_value=0),contextlib.redirect_stdout(out):exec(compile(CODE.replace('CAP=64',f'CAP={limit}'),'chunk_release_remote.py','exec'),{})
 result=json.loads(out.getvalue());assert result['status']=='verified',result
 if label=='full_body':assert not result['changes']
 if label=='overlap':assert state['54787']['dep']=='(null)' and state['54787']['cap']==limit-10 and '54786' in state['54789']['dep']
 if label=='older_done':assert state['54787']['cap']==limit-4
 if label.startswith('tail_'):assert state['54787']['cap']==limit-min(4,tail_count)
 assert all(not any('JobId=54733'==v for v in cmd) for cmd in commands)
 return {'case':label,'status':'passed','changes':len(result['changes'])}
results=[]
for limit in [32,64]:
 for args,kw in [
  (('full_body',100,limit-4),{}),
  (('overlap',6,limit-4),{}),
  (('older_done',0,6,True),{}),
  (('reconciled_overlap',6,6,True),{}),
  (('terminal_element_race',6,limit-4),{'terminal_race':True}),
  (('tail_two_remaining',0,6,True),{'tail_count':2}),
  (('tail_finished',0,6,True),{'tail_count':0}),
  (('tail_pending_reserve',0,6,True),{'tail_count':8}),
 ]:
  results.append(dict(check(*args,limit=limit,**kw),limit=limit))
results.append(check('migration_32_to_64',100,30,tail_count=2))
print(json.dumps(results))
