"""MediaPipe CPU pose backend: one saved image in, 33 landmarks out.

No camera, no NPU, no RKNN.  The image is decoded with OpenCV (headless wheel), cropped around the detection box
the caller supplies, and handed to MediaPipe's PoseLandmarker in IMAGE mode.  Landmarks are mapped back into the
preview coordinate system the detection box came from, so a point can be drawn on the preview without guessing.
"""
from __future__ import annotations

import os
import time

MODEL_SHA256 = '4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad'
MODEL_NAME = 'pose_landmarker_full.task'
LAYOUT = 'mediapipe33'
COORDINATE_SPACE = 'preview_pixels_normalized'


class BackendUnavailable(RuntimeError):
    pass


def _imports():
    try:
        import mediapipe as mp            # noqa: F401
        import numpy as np                # noqa: F401
        import cv2                        # noqa: F401
    except Exception as e:                # noqa: BLE001
        raise BackendUnavailable('%s: %s' % (type(e).__name__, e))
    return mp, np, cv2


def backend_fingerprint():
    """What is actually installed, for the record.  Never assume, always report."""
    try:
        mp, np, cv2 = _imports()
    except BackendUnavailable as e:
        return {'available': False, 'error': str(e)}
    import platform
    return {'available': True, 'mediapipe': getattr(mp, '__version__', 'unknown'),
            'numpy': np.__version__, 'opencv': cv2.__version__,
            'python': platform.python_version(), 'machine': platform.machine()}


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def image_size(path):
    """(width, height) of the file itself, or None when it cannot be decoded."""
    _mp, _np, cv2 = _imports()
    if cv2 is None:
        return None
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    return int(w), int(h)


def pad_box(box, width, height, frac=0.0):
    x0, y0, x1, y1 = [float(v) for v in box]
    bw, bh = x1 - x0, y1 - y0
    x0 -= bw * frac
    x1 += bw * frac
    y0 -= bh * frac
    y1 += bh * frac
    return [max(0.0, x0), max(0.0, y0), min(float(width), x1), min(float(height), y1)]


class PoseLandmarker:
    def __init__(self, model_path, min_detection_confidence=0.3):
        self.mp, self.np, self.cv2 = _imports()
        if not os.path.isfile(model_path):
            raise BackendUnavailable('model file not found: %s' % model_path)
        self.model_path = model_path
        self.model_sha256 = sha256_file(model_path)
        from mediapipe.tasks.python import vision
        base = self.mp.tasks.BaseOptions(model_asset_path=model_path)
        opts = vision.PoseLandmarkerOptions(
            base_options=base,
            running_mode=vision.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=min_detection_confidence,
            min_pose_presence_confidence=min_detection_confidence,
        )
        self.landmarker = vision.PoseLandmarker.create_from_options(opts)

    def infer(self, image_path, box, preview_size, pad=0.0):
        """Return a dict describing what came back, including why nothing came back."""
        cv2, np = self.cv2, self.np
        img = cv2.imread(image_path)
        if img is None:
            return {'ok': False, 'reason': 'image_unreadable', 'detail': image_path}
        h, w = img.shape[:2]
        pw, ph = preview_size
        if (w, h) != (pw, ph):
            # the caller declares which coordinate system the box is in; refuse to mix them silently
            return {'ok': False, 'reason': 'image_size_mismatch',
                    'detail': 'image %dx%d != preview %dx%d' % (w, h, pw, ph)}
        crop = pad_box(box, w, h, pad)
        x0, y0, x1, y1 = [int(round(v)) for v in crop]
        if x1 - x0 < 32 or y1 - y0 < 32:
            return {'ok': False, 'reason': 'crop_too_small', 'detail': crop}
        part = img[y0:y1, x0:x1]
        rgb = cv2.cvtColor(part, cv2.COLOR_BGR2RGB)
        mp_img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        t0 = time.perf_counter()
        res = self.landmarker.detect(mp_img)
        ms = (time.perf_counter() - t0) * 1000.0
        if not res.pose_landmarks:
            return {'ok': False, 'reason': 'no_pose_in_crop', 'inference_ms': ms,
                    'crop': [x0, y0, x1, y1]}
        lm = res.pose_landmarks[0]
        cw, ch = float(x1 - x0), float(y1 - y0)
        kps, clipped, vis_sum = [], 0, 0.0
        for p in lm:
            px = (x0 + float(p.x) * cw) / float(w)
            py = (y0 + float(p.y) * ch) / float(h)
            vis = float(getattr(p, 'visibility', 0.0) or 0.0)
            if px < 0.0 or px > 1.0 or py < 0.0 or py > 1.0:
                clipped += 1
            kps.append([min(1.0, max(0.0, px)), min(1.0, max(0.0, py)), min(1.0, max(0.0, vis))])
            vis_sum += vis
        return {'ok': True, 'keypoints': kps, 'keypoints_clipped': clipped,
                'pose_score': vis_sum / max(1, len(kps)), 'inference_ms': ms,
                'crop': [x0, y0, x1, y1], 'crop_source': 'detection_box' if pad == 0.0 else 'detection_box_padded',
                'image_size': [w, h]}


def load_detections(path):
    """Read a saved detection record (YOLO output) - boxes only, never keypoints."""
    import json
    d = json.load(open(path, encoding='utf-8'))
    if isinstance(d, dict) and 'detections' in d:
        dets = d['detections']
    elif isinstance(d, list):
        dets = d
    else:
        raise ValueError('detection file must be a list or {"detections": [...]}')
    out = []
    for i, item in enumerate(dets):
        box = item.get('xyxy') or item.get('box') or item.get('bbox')
        if not box or len(box) != 4:
            raise ValueError('detection %d has no xyxy' % i)
        out.append({'box': [float(v) for v in box], 'score': float(item.get('score', 0.0)),
                    'person_id': item.get('person_id', str(item.get('id', i)))})
    return out