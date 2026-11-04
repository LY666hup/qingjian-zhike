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


