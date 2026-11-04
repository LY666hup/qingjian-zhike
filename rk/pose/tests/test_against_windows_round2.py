#!/usr/bin/env python3
"""Acceptance against the Windows Round-2 tree, using their code as the judge.

The point of this file is that our panel is fed to the receiver that actually ships in
`第二轮_AI分支与骨架配对增量_20261101`, not to a re-implementation of it:

    profiles.validate_profile / analysis_source_keys
    pose_panel_adapter.adapt_pose_panel      (the formal qjzc.pose.panel.v1 entry)
    pose_input.validate_pose                 (the receive path, specified schema -> display object)
    pose_pairing.pair_pose                   (source identity + JPEG evidence + preview size)
    frame_identity.input_record_id           (the per-frame X-Input-Record-Id header)

Two GUI-side modules are stubbed because they are not part of the increment: `mjpeg_codec` (needs PySide6,
absent on this machine) and `exercise_events` (their action table lives in the base app).  Nothing else is
replaced, so a change in their validation rules breaks this test.

Run: python tests/test_against_windows_round2.py [--tree <path to the 第二轮 tree>]
"""
from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import io
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DEFAULT_TREE = r'C:\Users\Public\win_r2_20261101\第二轮_AI分支与骨架配对增量_20261101'
fails = []


def check(label, cond, detail=''):
    print('  %-66s %s%s' % (label, 'PASS' if cond else 'FAIL', ('  ' + str(detail)[:56]) if detail else ''))
    if not cond:
        fails.append(label)


def load(name):
    return json.load(io.open(os.path.join(ROOT, 'samples', 'expected', name), encoding='utf-8'))


def stub_gui_modules():
    """The two modules the increment imports but does not ship.  Stubs never influence validation."""
    codec = types.ModuleType('mjpeg_codec')
    codec.MAX_STATUS = 360
    codec.MAX_JPEG = 8 << 20

    def _unavailable(*a, **k):
        raise RuntimeError('stub: decoding is not part of this acceptance test')

    codec.decode_jpeg = _unavailable
    codec.boundary_from_content_type = lambda value: 'boundary'
    codec.MultipartParser = type('MultipartParser', (), {'__init__': lambda self, *a, **k: None})
    codec.parse_preview_status = _unavailable
    sys.modules['mjpeg_codec'] = codec
    events = types.ModuleType('exercise_events')
    events.action_code = lambda code: code
    sys.modules['exercise_events'] = events


def profile_for(panel, analysis_url='http://127.0.0.1:1/pose'):
    ident = panel['identity']
    later = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    return {'session_id': ident['session_id'], 'boot_id': ident['boot_id'],
            'camera_id': ident['camera_id'], 'processing_mode': ident['processing_mode'],
            'fpga_bit_sha256': ident['fpga_bit_sha256'], 'processor': ident['processor'],
            'status_url': 'http://127.0.0.1:1/status.json?session=' + ident['session_id'],
            'video_url': 'http://127.0.0.1:1/stream.mjpg',
            'analysis_url': analysis_url,
            'authorized_until': later}


def real_panels():
    """Every panel our worker actually emitted in the captured suites, plus the shipped fixtures."""
    out = []
    for name in ('panel_v3_sample.json', 'panel_v3_640.json', 'panel_v3_absent.json',
                 'panel_v3_960.json', 'panel_v3_absent960.json'):
        path = os.path.join(ROOT, 'samples', 'expected', name)
        if os.path.isfile(path):
            out.append((name, json.load(io.open(path, encoding='utf-8'))))
    for path in sorted(glob.glob(os.path.join(ROOT, 'samples', 'expected', 'v3_*.stdout'))):
        label = os.path.basename(path)
        for i, line in enumerate(io.open(path, encoding='utf-8')):
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get('panel'):
                out.append(('%s#%d' % (label, i + 1), row['panel']))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', default=os.environ.get('QJZC_R2_TREE', DEFAULT_TREE))
    a = ap.parse_args(argv)
    patch = os.path.join(a.tree, 'patch')
    if not os.path.isdir(patch):
        print('R2_TREE_MISSING: %s' % a.tree)
        return 2
    print('Round-2 tree: %s' % a.tree)
    stub_gui_modules()
    sys.path.insert(0, patch)

    from profiles import validate_profile  # noqa: F401
    import profiles
    import pose_panel_adapter
    import pose_input
    import pose_pairing
    import frame_identity

    print('== A. their profile rules accept the identity our worker reports ==')
    P = load('panel_v3_sample.json')
    A = load('panel_v3_absent.json')
    S = load('panel_v3_640.json')
    prof = validate_profile(profile_for(P))
    check('validate_profile normalises the project', prof['fpga_bit_sha256'] == P['fpga_bit_sha256'].lower())
    check('analysis_schema defaults to the panel once analysis_url is set',
          prof.get('analysis_schema') == 'qjzc.pose.panel.v1')
    check('analysis_source_keys points at the source carrying analysis_url',
          profiles.analysis_source_keys({(P['camera_id'], P['processing_mode']): prof})
          == {P['camera_id']: (P['camera_id'], P['processing_mode'])})

    print('== B. their formal entry: adapt_pose_panel(profile, our panel) ==')
    value = pose_panel_adapter.adapt_pose_panel(prof, P)
    check('schema is the display schema', value['schema'] == 'qjzc.pose.display.v1')
    check('kind is pose and 33 normalised points survive',
          value['kind'] == 'pose' and len(value['persons']) == 1
          and len(value['persons'][0]['keypoints']) == 33
          and value['persons'][0]['coordinate_space'] == 'normalized_image')
    check('frame identity stays preview_only and no frame id is invented',
          value['frame_identity'] == 'preview_only' and value['frame_id'] is None
          and value['source_frame_id'] is None and value['provider_source_frame_id'] is None)
    check('a proven frame is usable: the number is shown as a result',
          value['rep_count'] == P['rep_count'] and value['count_unavailable_reason'] is None
          and value['persons'][0]['rep_count'] == P['rep_count'])
    check('the provider number is preserved next to the display number',
          value['provider_rep_count'] == P['rep_count'])
    check('identity and JPEG evidence travel with the value',
          all(value[k] == P[k] for k in ('session_id', 'boot_id', 'camera_id', 'processing_mode',
                                         'fpga_bit_sha256', 'processor'))
          and value['jpeg_sha256'] == P['jpeg_sha256']
          and value['preview_size'] == P['preview_size'])

    paused = pose_panel_adapter.adapt_pose_panel(validate_profile(profile_for(S)), S)
    check('a frame without proven continuity still shows 33 points',
          len(paused['persons'][0]['keypoints']) == 33)
    check('but its number is withheld, with the reason and the provider value kept',
          paused['rep_count'] is None and paused['count_unavailable_reason'] == S['counter_reason']
          and paused['provider_rep_count'] == S['rep_count'] and paused['persons'][0]['rep_count'] is None)

    absent = pose_panel_adapter.adapt_pose_panel(validate_profile(profile_for(A)), A)
    check('an absent panel becomes kind=absent with no people and no count',
          absent['kind'] == 'absent' and absent['persons'] == [] and absent['rep_count'] is None
          and absent['pose_present'] is False)

    print('== C. every panel our worker emitted is accepted, without an adapter of our own ==')
    panels = real_panels()
    accepted, refused = 0, []
    for label, panel in panels:
        try:
            v = pose_panel_adapter.adapt_pose_panel(validate_profile(profile_for(panel)), panel)
            assert v['schema'] == 'qjzc.pose.display.v1'
            accepted += 1
        except Exception as e:                       # noqa: BLE001 - report, never swallow silently
            refused.append((label, '%s: %s' % (type(e).__name__, e)))
    check('%d real panels accepted, 0 refused' % accepted, not refused and accepted >= 12,
          refused[:1] or '%d panels' % accepted)

    print('== D. their receive path: validate_pose picks the panel schema by itself ==')
    message, image = pose_input.validate_pose(prof, P)
    check('validate_pose returns the display object and no image', image is None
          and message['schema'] == 'qjzc.pose.display.v1')
    check('a legacy qjzc.analysis.v1 record is refused when the profile expects the panel',
          _raises(lambda: pose_input.validate_pose(prof, dict(load('analysis_v1_sample.json'))),
                  'schema 与配置 analysis_schema 不一致'))

    print('== E. pairing: identity + JPEG evidence + preview size, not person ids ==')
    key = (P['camera_id'], P['processing_mode'])
    video_meta = dict(prof, jpeg_sha256=P['jpeg_sha256'], image_size=list(P['preview_size']),
                      input_record_id=P['input_record_id'])
    ok, why = pose_pairing.pair_pose(key, key, message, video_meta, tuple(P['preview_size']))
    check('the pair matches on our real panel', (ok, why) == (True, 'matched'), (ok, why))
    ok, why = pose_pairing.pair_pose(key, key, message, dict(video_meta, processing_mode='gray'),
                                     tuple(P['preview_size']))
    check('another processing mode is refused', (ok, why) == (False, 'processing_mode_mismatch'), why)
    other_key = (P['camera_id'], 'gray')
    ok, why = pose_pairing.pair_pose(other_key, key, message, video_meta, tuple(P['preview_size']))
    check('a preview that is not the AI input is refused', (ok, why) == (False, 'preview_is_not_ai_input'), why)
    ok, why = pose_pairing.pair_pose(key, key, message, dict(video_meta, jpeg_sha256='a' * 64),
                                     tuple(P['preview_size']))
    check('a different JPEG is refused', (ok, why) == (False, 'jpeg_sha256_mismatch'), why)
    ok, why = pose_pairing.pair_pose(key, key, message, dict(video_meta, image_size=[640, 360]),
                                     tuple(P['preview_size']))
    check('a contradicting video size is refused', (ok, why) == (False, 'video_size_mismatch'), why)
    ok, why = pose_pairing.pair_pose(key, key, message, dict(video_meta, input_record_id='other'),
                                     tuple(P['preview_size']))
    check('a different input record is refused', (ok, why) == (False, 'input_record_id_mismatch'), why)
    ok, why = pose_pairing.pair_pose(key, key, message, dict(video_meta, boot_id=''), tuple(P['preview_size']))
    check('a missing boot id is refused', (ok, why) == (False, 'boot_id_missing'), why)
    ok, why = pose_pairing.pair_pose((S['camera_id'], S['processing_mode']),
                                     (S['camera_id'], S['processing_mode']), paused,
                                     dict(validate_profile(profile_for(S)), jpeg_sha256=S['jpeg_sha256'],
                                          image_size=list(S['preview_size']),
                                          input_record_id=S['input_record_id']),
                                     tuple(S['preview_size']))
    check('a paused-but-present pose still pairs (points are shown, the count is not)',
          (ok, why) == (True, 'matched'), (ok, why))
    ok, why = pose_pairing.pair_pose((A['camera_id'], A['processing_mode']),
                                     (A['camera_id'], A['processing_mode']), absent,
                                     dict(validate_profile(profile_for(A)), jpeg_sha256=A['jpeg_sha256'],
                                          image_size=list(A['preview_size']),
                                          input_record_id=A['input_record_id']),
                                     tuple(A['preview_size']))
    check('an absent pose does not pair', (ok, why) == (False, 'pose_absent'), why)

    print('== F. the per-frame record header their receiver reads ==')
    check('a normal record id is accepted', frame_identity.input_record_id({'x-input-record-id': 'rk-A-gray-1'})
          == 'rk-A-gray-1')
    check('no header means None, never a generated id',
          frame_identity.input_record_id({}) is None)
    check('257 characters are refused',
          _raises(lambda: frame_identity.input_record_id({'x-input-record-id': 'a' * 257}), '256'))
    check('a non-printable character is refused',
          _raises(lambda: frame_identity.input_record_id({'x-input-record-id': 'a\tb'}), '可打印'))

    print('== G. the same panels, but with our claims broken: their adapter refuses ==')
    cases = [
        ('backend other than mediapipe_cpu', {'backend': 'rk_npu'}, 'backend'),
        ('identity field missing from the message', {'boot_id': None}, 'boot_id'),
        ('selection outside their vocabulary', {'person': dict(P['person'], selection='track_follow')},
         '人物选择方式无效'),
        ('reset reason outside their vocabulary', {'counter_reset_reason': 'session_switch'},
         'counter_reset_reason'),
        ('counting off but a number present', {'counting': 'disabled'}, 'disabled 的计数必须为 null'),
        ('32 landmarks', {'pose': dict(P['pose'], keypoints=P['pose']['keypoints'][:32])}, '33'),
        ('absent with points', {'pose_present': False, 'pose_available': False, 'state': 'pose_absent',
                                'pose': dict(A['pose'], keypoints=P['pose']['keypoints'])}, '缺失时必须为空'),
    ]
    for label, patch_fields, needle in cases:
        bad = copy.deepcopy(P)
        bad.update(patch_fields)
        err = _raises(lambda b=bad: pose_panel_adapter.adapt_pose_panel(prof, b))
        check('refused: %s' % label, bool(err) and needle in err, (err or 'accepted')[:56])

    print()
    print('VERDICT', 'WINDOWS_ROUND2_ACCEPTS_PANEL' if not fails
          else 'WINDOWS_ROUND2_FAILED: ' + ','.join(fails[:3]))
    return 0 if not fails else 1


def _raises(fn, needle=''):
    """Return the refusal text when the call raised (and, if asked, when it names the reason); '' otherwise."""
    try:
        fn()
    except Exception as e:                       # noqa: BLE001 - assertions below read the text
        text = '%s: %s' % (type(e).__name__, e)
        return text if (not needle or needle in text) else ''
    return ''


if __name__ == '__main__':
    raise SystemExit(main())