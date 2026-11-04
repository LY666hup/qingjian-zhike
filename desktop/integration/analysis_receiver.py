"""Receives results computed elsewhere; never loads a pose model."""
import time
from collections import OrderedDict
from PySide6.QtCore import QObject,QTimer,Signal
from PySide6.QtNetwork import QTcpSocket,QAbstractSocket
from contracts import parse_analysis,identity

class AnalysisReceiver(QObject):
    message=Signal(dict)
    status=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self.socket=QTcpSocket(self);self.buffer=bytearray();self.active=False;self.host='127.0.0.1';self.port=8766
        self.cache=OrderedDict();self.latest=None;self.last_at=0;self.invalid=0
        self.socket.readyRead.connect(self.read);self.socket.connected.connect(lambda:self.status.emit('分析数据已连接'))
        self.socket.disconnected.connect(self.disconnected);self.socket.errorOccurred.connect(lambda e:self.status.emit('分析连接失败，等待重连'))
        self.timer=QTimer(self);self.timer.setInterval(2000);self.timer.timeout.connect(self.connect_if_needed);self.timer.start()
    def configure(self,host,port):
        self.stop();self.host=host;self.port=int(port);self.active=True;self.connect_if_needed()
    def connect_if_needed(self):
        if self.active and self.socket.state()==QAbstractSocket.SocketState.UnconnectedState:
            self.buffer.clear();self.status.emit('正在连接分析数据');self.socket.connectToHost(self.host,self.port)
    def disconnected(self):
        self.buffer.clear();self.cache.clear();self.latest=None;self.last_at=0;self.status.emit('分析数据中断，等待重连' if self.active else '分析数据未连接')
    def read(self):
        self.buffer.extend(bytes(self.socket.readAll()))
        if len(self.buffer)>262144:self.invalid+=1;self.socket.abort();return
        while b'\n' in self.buffer:
            line,_,tail=self.buffer.partition(b'\n');self.buffer=bytearray(tail)
            try:m=parse_analysis(line)
            except (ValueError,TypeError,KeyError):self.invalid+=1;self.status.emit('已拒绝无效分析消息');continue
            ident=identity(m);now=time.monotonic();self.cache[ident]=(m,now);self.cache.move_to_end(ident)
            while len(self.cache)>256:self.cache.popitem(last=False)
            self.latest=m;self.last_at=now;self.message.emit(m)
    def matching(self,meta):
        item=self.cache.get(identity(meta))
        return item[0] if item and item[0]['source']==meta.get('source') and time.monotonic()-item[1]<2 else None
    def stop(self):
        self.active=False;self.socket.abort();self.disconnected()
    def close(self):self.stop();self.timer.stop()
