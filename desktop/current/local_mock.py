"""127.0.0.1-only test fixture. Never proxies a request or controls hardware."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
import base64
import hashlib
import json
import threading
import time
import uuid
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QPainter, QColor

def make_jpeg(camera='A',mode='original'):
    image=QImage(960,540,QImage.Format.Format_RGB32);image.fill(QColor('#173b51' if camera=='A' else '#413151'))
    painter=QPainter(image);painter.setPen(QColor('#bad4e0'));painter.drawLine(0,440,960,440)
    painter.drawRect(120,65,720,375);painter.fillRect(0,0,960,40,QColor('#71551a'))
    painter.drawText(20,27,'LOCAL SIMULATION / '+camera+' / '+mode)
    painter.end();data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer,'JPEG',85);return bytes(data)

def sample_person(pid='01',squat=3,pushup=None,offset=0):
    points=[[.5+offset,.5,.1] for _ in range(33)]
    for n,x,y in [(11,.43,.26),(12,.57,.26),(13,.37,.39),(14,.63,.39),(15,.32,.51),(16,.68,.51),
                  (23,.45,.49),(24,.55,.49),(25,.43,.69),(26,.57,.69),(27,.42,.88),(28,.58,.88),
                  (29,.40,.9),(30,.60,.9),(31,.38,.92),(32,.62,.92)]: points[n]=[x+offset,y,.98]
    return dict(person_id=pid,layout='mediapipe33',coordinate_space='normalized_image',keypoints=points,
                action_type='squat',rep_count=squat,counts_by_action=dict(squat=squat,pushup=pushup),
                action_phase='standing',profile={'identity_status':'unknown'})

class MockService:
    def __init__(self,port=0):
        self.lock=threading.Lock();self.state='CAPTURING';self.status_code=200;self.video_code=200;self.status_delay=0
        self.requests=[];self.active=0;self.peak=0;self.drop=False;self.overrides={};self.session=uuid.uuid4().hex
        self.boot=str(uuid.uuid4());self.jpgs={};self.server=None;self.port=port;self.thread=None
        self.seq=0;self.pose_seq=0;self.event_rows=[dict(event_id='local-event-1',event_seq=1,person_id='01',action_type='SQUAT',event_type='rep_completed',delta=1)]
    def start(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_GET(self):
                path=urlsplit(self.path).path;parts=path.strip('/').split('/')
                if len(parts)!=3 or parts[0] not in ('A','B'):
                    self.send_error(404);return
                camera,mode,kind=parts
                with owner.lock:owner.requests.append((time.monotonic(),path,'GET'))
                if kind=='status.json':
                    if owner.status_delay:time.sleep(owner.status_delay)
                    code=owner.status_code;value=owner.status(camera,mode)
                elif kind=='analysis.json':
                    code=200;owner.pose_seq+=1;jpeg=owner.jpeg(camera,mode)
                    value=dict(owner.status(camera,mode),schema='qjzc.analysis.v1',kind='pose',pose_present=True,
                               frame_id=owner.pose_seq,source_frame_id=None,persons=[sample_person()],
                               image_base64=base64.b64encode(jpeg).decode(),jpeg_sha256=hashlib.sha256(jpeg).hexdigest())
                elif kind=='events.json':
                    code=200;value=dict(owner.status(camera,mode),schema='qjzc.exercise.events.v1',training_id=owner.session+'-training',events=owner.event_rows)
                elif kind=='stream.mjpg':
                    if owner.video_code!=200:
                        self.send_response(owner.video_code);self.send_header('Content-Length','0');self.end_headers();return
                    self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=qjzc');self.send_header('Connection','close');self.end_headers()
                    with owner.lock:owner.active+=1;owner.peak=max(owner.peak,owner.active)
                    try:
                        while not owner.drop:
                            jpeg=owner.jpeg(camera,mode);owner.seq+=1
                            self.wfile.write(b'--qjzc\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpeg)).encode()+b'\r\nX-Preview-Sequence: '+str(owner.seq).encode()+b'\r\n\r\n'+jpeg+b'\r\n');self.wfile.flush();time.sleep(.05)
                    except (BrokenPipeError,ConnectionResetError,OSError):pass
                    finally:
                        self.close_connection=True
                        with owner.lock:owner.active-=1
                    return
                else:self.send_error(404);return
                payload=json.dumps(value).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            def do_POST(self):
                with owner.lock:owner.requests.append((time.monotonic(),self.path,'POST'))
                self.send_error(405)
        class Server(ThreadingHTTPServer):
            daemon_threads=True;allow_reuse_address=True
            def handle_error(self,*args):pass  # Expected when a timeout test closes its client socket.
        self.server=Server(('127.0.0.1',self.port),Handler);self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();return self
    def jpeg(self,camera,mode):
        key=camera,mode
        if key not in self.jpgs:self.jpgs[key]=make_jpeg(camera,mode)
        return self.jpgs[key]
    def status(self,camera='A',mode='gray'):
        value=dict(schema='qjzc.preview.v2',state=self.state,camera_id=camera,processing_mode=mode,
                    session_id=self.session,boot_id=self.boot,fpga_bit_sha256='0'*64,processor='simulation')
        value.update(self.overrides);return value
    def profile(self,camera='A',mode='gray',seconds=60,analysis=False,events=False):
        p={k:v for k,v in self.status(camera,mode).items() if k not in ('schema','state')}
        root=f'http://127.0.0.1:{self.port}/{camera}/{mode}/'
        p.update(video_url=root+'stream.mjpg?session='+self.session,status_url=root+'status.json?session='+self.session,
                 authorized_until=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat())
        if analysis:p['analysis_url']=root+'analysis.json?session='+self.session
        if events:p['events_url']=root+'events.json?session='+self.session
        return p
    def stop(self):
        self.drop=True
        if self.server:self.server.shutdown();self.server.server_close();self.thread.join(2)

if __name__=='__main__':
    from pathlib import Path
    from PySide6.QtGui import QGuiApplication
    app=QGuiApplication([]);service=MockService().start()
    data={'schema':'qjzc.windows.inputs.v1','inputs':{}}
    for c in ('A','B'):
        for m in ('original','gray','gaussian','histogram'):
            data['inputs'][c+'_'+m]=service.profile(c,m,seconds=3600,analysis=m=='original')
    path=Path(__file__).resolve().parent/'logs'/'LOCAL_SIMULATION.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('仅本机模拟服务；在界面手动载入：'+str(path),flush=True)
    try:
        while True:time.sleep(1)
    except KeyboardInterrupt:service.stop()
