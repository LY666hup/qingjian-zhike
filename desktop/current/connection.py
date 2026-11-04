"""One worker and one video connection per source; receive-only GET transport."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import http.client
import json
import socket
import ssl
import threading
import time
import traceback
import hashlib

from profiles import validate_profile, validate_status, deadline, ConfigError, IdentityError
from mjpeg_codec import MultipartParser, boundary_from_content_type, decode_jpeg, MAX_STATUS

class Ended(Exception):
    pass

class PermissionFailure(Exception):
    pass

class Transient(Exception):
    pass

def classify(error):
    if isinstance(error, Ended):
        return 'ended'
    if isinstance(error, PermissionFailure) or isinstance(error, PermissionError) or getattr(error, 'winerror', None) in (5, 10013):
        return 'permission'
    if isinstance(error, (ConfigError, IdentityError, ssl.SSLCertVerificationError, ValueError)):
        return 'mismatch'
    if isinstance(error, (Transient, OSError, http.client.HTTPException)):
        return 'retrying'
    return 'error'

class EventLog:
    def __init__(self, path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
    def write(self, event, **data):
        row = dict(at=datetime.now(timezone.utc).astimezone().isoformat(), event=event, **data)
        with self.lock, self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')

class SessionReceiver(threading.Thread):
    def __init__(self, profile, log, *, decoder=decode_jpeg, backoff=(1, 2, 4, 8, 10),
                 poll_interval=1.0, timeout=2.0, wall=time.time):
        super().__init__(daemon=True, name='QJZC-' + profile['camera_id'] + '-' + profile['processing_mode'])
        self.profile = validate_profile(profile, now=wall()); self.log = log; self.decoder = decoder
        self.backoff = backoff; self.poll_interval = poll_interval; self.timeout = timeout; self.wall = wall
        self.stop_event = threading.Event(); self.lock = threading.RLock(); self.connections = {}
        self.state = 'loaded'; self.detail = '配置已载入'; self.latest = None; self.status_snapshot = {}
        self.counts = dict(attempts=0, video_connections=0, received=0, decoded=0, overwritten=0, retries=0)
        self.next_retry_at = None; self.terminal = False; self.user_stopped = False; self.failure = None
        self.current_video_connections = 0; self.peak_video_connections = 0

    def event(self, event, **data):
        self.log.write(event, camera=self.profile['camera_id'], mode=self.profile['processing_mode'],
                       session=self.profile['session_id'], **data)

    def set_state(self, state, detail):
        with self.lock:
            changed = (self.state, self.detail) != (state, detail)
            self.state, self.detail = state, detail
        if changed:
            self.event('STATE', state=state, detail=detail)

    def check(self):
        if self.stop_event.is_set():
            raise InterruptedError('用户已断开')
        if self.wall() >= deadline(self.profile):
            raise Ended('配置已到截止时间')

    def shutdown(self, kind=None):
        with self.lock:
            items = list(self.connections.items())
        for key, c in items:
            if kind is not None and key != kind:
                continue
            try:
                if c.sock:
                    c.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()

    def stop(self):
        self.user_stopped = True; self.stop_event.set(); self.shutdown()
        with self.lock:
            self.latest = None; self.next_retry_at = None
        self.set_state('offline', '已断开')

    def request(self, url, kind):
        self.check()
        u = urlsplit(url); cls = http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection
        remaining = max(.01, deadline(self.profile) - self.wall())
        c = cls(u.hostname, u.port, timeout=min(self.timeout, remaining))
        with self.lock:
            self.connections[kind] = c
        try:
            self.event('HTTP_REQUEST', kind=kind, method='GET', url=url)
            c.connect(); self.check()
            c.request('GET', (u.path or '/') + ('?' + u.query if u.query else ''),
                      headers={'Connection': 'close', 'User-Agent': 'QJZC-Windows/Next',
                               'Accept': 'multipart/x-mixed-replace' if kind == 'video' else 'application/json'})
            r = c.getresponse(); self.check()
            self.event('HTTP_RESPONSE', kind=kind, code=r.status)
            if r.status == 410:
                raise Ended('HTTP 410：会话结束')
            if r.status in (401, 403, 407):
                raise PermissionFailure('HTTP ' + str(r.status) + '：访问被拒绝')
            if r.status in (408, 425, 429) or 500 <= r.status <= 599:
                raise Transient('HTTP ' + str(r.status) + '：服务暂时不可用')
            if r.status != 200:
                raise ConfigError('HTTP ' + str(r.status) + '：请检查配置地址')
            return c, r
        except Exception:
            c.close()
            with self.lock:
                self.connections.pop(kind, None)
            raise

    def read_json(self, url, kind, limit=MAX_STATUS):
        c = r = None
        try:
            c, r = self.request(url, kind)
            content_length = r.getheader('Content-Length')
            if content_length is not None and (not content_length.isdecimal() or int(content_length) > limit):
                raise ConfigError(kind + ' 响应过大')
            raw = r.read(limit + 1); self.check()
            if len(raw) > limit:
                raise ConfigError(kind + ' 响应过大')
            if kind == 'status':
                self.event('STATUS_BODY', body=raw.decode('utf-8', errors='replace'), sha256=hashlib.sha256(raw).hexdigest())
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError):
                raise ConfigError(kind + ' 响应不是有效 JSON') from None
        finally:
            if r: r.close()
            if c: c.close()
            with self.lock:
                self.connections.pop(kind, None)

    def read_status(self):
        value = validate_status(self.profile, self.read_json(self.profile['status_url'], 'status'))
        with self.lock:
            self.status_snapshot = value
        if value['state'] == 'ENDED':
            raise Ended('板端状态为 ENDED')
        return value

    def wait(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check()
            self.stop_event.wait(min(.1, max(0, until - time.monotonic())))
        self.check()

    def monitor(self, finished, errors):
        next_status = time.monotonic() + self.poll_interval
        while not finished.wait(.05):
            try:
                self.check()
                if time.monotonic() >= next_status:
                    snap = self.read_status()
                    if snap['state'] != 'CAPTURING':
                        raise Transient('板端尚未启动采集')
                    next_status = time.monotonic() + self.poll_interval
            except Exception as e:
                errors.append(e); self.shutdown('video'); return

    def stream(self):
        c = r = None; finished = threading.Event(); errors = []; watcher = None
        with self.lock:
            self.counts['video_connections'] += 1
            self.current_video_connections += 1
            self.peak_video_connections = max(self.peak_video_connections, self.current_video_connections)
        try:
            self.check()
            # Supervisor also closes a stalled HTTP header/read at expiry or state change.
            watcher = threading.Thread(target=self.monitor, args=(finished, errors), daemon=True)
            watcher.start()
            c, r = self.request(self.profile['video_url'], 'video')
            parser = MultipartParser(boundary_from_content_type(r.getheader('Content-Type', '')))
            last_frame = time.monotonic()
            while True:
                self.check()
                if errors: raise errors[0]
                chunk = r.read1(65536)
                if errors: raise errors[0]
                self.check()
                if not chunk:
                    raise Transient('视频连接已断开')
                if time.monotonic() - last_frame > 5:
                    raise Transient('超过 5 秒没有收到完整视频帧')
                for payload, headers in parser.feed(chunk):
                    self.check()
                    with self.lock: self.counts['received'] += 1
                    image = self.decoder(payload)
                    self.check()
                    meta = dict(self.profile, received_monotonic=time.monotonic(),
                                jpeg_sha256=hashlib.sha256(payload).hexdigest(),
                                preview_sequence=headers.get('x-preview-sequence'),
                                source_frame_id=None, status_snapshot=dict(self.status_snapshot))
                    with self.lock:
                        self.counts['decoded'] += 1
                        if self.latest is not None: self.counts['overwritten'] += 1
                        self.latest = image, meta
                    self.set_state('receiving', '正在接收')
                    last_frame = time.monotonic()
                if parser.finished:
                    raise Transient('视频流已关闭，重新查询会话状态')
        except Exception:
            if errors: raise errors[0]
            raise
        finally:
            finished.set(); self.shutdown('video')
            if r: r.close()
            if c: c.close()
            if watcher: watcher.join(self.timeout + .5)
            with self.lock:
                self.connections.pop('video', None)
                self.current_video_connections -= 1
                self.latest = None
            self.event('VIDEO_CLOSED')

    def run(self):
        failures = 0; armed_polls = 0
        try:
            while not self.stop_event.is_set():
                try:
                    self.check()
                    with self.lock: self.counts['attempts'] += 1
                    self.set_state('connecting', '正在连接')
                    snap = self.read_status()
                    if snap['state'] in ('ARMED', 'ARMED_NO_CAPTURE'):
                        self.set_state('waiting', '等待板端启动')
                        delay = self.backoff[min(armed_polls, len(self.backoff) - 1)]
                        armed_polls += 1; self.wait(delay); continue
                    armed_polls = 0
                    self.stream()
                except Exception as e:
                    if self.user_stopped or self.stop_event.is_set(): break
                    category = classify(e)
                    self.failure = type(e).__name__ + ': ' + str(e)
                    self.event('FAILURE', category=category, reason=self.failure)
                    if category != 'retrying':
                        self.set_state(category, str(e)); break
                    delay = self.backoff[min(failures, len(self.backoff) - 1)]; failures += 1
                    with self.lock:
                        self.counts['retries'] += 1; self.next_retry_at = self.wall() + delay
                    self.set_state('retrying', str(e))
                    self.event('RETRY_SCHEDULED', seconds=delay, retry_at=self.next_retry_at)
                    try: self.wait(delay)
                    finally:
                        with self.lock: self.next_retry_at = None
        except Exception as e:
            if not self.user_stopped:
                self.set_state(classify(e), str(e))
                self.event('FAILURE', category=classify(e), reason=str(e))
        finally:
            self.shutdown()
            with self.lock:
                self.terminal = True; self.latest = None; self.next_retry_at = None
            self.event('STOPPED', counts=self.counts, state=self.state, peak_video_connections=self.peak_video_connections)

    def take(self):
        with self.lock:
            latest = self.latest; self.latest = None
            return latest

    def info(self):
        with self.lock:
            return dict(state=self.state, detail=self.detail, next_retry_at=self.next_retry_at,
                        terminal=self.terminal, alive=self.is_alive(), counts=dict(self.counts),
                        peak_video_connections=self.peak_video_connections)
