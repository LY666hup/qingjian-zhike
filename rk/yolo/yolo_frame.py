"""One saved JPEG -> RKNN YOLOv8 result. Never opens camera/network or runs pose.
Execution is explicit, ARM64-only. Output contract supports independent Windows adapter.
"""
from dataclasses import dataclass
import argparse,hashlib,json,math,os,platform,subprocess,time,uuid
from pathlib import Path
import numpy as np
import imgproc_numpy as image_ops
from jpeg_header import jpeg_size
from yolo_reference import _xywh_to_xyxy,_dfl_dist2bbox,_grid,nms
IDENTITY=('session_id','boot_id','camera_id','processing_mode','fpga_bit_sha256','processor')
MODEL_SHA='c9f43d33ca07413bee582baf3bfd76a91b87c240482ff9e7a91e47f3a54b495b'
def sha(b):return hashlib.sha256(b).hexdigest()
def wire(d):return json.dumps(d,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
@dataclass(frozen=True)
class Frame:
 jpeg:bytes
 record_bytes:bytes
 @classmethod
 def freeze(cls,record,jpeg):
  if type(jpeg)is not bytes:raise ValueError('immutable original JPEG required')
  r=json.loads(wire(record));w,h=jpeg_size(jpeg)
  for k in IDENTITY:
   if type(r.get(k))is not str or not r[k]:raise ValueError('missing identity '+k)
  if r['camera_id']not in ('A','B')or r['processing_mode']not in ('original','gray','gaussian','histogram'):raise ValueError('unconfigured source')
  for k in ('fpga_bit_sha256','jpeg_sha256'):
   v=r[k]
   if len(v)!=64 or any(c not in '0123456789abcdef'for c in v):raise ValueError('invalid SHA '+k)
  if r['jpeg_sha256']!=sha(jpeg):raise ValueError('JPEG SHA mismatch')
  if type(r.get('width'))is not int or type(r.get('height'))is not int:raise ValueError('width and height are required integers')
  if r.get('preview_size')!=[w,h]or r['width']!=w or r['height']!=h:raise ValueError('actual JPEG dimensions mismatch')
  if not isinstance(r.get('input_record_id'),str)or not r['input_record_id']:raise ValueError('input_record_id required')
  if type(r.get('timestamp_ms'))is not int or r['timestamp_ms']<0 or type(r.get('time_source'))is not str or not r['time_source']:raise ValueError('capture time provenance missing')
  return cls(jpeg,wire(r))
 @property
 def record(self):return json.loads(self.record_bytes)
 def verify_binding(self,identity):
  if identity!={k:self.record[k]for k in IDENTITY}:raise ValueError('selected binding differs from original frame')
def decode_jpeg(jpeg):
 w,h=jpeg_size(jpeg)
 cmd=['gst-launch-1.0','-q','fdsrc','fd=0','!','jpegparse','!','jpegdec','!','videoconvert','!','video/x-raw,format=RGB','!','fdsink','fd=1','sync=false']
 p=subprocess.run(cmd,input=jpeg,capture_output=True,timeout=5,env=dict(os.environ,GST_REGISTRY_UPDATE='no'))
 if p.returncode or len(p.stdout)!=w*h*3:raise ValueError('JPEG RGB decode failed: '+p.stderr[-800:].decode(errors='replace'))
 return np.frombuffer(p.stdout,np.uint8).reshape(h,w,3)
def decode_heads(outs,conf=.35,iou=.45):
 if not 0<=conf<=1 or not 0<=iou<=1:raise ValueError('thresholds')
 outs=[np.asarray(o)for o in outs]
 if any(not np.isfinite(o).all()for o in outs):raise ValueError('nonfinite output tensor')
 if len(outs)==1:
  o=outs[0]
  if o.ndim!=3 or o.shape[0]!=1 or (o.shape[1]!=84 and o.shape[2]!=84):raise ValueError('expected detect head [1,84,N] or [1,N,84]')
  a=o[0].T if o.shape[1]==84 else o[0]
  boxes=_xywh_to_xyxy(a[:,:4]);scores=a[:,4:];kind='detect84'
 elif len(outs)==6:
  regs=[o for o in outs if o.ndim==4 and o.shape[:2]==(1,64)]
  clss=[o for o in outs if o.ndim==4 and o.shape[:2]==(1,80)]
  if len(regs)!=3 or len(clss)!=3:raise ValueError('unknown DFL output shapes')
  allb=[];alls=[]
  for h in (80,40,20):
   reg=[o for o in regs if o.shape[2:]==(h,h)];cls=[o for o in clss if o.shape[2:]==(h,h)]
   if len(reg)!=1 or len(cls)!=1:raise ValueError('DFL scale mismatch')
   allb.append(_dfl_dist2bbox(reg[0][0].reshape(64,-1).T,_grid(h,h,640//h),640//h));alls.append(cls[0][0].reshape(80,-1).T)
  boxes=np.concatenate(allb);scores=np.concatenate(alls);kind='dfl_split'
 else:raise ValueError('unsupported output set '+str([o.shape for o in outs]))
 if np.any(scores<0)or np.any(scores>1):raise ValueError('scores are not exported probabilities; refuse guessed activation')
 classes=scores.argmax(1);confidence=scores.max(1)
 selected=[]
 # Filter person before NMS; a stronger non-person box must not suppress person.
 ix=np.where((classes==0)&(confidence>=conf))[0]
 if len(ix):selected=ix[nms(boxes[ix],confidence[ix],iou)].tolist()
 return boxes[selected].reshape(-1,4),confidence[selected],kind

def make_result(frame,outs,geom,*,binding_id,attempt_id,simulation=False,npu_ms=None,conf=.35,iou=.45):
 if not binding_id or not attempt_id:raise ValueError('frozen binding and attempt required')
 r=frame.record
 if geom.get('src')!=r['preview_size']:raise ValueError('model geometry differs from actual JPEG')
 boxes,scores,kind=decode_heads(outs,conf,iou);boxes=image_ops.boxes_to_source(boxes,geom)
 detections=[]
 for b,s in zip(boxes,scores):
  if not np.isfinite(b).all()or b[2]<=b[0]or b[3]<=b[1]:continue
  detections.append(dict(class_id=0,class_name='person',person_id=None,score=float(s),xyxy=[float(v)for v in b]))
 return dict(schema='qjzc.yolo.frame.v1',simulation=simulation,frame_record=r,identity={k:r[k]for k in IDENTITY},
  input_record_id=r['input_record_id'],jpeg_sha256=r['jpeg_sha256'],preview_size=r['preview_size'],
  source_frame_id=r.get('source_frame_id'),parent_raw_sha256=r.get('parent_raw_sha256'),
  timestamp_ms=r['timestamp_ms'],time_source=r['time_source'],binding_id=binding_id,analysis_attempt_id=attempt_id,
  inference=dict(device='SIMULATED'if simulation else'rk3576-npu',backend='synthetic-tensors'if simulation else'rknnlite',model_sha256=MODEL_SHA,npu_ms=npu_ms,decode_kind=kind,conf_threshold=conf,iou_threshold=iou),
  geometry=dict(bbox_format='xyxy',coordinate_space='preview_pixels',original_size=r['preview_size'],model_size=[640,640],
   transform=dict(type='identity',scale_x=1,scale_y=1,pad_x=0,pad_y=0),model_to_jpeg=geom,
   tracker=False,person_filter=True,timestamp_source=r['time_source']),detections=detections)

def main():
 p=argparse.ArgumentParser();p.add_argument('--jpeg',type=Path,required=True);p.add_argument('--record',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--binding',type=Path,required=True);p.add_argument('--attempt-id');p.add_argument('--execute-approved',action='store_true');a=p.parse_args()
 # No native runtime imports before authorization and platform guard.
 if not a.execute_approved:raise SystemExit('Explicit approved RK execution required; no inference started')
 if platform.system()!='Linux'or platform.machine()not in ('aarch64','arm64'):raise SystemExit('RK ARM64 Linux only')
 f=Frame.freeze(json.loads(a.record.read_bytes()),a.jpeg.read_bytes());b=json.loads(a.binding.read_bytes());f.verify_binding(b['identity'])
 if b.get('state')!='ACTIVE'or not b.get('binding_id'):raise SystemExit('binding not active')
 if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=f.record['boot_id']:raise SystemExit('RK boot mismatch')
 if time.monotonic_ns()//1000000>=b['expires_monotonic_ms']:raise SystemExit('binding expired')
 if a.out.exists():raise SystemExit('output already exists; do not overwrite or rerun')
 if sha(a.model.read_bytes())!=MODEL_SHA:raise SystemExit('model SHA differs')
 import fcntl
 lock=open('/tmp/qjzc-yolo-single.lock','a+b')
 fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 a.out.mkdir(parents=True,exist_ok=False)
 (a.out/'input.jpg').write_bytes(f.jpeg);(a.out/'record.json').write_bytes(f.record_bytes);(a.out/'binding.json').write_bytes(wire(b))
 rgb=decode_jpeg(f.jpeg);canvas,geom=image_ops.letterbox(rgb,size=640,pad=114,rounding='round')
 # Preserve the actual model input before runtime loading/inference can fail.
 (a.out/'canvas.sha256').write_text(sha(canvas.tobytes())+'\n')
 (a.out/'letterbox.json').write_bytes(wire(geom))
 from rknnlite.api import RKNNLite
 runner=RKNNLite()
 try:
  if runner.load_rknn(str(a.model))!=0:raise RuntimeError('load_rknn failed')
  if runner.init_runtime(core_mask=RKNNLite.NPU_CORE_0)!=0:raise RuntimeError('init_runtime failed')
  if time.monotonic_ns()//1000000>=b['expires_monotonic_ms']:raise RuntimeError('binding expired before inference')
  began=time.monotonic();outs=runner.inference(inputs=[canvas[None,...]],data_format=['nhwc']);elapsed=(time.monotonic()-began)*1000
  if outs is None:raise RuntimeError('empty inference')
  # A dedicated new directory preserves all original outputs, frozen context and input bytes.
  np.savez(a.out/'raw_tensors.npz',**{'output_'+str(i):v for i,v in enumerate(outs)})
  (a.out/'output_shapes.json').write_bytes(wire(dict(outputs=[dict(index=i,shape=list(np.asarray(v).shape),dtype=str(np.asarray(v).dtype))for i,v in enumerate(outs)],npu_ms=elapsed,model_sha256=MODEL_SHA)))
  result=make_result(f,outs,geom,binding_id=b['binding_id'],attempt_id=a.attempt_id or uuid.uuid4().hex,npu_ms=elapsed)
  (a.out/'input.jpg').write_bytes(f.jpeg);(a.out/'record.json').write_bytes(f.record_bytes);(a.out/'binding.json').write_bytes(wire(b))
  import importlib.metadata
  try:runtime_version=importlib.metadata.version('rknn-toolkit-lite2')
  except importlib.metadata.PackageNotFoundError:runtime_version='unknown'
  result['expired_at_completion']=time.monotonic_ns()//1000000>=b['expires_monotonic_ms']
  result['inference'].update(runtime_version=runtime_version,numpy_version=np.__version__)
  (a.out/'yolo.json').write_bytes(wire(result));(a.out/'canvas.sha256').write_text(sha(canvas.tobytes())+'\n')
  print('YOLO_EXECUTED; accuracy and Windows real display still require acceptance')
 finally:
  runner.release();lock.close()
if __name__=='__main__':main()
