#!/usr/bin/env python3
"""Worker-entry tests for detections that carry no upstream tracking id.

IMPORTANT: these tests drive the real worker entry (bin/05_analyze_stream.py, Worker.process) on a real sample
JPEG, but the landmarker is an explicitly marked STUB in tests/_worker_harness.py that returns a fixed 33-point
skeleton.  No MediaPipe inference happens here, so this file proves the selection / counting / panel gates, never
model accuracy.  Real inference evidence is in samples/expected/v32_*.stdout (WSL, real model).

Run from the package root: python tests/test_worker_noid.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _worker_harness import ANON, BOX, P1, P2, Harness, W        # noqa: E402

h_global = Harness(session_id='s-noid')
run = h_global.run


def two_overlap():
    return [{'xyxy': BOX, 'score': 0.88}, {'xyxy': [260.0, 10.0, 740.0, 538.0], 'score': 0.55}]


def two_far():
    return [{'xyxy': [20.0, 10.0, 300.0, 500.0], 'score': 0.71},
            {'xyxy': [620.0, 10.0, 940.0, 500.0], 'score': 0.66}]


print('== A. one person, no upstream id: points yes, score no (stub landmarker) ==')
panels = [run(200 + i, 20000 + i * 100, ANON, 'rk-A-gaussian-0020%02d' % i) for i in range(3)]
h_global.check('every anonymous frame still yields 33 landmarks',
               [len(p['pose']['keypoints']) for p in panels] == [33] * 3)
h_global.check('selection is single, tracking says none, person id stays null',
               all(p['person']['selection'] == 'single' and p['person']['tracking']['method'] == 'none'
                   and p['person']['person_id'] is None for p in panels))
h_global.check('continuity is assumed on all three frames', [p['continuity'] for p in panels] == ['assumed'] * 3)
h_global.check('counting is off with an explicit reason, and no number is shipped',
               all(p['counting'] == 'disabled' and p['rep_count'] is None
                   and p['counter_reason'] == 'person_id_unverified' and p['score_eligible'] is False
                   for p in panels))
h_global.check('the panel contract accepts these honest anonymous records',
               all(W.C.validate(p) for p in panels))
legacy = h_global.run_full(299, 29900, ANON, 'rk-A-gaussian-002099')
h_global.check('the legacy channel reports them absent instead of naming a fake person',
               legacy['analysis']['kind'] == 'absent'
               and legacy['analysis']['pose_detail']['anonymous'] is True)

print('== B. two anonymous people: overlap may pick a box, never a person ==')
b1 = run(300, 30000, ANON, 'rk-A-gaussian-003000')
b2 = run(301, 30100, two_overlap(), 'rk-A-gaussian-003001')
b3 = run(302, 30200, two_far(), 'rk-A-gaussian-003002')
h_global.check('the first single box is selected',
               b1['person']['selection'] == 'single' and b1['person']['candidates'] == 1)
h_global.check('the overlapping box is followed by IoU, marked iou_follow',
               b2['person']['selection'] == 'single' and b2['person']['tracking']['method'] == 'iou_follow'
               and b2['person']['tracking']['iou'] is not None and b2['person']['person_id'] is None)
h_global.check('IoU following still cannot count', b2['counting'] == 'disabled'
               and b2['continuity'] == 'assumed' and b2['rep_count'] is None)
h_global.check('two boxes with no overlap at all are ambiguous, with no pose',
               b3['person']['selection'] == 'max_score_ambiguous' and b3['pose_present'] is False
               and b3['pose']['keypoints'] == [] and b3['counter_reason'] == 'person_ambiguous')
h_global.check('ambiguous is not a person: continuity unknown, no count', b3['continuity'] == 'unknown'
               and b3['rep_count'] is None and b3['score_eligible'] is False)

print('== C. a real upstream id still proves continuity ==')
h = Harness(session_id='s-noid-id')
c1 = h.run(400, 40000, P1, 'rk-A-gaussian-004000')
c2 = h.run(401, 40100, P1, 'rk-A-gaussian-004001')
h.check('frame two of the same id is proven and counted', c2['continuity'] == 'proven'
        and c2['counting'] == 'enabled' and c2['score_eligible'] is True
        and c2['counter_reason'] == 'ok' and isinstance(c2['rep_count'], int))
h.check('frame one was only assumed', c1['continuity'] == 'assumed' and c1['rep_count'] is None)
c3 = h.run(402, 40200, P1 + P2, 'rk-A-gaussian-004002', person='P2')
h.check('an id change resets to the new person', c3['person']['person_id'] == 'P2'
        and c3['counter_reset_reason'] == 'person_switch' and c3['rep_count'] is None)
c4 = h.run(403, 40300, P1 + P2, 'rk-A-gaussian-004003', person='P2')
h.check('and the new person has to prove himself again, then counts',
        c4['continuity'] == 'proven' and c4['counting'] == 'enabled')

print('== D. identity and time gates still win over any pose ==')
h = Harness(session_id='s-noid-gates')
d1 = h.run(500, 50000, P1, 'rk-A-gaussian-005000')
d2 = h.run(500, 50000, P1, 'rk-A-gaussian-005000')
h.check('a repeated record is a duplicate, not a second rep',
        d2['counter_reason'] == 'duplicate_frame' and d2['rep_count'] is None)
d3 = h.run(501, None, P1, 'rk-A-gaussian-005001', time_source=None)
h.check('without a timestamp the count pauses, the points stay',
        d3['counter_reason'] == 'no_time_base' and d3['rep_count'] is None
        and len(d3['pose']['keypoints']) == 33)
try:
    h.run(502, 50200, P1, 'rk-A-gaussian-005002', sha=('f' * 64))
    h.check('a detection record for another image is refused', False, 'accepted')
except Exception as e:                                       # noqa: BLE001
    h.check('a detection record for another image is refused', 'another image' in str(e)
            or 'sha' in str(e).lower(), str(e)[:50])
h.check('no person at all is a legal absent record',
        h.run(503, 50300, [], 'rk-A-gaussian-005003')['counter_reason'] == 'no_person')

print('== E. interleaved sources are not switches ==')
e = Harness(session_id='s-noid-ab')
e.run(600, 60000, P1, 'rk-A-gaussian-006000', camera='A')
e.run(600, 60000, P1, 'rk-B-gaussian-006000', camera='B')
a2 = e.run(601, 60100, P1, 'rk-A-gaussian-006001', camera='A')
b2 = e.run(601, 60100, P1, 'rk-B-gaussian-006001', camera='B')
e.run(602, 60200, P1, 'rk-A-gaussian-006002', camera='A')
e.run(602, 60200, P1, 'rk-B-gaussian-006002', camera='B')
e.check('alternating A/B does not fake a mode switch', e.worker.stats['mode_switch'] == 0
        and e.worker.stats['session_switch'] == 0)
e.check('each source proves continuity on its own second frame',
        a2['continuity'] == 'proven' and b2['continuity'] == 'proven'
        and a2['person']['tracking']['frames_with_id'] == 2
        and b2['person']['tracking']['frames_with_id'] == 2)
e.check('counters are isolated per full identity',
        len(e.worker.states) == 2 and all(st['reps'] == 0 for st in e.worker.states.values()))
e.run(603, 60300, P1, 'rk-A-gaussian-006003', camera='A', mode='gray')
e.check('a real mode change in one source is still reported', e.worker.stats['mode_switch'] == 1)
e.check('and it does not disturb the other source', e.worker.stats['session_switch'] == 0)

print()
print('VERDICT', 'WORKER_NOID_OK' if not h_global.fails else 'WORKER_NOID_FAILED: ' +
      ','.join(h_global.fails[:3]))
sys.exit(0 if not h_global.fails else 1)