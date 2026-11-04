"""Diagnostic wrapper: retains strict identity checks and never starts capture."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import threading
import time
import sprint_app

FIELDS = ('camera_id', 'processing_mode', 'session_id', 'boot_id',
          'fpga_bit_sha256', 'processor')
BASE = sprint_app.StrictFrames
ROOT = Path(__file__).resolve().parent


class EvidenceStrictFrames(BASE):
    def __init__(self, spec, profile):
        super().__init__(spec, profile)
        self.evidence_dir = ROOT / ('receive_' + profile['session_id'][:8] + '_' + str(time.time_ns()))
        self.evidence_dir.mkdir()
        self.audit_lock = threading.Lock()
        self.status_number = 0

    def record(self, event, **fields):
        item = dict(at=datetime.now().astimezone().isoformat(), event=event,
                    session=self.profile['session_id'], **fields)
        line = json.dumps(item, ensure_ascii=False)
        with self.audit_lock:
            with (self.evidence_dir / 'events.jsonl').open('a', encoding='utf8') as f:
                f.write(line + '\n')
            print(line, flush=True)

    def request(self, url, kind):
        self.record('REQUEST_ENTER', kind=kind, url=url)
        try:
            conn, response = super().request(url, kind)
            self.record('HTTP_RESPONSE', kind=kind, code=response.status,
                        content_type=response.getheader('Content-Type', ''))
            return conn, response
        except Exception as exc:
            self.record('REQUEST_FAILED', kind=kind, error_type=type(exc).__name__, error=str(exc))
            raise

    def read_status(self):
        conn = response = None
        try:
            conn, response = self.request(self.spec.status_url, 'status')
            raw = response.read(sprint_app.H.MAX_STATUS + 1)
            with self.audit_lock:
                self.status_number += 1
                number = self.status_number
            body_path = self.evidence_dir / f'status_{number:06d}.body'
            body_path.write_bytes(raw)
            self.record('STATUS_RAW', code=response.status, file=str(body_path),
                        bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            if response.status != 200:
                raise ValueError('Status HTTP ' + str(response.status))
            status = sprint_app.H.parse_preview_status(raw)
            differences = {key: {'expected': self.profile[key], 'actual': status.get(key),
                                 'present': key in status}
                           for key in FIELDS if status.get(key) != self.profile[key]}
            self.record('IDENTITY_CHECK', differences=differences, state=status.get('state'))
            sprint_app.validate_status(self.profile, status)
            with self.lock:
                self.snapshot = status
            if status['state'] == 'ENDED':
                self.ended('同会话ENDED · 保留末帧，非实时')
            return status
        except Exception as exc:
            detail = type(exc).__name__ + ': ' + str(exc)
            self.record('STATUS_FAILED', error=detail)
            with self.lock:
                self.status_error = detail
                self.terminal = True
                self.status = ('blocked', '状态核对失败：' + detail)
            self.shutdown('video')
            raise
        finally:
            if response is not None:
                response.close()
            if conn is not None:
                conn.close()

    def run(self):
        self.record('RECEIVER_START')
        try:
            super().run()
        finally:
            self.record('RECEIVER_END', video_gets=self.video_gets, decoded=self.sequence,
                        status=self.status, status_error=self.status_error)
