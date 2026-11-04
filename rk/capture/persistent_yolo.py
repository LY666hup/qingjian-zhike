"""RK persistent single-model worker. Loads NPU only behind explicit CLI gate."""
from pathlib import Path
import argparse,hashlib,json,os,platform,signal,threading,time,uuid
from control import Control,read,atomic,IDENTITY
from yolo_frame import Frame,decode_jpeg,make_result,MODEL_SHA
import imgproc_numpy
class Pump:
 def __init__(self,c,infer,clock=time.monotonic):
  self.c=c;self.root=Path(c['run_dir']);self.control=Control(c,clock);self.infer=infer;self.clock=clock;self.last=None;self.last_observed=None;self.count=0;self.discarded=0;self.binding=None;self.last_health=None;self.last_health_at=-1e12
 def health(self,state,error=None):
  b=self.binding or {}
  stamp=(state,error,b.get('binding_id'),self.count,self.discarded)
  if stamp==self.last_health and self.clock()-self.last_health_at<.25:return
  self.last_health=stamp;self.last_health_at=self.clock()
  atomic(self.root/'ai/health.json',dict(state=state,error=error,heartbeat_ms=self.clock()*1000,binding_id=b.get('binding_id'),identity=b.get('identity'),inferred=self.count,discarded_changed_binding=self.discarded))
 def step(self):
  if self.control.expired():self.health('ENDED');return False
  phase=read(self.root/'run.json',{}).get('state','ARMED')
  if phase=='ENDED':self.health('ENDED');return False
  request=read(self.root/'ai/request.json')
  if not request:self.health('READY_NO_BINDING');return True
  if request.get('identity')!=self.control.identity(request['source_key']):raise ValueError('requested source changed')
  self.binding=request # snapshot before inference; never rewrite this in-flight context
  if phase!='CAPTURING':self.health('WAITING_SOURCE');return True
  item=read(self.root/'latest'/(request['source_key']+'.json'))
  if not item:self.health('WAITING_SOURCE');return True
  record=item['record'];jpeg=Path(item['jpeg_path']).resolve()
  if not jpeg.is_relative_to(self.root.resolve()):raise ValueError('JPEG outside run')
  if not record.get('ready')or self.clock()*1000-record['timestamp_ms']>1500:self.health('WAITING_SOURCE');return True
  frame=Frame.freeze(record,jpeg.read_bytes());frame.verify_binding(request['identity'])
  token=(request['binding_id'],record['input_record_id'],record['jpeg_sha256'])
  self.health('ACTIVE')
  if token==self.last:return True
  observed=record.get('observed_frame_id')
  if observed is not None and (type(observed)is not int or not 0<=observed<2**32):raise ValueError('Invalid source observation number')
  observed_key=(request['binding_id'],tuple(request['identity'][k]for k in IDENTITY),observed)if observed is not None else None
  if observed_key is not None and observed_key==self.last_observed:return True
  from action_control import desired
  self.action_context=dict(desired(self.c))
  attempt=uuid.uuid4().hex;y=self.infer(frame,request['binding_id'],attempt);self.count+=1;self.last=token;self.last_observed=observed_key
  if read(self.root/'ai/request.json')!=request:self.discarded+=1;return True
  if self.control.expired()or read(self.root/'run.json',{}).get('state')!='CAPTURING':return True
  if y.get('binding_id')!=request['binding_id']or y.get('identity')!=request['identity']or y.get('input_record_id')!=record['input_record_id']or y.get('jpeg_sha256')!=record['jpeg_sha256']:raise ValueError('executor changed frozen context')
  atomic(self.root/'ai/result.json',y);self.health('ACTIVE');return True

def main():
 p=argparse.ArgumentParser();p.add_argument('--window',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--max-seconds',type=int,default=180);p.add_argument('--decoder-proof',type=Path);p.add_argument('--execute-approved',action='store_true');a=p.parse_args()
 if not a.execute_approved:raise SystemExit('Explicit NPU window approval required')
 if platform.system()!='Linux'or platform.machine()not in ('aarch64','arm64'):raise SystemExit('RK ARM64 only')
 if not 1<=a.max_seconds<=3600:raise SystemExit('Bounded maximum 3600 seconds including ARMED wait')
 c=json.loads(a.window.read_bytes())
 if c.get('approval_status')!='APPROVED_BY_USER':raise SystemExit('Session NPU/capture approval absent')
 if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=c['boot_id']or Control(c).expired():raise SystemExit('boot/window changed')
 if hashlib.sha256(a.model.read_bytes()).hexdigest()!=MODEL_SHA:raise SystemExit('model SHA differs')
 import fcntl,numpy as np
 lock=open('/tmp/qjzc-yolo-single.lock','a+b');fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 root=Path(c['run_dir']);(root/'ai').mkdir(exist_ok=True)
 with(root/'ai/WORKER_ONCE.token').open('x')as f:f.write('one approved worker window')
 stop=threading.Event()
 for s in (signal.SIGINT,signal.SIGTERM):signal.signal(s,lambda *_:stop.set())
 from rknnlite.api import RKNNLite
 decoder=None
 if a.decoder_proof:
  from verify_decoder import verify_proof
  verify_proof(a.decoder_proof)
 runner=RKNNLite();pump=None;started=time.monotonic();saved_first=False;failure=None
 try:
  if a.decoder_proof:
   from persistent_rgb import PersistentRGB
   decoder=PersistentRGB()
  if runner.load_rknn(str(a.model))!=0 or runner.init_runtime(core_mask=RKNNLite.NPU_CORE_0)!=0:raise RuntimeError('RKNN load/init failed')
  def infer(frame,bid,aid):
   nonlocal saved_first
   began=time.monotonic();rgb=decoder.decode(frame.jpeg)if decoder else decode_jpeg(frame.jpeg);decoded=time.monotonic()
   canvas,geom=imgproc_numpy.letterbox(rgb,size=640,pad=114,rounding='round');t=time.monotonic()
   outs=runner.inference(inputs=[canvas[None,...]],data_format=['nhwc']);ms=(time.monotonic()-t)*1000
   if outs is None:raise RuntimeError('empty model output')
   post_started=time.monotonic();y=make_result(frame,outs,geom,binding_id=bid,attempt_id=aid,npu_ms=ms);post_done=time.monotonic()
   y['expired_at_completion']=Control(c).expired()
   y['performance']=dict(decode_ms=(decoded-began)*1000,preprocess_ms=(t-decoded)*1000,postprocess_ms=(post_done-post_started)*1000,rk_receive_to_inference_result_ms=post_done*1000-frame.record['timestamp_ms'],decoder='persistent_gstreamer'if decoder else'legacy_gstreamer')
   if not saved_first:
    d=root/'ai/first';d.mkdir();(d/'input.jpg').write_bytes(frame.jpeg);(d/'record.json').write_bytes(frame.record_bytes)
    np.savez(d/'raw_tensors.npz',**{'output_'+str(i):v for i,v in enumerate(outs)});atomic(d/'yolo.json',y);saved_first=True
   with(root/'ai/results.jsonl').open('a')as log:
    if log.tell()>64*1024**2:raise RuntimeError('result log budget exceeded')
    log.write(json.dumps(y,allow_nan=False)+'\n')
   # Independent bounded latest-frame mailbox. No MediaPipe wait in the NPU path.
   import base64
   frozen=dict(record=frame.record,jpeg=base64.b64encode(frame.jpeg).decode(),yolo=y,binding=dict(pump.binding),action=dict(pump.action_context))
   if read(root/'ai/request.json')==pump.binding:
    atomic(root/'ai/pose_pending.json',frozen)
   return y
  pump=Pump(c,infer);pump.health('READY_NO_BINDING')
  while not stop.is_set()and time.monotonic()-started<a.max_seconds:
   if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=c['boot_id']:raise RuntimeError('boot changed')
   previous=pump.count
   if not pump.step():break
   stop.wait(.005 if pump.count>previous else .05)
 except Exception as e:
  failure=str(e)
  if pump:pump.health('ERROR',str(e))
  raise
 finally:
  try:runner.release()
  finally:
   try:
    if decoder:decoder.close()
   finally:lock.close()
  if pump:pump.health('ERROR' if failure else 'ENDED',failure)
if __name__=='__main__':main()
