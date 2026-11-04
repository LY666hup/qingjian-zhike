"""File IPC for one active AI source. No camera control or inference here."""
from pathlib import Path
import json,time,os,uuid
from mosaic_core import ROIS,BIT
IDENTITY=('session_id','boot_id','camera_id','processing_mode','fpga_bit_sha256','processor')
def atomic(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
 with tmp.open('x')as f:json.dump(value,f,allow_nan=False);f.flush();os.fsync(f.fileno())
 for attempt in range(8):
  try:os.replace(tmp,path);break
  except PermissionError:
   if os.name!='nt' or attempt==7:raise
   time.sleep(.005*(attempt+1)) # Windows readers can briefly hold a delete-sharing lock.

def read(path,default=None):
 try:return json.loads(Path(path).read_bytes())
 except FileNotFoundError:return default
class Control:
 def __init__(self,c,clock=time.monotonic):self.c=c;self.root=Path(c['run_dir']);self.clock=clock
 def identity(self,key):
  camera,mode,_=ROIS[key]
  return dict(session_id=self.c['session_id'],boot_id=self.c['boot_id'],camera_id=camera,processing_mode=mode,fpga_bit_sha256=BIT,processor='fpga')
 def expired(self):return self.clock()*1000>=self.c['expires_monotonic_ms']
 def bind(self,b):
  if self.expired()or read(self.root/'run.json',{}).get('state')=='ENDED':raise ValueError('session ended')
  key=b.get('source_key');bid=b.get('binding_id')
  if key not in ROIS or key!='B_original' or b.get('identity')!=self.identity(key):raise ValueError('source identity mismatch')
  if not isinstance(bid,str)or not 8<=len(bid)<=64 or not bid.isalnum():raise ValueError('binding id')
  r=dict(binding_id=bid,source_key=key,identity=self.identity(key));atomic(self.root/'ai/request.json',r);return r
 def unbind(self,b):
  if read(self.root/'ai/request.json')==b:
   atomic(self.root/'ai/request.json',None);return True
  return False
 def reply(self,key):
  if key not in ROIS:raise ValueError('unknown source')
  run=read(self.root/'run.json',{});state=run.get('state','ARMED')
  if state=='CAPTURING'and self.clock()*1e9-run.get('heartbeat_ns',0)>2e9:return 410,dict(state='ENDED',error='capture heartbeat stopped')
  if self.expired()or state=='ENDED':return 410,dict(state='ENDED')
  h=read(self.root/'ai/health.json',{})
  r=read(self.root/'ai/request.json',{}) or {}
  reply=dict(schema='qjzc.yolo.transport.v1',capture_state=state,state=h.get('state','WAITING_WORKER'),error=h.get('error'),ack=None,result=None)
  if self.clock()*1000-h.get('heartbeat_ms',-1e12)>2000:reply['state']='WORKER_NOT_UPDATING';return 200,reply
  if r.get('source_key')!=key:return 200,reply
  if h.get('binding_id')==r.get('binding_id')and h.get('identity')==self.identity(key)and h.get('state')=='ACTIVE':
   reply['ack']=r
   y=read(self.root/'ai/result.json')
   if state=='CAPTURING'and y and y.get('binding_id')==r['binding_id']and y.get('identity')==r['identity']:reply['result']=y
  return 200,reply
