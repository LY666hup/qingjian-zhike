import sys,json,hashlib,unittest,copy,os,tempfile
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent;BASE=ROOT.parent
from yolo_frame import Frame,decode_heads,make_result,sha
import imgproc_numpy
from yolo_overlay import DetectionGate
JPEG=(BASE/'pose_v3_3_received/samples/frame_640x360.jpg').read_bytes()
IDENT=dict(session_id='SIM-session',boot_id='SIM-boot',camera_id='A',processing_mode='gaussian',fpga_bit_sha256='a'*64,processor='fpga')
def record(jpeg=JPEG):
 from jpeg_header import jpeg_size
 w,h=jpeg_size(jpeg)
 return dict(IDENT,input_record_id='SIM-record',jpeg_sha256=sha(jpeg),width=w,height=h,preview_size=[w,h],timestamp_ms=1000,time_source='SIM_capture')
def tensor():
 t=np.zeros((1,84,2),np.float32);t[0,:4,0]=[320,320,320,360];t[0,4,0]=.9
 t[0,:4,1]=[320,320,320,360];t[0,5,1]=.99 # non-person must not suppress person
 return t
def result(f=None):
 f=f or Frame.freeze(record(),JPEG);w,h=f.record['preview_size'];_,g=imgproc_numpy.letterbox(np.zeros((h,w,3),np.uint8),size=640,pad=114,rounding='round')
 return make_result(f,[tensor()],g,binding_id='SIM-bind',attempt_id='SIM-attempt',simulation=True)
class FrameAndOutput(unittest.TestCase):
 def test_identity_SHA_size_required_and_input_immutable(self):
  r=record();f=Frame.freeze(r,JPEG);r['camera_id']='B';self.assertEqual(f.record['camera_id'],'A')
  for key in IDENT:
   r=record();r.pop(key)
   with self.assertRaises((ValueError,KeyError)):Frame.freeze(r,JPEG)
  for key,value in [('jpeg_sha256','b'*64),('preview_size',[960,540]),('input_record_id',''),('timestamp_ms',None)]:
   r=record();r[key]=value
   with self.assertRaises(ValueError):Frame.freeze(r,JPEG)
 def test_person_filter_before_NMS_and_xyxy_original(self):
  o=result();self.assertEqual(len(o['detections']),1);b=o['detections'][0]
  np.testing.assert_allclose(b['xyxy'],[160,0,480,360]);self.assertIsNone(b['person_id']);self.assertAlmostEqual(b['score'],.9,places=5)
 def test_960_source_geometry_is_not_640_or_hardcoded(self):
  jpeg=(BASE/'pose_v3_3_received/samples/frame_0001_seq_000012.jpg').read_bytes();f=Frame.freeze(record(jpeg),jpeg)
  self.assertEqual(f.record['preview_size'],[960,540]);np.testing.assert_allclose(result(f)['detections'][0]['xyxy'],[240,0,720,540])
 def test_DFL_and_shape_refusal(self):
  heads=[]
  for h in (80,40,20):heads.extend([np.zeros((1,64,h,h),np.float32),np.zeros((1,80,h,h),np.float32)])
  self.assertEqual(len(decode_heads(heads)[0]),0)
  heads[1][0,0,40,40]=.9;self.assertEqual(len(decode_heads(heads)[0]),1)
  for outs in ([np.zeros((1,56,8400))],[np.full((1,84,2),np.nan)],[np.ones((1,84,2))*2]):
   with self.assertRaises(ValueError):decode_heads(outs)
 def test_bound_source_mismatch_rejected(self):
  f=Frame.freeze(record(),JPEG)
  with self.assertRaises(ValueError):f.verify_binding(dict(IDENT,camera_id='B'))
class GateTests(unittest.TestCase):
 def setUp(self):
  self.now=[0.];self.g=DetectionGate(clock=lambda:self.now[0],simulation=True);self.g.activate('SIM-bind',IDENT);self.o=result()
 def test_live_exact_frame_required(self):
  self.g.accept(json.dumps(self.o));r=record();self.assertEqual(len(self.g.boxes(('A','gaussian'),r,[640,360])),1)
  for key,value in [('camera_id','B'),('processing_mode','gray'),('session_id','wrong'),('boot_id','wrong'),('fpga_bit_sha256','b'*64),('processor','rk'),('input_record_id','wrong'),('jpeg_sha256','b'*64),('time_source','wrong')]:
   self.assertEqual(self.g.boxes(('A','gaussian'),dict(r,**{key:value}),[640,360]),[])
  self.assertEqual(self.g.boxes(('A','gray'),r,[640,360]),[]);self.assertEqual(self.g.boxes(('A','gaussian'),r,[960,540]),[]);self.assertEqual(self.g.boxes(('A','gaussian'),r,[640,360],False),[])
 def test_late_result_after_switch_rejected(self):
  self.g.activate('new-bind',dict(IDENT,camera_id='B'))
  with self.assertRaises(ValueError):self.g.accept(json.dumps(self.o))
 def test_duplicate_does_not_refresh_TTL(self):
  self.g.accept(json.dumps(self.o));self.now[0]=1.8;self.assertFalse(self.g.accept(json.dumps(self.o)))
  self.now[0]=2.1;self.assertEqual(self.g.boxes(('A','gaussian'),record(),[640,360]),[])
 def test_expired_binding_and_RK_completion_rejected(self):
  bad=copy.deepcopy(self.o);bad['expired_at_completion']=True
  with self.assertRaises(ValueError):self.g.accept(json.dumps(bad))
  self.now[0]=31
  with self.assertRaises(ValueError):self.g.accept(json.dumps(self.o))
 def test_simulation_and_invalid_boxes_refused(self):
  g=DetectionGate();g.activate('SIM-bind',IDENT)
  with self.assertRaises(ValueError):g.accept(json.dumps(self.o))
  bad=copy.deepcopy(self.o);bad['detections'][0]['person_id']='P1'
  with self.assertRaises(ValueError):self.g.accept(json.dumps(bad))
if __name__=='__main__':unittest.main(verbosity=2)

