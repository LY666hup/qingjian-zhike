"""Bounded asynchronous image-sequence recording. Timestamps remain authoritative."""
import json,queue,threading,time,hashlib
from pathlib import Path
from PySide6.QtGui import QImage

class Recorder:
    def __init__(self,directory):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=False)
        self.queue=queue.Queue(maxsize=12);self.stop_event=threading.Event();self.written=0;self.dropped=0;self.error=None;self.events=0
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def frame(self,lane,image,meta):
        self.offer(('frame',lane,image.copy(),dict(meta)))
    def analysis(self,msg):self.offer(('analysis',msg))
    def offer(self,item):
        if self.stop_event.is_set():return
        try:self.queue.put_nowait(item)
        except queue.Full:self.dropped+=1
    def stop(self):self.stop_event.set()
    def run(self):
        try:
            with (self.directory/'events.jsonl').open('w',encoding='utf8') as log:
                while not self.stop_event.is_set() or not self.queue.empty():
                    try:item=self.queue.get(timeout=.1)
                    except queue.Empty:continue
                    if item[0]=='frame':
                        _,lane,image,meta=item;name=f'{self.written:07d}_{lane}.png';p=self.directory/name
                        if not image.save(str(p),'PNG'):raise OSError('图像保存失败')
                        event={'type':'frame','lane':lane,'file':name,'metadata':meta,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()};self.written+=1
                    else:event={'type':'analysis','message':item[1]}
                    log.write(json.dumps(event,ensure_ascii=False)+'\n');log.flush();self.events+=1
        except Exception as e:self.error=str(e)
        finally:
            (self.directory/'summary.json').write_text(json.dumps({'frames':self.written,'events':self.events,'record_queue_dropped':self.dropped,'error':self.error,'format':'PNG sequence + JSONL, original receive timestamps; not fixed-rate source video','pose_overlay_burned_in':False},ensure_ascii=False,indent=2),encoding='utf8')

def snapshot(directory,lane,image,meta):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    p=directory/f'{time.time_ns()}_{lane}.png'
    if not image.save(str(p),'PNG'):raise OSError('截图保存失败')
    p.with_suffix('.json').write_text(json.dumps({'metadata':meta,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'scope':'decoded video frame without UI/pose overlay'},ensure_ascii=False,indent=2),encoding='utf8')
    return p
