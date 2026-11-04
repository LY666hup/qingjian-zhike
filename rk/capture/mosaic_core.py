"""Pure byte operations for the diagnostic mosaic. No capture or inference."""
from dataclasses import dataclass,field
import hashlib,json
import queue

ROIS = {
 'A_original':('A','original',(0,0,640,360)),
 'A_gray':('A','gray',(640,0,640,360)),
 'A_gaussian':('A','gaussian',(1280,0,640,360)),
 'A_histogram':('A','histogram',(0,360,640,360)),
 'B_original':('B','original',(640,360,640,360)),
}
LABELS=dict(zip(ROIS,('A原图','A灰度','A高斯','A均衡','B原图')))
BIT='78fd65db99e9193e292a5a6146a44d81c677a3f18c4883b6da9b093f65731d7f'

@dataclass(frozen=True)
class RawFrame:
 source_frame_id:str
 plane:bytes
 stride:int
 bytesused:int
 data_offset:int
 timestamp_ms:int
 v4l2_sequence:int
 width:int=1920
 height:int=1080
 raw_sha256:str=field(init=False,repr=False)

 def __post_init__(self):
  if type(self.plane)is not bytes:raise ValueError('raw frame must own immutable bytes')
  if (self.width,self.height)!=(1920,1080):raise ValueError('mosaic dimensions must be exactly 1920x1080')
  if self.stride<3840 or self.stride%4:raise ValueError('invalid YUYV stride')
  needed=self.data_offset+(self.height-1)*self.stride+self.width*2
  if self.data_offset<0 or self.bytesused<needed or self.bytesused!=len(self.plane):raise ValueError('incomplete/mismatched bytesused')
  object.__setattr__(self,'raw_sha256',hashlib.sha256(self.plane).hexdigest())

 def crop(self,roi):
  x,y,w,h=roi
  if x%2 or w%2 or min(x,y)<0 or min(w,h)<=0 or x+w>self.width or y+h>self.height:raise ValueError('ROI invalid or not chroma-aligned')
  # Copy from immutable userspace bytes, NEVER a borrowed mmap view.
  return b''.join(self.plane[self.data_offset+(y+j)*self.stride+x*2:self.data_offset+(y+j)*self.stride+(x+w)*2]for j in range(h))

 def manifest(self):
  return dict(source_frame_id=self.source_frame_id,source_frame_id_kind='rk_received_mosaic_record',
    raw_sha256=self.raw_sha256,bytesused=self.bytesused,stride=self.stride,
    data_offset=self.data_offset,width=self.width,height=self.height,
    timestamp_ms=self.timestamp_ms,time_source='rk_yuyv_receive_monotonic',
    v4l2_sequence=self.v4l2_sequence,camera_source_frame_id=None)

class GlobalFault(ValueError):pass

class Protocol:
 """Actual Ly QJ5V v1 parser, reused byte-for-byte; no guessed field map."""
 def __init__(self,spec,allow_simulation=False):
  from pathlib import Path
  self.spec=spec;self.last_metadata=None
  if spec.get('simulation')and not allow_simulation:raise ValueError('simulation protocol forbidden for real capture')
  if spec.get('status')!='CONFIRMED_BY_LY' or spec.get('bit_sha256')!=BIT or spec.get('protocol')!='QJ5V/1':raise ValueError('QJ5V definition is not confirmed for this bit')
  if spec.get('reserved_regions')!=[[1280,360,640,360],[0,720,1920,360]]:raise ValueError('reserved geometry differs')
  if spec.get('metadata_roi')!=[1280,360,40,1]:raise ValueError('QJ5V placement differs')
  if spec.get('rois')!={key:list(value[2])for key,value in ROIS.items()}:raise ValueError('five ROI definitions differ')
  path=Path(__file__).with_name('ly_parse_qj5v.py')
  expected='d183048ed943baabc519c368762018470179365e7c21c961e63bb959764d98e8'
  if hashlib.sha256(path.read_bytes()).hexdigest()!=expected or spec.get('parser_sha256')!=expected:raise ValueError('Ly parser provenance mismatch')

 def decode(self,frame):
  from ly_parse_qj5v import parse_frame
  self.last_metadata=None
  end=frame.data_offset+frame.stride*1080
  if len(frame.plane)<end:raise ValueError('QJ5V parser requires all 1080 full-stride rows')
  parsed=parse_frame(frame.plane[frame.data_offset:end],frame.stride)
  base=frame.data_offset+360*frame.stride+2560
  parsed['message_hex']=bytes(frame.plane[base+2*i]for i in range(40)).hex()
  self.last_metadata=parsed
  if parsed['fault_mask']!=0:raise GlobalFault('FPGA fault_mask='+hex(parsed['fault_mask']))
  return {v['view']:dict(ready=v['ready'],observed_frame_id=v['observed_frame_id'],observed_bits=32,
    observed_meaning='32-bit branch SOF observation number of the leased published preview snapshot; not sensor exposure ID',
    composite_sequence=parsed['composite_sequence'],ready_mask=parsed['ready_mask'],fault_mask=parsed['fault_mask'])for v in parsed['views']}

def diagnostics(packed,previous,observed,now_ms):
 # Indicators are hypotheses, never a synthetic hardware PASS.
 samples=[packed[y*1280+x*2]for y in range(0,360,16)for x in range(0,640,16)]
 mean=sum(samples)/len(samples);variance=sum((v-mean)**2 for v in samples)/len(samples)
 rows=[sum(samples[i:i+40])/40 for i in range(0,len(samples),40)]
 row_delta=max((abs(b-a)for a,b in zip(rows,rows[1:])),default=0)
 digest=hashlib.sha256(packed).hexdigest()
 old=previous or {};same_id=old.get('observed_frame_id')==observed['observed_frame_id']
 since=old.get('unchanged_since_ms',now_ms)if same_id else now_ms
 delta=None if not previous else (observed['observed_frame_id']-old['observed_frame_id'])%(1<<observed['observed_bits'])
 return dict(luma_mean=mean,luma_std=variance**.5,black_suspect=mean<=20 and variance<9,
   row_stripe_suspect=row_delta>80,raw_ROI_sha256=digest,identical_pixels=old.get('raw_ROI_sha256')==digest,
   observed_frame_id=observed['observed_frame_id'],observed_delta_modulo=delta,
   unchanged_since_ms=since,observed_stall_suspect=same_id and now_ms-since>=1000,
   observed_backward_or_large_jump_suspect=delta is not None and delta>0x80000000,
   ready=observed['ready'],misalignment='UNVERIFIED_WITHOUT_CONFIRMED_TEST_PATTERN')

def offer_latest(q,item,protected_parent=None):
 """One producer, bounded queue. Preserve first common five-ready batch."""
 try:q.put_nowait(item);return True,None
 except queue.Full:
  try:old=q.get_nowait()
  except queue.Empty:q.put_nowait(item);return True,None
  if protected_parent and old[0].source_frame_id==protected_parent:
   q.put_nowait(old);return False,'new_dropped_to_preserve_common_anchor'
  q.put_nowait(item);return True,'old_evicted'
