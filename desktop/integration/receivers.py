"""Replaceable input adapters. Qt handles file/RTSP/HTTP; reference TCP is optional."""
from __future__ import annotations
from collections import deque
import json, socket, struct, threading, time
from urllib.parse import urlparse, urlsplit, urlunsplit
from PySide6.QtCore import QObject,QTimer,Signal
from PySide6.QtGui import QImage
from video_sources import VideoLane,SourceSpec
from contracts import parse_frame_header,identity
from http_mjpeg import HttpMjpegFrames

def safe_url(url):
    try:p=urlsplit(url)
    except ValueError:return '[地址格式无效]'
    if not p.scheme:return url
    return urlunsplit((p.scheme,p.netloc.split('@')[-1],p.path,'',''))

class TcpFrames(threading.Thread):
    """Bounded latest-frame mailbox; decode happens off the GUI thread."""
    def __init__(self,url):
        super().__init__(daemon=True);p=urlparse(url)
        if p.scheme!='tcpjpeg' or not p.hostname or not p.port:raise ValueError('格式：tcpjpeg://主机:端口')
        self.host,self.port=p.hostname,p.port;self.stop_event=threading.Event();self.lock=threading.Lock()
        self.latest=None;self.sequence=0;self.status=('connecting','连接网络视频');self.sock=None
        self.gaps=0;self.duplicates=0;self.out_of_order=0;self.last_ids={};self.overwritten=0
    def stop(self):
        self.stop_event.set()
        if self.sock:
            try:self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
    def take(self):
        with self.lock:
            packet=self.latest;self.latest=None
            return packet,self.sequence,self.status,self.gaps,self.duplicates,self.out_of_order,self.overwritten
    def run(self):
        try:
            with socket.create_connection((self.host,self.port),timeout=2) as s:
                self.sock=s;s.settimeout(.2);buf=bytearray();header=None;need=None;last=time.monotonic()
                while not self.stop_event.is_set():
                    try:chunk=s.recv(65536)
                    except socket.timeout:
                        if time.monotonic()-last>5:raise TimeoutError('5 秒未收到完整视频帧')
                        continue
                    if not chunk:raise ConnectionError('视频服务已断开')
                    buf.extend(chunk)
                    if time.monotonic()-last>5:raise TimeoutError('5 秒未收到完整视频帧')
                    while True:
                        if need is None:
                            if len(buf)<4:break
                            need=struct.unpack('!I',buf[:4])[0];del buf[:4]
                            if not 0<need<=65536:raise ValueError('帧头长度超限')
                        if header is None:
                            if len(buf)<need:break
                            header=parse_frame_header(bytes(buf[:need]));del buf[:need]
                        n=header['payload_bytes']
                        if len(buf)<n:break
                        payload=bytes(buf[:n]);del buf[:n]
                        # Inspect size before raster allocation (untrusted JPEG metadata).
                        from PySide6.QtCore import QBuffer,QByteArray,QIODevice
                        from PySide6.QtGui import QImageReader
                        b=QBuffer();b.setData(QByteArray(payload));b.open(QIODevice.OpenModeFlag.ReadOnly)
                        reader=QImageReader(b,b'jpeg');size=reader.size()
                        if size.width()!=header['width'] or size.height()!=header['height']:raise ValueError('JPEG 尺寸与帧头不符')
                        image=reader.read()
                        if image.isNull():raise ValueError('JPEG 解码失败')
                        now=time.monotonic();meta=dict(header,received_monotonic=now,transport='tcpjpeg')
                        ident=identity(meta);key=ident[:3];fid=ident[3];prev=self.last_ids.get(key)
                        with self.lock:
                            if prev is not None:
                                if fid==prev:self.duplicates+=1
                                elif fid<prev:self.out_of_order+=1
                                elif fid>prev+1:self.gaps+=fid-prev-1
                            self.last_ids[key]=max(fid,prev) if prev is not None else fid
                            if len(self.last_ids)>32:self.last_ids={key:fid}
                            if self.latest is not None:self.overwritten+=1
                            self.sequence+=1;self.latest=(image,meta);self.status=('connected','网络视频已接收；来源按发送端声明')
                        last=now;header=None;need=None
        except Exception as e:
            with self.lock:self.status=('interrupted',str(e))
        finally:self.sock=None

class Receiver(QObject):
    frame=Signal(QImage,dict)
    state=Signal(str,str)
    stats=Signal(dict)
    def __init__(self,parent=None):
        super().__init__(parent);self.qt=VideoLane(self);self.qt.frame_ready.connect(self._qt_frame);self.qt.state_changed.connect(self._qt_state)
        self.worker=None;self.retired=[];self.spec=SourceSpec();self.active=False;self.paused=False;self.closed=False
        self.seq=0;self.rendered=0;self.discarded=0;self.pause_discarded=0;self.rx_times=deque();self.render_times=deque();self.last_received=0;self.last_image_at=0;self.local_ms=None
        self.detail='未连接';self.status='disconnected';self.last_meta={};self.retry_at=0;self.reconnects=0
        self.received_total=0;self.drop_base=0;self.gap_base=0;self.duplicate_base=0;self.order_base=0
        self.http_attempted=False;self.preview_info={}
        self.timer=QTimer(self);self.timer.setInterval(16);self.timer.timeout.connect(self._tick);self.timer.start()
    def _state(self,s,d):
        if (s,d)!=(self.status,self.detail):self.status,self.detail=s,d;self.state.emit(s,d)
    def start(self,spec):
        self.disconnect();self.spec=spec;self.active=spec.kind!='none';self.seq=0;self.rendered=0;self.discarded=0;self.pause_discarded=0;self.last_received=0;self.rx_times.clear();self.render_times.clear();self.last_meta={};self.local_ms=None;self.reconnects=0
        self.received_total=0;self.drop_base=0;self.gap_base=0;self.duplicate_base=0;self.order_base=0
        self.http_attempted=False;self.preview_info={}
        self.qt.skipped_count=0
        if spec.kind in ('tcpjpeg','mjpeg'):
            self.worker=HttpMjpegFrames(spec) if spec.kind=='mjpeg' else TcpFrames(spec.location);self.worker.start();self._state('connecting','正在连接网络视频')
        else:self.qt.start(spec)
    def disconnect(self):
        self.active=False;self.paused=False;self.retry_at=0;self.qt.disconnect()
        if self.worker:self.worker.stop();self.retired.append(self.worker);self.worker=None
        self._state('disconnected','未连接')
    def _qt_state(self,s,d):
        if self.spec.kind not in ('tcpjpeg','mjpeg'):self._state(s,d)
    def _qt_frame(self,image):
        now=time.monotonic();network=self.spec.kind in ('rtsp','http')
        self._publish(image,dict(source='external' if network else 'replay',transport=self.spec.kind,processing_mode=self.spec.expected_processing_mode if self.spec.kind=='snapshot' else 'unknown',processor='fpga-diagnostic-snapshot' if self.spec.kind=='snapshot' else 'unknown',diagnostic_snapshot=self.spec.kind=='snapshot',received_monotonic=self.qt._last_frame_at,frame_id=None,stream_id=None,session_id=None,camera_id=None))
    def _publish(self,image,meta):
        now=time.monotonic();self.local_ms=max(0,(now-meta['received_monotonic'])*1000)
        self.rendered+=1;self.render_times.append(now);self.last_meta=meta;self.last_image_at=now;self.frame.emit(image,meta)
    def pause(self,value):
        if not self.active or self.status not in ('connected','buffering','paused'):return
        self.paused=value
        if self.spec.kind in ('tcpjpeg','mjpeg'):self._state('paused' if value else 'connected','显示暂停；接收继续并丢弃旧帧' if value else '播放中')
        else:self.qt.set_paused(value)
    def _tick(self):
        now=time.monotonic();self.retired=[x for x in self.retired if x.is_alive()]
        gaps=duplicates=out_of_order=None
        if self.worker:
            packet,n,status,gaps,duplicates,out_of_order,dropped=self.worker.take()
            is_http=self.spec.kind=='mjpeg'
            if is_http:
                self.http_attempted=self.http_attempted or self.worker.stream_attempted;self.preview_info=self.worker.info()
            if packet and not self.paused:self._publish(*packet)
            elif packet:self.pause_discarded+=1
            self.discarded=self.drop_base+dropped+self.pause_discarded
            terminal=is_http and self.worker.terminal
            if terminal:self.active=False;self.paused=False;self.retry_at=0
            if terminal or not self.paused:self._state(*status)
            if not self.worker.is_alive() and self.active and not self.retry_at:self.retry_at=now+2;self._state('reconnecting','2 秒后自动重连')
            if self.retry_at and now>=self.retry_at:
                self.drop_base+=dropped;self.gap_base+=gaps or 0;self.duplicate_base+=duplicates or 0;self.order_base+=out_of_order or 0
                self.retry_at=0;self.reconnects+=1
                self.retired.append(self.worker)
                self.worker=HttpMjpegFrames(self.spec,allow_start=not self.http_attempted) if is_http else TcpFrames(self.spec.location)
                self.worker.start();self.last_received=0
                # The old connection's n must never be counted again at restart.
                n=0;gaps=duplicates=out_of_order=None if is_http else 0
            if not is_http:gaps+=self.gap_base;duplicates+=self.duplicate_base;out_of_order+=self.order_base
        else:
            n=self.qt.raw.latest()[0] if self.qt.raw else self.qt._video_seq
            self.discarded=self.qt.skipped_count
            if self.spec.kind in ('rtsp','http') and self.active and not self.paused and self.qt._has_frame and now-self.qt._last_frame_at>6:
                self.qt._fail('连续 6 秒未收到新帧')
        delta=n-self.last_received if n>=self.last_received else n
        if delta>0:self.rx_times.extend([now]*delta);self.received_total+=delta
        self.last_received=n
        for q in (self.rx_times,self.render_times):
            while q and q[0]<now-2:q.popleft()
        self.stats.emit(dict(receive_fps=len(self.rx_times)/2,render_fps=len(self.render_times)/2,received=self.received_total,rendered=self.rendered,display_discarded=self.discarded,sequence_gaps=gaps,duplicate_ids=duplicates,out_of_order=out_of_order,reconnects=self.reconnects,local_ms=self.local_ms if now-self.last_image_at<.2 else None,preview=self.preview_info))
    def close(self):
        self.closed=True;self.timer.stop();self.disconnect();self.qt.close()
        for x in self.retired:x.join(.02)
