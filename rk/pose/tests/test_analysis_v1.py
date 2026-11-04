#!/usr/bin/env python3
"""Adapter tests: qjzc.pose.panel.v1 -> qjzc.analysis.v1.  No mediapipe needed.

Run from the package root: python tests/test_analysis_v1.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pose import analysis_v1 as A   # noqa: E402

fails = []
EXP = os.path.join(ROOT, 'samples', 'expected')


def check(label, cond, detail=''):
    print('  %-64s %s%s' % (label, 'PASS' if cond else 'FAIL', ('  ' + str(detail)[:58]) if detail else ''))
    if not cond:
        fails.append(label)


def need_error(label, fn, needle=''):
    try:
        fn()
        check(label, False, 'no error raised')
    except A.AdapterError as e:
        check(label, needle in str(e), str(e)[:58])


P = json.load(io.open(os.path.join(EXP, 'panel_v3_sample.json'), encoding='utf-8'))
AB = json.load(io.open(os.path.join(EXP, 'panel_v3_absent.json'), encoding='utf-8'))
IDENT = P['identity']
SMALL = os.path.join(ROOT, 'samples', 'frame_640x360.jpg')
FULL = os.path.join(ROOT, 'samples', 'frame_0001_seq_000012.jpg')

print('== A. the happy path ==')
S = json.load(io.open(os.path.join(EXP, 'panel_v3_640.json'), encoding='utf-8'))
msg = A.to_analysis_v1(P, IDENT, 40, image_path=FULL, action_type='SQUAT', panel_identity=True)
small = A.to_analysis_v1(S, S['identity'], 40, image_path=SMALL, action_type='SQUAT', panel_identity=True)
check('the 640x360 panel converts too, with 33 points',
      len(small['persons'][0]['keypoints']) == 33
      and small['persons'][0]['coordinate_space'] == 'normalized_image')
check('a paused frame keeps rep_count null and its reason in pose_detail',
      small['persons'][0]['rep_count'] is None
      and small['pose_detail']['counter_reason'] == S['counter_reason']
      and small['pose_detail']['score_eligible'] is False)
check('continuity, tracking and geometry survive into the compatibility message',
      small['pose_detail']['continuity'] == S['continuity']
      and small['pose_detail']['person_tracking'] == S['person']['tracking']
      and msg['pose_detail']['continuity'] == P['continuity'])
check('schema is qjzc.analysis.v1', msg['schema'] == 'qjzc.analysis.v1')
check('kind is pose', msg['kind'] == 'pose')
check('one person, 33 points, normalized_image',
      len(msg['persons']) == 1 and len(msg['persons'][0]['keypoints']) == 33
      and msg['persons'][0]['coordinate_space'] == 'normalized_image')
check('identity comes from the panel, byte for byte', all(msg[k] == IDENT[k] for k in A.IDENTITY_KEYS))
check('the upstream person id is used (no invented P1)',
      msg['persons'][0]['person_id'] == P['person']['person_id'])
check('rep_count and counting travel together',
      (msg['persons'][0]['rep_count'] is None) == (P['counting'] != 'enabled'))
check('quality_score stays null (no invented score)', msg['persons'][0]['quality_score'] is None)
check('provenance is visible in pose_detail',
      msg['pose_detail']['input_record_id'] == P['input_record_id']
      and msg['pose_detail']['processing_mode'] == P['processing_mode'])

print('== B. absence ==')
amb = A.to_analysis_v1(AB, AB['identity'], 30, panel_identity=True)
check('kind is absent', amb['kind'] == 'absent')
check('no persons, no top-level count', amb['persons'] == [] and 'rep_count' not in amb)
check('the reason survives in pose_detail', amb['pose_detail']['counter_reason'] == 'no_person')

print('== C. refusals ==')
need_error('panel identity differs from the identity given',
           lambda: A.to_analysis_v1(P, dict(IDENT, processing_mode='original'), 1, panel_identity=True),
           'relabel')
need_error('frame_id negative', lambda: A.to_analysis_v1(P, IDENT, -1, panel_identity=True), 'frame_id')
bad = json.loads(json.dumps(P)); bad['counting'] = 'disabled'
need_error('counting off but a number is present',
           lambda: A.to_analysis_v1(bad, IDENT, 1, panel_identity=True), 'counting is off')
bad = json.loads(json.dumps(P)); bad['rep_count'] = None
need_error('counting on but no number (paused frames must be null)',
           lambda: A.to_analysis_v1(bad, IDENT, 1, panel_identity=True), 'counting is on')
need_error('image size disagreeing with the panel',
           lambda: A.to_analysis_v1(S, S['identity'], 1, image_path=FULL, panel_identity=True), 'disagrees')
b64_other = base64.b64encode(open(SMALL, 'rb').read()).decode('ascii')
need_error('image bytes that do not match the panel hash',
           lambda: A.to_analysis_v1(P, IDENT, 1, image_b64=b64_other, panel_identity=True), 'hash')
payload = open(FULL, 'rb').read()
good = A.to_analysis_v1(P, IDENT, 1, image_b64=base64.b64encode(payload).decode('ascii'), panel_identity=True)
check('matching image bytes are attached with a verified hash',
      base64.b64decode(good['image_base64']) == payload)
bad = json.loads(json.dumps(P)); bad['person']['person_id'] = None; bad['person']['key'] = None
bad['person']['tracking']['method'] = 'none'
bad.update({'continuity': 'assumed', 'counting': 'disabled', 'rep_count': None, 'score_eligible': False,
            'counter_reason': 'person_id_unverified'})
amb = A.to_analysis_v1(bad, IDENT, 1, panel_identity=True)
check('an anonymous pose is reported as absent on the legacy channel, with the reason',
      amb['kind'] == 'absent' and amb['pose_present'] is False and amb['persons'] == []
      and amb['pose_detail']['anonymous'] is True
      and amb['pose_detail']['absent_reason'] == 'person_id_unverified')
check('the panel still carries the 33 points for that frame',
      len(bad['pose']['keypoints']) == 33 and A.to_analysis_v1(
          bad, IDENT, 1, person_id='P9', panel_identity=True)['persons'][0]['person_id'] == 'P9')
ok = A.to_analysis_v1(bad, IDENT, 1, person_id='P9', panel_identity=True)
check('an explicit override names the person', ok['persons'][0]['person_id'] == 'P9')

print('== D. size comes from the file ==')
for path, want in ((FULL, [960, 540]), (SMALL, [640, 360])):
    check('%s -> %dx%d' % (os.path.basename(path), want[0], want[1]),
          list(A.image_size_from_jpeg(path)) == want, list(A.image_size_from_jpeg(path)))

print()
print('VERDICT', 'ADAPTER_OK' if not fails else 'ADAPTER_FAILED: ' + ','.join(fails[:3]))
sys.exit(0 if not fails else 1)