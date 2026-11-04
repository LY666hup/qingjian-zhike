#!/usr/bin/env python3
"""Panel contract tests (qjzc.pose.panel.v1): accepted fixtures and the refusals that matter.

The fixtures are records the worker really produced (see samples/expected/v3_*.stdout); they are checked as data,
so this file needs no mediapipe.  Run from the package root: python tests/test_panel_v3.py
"""
from __future__ import annotations

import copy
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pose import contract as C   # noqa: E402

fails = []
EXP = os.path.join(ROOT, 'samples', 'expected')


def check(label, cond, detail=''):
    print('  %-64s %s%s' % (label, 'PASS' if cond else 'FAIL', ('  ' + str(detail)[:58]) if detail else ''))
    if not cond:
        fails.append(label)


def refuse(label, rec, needle=''):
    try:
        C.validate(rec)
        check(label, False, 'accepted, should have been refused')
    except ValueError as e:
        check(label, needle in str(e), str(e)[:58])


def load(name):
    return json.load(io.open(os.path.join(EXP, name), encoding='utf-8'))


P = load('panel_v3_sample.json')     # a frame whose continuity is proven, so counting is on
A = load('panel_v3_absent.json')     # the same stage with nobody in frame
S = load('panel_v3_640.json')        # a single 640x360 frame: legal, but it cannot prove anything yet

print('== A. the shipped fixtures ==')
check('proven panel validates', C.validate(P) is True)
check('absent panel validates', C.validate(A) is True)
check('single-frame 640x360 panel validates', C.validate(S) is True)
check('schema is the panel schema', P['schema'] == 'qjzc.pose.panel.v1')
check('identity carries the six fields', sorted(P['identity']) == sorted(C.IDENTITY_KEYS))
check('top-level mirrors agree with identity', all(P[k] == P['identity'][k] for k in C.IDENTITY_KEYS))
check('backend is mediapipe_cpu on the RK3576J target',
      P['backend'] == 'mediapipe_cpu' and P['inference_device'] == 'RK3576J')
check('the source processor is kept separate from our device', P['processor'] == 'fpga')
check('provenance: input_record_id, mode, sha, size, sequence',
      all(P.get(k) for k in ('input_record_id', 'processing_mode', 'jpeg_sha256', 'preview_size',
                             'preview_sequence')))
check('33 landmarks, normalised to the analysed image',
      len(P['pose']['keypoints']) == 33 and P['pose']['coordinate_space'] == 'preview_pixels_normalized')
check('the 640x360 preview also carries exactly 33 points',
      len(S['pose']['keypoints']) == 33 and S['preview_size'] == [640, 360]
      and S['pose']['coordinate_space'] == 'preview_pixels_normalized')
check('model sha256 is the required one', P['model']['sha256'] == C.MODEL_SHA256)
check('the counted frame says continuity is proven and names its tracking',
      P['continuity'] == 'proven' and P['score_eligible'] is True and P['counting'] == 'enabled'
      and P['person']['tracking']['method'] == 'upstream_id' and P['person']['person_id'] is not None)
check('a single frame is legal but not a result',
      S['counting'] == 'disabled' and S['rep_count'] is None and S['score_eligible'] is False
      and S['continuity'] == 'assumed' and S['counter_reason'] == 'continuity_unproven')
check('absent panel: no points, no box, no count, nothing proven',
      A['pose_present'] is False and A['pose']['keypoints'] == [] and A['person']['box'] is None
      and A['rep_count'] is None and A['counting'] == 'disabled' and A['counter_reason'] == 'no_person'
      and A['continuity'] == 'unknown' and A['score_eligible'] is False)
check('the absent record still carries numeric pose_score and inference_ms',
      isinstance(A['pose']['pose_score'], float) and isinstance(A['pose']['inference_ms'], float))
check('selection vocabulary is the Windows Round-2 one',
      P['person']['selection'] in C.SELECTIONS and all(v in C.SELECTIONS for v in
                                                      ('by_id', 'by_box', 'single', 'max_score_ambiguous',
                                                       'person_id_not_found', 'no_person')))
check('geometry is null when the boxes were already in preview pixels', P['geometry'] is None)

print('== B. refusals: provenance ==')
r = copy.deepcopy(P); r['identity']['processing_mode'] = 'blur'
refuse('unknown processing_mode', r, 'processing_mode')
r = copy.deepcopy(P); r['processing_mode'] = 'original'
refuse('top-level mode disagrees with identity', r, 'disagrees with identity')
r = copy.deepcopy(P); r['jpeg_sha256'] = 'xyz'
refuse('jpeg_sha256 that is not a hash', r, '64 lowercase hex')
r = copy.deepcopy(P); del r['input_record_id']
refuse('no input_record_id', r, 'input_record_id')
r = copy.deepcopy(P); r['input_record_id'] = 'not a record id'
refuse('input_record_id with spaces', r, 'input_record_id')
r = copy.deepcopy(P); r['identity']['camera_id'] = 'front'
refuse('camera front instead of A/B', r, 'camera_id')
r = copy.deepcopy(P); del r['identity']['boot_id']
refuse('identity missing boot_id', r, 'boot_id')
r = copy.deepcopy(P); r['capture_group_id'] = 'invented'
check('capture_group_id may be a real string', C.validate(r) is True)
r = copy.deepcopy(A); r['capture_group_id'] = 12345
refuse('capture_group_id that is not a string', r, 'capture_group_id')
r = copy.deepcopy(P); r['identity']['fpga_bit_sha256'] = 'zz'
refuse('bitstream hash not 64 hex', r, 'fpga_bit_sha256')
r = copy.deepcopy(P); r['source_frame_id'] = ''
refuse('empty source_frame_id', r, 'source_frame_id')

print('== C. refusals: our own claims ==')
r = copy.deepcopy(P); r['backend'] = 'rk_npu'
refuse('backend must not be the NPU', r, 'backend')
r = copy.deepcopy(P); r['inference_device'] = 'RK3588'
refuse('inference device must be the RK3576J target', r, 'inference device')
r = copy.deepcopy(P); r['processor'] = 'mediapipe'
refuse('the source processor field must not be overwritten by our backend', r, 'processor')
r = copy.deepcopy(P); r['inference_host'] = ''
refuse('empty inference_host', r, 'inference_host')
r = copy.deepcopy(P); r['pose_available'] = 1
refuse('pose_available as a truthy int', r, 'pose_available')

print('== D. refusals: counting, continuity and eligibility ==')
r = copy.deepcopy(P); r['counting'] = 'disabled'; r['rep_count'] = 0; r['score_eligible'] = False
refuse('counting off but a number is present', r, 'contradicts')
r = copy.deepcopy(P); r['rep_count'] = None
refuse('counting on without a number', r, 'contradicts')
r = copy.deepcopy(P); r['timing'] = {'timestamp_ms': None, 'time_source': None}
refuse('counting on without a timestamp', r, 'without a timestamp')
r = copy.deepcopy(P); r['timing'] = {'timestamp_ms': 1, 'time_source': None}
refuse('counting on without a time source', r, 'where the timestamp came from')
r = copy.deepcopy(P); r['counter_reset_reason'] = 'vibes'
refuse('unknown reset reason', r, 'counter_reset_reason')
r = copy.deepcopy(P); r['counter_reset_reason'] = 'session_switch'
refuse('a session switch must not reset the Round-2 counter', r, 'counter_reset_reason')
r = copy.deepcopy(P); r['counter_reset_detail'] = 'person_switch'
refuse('reset detail that claims a person switch the reason denies', r, 'disagrees with detail')
r = copy.deepcopy(P); r['counter_reset_detail'] = 'mode_switch'
check('a mode switch is a detail, not a Round-2 counter reset', C.validate(r) is True)
r = copy.deepcopy(P); r['counter_reason'] = 'because'
refuse('unknown counter reason', r, 'counter_reason')
r = copy.deepcopy(P); r['counter_reason'] = 'low_visibility_23'
refuse('a paused note must not pass for a shown number', r, 'contradicts counting')
r = copy.deepcopy(P); r['identity']['processing_mode'] = 'sepia'; r['processing_mode'] = 'sepia'
refuse('unknown processing mode', r, 'processing_mode')
r = copy.deepcopy(P); r['identity']['processor'] = 'gpu'; r['processor'] = 'gpu'
refuse('unknown source processor', r, 'processor')
r = copy.deepcopy(P); r['continuity'] = 'assumed'
refuse('a counted number without proven continuity', r, 'proven continuity')
r = copy.deepcopy(P); r['continuity'] = 'proven'; r['person']['tracking']['method'] = 'none'
refuse('proven continuity from a tracker that is not an identity', r, 'disagrees with person_id')
r = copy.deepcopy(P); r['score_eligible'] = False
refuse('score_eligible disagreeing with counting', r, 'score_eligible')
r = copy.deepcopy(P); r['score_eligible'] = None
refuse('score_eligible that is not a boolean', r, 'score_eligible')
r = copy.deepcopy(P); r['person']['tracking']['frames_with_id'] = 0
refuse('a counted result with no frame carrying the id', r, 'at least one frame')
r = copy.deepcopy(P); r['counter_note'] = 5
refuse('a non-string counter note', r, 'counter_note')
r = copy.deepcopy(P); r['counter_note'] = 'low_visibility_23'
check('a note may accompany a shown number', C.validate(r) is True)

print('== E. refusals: geometry and pose ==')
r = copy.deepcopy(P); r['person']['box'] = [0, 0, 10000, 10000]
refuse('person box outside the real preview size', r, 'not inside the preview')
r = copy.deepcopy(P); r['pose']['keypoints'] = P['pose']['keypoints'][:32]
refuse('32 landmarks', r, 'keypoints must be 33')
r = copy.deepcopy(P); r['pose']['keypoints'][0] = [1.5, 0.5, 0.9]
refuse('a landmark outside [0,1]', r, 'outside [0,1]')
r = copy.deepcopy(P); r['pose']['keypoints'][5] = [0.5, 0.5, 1.5]
refuse('visibility above 1', r, 'visibility out of range')
r = copy.deepcopy(A); r['pose']['keypoints'] = P['pose']['keypoints']
refuse('absent record that still carries points', r, 'zero keypoints')
r = copy.deepcopy(A); r['rep_count'] = 3
refuse('absent record with a repetition count', r, 'must not carry a rep count')
r = copy.deepcopy(A); r['continuity'] = 'proven'
refuse('an absent record cannot claim proven continuity', r, 'absent pose cannot claim')
r = copy.deepcopy(P); r['pose_present'] = False; r['pose_available'] = False
refuse('state that disagrees with pose_present', r, 'state does not agree')
r = copy.deepcopy(P); r['person']['selection'] = 'vibes'
refuse('unknown person selection', r, 'person.selection')
r = copy.deepcopy(P); r['person_selection'] = 'track_follow'
refuse('a selection nobody downstream knows', r, 'person.selection')
r = copy.deepcopy(P); r['person']['person_id'] = 1
refuse('a numeric person id', r, 'person_id')
r = copy.deepcopy(P); r['preview_size'] = [0, 540]
refuse('zero preview size', r, 'preview_size')

print('== F. geometry provenance ==')
r = copy.deepcopy(S); r['geometry'] = {'source_space': 'mosaic_pixels', 'mosaic_size': [1920, 1080],
                                       'roi': [0, 0, 960, 540], 'scale': [640.0 / 960, 360.0 / 540],
                                       'target_size': [640, 360]}
check('a real mosaic mapping is accepted', C.validate(r) is True)
g = copy.deepcopy(r); g['geometry']['target_size'] = [960, 540]
refuse('geometry that targets another size', g, 'disagrees with preview_size')
g = copy.deepcopy(r); g['geometry']['source_space'] = 'camera'
refuse('geometry from an unknown space', g, 'source_space')
g = copy.deepcopy(r); g['geometry']['roi'] = [0, 0, 5000, 540]
refuse('geometry whose roi leaves the mosaic', g, 'not inside the mosaic')
g = copy.deepcopy(r); g['geometry']['scale'] = [0.5, 0.5]
refuse('geometry whose scale does not reach the image', g, 'do not map onto preview_size')
g = copy.deepcopy(r); del g['geometry']['scale']
refuse('geometry without a scale', g, 'geometry.scale is required')

print('== G. the absent record is a legal record, not an error path ==')
check('no_person absent says why, and carries no number',
      A['person']['selection'] == 'no_person' and A['person']['reason'] == 'no_person'
      and A['rep_count'] is None and A['action_phase'] == 'unknown')
check('absent still carries full provenance', all(
    A.get(k) for k in ('identity', 'input_record_id', 'jpeg_sha256', 'preview_size')))
check('absent reports continuity unknown and no tracking method', A['continuity'] == 'unknown'
      and A['person']['tracking']['method'] == 'none' and A['person']['person_id'] is None)

print('== H. anonymous people: points yes, scores no ==')
r = copy.deepcopy(S)
r['person'].update({'person_id': None, 'key': None, 'selection': 'single',
                    'tracking': {'method': 'none', 'iou': None, 'frames_with_id': 0}})
r['continuity'], r['score_eligible'] = 'assumed', False
check('a single anonymous person with 33 points is legal', C.validate(r) is True)
check('and it cannot carry a count', r['rep_count'] is None and r['counting'] == 'disabled')
r2 = copy.deepcopy(r)
r2['person']['tracking'] = {'method': 'iou_follow', 'iou': 0.71, 'frames_with_id': 0}
check('an anonymous person chosen by overlap is legal', C.validate(r2) is True)
bad = copy.deepcopy(r2)
bad['person']['person_id'], bad['person']['key'] = 'P1', 'upstream:P1'
refuse('iou_follow cannot claim it has an upstream id', bad, 'disagrees with person_id')
bad = copy.deepcopy(r)
bad['person']['tracking']['method'] = 'upstream_id'
refuse('an anonymous person cannot claim upstream_id tracking', bad, 'disagrees with person_id')
bad = copy.deepcopy(r)
bad.update({'counting': 'enabled', 'rep_count': 3, 'score_eligible': True, 'counter_reason': 'ok',
            'continuity': 'proven'})
refuse('an anonymous person cannot be counted, even with points', bad,
       'a counted result needs a stable person id')
check('proven continuity with a paused counter is legal (e.g. a duplicate record)',
      C.validate(dict(copy.deepcopy(P), counting='disabled', rep_count=None, score_eligible=False,
                      counter_reason='duplicate_frame')) is True)
bad = copy.deepcopy(r)
bad['person']['tracking']['iou'] = 1.5
refuse('an iou outside [0,1]', bad, 'iou')

print('== I. no person id is not the same as no person ==')
r = copy.deepcopy(S)
r['person']['selection'] = 'max_score_ambiguous'
r['person_selection'] = 'max_score_ambiguous'
refuse('ambiguous selection must be empty of boxes', r, 'cannot be shipped with the selection')
r = copy.deepcopy(A)
r['person']['selection'] = 'single'
r['person_selection'] = 'single'
refuse('absent cannot call itself a single selected person', r, 'without a box')

print()
print('VERDICT', 'PANEL_CONTRACT_OK' if not fails else 'PANEL_CONTRACT_FAILED: ' + ','.join(fails[:3]))
sys.exit(0 if not fails else 1)