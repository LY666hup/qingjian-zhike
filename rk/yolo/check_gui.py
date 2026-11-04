import os,sys,tempfile,json,unittest
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path.cwd();SRC=ROOT/'work/yolo_single_frame_v1';WIN=ROOT/'outputs/YOLO单路集成候选_20261102/Windows_工作副本'
sys.path.insert(0,str(SRC));sys.path.insert(0,str(WIN))
import app as gui
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from test_yolo import result,record,IDENT,JPEG
from yolo_overlay import DetectionGate
APP=QApplication([]);TMP=tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='yolo_gui_');gui.ROOT=Path(TMP.name)
class Video:
 def take(self):return None
 def info(self):return dict(state='receiving',counts={'decoded':1},terminal=False,detail='OFFLINE SYNTHETIC GUI TEST')
 def is_alive(self):return False
 def stop(self):pass
 def join(self,*a):pass
class GuiCheck(unittest.TestCase):
 def setUp(self):
  self.w=gui.Monitor();self.w.timer.stop();self.w.yolo_gate=DetectionGate(simulation=True);self.w.select_preview_source(('A','gaussian'))
  self.w.images[('A','gaussian')]=(QImage.fromData(JPEG),record(),0);self.w.workers[('A','gaussian')]=Video()
  self.w.acknowledge_yolo_binding('SIM-bind',IDENT)
 def tearDown(self):
  self.w.workers={};self.w.close()
 def test_actual_GUI_consumes_and_paints_detection(self):
  self.assertTrue(self.w.receive_yolo_result(json.dumps(result())))
  self.assertEqual(len(self.w.pose_canvas.detections),1)
  self.w.resize(1640,940);self.w.show();APP.processEvents();self.w.pose_canvas.grab() # actual QPainter path
  self.assertEqual(self.w.pose_canvas.people,[]) # no pose inference/fixture landmarks
 def test_changed_video_and_source_clear_boxes(self):
  self.w.receive_yolo_result(json.dumps(result()));self.w.images[('A','gaussian')][1]['input_record_id']='new-frame';self.w.refresh()
  self.assertEqual(self.w.pose_canvas.detections,[])
  self.w.select_preview_source(('B','original'));self.assertIsNone(self.w.yolo_gate.binding)
 def test_wrong_mode_result_rejected(self):
  obj=result();obj['identity']['processing_mode']='gray'
  self.assertFalse(self.w.receive_yolo_result(json.dumps(obj)));self.assertEqual(self.w.pose_canvas.detections,[])
class WireChecks(unittest.TestCase):
 def test_full_wire_record_is_verified_without_changing_parent_semantics(self):
  import base64
  from frame_identity import verified_frame_record
  r=dict(record(),schema='qjzc.mosaic.branch.v1')
  headers={'x-input-record-id':r['input_record_id'],'x-frame-record':base64.urlsafe_b64encode(json.dumps(r).encode()).decode()}
  self.assertEqual(verified_frame_record(headers,JPEG,IDENT,[640,360]),r)
  for key,value in [('processing_mode','gray'),('boot_id','changed'),('jpeg_sha256','b'*64),('preview_size',[960,540])]:
   bad=dict(r,**{key:value});headers['x-frame-record']=base64.urlsafe_b64encode(json.dumps(bad).encode()).decode()
   with self.assertRaises(ValueError):verified_frame_record(headers,JPEG,IDENT,[640,360])
 def test_legacy_frame_remains_preview_only(self):
  from frame_identity import verified_frame_record
  self.assertIsNone(verified_frame_record({},JPEG,IDENT,[640,360]))

if __name__=='__main__':unittest.main(verbosity=2)
