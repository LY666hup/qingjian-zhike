"""Offline response fixtures only: never creates a socket."""
from pathlib import Path
import json
import status_evidence_receiver as D
from video_sources import SourceSpec

profile_path = Path(__file__).with_name('open_authorized_config_50631923.py')
import open_authorized_config_50631923 as launch
p = json.loads(launch.loader.PROFILE.read_text(encoding='utf-8-sig'))['inputs']['gray']
D.ROOT = Path(__file__).resolve().parent / 'forensics_test_artifacts'
D.ROOT.mkdir(exist_ok=True)

class Conn:
    def close(self): pass

class Response:
    def __init__(self, raw, code): self.raw, self.status = raw, code
    def read(self, n): return self.raw[:n]
    def close(self): pass

spec = SourceSpec('mjpeg', p['video_url'], 960, 540, 15, False,
                  status_url=p['status_url'], provenance='external', expected_processing_mode='gray')
for name, change, code, want in [('valid', {}, 200, True), ('wrong_camera', {'camera_id':'B'}, 200, False), ('http_410', {}, 410, False), ('malformed', {}, 200, False)]:
    state = dict(p, schema='qjzc.preview.v2', state='CAPTURING', source_frame_id=None)
    state.update(change)
    raw = b'not json' if name == 'malformed' else json.dumps(state).encode()
    worker = D.EvidenceStrictFrames(spec, p)
    calls = []
    def fake(url, kind):
        calls.append(kind)
        return Conn(), Response(raw, code)
    worker.request = fake
    ok = True
    try: worker.read_status()
    except Exception: ok = False
    assert ok == want, name
    assert calls == ['status'] and worker.video_gets == 0
    assert next(worker.evidence_dir.glob('status_*.body')).read_bytes() == raw
    if not want: assert worker.terminal and worker.status_error
    print('PASS', name)
print('PASS 4 offline cases; no sockets, raw bytes retained, strict rejection preserved')
