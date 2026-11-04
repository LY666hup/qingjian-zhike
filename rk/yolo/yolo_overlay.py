"""Windows-only YOLO result gate. No inference, network or count calculation."""
import copy,json,math,time,hashlib
IDENTITY=('session_id','boot_id','camera_id','processing_mode','fpga_bit_sha256','processor')
class DetectionGate:
 def __init__(self,clock=time.monotonic,ttl=2,simulation=False):
  self.clock=clock;self.ttl=ttl;self.simulation=simulation;self.binding=None;self.result=None;self.at=None;self.seen=set()
 def activate(self,binding_id,identity,valid_for_seconds=30):
  if not binding_id or any(not isinstance(identity.get(k),str)or not identity[k]for k in IDENTITY):raise ValueError('full acknowledged binding required')
  if not 0<valid_for_seconds<=60:raise ValueError('bounded local acknowledgment lease required')
  self.binding_until=self.clock()+valid_for_seconds
  self.binding=(binding_id,{k:identity[k]for k in IDENTITY});self.result=None;self.at=None
 def invalidate(self):self.binding=None;self.result=None;self.at=None
 def accept(self,raw):
  obj=json.loads(raw)
  if not self.binding or self.clock()>=self.binding_until:raise ValueError('no current acknowledged binding')
  if obj.get('expired_at_completion'):raise ValueError('result completed after RK deadline')
  bid,ident=self.binding
  if obj.get('schema')!='qjzc.yolo.frame.v1'or obj.get('binding_id')!=bid or obj.get('identity')!=ident:raise ValueError('result binding/identity mismatch')
  if type(obj.get('simulation'))is not bool or obj['simulation']!=self.simulation:raise ValueError('simulation mismatch')
  for k in ('input_record_id','jpeg_sha256','analysis_attempt_id'):
   if not isinstance(obj.get(k),str)or not obj[k]:raise ValueError('missing '+k)
  if type(obj.get('timestamp_ms'))is not int or obj['timestamp_ms']<0 or not obj.get('time_source'):raise ValueError('missing capture time')
  size=obj.get('preview_size');g=obj.get('geometry',{})
  if not isinstance(size,list)or len(size)!=2 or any(type(v)is not int or v<=0 for v in size):raise ValueError('size')
  if g.get('bbox_format')!='xyxy'or g.get('coordinate_space')!='preview_pixels'or g.get('original_size')!=size or g.get('transform',{}).get('type')!='identity':raise ValueError('geometry')
  if g.get('tracker')is not False or g.get('person_filter')is not True:raise ValueError('unverified tracker/classes')
  for b in obj['detections']:
   coords=b['xyxy'];score=b['score']
   if len(coords)!=4 or any(type(v)not in (int,float)or not math.isfinite(v)for v in coords):raise ValueError('coordinates')
   x1,y1,x2,y2=coords
   if not(0<=x1<x2<=size[0]and 0<=y1<y2<=size[1])or type(score)not in (int,float)or not math.isfinite(score)or not 0<=score<=1:raise ValueError('box/score')
   if b.get('class_id')!=0 or b.get('person_id')is not None:raise ValueError('person identity')
  # Same business frame/result replay cannot refresh display TTL or resurrect old data.
  token=(bid,obj['input_record_id'],obj['jpeg_sha256'])
  if token in self.seen:return False
  self.seen.add(token);self.result=copy.deepcopy(obj);self.at=self.clock();return True
 def boxes(self,key,video,actual_size,live=True):
  r=self.result
  if not live or not self.binding or self.clock()>=self.binding_until or r is None or self.at is None or self.clock()-self.at>=self.ttl:return []
  if key!=(r['identity']['camera_id'],r['identity']['processing_mode']):return []
  if any(not video.get(k)or video[k]!=r['identity'][k]for k in IDENTITY):return []
  if any(video.get(k)!=r[k]for k in ('input_record_id','jpeg_sha256'))or list(actual_size)!=r['preview_size']:return []
  if any(video.get(k)!=r[k]for k in ('timestamp_ms','time_source')):return []
  return copy.deepcopy(r['detections'])
