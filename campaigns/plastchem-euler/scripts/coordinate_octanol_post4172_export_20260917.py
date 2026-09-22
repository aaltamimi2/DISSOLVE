"""Hold the panel parent at its completed-pass export boundary, then let the existing handoff proceed."""
import os,signal,time,json,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];pid=825535;supervisor=851353;P=Path(f'/proc/{pid}');receipt=ROOT/'state/octanol-post4172-export-boundary-20260917.json'
def save(**kw):receipt.write_text(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'panel_pid':pid,'existing_handoff_pid':supervisor,**kw},indent=2)+'\n')
def children():return (P/f'task/{pid}/children').read_text().split()
held=False
try:
 while P.exists():
  assert Path(f'/proc/{supervisor}').exists(),'Existing handoff no longer live; do not change panel worker'
  assert 'watch_thermodynamics.py' in (P/'cmdline').read_text()
  kids=children()
  if (P/'wchan').read_text()=='do_wait' and len(kids)==1:
   child=Path('/proc')/kids[0]
   if child.exists() and 'export_thermodynamic_table.py' in (child/'cmdline').read_text():
    os.kill(pid,signal.SIGSTOP);held=True;time.sleep(.1)
    # If the child was reaped before STOP landed, the boundary is unproven.
    if children()!=kids:
     os.kill(pid,signal.SIGCONT);held=False;time.sleep(2);continue
    save(status='parent_held_at_export_boundary',export_pid=int(kids[0]))
    while child.exists():
     stat=(child/'stat').read_text().split(') ',1)[1].split()
     if stat[0]=='Z':break
     time.sleep(2)
    # The child is terminal; the stopped parent cannot start the next pass.
    os.kill(pid,signal.SIGTERM);os.kill(pid,signal.SIGCONT);held=False
    while P.exists():time.sleep(.2)
    save(status='export_terminal_parent_stopped_existing_handoff_can_continue',export_pid=int(kids[0]))
    print(receipt.read_text(),flush=True);break
  time.sleep(2)
 else:save(status='parent_already_ended_no_action')
finally:
 if held and P.exists():os.kill(pid,signal.SIGCONT)
