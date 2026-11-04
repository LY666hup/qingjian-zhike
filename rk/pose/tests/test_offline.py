#!/usr/bin/env python3
"""Offline suite that needs nothing but the Python standard library.

It checks what can be checked without a landmarker: every shipped panel fixture is valid, the model identity is
the required one, the counter configuration is the agreed one, and the job fixtures carry enough provenance to be
bound.  The heavier suites are `test_binding.py`, `test_panel_v3.py`, `test_analysis_v1.py` and
`test_against_windows.py`.

Run from the package root: python tests/test_offline.py
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pose import contract as C   # noqa: E402

fails = []


def check(label, cond, detail=''):
    print('  %-64s %s%s' % (label, 'PASS' if cond else 'FAIL', ('  ' + str(detail)[:58]) if detail else ''))
    if not cond:
        fails.append(label)


EXP = os.path.join(ROOT, 'samples', 'expected')

print('== A. model identity ==')
check('the required model sha256 is pinned in the contract',
      C.MODEL_SHA256 == '4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad')
model = os.path.join(ROOT, 'models', 'pose_landmarker_full.task')
if os.path.isfile(model):
    h = hashlib.sha256(open(model, 'rb').read()).hexdigest()
    check('the model on disk matches the pinned sha256', h == C.MODEL_SHA256, h[:16])
else:
    print('  %-64s SKIP  (not shipped in the package; step 3 verifies it on the board)' % 'model file present')

print('== B. every shipped panel fixture is valid ==')
panels = [p for p in sorted(glob.glob(os.path.join(EXP, '*panel*.json')))
          if not p.endswith('.analysis.json')]
check('there are panel fixtures to check', len(panels) >= 2, len(panels))
for path in panels:
    try:
        rec = json.load(io.open(path, encoding='utf-8'))
        C.validate(rec)
        check(os.path.basename(path), True)
    except (ValueError, OSError) as e:
        check(os.path.basename(path), False, str(e)[:50])

print('== C. counter configuration ==')
from pose.squat import SquatCounter   # noqa: E402
c = SquatCounter()
check('a fresh counter starts at zero', getattr(c, 'reps', 0) == 0)
out = c.update([{'i': i, 'x': 0.5, 'y': 0.5, 'visibility': 0.0} for i in range(33)], 1000)
check('invisible joints are not counted', out.get('reps') == 0, out.get('reason'))

print('== D. job fixtures are bindable in shape ==')
jobs = sorted(glob.glob(os.path.join(ROOT, 'samples', 'jobs', '*.json')))
check('there are job fixtures', len(jobs) >= 1, len(jobs))
for path in jobs[:4]:
    j = json.load(io.open(path, encoding='utf-8'))
    ok = (isinstance(j.get('identity'), dict) and set(C.IDENTITY_KEYS) <= set(j['identity'])
          and isinstance(j.get('detections'), dict)
          and {'input_record_id', 'jpeg_sha256', 'camera_id', 'processing_mode', 'items'} <= set(j['detections']))
    check('job fixture %s has full provenance' % os.path.basename(path), ok)

print()
print('VERDICT', 'POSE_MODULE_OFFLINE_PASS' if not fails else 'POSE_MODULE_OFFLINE_FAILED: ' + ','.join(fails[:3]))
sys.exit(0 if not fails else 1)