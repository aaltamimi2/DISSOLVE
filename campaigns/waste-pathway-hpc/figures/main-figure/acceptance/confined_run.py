import ctypes,os,sys,runpy,json
from pathlib import Path
sys.dont_write_bytecode=True
script=Path(sys.argv[1]).resolve();args=sys.argv[2:];out=Path(args[args.index('--out')+1]).resolve();out.mkdir(parents=True,exist_ok=True)
lib=ctypes.CDLL(None,use_errno=True)
class Rules(ctypes.Structure):_fields_=[('handled_access_fs',ctypes.c_uint64)]
class Beneath(ctypes.Structure):_pack_=1;_fields_=[('allowed_access',ctypes.c_uint64),('parent_fd',ctypes.c_int32)]
abi=lib.syscall(444,0,0,1);assert abi>=3,abi
mask=sum(1<<i for i in [1,4,5,6,7,8,9,10,11,12,13,14])
rules=Rules(mask);fd=lib.syscall(444,ctypes.byref(rules),ctypes.sizeof(rules),0);assert fd>=0,ctypes.get_errno()
pfd=os.open(out,os.O_PATH|os.O_CLOEXEC);rule=Beneath(mask,pfd)
assert lib.syscall(445,fd,1,ctypes.byref(rule),0)==0,ctypes.get_errno()
nfd=os.open('/dev/null',os.O_PATH|os.O_CLOEXEC);nullrule=Beneath(1<<1,nfd)
assert lib.syscall(445,fd,1,ctypes.byref(nullrule),0)==0,ctypes.get_errno()
os.close(nfd)
assert lib.prctl(38,1,0,0,0)==0,ctypes.get_errno()
assert lib.syscall(446,fd,0)==0,ctypes.get_errno()
os.close(pfd);os.close(fd)
events=[]
def audit(event,args):
 if event=='open' and isinstance(args[0],(str,bytes)):
  p=Path(os.fsdecode(args[0])).resolve();mode=args[1];flags=args[2]
  write=bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
  if write and str(p)!='/dev/null':
   events.append(str(p))
   if not p.is_relative_to(out):raise RuntimeError('Write outside output: '+str(p))
sys.addaudithook(audit)
sys.argv=[str(script)]+args
exit_code=0
try:runpy.run_path(str(script),run_name='__main__')
except SystemExit as e:exit_code=e.code or 0
(out/('write-confinement-'+script.stem+'.json')).write_text(json.dumps({'landlock_abi':abi,'kernel_write_confinement':True,'allowed_write_tree':str(out),'python_write_paths':sorted(set(events)),'writes_outside_output':False,'device_sink_exception':'/dev/null (Cairo library discovery subprocess)'},indent=2)+'\n')

if exit_code:sys.exit(exit_code)
