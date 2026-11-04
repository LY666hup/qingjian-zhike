"""Two explicit loopback SIMULATION sources, one interleaved analysis channel."""
import json,struct,time
from PySide6.QtCore import QByteArray,QBuffer,QIODevice,QRectF
from PySide6.QtGui import QImage,QPainter,QColor,QPen,QFont
from local_test_service import LocalService
from video_sources import SourceSpec

class DemoService(LocalService):
    def __init__(self):
        super().__init__(0,0,fps=20)
        self.side=self.listen(0);self.side_port=self.side.getsockname()[1];self.start(self.accept,(self.side,'video_B'))
    @staticmethod
    def people(camera,fid):
        persons=[]
        for i,x in enumerate((.2,.48,.76)):
            y=.23;shift=.015*((fid//10+i)%3-1)
            if camera=='B':x=1-x
            points=[[x,y,.99],[x-.012,y-.008,.99],[x+.012,y-.008,.99],[x-.025,y,.98],[x+.025,y,.98],
                    [x-.04,y+.09,.99],[x+.04,y+.09,.99],[x-.07,y+.20,.99],[x+.07,y+.20,.99],
                    [x-.085,y+.30,.99],[x+.085,y+.30,.99],[x-.035,y+.32,.99],[x+.035,y+.32,.99],
                    [x-.05+shift,y+.46,.99],[x+.05-shift,y+.46,.99],[x-.06,y+.60,.99],[x+.06,y+.60,.99]]
            count=fid//80+i+(2 if camera=='A' else 9)
            persons.append(dict(person_id='demo-person-'+str(i),layout='coco17',coordinate_space='normalized_image',keypoints=points,
                action_type='模拟深蹲',action_phase='模拟阶段',rep_count=count,correct_count=max(0,count-1),incorrect_count=1,
                quality_score=80+i if camera=='A' else 70+i,error_flags=['模拟提示 · 非模型输出'],
                profile=dict(identity_status='unconfirmed',display_name='模拟对象'+str(i+1))))
        return persons
    @classmethod
    def picture(cls,camera,fid):
        w,h=960,540;im=QImage(w,h,QImage.Format.Format_RGB32);im.fill(QColor('#0c263d'))
        p=QPainter(im);p.fillRect(0,int(h*.55),w,h,QColor('#12403f' if camera=='A' else '#173a57'))
        p.setPen(QPen(QColor('#477478'),2));p.drawRect(28,120,w-56,h-150);p.drawLine(w//2,120,w//2,h-30)
        p.drawEllipse(QRectF(w*.39,h*.55,w*.22,h*.28));p.setPen(QColor('#a8d6e6'));font=QFont('Segoe UI');font.setPixelSize(20);p.setFont(font)
        p.drawText(24,35,'LOCAL SIMULATION '+camera+'  /  UI TEST ONLY')
        p.setPen(QColor('#6d98ac'));p.drawText(24,65,'No camera capture, pose model or FPGA filter is running')
        for i,person in enumerate(cls.people(camera,fid)):
            pts=person['keypoints'];p.setPen(QPen(QColor(('#4f7c8a','#426f80','#567586')[i]),10))
            for a,b in ((5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)):
                p.drawLine(int(pts[a][0]*w),int(pts[a][1]*h),int(pts[b][0]*w),int(pts[b][1]*h))
            p.setBrush(QColor('#547b89'));p.drawEllipse(QRectF(pts[0][0]*w-14,pts[0][1]*h-16,28,32))
        p.end();return im
    def produce(self):
        fid=0;due=time.monotonic()
        while not self.stop_event.is_set():
            if not self.enabled:self.stop_event.wait(.05);due=time.monotonic();continue
            videos={};motions=[]
            for camera in ('A','B'):
                image=self.picture(camera,fid);array=QByteArray();buf=QBuffer(array);buf.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buf,'JPEG',85);payload=bytes(array)
                base=dict(stream_id='sim-'+camera,session_id=self.session,camera_id='SIM-'+camera,frame_id=fid,source='simulation',source_timestamp_ns=time.monotonic_ns(),clock_domain='simulation-monotonic')
                head=dict(base,schema='qjzc.frame.v1',encoding='jpeg',payload_bytes=len(payload),width=image.width(),height=image.height(),processing_mode='original',processor='simulation')
                h=json.dumps(head).encode();videos[camera]=struct.pack('!I',len(h))+h+payload
                motions.append((json.dumps(dict(base,schema='qjzc.analysis.v1',persons=self.people(camera,fid)),ensure_ascii=False)+'\n').encode())
            with self.lock:
                alive=[]
                for sock,kind in self.clients:
                    try:
                        if kind=='video':sock.sendall(videos['A'])
                        elif kind=='video_B':sock.sendall(videos['B'])
                        elif self.send_analysis:sock.sendall(b''.join(motions))
                        alive.append((sock,kind))
                    except OSError:sock.close()
                self.clients=alive
            fid+=1;due+=1/self.fps;self.stop_event.wait(max(0,due-time.monotonic()))
            if due<time.monotonic()-1:due=time.monotonic()
    def close(self):
        if hasattr(self,'side'):self.side.close()
        super().close()

class DualDemo:
    def __init__(self,window):self.window=window;self.service=DemoService()
    def connect(self):
        w=self.window
        for camera,port in [('A',self.service.video_port),('B',self.service.side_port)]:
            tile=w.camera_tiles[camera]['original'];tile.expected_camera_id='SIM-'+camera
            tile.play(SourceSpec('tcpjpeg',f'tcpjpeg://127.0.0.1:{port}'))
        w.analysis.configure('127.0.0.1',self.service.analysis_port)
    def close(self):self.service.close()
