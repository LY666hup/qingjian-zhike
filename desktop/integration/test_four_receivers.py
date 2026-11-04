"""Synthetic in-memory HTTP fixtures, no server, socket or hardware."""
import io,json,unittest
from unittest.mock import patch
from PySide6.QtCore import QByteArray,QBuffer,QIODevice
from PySide6.QtGui import QImage,QColor
from sprint_app import StrictFrames
from video_sources import SourceSpec
import http_mjpeg as H

def jpeg(color):
 image=QImage(32,18,QImage.Format.Format_RGB32);image.fill(QColor(color));b=QByteArray();device=QBuffer(b);device.open(QIODevice.OpenModeFlag.WriteOnly);image.save(device,'JPEG');return bytes(b)
class Reply(io.BytesIO):
 def __init__(self,data,content):super().__init__(data);self.status=200;self.headers={'Content-Type':content,'Content-Length':str(len(data))}
 def getheader(self,k,default=None):return self.headers.get(k,default)
 def getheaders(self):return list(self.headers.items())
class Sock:
 def shutdown(self,*a):pass
class Tests(unittest.TestCase):
 def profile(self,slot,camera,mode):return dict(camera_id=camera,processing_mode=mode,session_id='SYNTHETIC_'+slot,boot_id='SYNTHETIC_BOOT',fpga_bit_sha256='a'*64,processor='fpga',video_url='http://offline.invalid/'+slot+'/stream',status_url='http://offline.invalid/'+slot+'/status',authorized_until='2099-01-01T00:00:00+08:00')
 def run_worker(self,p,color,state='CAPTURING',wrong=None):
  calls=[];payload=jpeg(color);status={'schema':'qjzc.preview.v2',**p,'state':state,'encoded_preview_frames':1,'source_frame_id':None,'encoder':'SYNTHETIC FIXTURE ONLY'}
  if wrong:status['session_id']='WRONG_SESSION'
  class Connection:
   def __init__(self,*a,**kw):self.sock=Sock()
   def connect(self):pass
   def request(self,method,path,headers):calls.append((method,path));self.path=path
   def getresponse(self):
    if self.path.endswith('/status'):return Reply(json.dumps(status).encode(),'application/json')
    data=b'--fixture\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(payload)).encode()+b'\r\nX-Preview-Sequence: 1\r\n\r\n'+payload+b'\r\n--fixture--\r\n'
    return Reply(data,'multipart/x-mixed-replace; boundary=fixture')
   def close(self):pass
  spec=SourceSpec('mjpeg',p['video_url'],status_url=p['status_url'],provenance='simulation',expected_processing_mode=p['processing_mode'])
  with patch.object(H.http.client,'HTTPConnection',Connection):
   worker=StrictFrames(spec,p);worker.run()
  return worker,calls
 def test_four_independent_sources_and_counts(self):
  colors=[]
  for slot,camera,mode,color in [('gray','A','gray','red'),('gaussian','A','gaussian','green'),('histogram','A','histogram','blue'),('B','B','gray','yellow')]:
   p=self.profile(slot,camera,mode);w,calls=self.run_worker(p,color)
   self.assertEqual(w.sequence,1);self.assertEqual(w.video_gets,1);self.assertTrue(w.terminal)
   image,meta=w.latest;self.assertEqual(meta['camera_id'],camera);self.assertEqual(meta['processing_mode'],mode);self.assertEqual(meta['status_snapshot']['session_id'],p['session_id']);colors.append(image.pixelColor(0,0).name())
   self.assertTrue(all(method=='GET' for method,_ in calls))
  self.assertEqual(len(set(colors)),4)
 def test_armed_does_not_GET_video(self):
  p=self.profile('A','A','gray');w,calls=self.run_worker(p,'red',state='ARMED_NO_CAPTURE');self.assertEqual(w.video_gets,0);self.assertEqual(w.sequence,0);self.assertTrue(w.terminal)
 def test_identity_failure_does_not_GET_video(self):
  p=self.profile('A','A','gray');w,calls=self.run_worker(p,'red',wrong=True);self.assertEqual(w.video_gets,0);self.assertEqual(w.sequence,0)
if __name__=='__main__':unittest.main(verbosity=2)
