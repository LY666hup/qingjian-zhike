"""One persistent GStreamer JPEG->RGB decoder, exact one-in-flight frame.

Uses the same decode/color caps as the legacy one-process-per-JPEG path.
No camera, AI, retry or file mutation. Native byte-equivalence is a deployment gate.
"""
import os,subprocess,threading,queue,time
from jpeg_header import jpeg_size

class PersistentRGB:
    def __init__(self,width=640,height=360):
        if (width,height)!=(640,360):raise ValueError('Only reviewed 640x360 branch')
        self.width=width;self.height=height;self.size=width*height*3
        self.out=queue.Queue(maxsize=1);self.lock=threading.Lock();self.failed=False;self.closed=False
        command=['gst-launch-1.0','-q','fdsrc','fd=0','!','jpegparse','!','jpegdec','!','videoconvert','!','video/x-raw,format=RGB','!','fdsink','fd=1','sync=false']
        self.proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,env=dict(os.environ,GST_REGISTRY_UPDATE='no',GST_DEBUG='0'))
        self.reader=threading.Thread(target=self.read,daemon=True);self.reader.start()
    def read(self):
        data=bytearray()
        try:
            while not self.closed:
                chunk=self.proc.stdout.read(65536)
                if not chunk:break
                data.extend(chunk)
                if len(data)>self.size+65536:raise ValueError('RGB backlog exceeds bound')
                while len(data)>=self.size:
                    frame=bytes(data[:self.size]);del data[:self.size];self.out.put_nowait(frame)
        except Exception:self.failed=True
        finally:
            if not self.closed:self.failed=True
    def decode_bytes(self,jpeg,timeout=1.):
        if not self.lock.acquire(blocking=False):raise RuntimeError('Only one in-flight JPEG')
        try:
            if self.failed or self.closed or self.proc.poll()is not None:raise RuntimeError('Decoder failed; no restart')
            if not isinstance(jpeg,bytes)or len(jpeg)>2*1024**2 or jpeg_size(jpeg)!=(self.width,self.height):raise ValueError('JPEG dimensions/byte limit')
            if not self.out.empty():raise ValueError('Unexpected old RGB output; frame alignment lost')
            if not 0<timeout<=5:raise ValueError('Bounded decoder timeout')
            deadline=time.monotonic()+timeout;errors=[]
            def write():
                try:
                    view=memoryview(jpeg)
                    while view:
                        n=self.proc.stdin.write(view)
                        if not n:raise OSError('Decoder stdin closed')
                        view=view[n:]
                except Exception as e:errors.append(e)
            writer=threading.Thread(target=write,daemon=True);writer.start();writer.join(max(0,deadline-time.monotonic()))
            if writer.is_alive():raise TimeoutError('Decoder write timeout')
            if errors:raise errors[0]
            result=self.out.get(timeout=max(0,deadline-time.monotonic()))
            if self.failed:raise ValueError('Decoder stream invalid')
            return result
        except Exception:
            self.failed=True;self.close();raise
        finally:self.lock.release()
    def decode(self,jpeg,timeout=1.):
        import numpy as np
        return np.frombuffer(self.decode_bytes(jpeg,timeout),np.uint8).reshape(self.height,self.width,3)
    def close(self):
        if self.closed:return
        self.closed=True
        if self.proc.poll()is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=.3)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=.3)
        for stream in(self.proc.stdin,self.proc.stdout):
            try:stream.close()
            except OSError:pass
