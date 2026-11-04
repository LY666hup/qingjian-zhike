"""Linux ARM64 single-fd MMAP capture backend. Import performs no I/O.

No restart/retry. Format is committed ONLY by an explicitly approved caller.
UAPI layout: Linux v4l2_format=208, buffer=88, plane=64 on AArch64.
"""
import ctypes as C
import errno,os,platform,select,time,threading

U32=C.c_uint32
class Capability(C.Structure):_fields_=[('driver',C.c_char*16),('card',C.c_char*32),('bus_info',C.c_char*32),('version',U32),('capabilities',U32),('device_caps',U32),('reserved',U32*3)]
class PlaneUnion(C.Union):_fields_=[('mem_offset',U32),('userptr',C.c_uint64),('fd',C.c_int32)]
class Plane(C.Structure):_fields_=[('bytesused',U32),('length',U32),('m',PlaneUnion),('data_offset',U32),('reserved',U32*11)]
class BufferUnion(C.Union):_fields_=[('offset',U32),('userptr',C.c_uint64),('planes',C.c_uint64),('fd',C.c_int32)]
class Buffer(C.Structure):
 _fields_=[('index',U32),('type',U32),('bytesused',U32),('flags',U32),('field',U32),
  ('tv_sec',C.c_int64),('tv_usec',C.c_int64),('timecode',C.c_ubyte*16),('sequence',U32),
  ('memory',U32),('m',BufferUnion),('length',U32),('reserved2',U32),('request_fd',C.c_int32)]
class PlaneFormat(C.Structure):_pack_=1;_fields_=[('sizeimage',U32),('bytesperline',U32),('reserved',C.c_uint16*6)]
class PixelFormat(C.Structure):
 _pack_=1
 _fields_=[('width',U32),('height',U32),('pixelformat',U32),('field',U32),('colorspace',U32),
  ('planes',PlaneFormat*8),('num_planes',C.c_uint8),('flags',C.c_uint8),('ycbcr',C.c_uint8),
  ('quantization',C.c_uint8),('xfer_func',C.c_uint8),('reserved',C.c_uint8*7)]
class FormatUnion(C.Union):_fields_=[('pix_mp',PixelFormat),('raw',C.c_ubyte*200),('alignment',C.c_uint64)]
class Format(C.Structure):_fields_=[('type',U32),('fmt',FormatUnion)]
class Request(C.Structure):_fields_=[('count',U32),('type',U32),('memory',U32),('capabilities',U32),('flags',C.c_uint8),('reserved',C.c_uint8*3)]

def abi():
 got={x.__name__:C.sizeof(x)for x in (Plane,Buffer,Format,Request,PixelFormat)}
 if got!=dict(Plane=64,Buffer=88,Format=208,Request=20,PixelFormat=192):raise RuntimeError('unsupported V4L2 ABI '+str(got))
 return got
def req(n,size,direction=3):return (direction<<30)|(size<<16)|(ord('V')<<8)|n
def ioctl(fd,n,obj,direction=3):
 import fcntl
 fcntl.ioctl(fd,req(n,C.sizeof(obj),direction),memoryview(obj).cast('B'),True)
def fmt_info(f):
 p=f.fmt.pix_mp
 return dict(width=p.width,height=p.height,pixelformat=int(p.pixelformat).to_bytes(4,'little').decode('ascii',errors='replace'),
  field=p.field,num_planes=p.num_planes,stride=p.planes[0].bytesperline,sizeimage=p.planes[0].sizeimage)

class OnceCapture:
 def __init__(self,device,stop,deadline_seconds=11.0):
  if platform.system()!='Linux' or platform.machine()not in ('aarch64','arm64'):raise RuntimeError('RK ARM64 Linux only')
  abi();self.device=device;self.stop=stop;self.seconds=deadline_seconds
  self.lock=threading.RLock();self.cleanup_errors=[];self.fd=None;self.maps=[];self.streaming=False;self.open_ns=None;self.close_ns=None;self.format=None

 def open(self):
  with self.lock:return self._open_unlocked()

 def deadline_guard(self):
  self.stop.wait(max(0,self.end-time.monotonic()))
  self.stop.set()
  self.close()  # independent of encoding, disk logging and main-thread work

 def _open_unlocked(self):
  import fcntl,mmap
  if self.fd is not None or self.open_ns is not None:raise RuntimeError('capture can only be opened once')
  self.open_ns=time.monotonic_ns();self.end=self.open_ns/1e9+self.seconds
  self.fd=os.open(self.device,os.O_RDWR|os.O_NONBLOCK|os.O_CLOEXEC)
  threading.Thread(target=self.deadline_guard,daemon=True).start()
  fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  capability=Capability();ioctl(self.fd,0,capability,2)
  if capability.driver!=b'rkcif' or capability.bus_info!=b'platform:rkcif-mipi-lvds3' or capability.device_caps&0x04001000!=0x04001000:
   raise RuntimeError('video22 driver/bus/capabilities changed')
  f=Format();f.type=9
  ioctl(self.fd,4,f);self.before=fmt_info(f)
  f=Format();f.type=9;p=f.fmt.pix_mp;p.width=1920;p.height=1080;p.pixelformat=int.from_bytes(b'YUYV','little');p.field=1
  ioctl(self.fd,5,f)  # S_FMT: occurs only after protocol/identity/approval gate
  committed=fmt_info(f)
  f=Format();f.type=9;ioctl(self.fd,4,f);self.format=fmt_info(f)
  if committed!=self.format:raise RuntimeError('S_FMT and G_FMT disagree')
  q=self.format
  if (q['width'],q['height'],q['pixelformat'],q['num_planes'],q['field'])!=(1920,1080,'YUYV',1,1):raise RuntimeError('actual format/field differs '+str(q))
  if q['stride']<3840 or q['stride']%4 or q['sizeimage']<q['stride']*1080 or q['sizeimage']>16*1024*1024:raise RuntimeError('invalid actual stride/sizeimage')
  request=Request();request.count=4;request.type=9;request.memory=1;ioctl(self.fd,8,request)
  if not 2<=request.count<=8:raise RuntimeError('driver buffer count unsupported')
  for i in range(request.count):
   b,planes=self.buffer(i);ioctl(self.fd,9,b)
   if b.length!=1 or planes[0].length<q['sizeimage']:raise RuntimeError('invalid MMAP plane allocation')
   self.maps.append(mmap.mmap(self.fd,planes[0].length,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE,offset=planes[0].m.mem_offset))
   self.enqueue(i)
  if time.monotonic()>=self.end or self.stop.is_set():raise TimeoutError('setup exhausted window')
  self.streaming=True;ioctl(self.fd,18,U32(9),1)

 def buffer(self,index=0):
  planes=(Plane*8)();b=Buffer();b.type=9;b.memory=1;b.index=index;b.length=8;b.m.planes=C.addressof(planes)
  return b,planes

 def enqueue(self,index):
  b,p=self.buffer(index);b.length=1;p[0].length=len(self.maps[index]);ioctl(self.fd,15,b)

 def next(self):
  with self.lock:return self._next_unlocked()

 def _next_unlocked(self):
  while not self.stop.is_set() and time.monotonic()<self.end:
   if not select.select([self.fd],[],[],min(.1,max(0,self.end-time.monotonic())))[0]:continue
   b,p=self.buffer()
   try:ioctl(self.fd,17,b)
   except OSError as e:
    if e.errno==errno.EAGAIN:continue
    raise
   if b.index>=len(self.maps):raise RuntimeError('DQBUF invalid index')
   try:
    plane=p[0];q=self.format;used=plane.bytesused;offset=plane.data_offset
    self.last_packet=dict(raw=bytes(self.maps[b.index][:min(used,len(self.maps[b.index]))]),
      bytesused=used,data_offset=offset,sequence=b.sequence,flags=b.flags,timestamp_ms=time.monotonic_ns()//1000000)
    if b.flags&0x40:raise RuntimeError('V4L2_BUF_FLAG_ERROR')
    needed=offset+(q['height']-1)*q['stride']+q['width']*2
    if b.length!=1 or used<needed or used>len(self.maps[b.index]):raise RuntimeError('incomplete bytesused/data_offset')
    raw=self.last_packet['raw']  # own copy BEFORE QBUF
    return raw,offset,b.sequence,time.monotonic_ns()//1000000
   finally:self.enqueue(b.index)
  return None

 def close(self):
  with self.lock:
   self.cleanup_errors.extend(self._close_unlocked())
   return list(self.cleanup_errors)

 def _close_unlocked(self):
  errors=[]
  if self.fd is not None:
   if self.streaming:
    try:ioctl(self.fd,19,U32(9),1)
    except Exception as e:errors.append('STREAMOFF: '+str(e))
    self.streaming=False
   for m in self.maps:
    try:m.close()
    except Exception as e:errors.append('munmap: '+str(e))
   self.maps=[]
   try:
    request=Request();request.type=9;request.memory=1;ioctl(self.fd,8,request)
   except Exception as e:errors.append('REQBUFS(0): '+str(e))
   os.close(self.fd);self.fd=None;self.close_ns=time.monotonic_ns()
  return errors
