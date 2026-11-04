#!/usr/bin/env python3
"""Acceptance against the real Windows receiver, not against my reading of it.

It imports `pose_input.validate_pose` and `profiles` from the Windows package and feeds them:

  * the analysis message our adapter derives from the shipped panel fixture (must be accepted);
  * the absent message (must be accepted);
  * panels with a wrong processing mode / hash / identity (the adapter must refuse them, and the receiver must
    refuse the equivalent mutation - two independent gates);
  * hand-made defects in the analysis channel itself (each must be rejected).

PySide6 is not installed here, so `mjpeg_codec` is replaced by a stub providing only the names the imported
modules need; `validate_pose` never touches Qt itself.  What is tested is the receiver's own validation code.

Usage: python tests/test_against_windows.py --windows "<path to .../Windows新版>"
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pose import analysis_v1 as A   # noqa: E402

fails = []
EXP = os.path.join(ROOT, 'samples', 'expected')


def check(label, cond, detail=''):
    print('  %-64s %s%s' % (label, 'PASS' if cond else 'FAIL', ('  ' + str(detail)[:56]) if detail else ''))
    if not cond:
        fails.append(label)


def jpeg_size(payload):
    i = 2
    while i < len(payload) - 9:
        if payload[i] != 0xFF:
            i += 1
            continue
        marker = payload[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h = (payload[i + 5] << 8) | payload[i + 6]
            w = (payload[i + 7] << 8) | payload[i + 8]
            return w, h
        seg = (payload[i + 2] << 8) | payload[i + 3]
        i += 2 + seg
    raise ValueError('no SOF marker')


def install_codec_stub():
    mod = types.ModuleType('mjpeg_codec')

    class Image:
        def __init__(self, w, h):
            self.w, self.h = w, h

    def decode_jpeg(payload):
        w, h = jpeg_size(payload)
        return Image(w, h)

    mod.MAX_JPEG = 16 * 1024 * 1024
    mod.MAX_HEADER = 8192
    mod.MAX_STATUS = 65536
    mod.decode_jpeg = decode_jpeg
    mod.boundary_from_content_type = lambda v: 'frame'
    mod.MultipartParser = object
    mod.parse_preview_status = lambda raw: {}
    mod.checked_url = lambda v: v
    sys.modules['mjpeg_codec'] = mod


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--windows', required=True)
    ap.add_argument('--panel', default=os.path.join(EXP, 'panel_v3_sample.json'))
    ap.add_argument('--absent', default=os.path.join(EXP, 'panel_v3_absent.json'))
    a = ap.parse_args(argv)

    install_codec_stub()
    sys.path.insert(0, a.windows)
    import pose_input
    from profiles import ConfigError, IdentityError

    panel = json.load(io.open(a.panel, encoding='utf-8'))
    absent = json.load(io.open(a.absent, encoding='utf-8'))
    profile = dict(panel['identity'])
    print('== receiver from the Windows package ==')
    print('  pose_input:', os.path.join(a.windows, 'pose_input.py'))
    print('  panel mode=%s camera=%s size=%s' % (panel['processing_mode'], panel['camera_id'],
                                                 panel['preview_size']))

    print('== A. our converted panel is accepted as it ships ==')
    msg = A.to_analysis_v1(panel, profile, panel['preview_sequence'], panel_identity=True)
    try:
        got, image = pose_input.validate_pose(profile, msg)
        check('validate_pose accepts the converted panel', got['schema'] == 'qjzc.analysis.v1')
        check('persons carry 33 normalized points',
              len(got['persons'][0]['keypoints']) == 33
              and got['persons'][0]['coordinate_space'] == 'normalized_image')
        check('no image payload means no decode', image is None)
    except Exception as e:                                   # noqa: BLE001
        check('validate_pose accepts the converted panel', False, repr(e))

    print('== B. the image hash gate is real ==')
    jpg = open(os.path.join(ROOT, 'samples', 'frame_640x360.jpg'), 'rb').read()
    msg2 = dict(msg)
    msg2['image_base64'] = base64.b64encode(jpg).decode('ascii')
    msg2['jpeg_sha256'] = hashlib.sha256(jpg).hexdigest()
    try:
        _, image = pose_input.validate_pose(profile, msg2)
        check('image + matching sha accepted and decoded', image is not None and image.w == 640)
    except Exception as e:                                   # noqa: BLE001
        check('image + matching sha accepted and decoded', False, repr(e))
    bad = dict(msg2); bad['jpeg_sha256'] = 'b' * 64
    try:
        pose_input.validate_pose(profile, bad)
        check('wrong sha rejected', False)
    except ConfigError as e:
        check('wrong sha rejected', 'SHA' in str(e), str(e)[:40])

    print('== C. absent pose is accepted only when it carries nothing ==')
    amsg = A.to_analysis_v1(absent, absent['identity'], absent['preview_sequence'], panel_identity=True)
    try:
        got, _ = pose_input.validate_pose(dict(absent['identity']), amsg)
        check('absent message accepted', got['kind'] == 'absent' and got['persons'] == [])
    except Exception as e:                                   # noqa: BLE001
        check('absent message accepted', False, repr(e))

    print('== D. a panel with the wrong provenance cannot be relabelled into an accepted message ==')
    for label, mutate in (
            ('wrong processing_mode', lambda p: p['identity'].__setitem__('processing_mode', 'original')),
            ('wrong camera', lambda p: p['identity'].__setitem__('camera_id', 'B')),
            ('wrong jpeg_sha256', lambda p: p.__setitem__('jpeg_sha256', 'c' * 64)),
            ('wrong session', lambda p: p['identity'].__setitem__('session_id', 'f' * 32)),
            ('wrong bitstream hash', lambda p: p['identity'].__setitem__('fpga_bit_sha256', '1' * 64)),
    ):
        p = json.loads(json.dumps(panel))
        mutate(p)
        try:
            converted = A.to_analysis_v1(p, dict(p['identity']), 1, panel_identity=True,
                                         image_path=os.path.join(ROOT, 'samples', 'frame_640x360.jpg'))
        except A.AdapterError as e:
            check('refused: %s (adapter)' % label, True, str(e)[:36])
            continue
        try:
            pose_input.validate_pose(profile, converted)
            check('refused: %s' % label, False, 'accepted by both gates')
        except (ConfigError, IdentityError) as e:
            check('refused: %s (receiver)' % label, True, str(e)[:36])

    print('== E. the receiver still refuses the mistakes we used to make ==')
    mutants = [
        ('panel schema instead of analysis', lambda m: m.__setitem__('schema', 'qjzc.pose.panel.v1')),
        ('preview coordinate space',
         lambda m: m['persons'][0].__setitem__('coordinate_space', 'preview_pixels_normalized')),
        ('negative frame_id', lambda m: m.__setitem__('frame_id', -1)),
        ('kind=bogus', lambda m: m.__setitem__('kind', 'boxes')),
        ('two persons with the same id',
         lambda m: m['persons'].append(json.loads(json.dumps(m['persons'][0])))),
        ('rep_count as a string', lambda m: m['persons'][0].__setitem__('rep_count', '3')),
        ('quality_score out of range', lambda m: m['persons'][0].__setitem__('quality_score', 120)),
        ('action code starting with a digit',
         lambda m: m['persons'][0].__setitem__('counts_by_action', {'9BAD': 1})),
        ('wrong camera for this profile', lambda m: m.__setitem__('camera_id', 'B')),
        ('missing boot_id', lambda m: m.pop('boot_id')),
    ]
    for label, mut in mutants:
        m = json.loads(json.dumps(msg))
        mut(m)
        try:
            pose_input.validate_pose(profile, m)
            check('rejected: %s' % label, False)
        except (ConfigError, IdentityError) as e:
            check('rejected: %s' % label, True, str(e)[:36])

    print()
    print('VERDICT', 'WINDOWS_ACCEPTS_PANEL' if not fails else 'WINDOWS_REJECTS: ' + ','.join(fails[:3]))
    return 0 if not fails else 1


if __name__ == '__main__':
    raise SystemExit(main())