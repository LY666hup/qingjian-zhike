"""One software JPEG process per branch, one in-flight frame, no restart."""
import subprocess,threading,queue,time,os
from jpeg_header import jpeg_size

class PersistentJPEG:
    def __init__(self):
        self.out=queue.Queue(maxsize=1);self.failed=False;self.closed=False
        cmd=['gst-launch-1.0','-q','fdsrc','fd=0','!','rawvideoparse','format=yuy2','width=640','height=360','framerate=30/1','!','videoconvert','!','jpegenc','quality=80','!','fdsink','fd=1','sync=false']
        self.proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,env=dict(os.environ,GST_REGISTRY_UPDATE='no',GST_DEBUG='0'))
        self.reader=threading.Thread(target=self.read,daemon=True);self.reader.start()

    def read(self):
        data=bytearray()
        try:
            while not self.closed:
                block=self.proc.stdout.read(65536)
                if not block:break
                data.extend(block)
                if len(data)>2*1024**2:raise ValueError('JPEG stream exceeds bound')
                while True:
                    end=data.find(b'\xff\xd9')
                    if end<0:break
                    frame=bytes(data[:end+2]);del data[:end+2]
                    if jpeg_size(frame)!=(640,360):raise ValueError('JPEG size differs')
                    self.out.put_nowait(frame)
        except Exception:self.failed=True
        finally:
            if not self.closed:self.failed=True

    def encode(self,packed,timeout):
        if self.failed or self.closed or self.proc.poll()is not None:raise RuntimeError('JPEG stream ended; no restart')
        if len(packed)!=640*360*2 or not self.out.empty():self.failed=True;raise ValueError('JPEG in-flight alignment differs')
        try:
            view=memoryview(packed)
            while view:
                n=self.proc.stdin.write(view)
                if not n:raise OSError('JPEG pipe closed')
                view=view[n:]
            result=self.out.get(timeout=timeout)
            if self.failed:raise ValueError('JPEG stream invalid')
            return result
        except Exception:
            self.failed=True;self.close();raise

    def close(self):
        if self.closed:return
        self.closed=True
        if self.proc.poll()is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=.25)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=.25)
        for stream in(self.proc.stdin,self.proc.stdout):
            try:stream.close()
            except OSError:pass
