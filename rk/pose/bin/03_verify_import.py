#!/usr/bin/env python3
"""Step 3 - verify the import and the model before touching any image.

A successful `import mediapipe` is not enough: the wheel can import and still fail on the first real decode
(that is the classic glibc/musl failure).  So this step also builds a tiny in-memory image and runs the
landmarker once, which forces every shared library to load.

Prints `IMPORT OK: ...` lines, or `IMPORT FAIL: <code> :: <detail>`.
Exit codes: 0 ok, 1 failure (the IMPORT FAIL line names which check failed).
"""
from __future__ import annotations

import json
import os
import platform
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

EXPECTED_MODEL_SHA = '4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad'


def fail(code, detail):
    print('IMPORT FAIL: %s :: %s' % (code, detail))
    return 1


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='verify imports and the pose model before anything else')
    ap.add_argument('--model', default=os.path.join(ROOT, 'models', 'pose_landmarker_full.task'),
                    help='model file to verify (default: the one in this package)')
    a = ap.parse_args(argv)
    try:
        import numpy as np
        print('IMPORT OK: numpy %s' % np.__version__)
    except Exception as e:                     # noqa: BLE001
        return fail('numpy', '%s: %s' % (type(e).__name__, e))
    try:
        import cv2
        print('IMPORT OK: opencv %s' % cv2.__version__)
    except Exception as e:                     # noqa: BLE001
        return fail('opencv', '%s: %s' % (type(e).__name__, e))
    try:
        import mediapipe as mp
        print('IMPORT OK: mediapipe %s' % getattr(mp, '__version__', 'unknown'))
    except Exception as e:                     # noqa: BLE001
        print(traceback.format_exc())
        return fail('mediapipe', '%s: %s -- if this is a musl/glibc mismatch, the message says '
                                 '"Error loading shared library" or "GLIBC_2.xx not found"' % (type(e).__name__, e))

    model = os.path.abspath(a.model)
    if not os.path.isfile(model):
        return fail('model_missing', 'expected %s (sha256 %s)' % (model, EXPECTED_MODEL_SHA))
    import hashlib
    h = hashlib.sha256()
    with open(model, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    if h.hexdigest() != EXPECTED_MODEL_SHA:
        return fail('model_sha256', 'got %s expected %s' % (h.hexdigest(), EXPECTED_MODEL_SHA))
    print('IMPORT OK: model sha256 matches')

    # force every native library to load: create a landmarker and run it once on a blank image
    try:
        from mediapipe.tasks.python import vision
        base = mp.tasks.BaseOptions(model_asset_path=model)
        opts = vision.PoseLandmarkerOptions(base_options=base, running_mode=vision.RunningMode.IMAGE,
                                           num_poses=1, min_pose_detection_confidence=0.3)
        lm = vision.PoseLandmarker.create_from_options(opts)
        blank = np.zeros((256, 256, 3), dtype=np.uint8)
        lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=blank))
        print('IMPORT OK: landmarker created and ran on a blank image')
    except Exception as e:                     # noqa: BLE001
        print(traceback.format_exc())
        return fail('landmarker_runtime', '%s: %s' % (type(e).__name__, e))

    print('IMPORT OK: all checks passed on %s / %s' % (platform.machine(), platform.python_version()))
    print(json.dumps({'mediapipe': getattr(mp, '__version__', None), 'numpy': np.__version__,
                      'opencv': cv2.__version__, 'model_sha256': EXPECTED_MODEL_SHA,
                      'machine': platform.machine(), 'python': platform.python_version()}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())