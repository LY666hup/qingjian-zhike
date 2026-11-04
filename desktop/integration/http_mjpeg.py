"""Bounded multipart JPEG + qjzc.preview.v1 receiver. Never contacts a board by itself."""
from __future__ import annotations
import http.client,json,re,socket,threading,time
from email.message import Message
from urllib.parse import urlsplit
from PySide6.QtCore import QBuffer,QByteArray,QIODevice
from PySide6.QtGui import QImageReader

MAX_JPEG=16*1024*1024
MAX_HEADER=8192
MAX_STATUS=65536

def boundary_from_content_type(value):
    if len(value)>1024:raise ValueError('HTTP Content-Type过长')
    m=Message();m['Content-Type']=value
    if m.get_content_type().lower()!='multipart/x-mixed-replace':raise ValueError('不是HTTP multipart MJPEG')
    boundary=m.get_param('boundary')
    if not isinstance(boundary,str) or not re.fullmatch(r"[A-Za-z0-9'()+_,./:=? -]{1,70}",boundary) or boundary.endswith(' '):raise ValueError('multipart boundary无效')
    return boundary.encode('ascii')

class MultipartParser:
    def __init__(self,boundary,max_jpeg=MAX_JPEG):
        self.marker=b'--'+boundary;self.max_jpeg=max_jpeg;self.buffer=bytearray();self.state='boundary';self.finished=False;self.headers={};self.need=0;self.peak_buffer=0
    def feed(self,data):
        if self.finished:
            if data.strip():raise ValueError('结束边界后存在额外数据')
            return []
        if len(self.buffer)+len(data)>self.max_jpeg+MAX_HEADER+65536+len(self.marker):raise ValueError('MJPEG输入缓冲超限')
        self.buffer.extend(data);self.peak_buffer=max(self.peak_buffer,len(self.buffer));frames=[]
        while True:
            if self.state=='boundary':
                if self.buffer.startswith(b'\r\n'):del self.buffer[:2]
                if len(self.buffer)<len(self.marker)+2:break
                if not self.buffer.startswith(self.marker):raise ValueError('multipart边界不匹配 / Content-Length错误')
                suffix=bytes(self.buffer[len(self.marker):len(self.marker)+2]);del self.buffer[:len(self.marker)+2]
                if suffix==b'--':self.finished=True;break
                if suffix!=b'\r\n':raise ValueError('multipart边界行无效')
                self.state='headers'
            if self.state=='headers':
                end=self.buffer.find(b'\r\n\r\n')
                if end<0:
                    if len(self.buffer)>MAX_HEADER:raise ValueError('part头部超限')
                    break
                if end>MAX_HEADER:raise ValueError('part头部超限')
                headers={}
                for line in bytes(self.buffer[:end]).split(b'\r\n'):
                    key,sep,value=line.partition(b':')
                    if not sep:raise ValueError('part头部缺少冒号')
                    key=key.decode('ascii').strip().lower();value=value.decode('ascii').strip()
                    if not key or key in headers or len(headers)>=32:raise ValueError('重复或过多part头部')
                    headers[key]=value
                n=headers.get('content-length','')
                if not re.fullmatch('[0-9]{1,9}',n) or not 0<int(n)<=self.max_jpeg:raise ValueError('JPEG Content-Length无效或超限')
                if headers.get('content-type','').split(';')[0].strip().lower()!='image/jpeg':raise ValueError('part不是image/jpeg')
                seq=headers.get('x-preview-sequence')
                if seq is not None and (not re.fullmatch('[0-9]{1,19}',seq) or int(seq)>=2**63):raise ValueError('预览编码序号无效')
                self.headers=headers;self.need=int(n);del self.buffer[:end+4];self.state='body'
            if self.state=='body':
                if len(self.buffer)<self.need+2:break
                if self.buffer[self.need:self.need+2]!=b'\r\n':raise ValueError('JPEG长度与part分隔不符')
                payload=bytes(self.buffer[:self.need]);del self.buffer[:self.need+2]
                if not payload.startswith(b'\xff\xd8') or not payload.endswith(b'\xff\xd9'):raise ValueError('JPEG首尾标记与Content-Length不符')
                frames.append((payload,dict(self.headers)));self.state='boundary'
        return frames
    def finish(self):
        if not self.finished and (self.state!='boundary' or self.buffer not in (b'',b'\r\n')):raise ValueError('EOF截断了part / JPEG / 边界')

def decode_jpeg(payload):
    b=QBuffer();b.setData(QByteArray(payload));b.open(QIODevice.OpenModeFlag.ReadOnly);reader=QImageReader(b,b'jpeg');s=reader.size()
    if not 0<s.width()<=4096 or not 0<s.height()<=2160 or s.width()*s.height()>4096*2160:raise ValueError('JPEG尺寸超限或无效')
    image=reader.read()
    if image.isNull():raise ValueError('JPEG解码失败')
    return image

def parse_preview_status(raw):
    if len(raw)>MAX_STATUS:raise ValueError('状态JSON超限')
    d=json.loads(raw)
    if not isinstance(d,dict) or d.get('schema') not in ('qjzc.preview.v1','qjzc.preview.v2'):raise ValueError('状态schema不匹配')
    v2=d['schema']=='qjzc.preview.v2'
    if d.get('state') not in ('ARMED_NO_CAPTURE','CAPTURING','ENDED'):raise ValueError('状态值无效')
    for key,n in [('camera_id',64),('processing_mode',128),('encoder',256)]:
        if key in d and not(v2 and key=='camera_id' and d[key] is None) and (not isinstance(d[key],str) or len(d[key])>n):raise ValueError('状态字段无效: '+key)
    if 'camera_B_connected' in d and type(d['camera_B_connected']) is not bool:raise ValueError('B连接状态无效')
    if 'encoded_preview_frames' in d and (type(d['encoded_preview_frames']) is not int or not 0<=d['encoded_preview_frames']<2**63):raise ValueError('编码帧计数无效')
    bit=d.get('fpga_bit_sha256')
    if bit is not None and (not isinstance(bit,str) or not re.fullmatch('[0-9a-fA-F]{64}',bit)):raise ValueError('bit SHA256无效')
    if d.get('source_frame_id') is not None:raise ValueError('尚未约定非空source_frame_id，请复核接口')
    if 'processor' in d and d['processor'] not in ('fpga','rk','simulation','unknown'):raise ValueError('processor声明无效')
    # v2 extras are diagnostics only. Never execute pending/pipeline/start fields.
    for key in ('session_id','boot_id','source_image_quality','fpga_quality'):
        if key in d and (not isinstance(d[key],str) or not 0<len(d[key])<=128):raise ValueError('v2状态字段无效: '+key)
    keys=('schema','state','camera_id','camera_B_connected','fpga_bit_sha256','processing_mode','processor','encoded_preview_frames','source_frame_id','encoder','session_id','boot_id','source_image_quality','fpga_quality')
    return {k:d[k] for k in keys if k in d}

def checked_url(value):
    p=urlsplit(value)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.fragment:raise ValueError('需要不含凭据的HTTP/HTTPS地址')
    _=p.port
    return p

class HttpMjpegFrames(threading.Thread):
    """Worker with a one-frame mailbox. Status polling never requests stream URL."""
    def __init__(self,spec,allow_start=True):
        super().__init__(daemon=True);self.spec=spec;video=checked_url(spec.location);status=checked_url(spec.status_url)
        def origin(p):return p.scheme,p.hostname,p.port or (443 if p.scheme=='https' else 80)
        if origin(video)!=origin(status):raise ValueError('视频与状态URL必须同源')
        if spec.provenance not in ('external','simulation','replay'):raise ValueError('MJPEG来源声明无效')
        self.allow_start=allow_start;self.stream_attempted=False;self.stop_event=threading.Event();self.lock=threading.Lock();self.connections={};self.sockets={}
        self.latest=None;self.sequence=0;self.status=('connecting','先读取诊断状态，未请求视频');self.terminal=False
        self.gaps=None;self.duplicates=None;self.out_of_order=None;self.overwritten=0;self.snapshot={};self.status_error='';self.parser_peak=0;self.last_preview_seq=None;self.preview_gaps=0;self.monitor=None
    def stop(self):
        self.stop_event.set();self.shutdown()
    def shutdown(self,kind=None):
        with self.lock:connections=list(self.sockets.items())
        for k,s in connections:
            if kind is not None and k!=kind:continue
            if s:
                try:s.shutdown(socket.SHUT_RDWR)
                except OSError:pass
    def take(self):
        with self.lock:
            packet=self.latest;self.latest=None
            return packet,self.sequence,self.status,None,None,None,self.overwritten
    def info(self):
        with self.lock:return dict(status_snapshot=dict(self.snapshot),status_error=self.status_error,transport_state=self.status[0],terminal=self.terminal,preview_sequence=self.last_preview_seq,preview_sequence_gaps=self.preview_gaps,parser_peak_buffer=self.parser_peak)
    def ended(self,detail):
        with self.lock:self.terminal=True;self.status=('ended',detail)
        self.shutdown('video')
    def request(self,url,kind):
        p=checked_url(url);cls=http.client.HTTPSConnection if p.scheme=='https' else http.client.HTTPConnection
        c=cls(p.hostname,p.port,timeout=2)
        with self.lock:self.connections[kind]=c
        try:
            c.connect()
            with self.lock:self.sockets[kind]=c.sock
            if self.stop_event.is_set():raise InterruptedError('接收器已停止')
            c.request('GET',(p.path or '/')+('?' + p.query if p.query else ''),headers={'Accept':'application/json' if kind=='status' else 'multipart/x-mixed-replace','Connection':'close','User-Agent':'QJZC-Windows-Display/HTTP-Delta'})
            return c,c.getresponse()
        except Exception:
            c.close()
            with self.lock:self.connections.pop(kind,None);self.sockets.pop(kind,None)
            raise
    def read_status(self):
        c=r=None
        try:
            c,r=self.request(self.spec.status_url,'status')
            if r.status==410:self.ended('HTTP 410：本次诊断结束，保留末帧');return {'state':'ENDED'}
            if r.status!=200:raise ConnectionError(f'状态页HTTP {r.status}')
            length=r.getheader('Content-Length')
            if length is not None and (not length.isdecimal() or int(length)>MAX_STATUS):raise ValueError('状态页长度超限')
            raw=r.read(MAX_STATUS+1);d=parse_preview_status(raw)
            with self.lock:self.snapshot=d;self.status_error=''
            if d['state']=='ENDED':self.ended('status ENDED：本次诊断结束，保留末帧')
            return d
        finally:
            if r:r.close()
            if c:c.close()
            with self.lock:self.connections.pop('status',None);self.sockets.pop('status',None)
    def poll(self):
        while not self.stop_event.wait(.5) and not self.terminal:
            try:
                d=self.read_status()
                if not self.terminal and self.spec.expected_processing_mode and d.get('processing_mode')!=self.spec.expected_processing_mode:
                    with self.lock:self.terminal=True;self.status=('blocked','运行时处理模式发生变化，停止接收并保留末帧')
                    self.shutdown('video')
            except Exception as e:
                with self.lock:self.status_error=type(e).__name__+': '+str(e)[:160]
    def run(self):
        c=r=None
        try:
            d=self.read_status()
            if self.stop_event.is_set() or self.terminal:return
            if d['state']=='ARMED_NO_CAPTURE' and not self.allow_start:
                with self.lock:self.terminal=True;self.status=('armed','等待新的获准窗口；不会自动启动新采集')
                return
            if self.spec.expected_processing_mode and d.get('processing_mode')!=self.spec.expected_processing_mode:
                with self.lock:self.terminal=True;self.status=('blocked','状态页处理模式与配置不匹配，未请求视频')
                return
            self.monitor=threading.Thread(target=self.poll,daemon=True);self.monitor.start()
            if self.stop_event.is_set() or self.terminal:return
            self.stream_attempted=True;c,r=self.request(self.spec.location,'video')
            if r.status==410:self.ended('HTTP 410：本次诊断结束，保留末帧');return
            if r.status!=200:raise ConnectionError(f'视频HTTP {r.status}')
            parser=MultipartParser(boundary_from_content_type(r.getheader('Content-Type','')));last=time.monotonic()
            while not self.stop_event.is_set() and not self.terminal:
                data=r.read1(65536)
                if not data:
                    parser.finish()
                    if parser.finished:self.ended('multipart正常结束：本次诊断结束，保留末帧');return
                    raise ConnectionError('HTTP EOF：视频中断，待查状态后重连')
                if time.monotonic()-last>5:raise TimeoutError('5秒未收到完整预览帧')
                for payload,headers in parser.feed(data):
                    if self.stop_event.is_set() or self.terminal:break
                    image=decode_jpeg(payload);now=time.monotonic()
                    with self.lock:
                        snap=dict(self.snapshot);seq=headers.get('x-preview-sequence');seq=int(seq) if seq is not None else None
                        if seq is not None and self.last_preview_seq is not None and seq>self.last_preview_seq+1:self.preview_gaps+=seq-self.last_preview_seq-1
                        self.last_preview_seq=seq
                        declared=snap.get('processing_mode');mode=declared if declared in ('original','gray','gaussian','histogram') else 'unknown'
                        meta=dict(source=self.spec.provenance,transport='http-mjpeg',processing_mode=mode,processor=snap.get('processor','unknown'),declared_processing_mode=declared,camera_id=snap.get('camera_id'),stream_id=None,session_id=None,frame_id=None,source_frame_id=None,preview_sequence=seq,sequence_position='RK preview JPEG encoding; NOT sensor exposure',encoder=snap.get('encoder'),fpga_bit_sha256=snap.get('fpga_bit_sha256'),status_snapshot=snap,received_monotonic=now)
                        if self.latest is not None:self.overwritten+=1
                        self.sequence+=1;self.latest=(image,meta);self.status=('connected','正在接收HTTP预览；源画质未验收' if snap.get('source_image_quality')=='NOT_ACCEPTED' or snap.get('fpga_quality')=='NOT_ACCEPTED' else '正在接收HTTP预览；不是传感器更新率证明');self.parser_peak=parser.peak_buffer
                    last=now
                if parser.finished:self.ended('multipart正常结束：本次诊断结束，保留末帧');return
        except Exception as e:
            if not self.stop_event.is_set() and not self.terminal:
                with self.lock:self.status=('interrupted',type(e).__name__+': '+str(e)[:160])
        finally:
            self.stop_event.set();self.shutdown()
            if r:r.close()
            if c:c.close()
            if self.monitor:self.monitor.join(2.2)
            with self.lock:self.connections.pop('video',None);self.sockets.pop('video',None)
