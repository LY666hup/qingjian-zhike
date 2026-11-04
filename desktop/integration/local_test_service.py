"""Loopback-only SIMULATION fixture. No camera, model or RK connection."""
from __future__ import annotations
import argparse,json,socket,struct,threading,time
from PySide6.QtCore import QBuffer,QByteArray,QIODevice,QRectF
from PySide6.QtGui import QImage,QPainter,QColor


class LocalService:
    def __init__(self,video_port=8765,analysis_port=8766,fps=20):
        self.fps=fps;self.stop_event=threading.Event();self.enabled=True;self.send_analysis=True
        self.gap_ids=False;self.session='simulation-session';self.clients=[];self.lock=threading.Lock();self.threads=[]
        self.video=self.listen(video_port);self.analysis=self.listen(analysis_port)
        self.video_port=self.video.getsockname()[1];self.analysis_port=self.analysis.getsockname()[1]
        self.start(self.accept,(self.video,'video'));self.start(self.accept,(self.analysis,'analysis'));self.start(self.produce,())
    @staticmethod
    def listen(port):
        s=socket.socket();s.bind(('127.0.0.1',port));s.listen(8);s.settimeout(.2);return s
    def start(self,fn,args):
        t=threading.Thread(target=fn,args=args,daemon=True);self.threads.append(t);t.start()
    def accept(self,listener,kind):
        while not self.stop_event.is_set():
            try:s,_=listener.accept()
            except socket.timeout:continue
            except OSError:return
            s.settimeout(.3)
            with self.lock:self.clients.append((s,kind))
    def drop_connections(self):
        with self.lock:
            for s,_ in self.clients:
                try:s.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                s.close()
            self.clients=[]
    @staticmethod
    def image(fid,width=640,height=360):
        image=QImage(width,height,QImage.Format.Format_RGB32);image.fill(QColor('#102b42'))
        p=QPainter(image)
        for i,color in enumerate(['#4f83b8','#448c85','#af9756','#a56076']):p.fillRect(i*width//4,0,width//4,height,QColor(color))
        p.setBrush(QColor('#d9f6ed'));p.setPen(QColor('#d9f6ed'));p.drawEllipse(QRectF((fid*7)%(width-60),height/2-30,60,60));p.end()
        return image
    def produce(self):
        fid=0;due=time.monotonic()
        while not self.stop_event.is_set():
            if not self.enabled:self.stop_event.wait(.05);due=time.monotonic();continue
            image=self.image(fid);data=QByteArray();b=QBuffer(data);b.open(QIODevice.OpenModeFlag.WriteOnly);image.save(b,'JPEG',85);payload=bytes(data)
            base=dict(stream_id='sim-original',session_id=self.session,camera_id='sim-A',frame_id=fid,source='simulation',source_timestamp_ns=time.monotonic_ns(),clock_domain='simulator-monotonic')
            head=dict(base,schema='qjzc.frame.v1',encoding='jpeg',payload_bytes=len(payload),width=image.width(),height=image.height(),processing_mode='original',processor='simulation')
            points=[[.3+(i%3)*.15,.2+(i//3)*.09,.95] for i in range(17)]
            analysis=dict(base,schema='qjzc.analysis.v1',subject='模拟训练对象',action_type='模拟深蹲',action_phase='模拟阶段',rep_count=fid//40,correct_count=fid//40,incorrect_count=0,quality_score=82,error_flags=['仅用于接口测试，无模型推理'],pose_state='模拟骨架',persons=[dict(person_id='sim-person',layout='coco17',coordinate_space='normalized_image',keypoints=points)])
            h=json.dumps(head,ensure_ascii=False).encode();video=struct.pack('!I',len(h))+h+payload;motion=(json.dumps(analysis,ensure_ascii=False)+'\n').encode()
            with self.lock:
                alive=[]
                for s,kind in self.clients:
                    try:
                        if kind=='video':s.sendall(video)
                        elif self.send_analysis:s.sendall(motion)
                        alive.append((s,kind))
                    except OSError:s.close()
                self.clients=alive
            fid+=2 if self.gap_ids else 1;due+=1/self.fps
            self.stop_event.wait(max(0,due-time.monotonic()))
            if due<time.monotonic()-1:due=time.monotonic()
    def close(self):
        self.stop_event.set();self.video.close();self.analysis.close();self.drop_connections()
        for t in self.threads:t.join(.6)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--video-port',type=int,default=8765);p.add_argument('--analysis-port',type=int,default=8766);a=p.parse_args()
    service=LocalService(a.video_port,a.analysis_port);print(f'SIMULATION ONLY tcpjpeg://127.0.0.1:{service.video_port}; analysis 127.0.0.1:{service.analysis_port}',flush=True)
    try:
        while True:time.sleep(.5)
    except KeyboardInterrupt:pass
    finally:service.close()
if __name__=='__main__':main()
