#!/usr/bin/env python3
"""Shared harness for suites that drive the real Worker entry (bin/05_analyze_stream.py).

The landmarker is an explicitly marked STUB returning a fixed 33-point skeleton: these suites prove selection,
identity, continuity and counting gates, never model accuracy.  JPEG size is read from the SOF header so the
harness also runs where mediapipe/OpenCV are absent; a real-model run lives in samples/expected/v3[23]_*.stdout.

No test collects here: importing this module has no side effects.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, 'bin', '05_analyze_stream.py')
_spec = importlib.util.spec_from_file_location('worker_mod', SRC)
W = importlib.util.module_from_spec(_spec)
sys.modules['worker_mod'] = W
_spec.loader.exec_module(W)


class StubLandmarker:
    """Fixed skeleton in normalised coordinates; explicitly NOT a pose model."""

    is_stub = True

    def infer(self, image, box, size, pad):
        pts = []
        for i in range(33):
            fx, fy = 0.2 + 0.02 * (i % 10), 0.2 + 0.02 * (i // 10)
            pts.append([round(fx, 4), round(fy, 4), 0.9])
        return {'ok': True, 'keypoints': pts, 'keypoints_clipped': 0, 'pose_score': 0.5,
                'inference_ms': 1.0, 'crop': [float(v) for v in box], 'crop_source': 'detection_box'}


def _jpeg_size(path):
    """Read the SOF size without OpenCV."""
    with open(path, 'rb') as f:
        data = f.read()
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = (data[i + 5] << 8) | data[i + 6]
            w = (data[i + 7] << 8) | data[i + 8]
            return int(w), int(h)
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + ((data[i + 2] << 8) | data[i + 3])
    raise RuntimeError('no SOF marker in %s' % path)


W.B.image_size = _jpeg_size
W.A.image_size_from_jpeg = _jpeg_size

SAMPLES = os.path.join(ROOT, 'samples')
IMG = os.path.join(SAMPLES, 'frame_0001_seq_000012.jpg')
SHA = hashlib.sha256(open(IMG, 'rb').read()).hexdigest()

BASE_IDENT = {'session_id': 's-id', 'boot_id': 'b-id', 'camera_id': 'A', 'processing_mode': 'gaussian',
              'fpga_bit_sha256': '0' * 64, 'processor': 'fpga'}
BOX = [237.75, 0.0, 720.75, 540.0]
ARGS = types.SimpleNamespace(model=os.path.join(ROOT, 'models', 'pose_landmarker_full.task'),
                             min_detection_confidence=0.5, pad=0.0, track_iou=0.3,
                             max_gap_ms=2000, out_ndjson=None, http=None, idle_exit_s=None)


class Harness:
    """One Worker plus job builder and a pass/fail recorder."""

    FAILS = []          # shared, so a suite that builds several workers reports every failure

    def __init__(self, **ident):
        self.worker = W.Worker(ARGS)
        self.worker.landmarker = StubLandmarker()
        self.worker._model_error = None
        self.ident = dict(BASE_IDENT)
        self.ident.update(ident)
        self.fails = Harness.FAILS

    def check(self, label, cond, detail=''):
        print('  %-72s %s%s' % (label, 'PASS' if cond else 'FAIL',
                                ('  ' + str(detail)[:60]) if detail else ''))
        if not cond:
            self.fails.append(label)

    def job(self, seq, ts, items, record, time_source='board_monotonic', sha=None, **over):
        ident = copy.deepcopy(self.ident)
        for key in ('session_id', 'boot_id', 'fpga_bit_sha256', 'processor'):
            if key in over:
                ident[key] = over.pop(key)
        if 'camera' in over:
            ident['camera_id'] = over.pop('camera')
        if 'mode' in over:
            ident['processing_mode'] = over.pop('mode')
        person = over.pop('person', None)
        if 'time_source' in over:
            time_source = over.pop('time_source')
        if 'sha' in over:
            sha = over.pop('sha')
        if over:
            raise TypeError('unexpected job options: %s' % sorted(over))
        det = {'input_record_id': record, 'jpeg_sha256': sha or SHA,
               'camera_id': ident['camera_id'], 'processing_mode': ident['processing_mode'],
               'coordinate_space': 'preview_pixels', 'items': items}
        j = {'image': IMG, 'identity': ident, 'input_record_id': record,
             'preview_sequence': seq, 'source_frame_id': None, 'timestamp_ms': ts,
             'time_source': time_source, 'capture_group_id': None,
             'jpeg_sha256': sha or SHA, 'detections': det}
        if person is not None:
            j['person_id'] = person
        return j

    def run(self, seq, ts, items, record, **kw):
        return self.run_full(seq, ts, items, record, **kw)['panel']

    def run_full(self, seq, ts, items, record, **kw):
        return self.worker.process(self.job(seq, ts, items, record, **kw))

    def verdict(self, name):
        print()
        print('VERDICT', name if not self.fails else name.replace('_OK', '_FAILED') +
              ': ' + ','.join(self.fails[:3]))
        return 0 if not self.fails else 1


ANON = [{'xyxy': BOX, 'score': 0.88}]
P1 = [{'person_id': 'P1', 'xyxy': BOX, 'score': 0.9}]
P2 = [{'person_id': 'P2', 'xyxy': BOX, 'score': 0.9}]