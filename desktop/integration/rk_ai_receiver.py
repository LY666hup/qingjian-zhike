"""Explicitly connected, read-only RK result polling with session and age guards."""
from __future__ import annotations

import base64
import json
import time
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QObject, QTimer, Signal, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from http_mjpeg import parse_preview_status, decode_jpeg
from rk_ai_panel_adapter import normalize

MAX_RESPONSE = 2 * 1024 * 1024


class RkAiReceiver(QObject):
    message = Signal(dict)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.manager.finished.connect(self.finished)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)
        self.url = ''
        self.active = False
        self.latest = None
        self.image = None
        self.last_at = 0
        self.reply = None
        self.invalid = 0
        self.snapshot = None
        self.last_identity = None
        self.last_sequence = None

    def configure(self, url):
        self.stop()
        p = urlsplit(url.strip())
        if (p.scheme not in ('http', 'https') or not p.hostname or p.username
                or p.password or p.query or p.fragment):
            self.status.emit('RK 检测地址无效')
            return
        self.url = urlunsplit((p.scheme, p.netloc, p.path.rstrip('/'), '', ''))
        self.active = True
        self.status.emit('正在连接 RK 检测')
        self.timer.start()
        self.poll()

    def clear_result(self):
        self.latest = None
        self.image = None
        self.last_at = 0

    def poll(self):
        if self.latest is not None and time.monotonic() - self.last_at >= 2:
            self.clear_result()
            self.status.emit('RK 检测结果已过期')
        if self.active and self.reply is None:
            self.request('/status.json', 'status')

    def request(self, suffix, kind):
        req = QNetworkRequest(QUrl(self.url + suffix))
        req.setTransferTimeout(2000)
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        req.setRawHeader(b'Cache-Control', b'no-store')
        self.reply = self.manager.get(req)
        self.reply.setProperty('rk_kind', kind)
        self.reply.readyRead.connect(self.check_size)
        self.reply.metaDataChanged.connect(self.check_size)

    def check_size(self):
        reply = self.sender()
        length = bytes(reply.rawHeader('Content-Length'))
        if (length and (not length.isdigit() or int(length) > MAX_RESPONSE)) or reply.bytesAvailable() > MAX_RESPONSE:
            reply.setProperty('rk_oversize', True)
            reply.abort()

    def finished(self, reply):
        if reply is not self.reply:
            reply.deleteLater()
            return
        self.reply = None
        try:
            if not self.active:
                return
            if reply.property('rk_oversize'):
                raise ValueError('RK JSON 超过大小限制')
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError(reply.errorString())
            if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
                raise ValueError('RK HTTP 状态不是 200')
            raw = bytes(reply.readAll())
            if len(raw) > MAX_RESPONSE:
                raise ValueError('RK JSON 超过大小限制')
            if reply.property('rk_kind') == 'status':
                snap = parse_preview_status(raw)
                self.snapshot = snap
                if snap['state'] == 'ENDED':
                    self.stop()
                    self.status.emit('本次检测窗口已结束')
                    return
                if snap['state'] != 'CAPTURING':
                    self.clear_result()
                    self.status.emit('RK 待机，尚无采集')
                    return
                if not snap.get('session_id'):
                    raise ValueError('RK 检测需要明确 session_id')
                scope = (snap.get('boot_id'), snap['session_id'], snap.get('processing_mode'))
                if scope != self.last_identity:
                    self.clear_result()
                    self.last_identity = scope
                    self.last_sequence = None
                self.request('/analysis.json?session=' + snap['session_id'], 'analysis')
                return
            panel = normalize(json.loads(raw))
            if panel.get('source_session') != self.snapshot['session_id']:
                raise ValueError('检测结果与当前视频会话不一致')
            if panel['processing_mode'] != self.snapshot.get('processing_mode'):
                raise ValueError('检测结果与当前处理模式不一致')
            if (self.snapshot.get('camera_id') is not None
                    and self.snapshot['camera_id'] != panel['camera_id']):
                raise ValueError('检测结果与当前相机不一致')
            if panel['state'] != 'RUNNING':
                self.clear_result()
                self.status.emit('RK 检测状态：' + panel['state'])
                return
            sequence = panel['preview_sequence']
            if sequence is None:
                raise ValueError('检测结果缺少预览序号')
            if self.last_sequence is not None and sequence <= self.last_sequence:
                # Repeated HTTP responses are not fresh inference results.
                return
            image = None
            if panel['overlay_allowed']:
                image = decode_jpeg(base64.b64decode(panel['image_base64'], validate=True))
                if (image.width(), image.height()) != (960, 540):
                    raise ValueError('RK 检测输入尺寸不是 960×540')
            self.latest = panel
            self.image = image
            self.last_at = time.monotonic()
            self.last_sequence = sequence
            self.message.emit(panel)
            self.status.emit('RK 检测已接收（只读）')
        except Exception as exc:
            self.invalid += 1
            self.clear_result()
            self.status.emit('RK 检测等待数据：' + str(exc)[:100])
        finally:
            reply.deleteLater()

    def stop(self):
        self.active = False
        self.timer.stop()
        self.clear_result()
        self.snapshot = None
        self.last_identity = None
        self.last_sequence = None
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
        self.status.emit('RK 检测未连接')

    def close(self):
        self.stop()
