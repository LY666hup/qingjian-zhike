#!/usr/bin/env python3
"""Round 5: person continuity, full identity isolation, anonymous record de-duplication.

Drives the real Worker.process through tests/_worker_harness.py (stub landmarker, marked in the harness).
Expected behaviour under test:

* a re-delivered record is duplicate_frame even when nobody has a person_id, and even when the only record
  identity is input_record_id;
* identical JPEG bytes under a different record identity are NOT a duplicate (a still person keeps counting);
* an anonymous frame between two frames of P1 breaks P1's chain: the next P1 frame is assumed, not proven;
* a duplicate frame does not lengthen frames_with_id, and the chain starts over after it;
* the whole six-field identity isolates state: new boot, new fpga bit, or a different processor each get a fresh
  chain and fresh counters, and re-used sequence/timestamp numbers across those boundaries are not duplicates.

Run from the package root: python tests/test_round5_identity.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _worker_harness import ANON, BOX, P1, Harness        # noqa: E402

OK = 'qjzc.pose.panel.v1'


def counted(panel):
    return panel['counting'] == 'enabled'


# ---------------------------------------------------------------- A. anonymous de-duplication
print('== A. anonymised records are de-duplicated too ==')
h = Harness(session_id='s-r5-dup')
a1 = h.run(10, 10000, ANON, 'rk-A-gaussian-000010')
a2 = h.run(10, 10000, ANON, 'rk-A-gaussian-000010')
h.check('first anonymous record is processed with its 33 points',
        a1['schema'] == OK and a1['pose_present'] and len(a1['pose']['keypoints']) == 33)
h.check('the same anonymous record again is duplicate_frame',
        a2['counter_reason'] == 'duplicate_frame', a2['counter_reason'])
h.check('a duplicate still carries the pose it computed, but no number',
        len(a2['pose']['keypoints']) == 33 and a2['rep_count'] is None and a2['counting'] == 'disabled')
h.check('the duplicate is not counted as a new person or a switch',
        h.worker.stats['duplicate'] == 1 and h.worker.stats['person_switch'] == 0)
h.check('a duplicate record keeps continuity honest (assumed, not proven)', a2['continuity'] == 'assumed')

b1 = h.run(None, None, ANON, 'rk-A-gaussian-000011', time_source=None)
b2 = h.run(None, None, ANON, 'rk-A-gaussian-000011', time_source=None)
h.check('without sequence or timestamp the record id alone still de-duplicates',
        b1['counter_reason'] != 'duplicate_frame' and b2['counter_reason'] == 'duplicate_frame',
        b2['counter_reason'])
h.check('and that duplicate keeps its points', len(b2['pose']['keypoints']) == 33)

c1 = h.run(12, 12000, ANON, 'rk-A-gaussian-000012')
c2 = h.run(13, 13000, ANON, 'rk-A-gaussian-000013')
h.check('identical bytes under a new record identity are NOT a duplicate',
        c1['counter_reason'] != 'duplicate_frame' and c2['counter_reason'] != 'duplicate_frame',
        c2['counter_reason'])
h.check('so a still person still gets points on every new record',
        len(c1['pose']['keypoints']) == 33 and len(c2['pose']['keypoints']) == 33)

# ---------------------------------------------------------------- B. anonymous frame breaks the chain
print('== B. an anonymous frame breaks a proven chain ==')
h = Harness(session_id='s-r5-chain')
p1 = h.run(20, 20000, P1, 'rk-A-gaussian-000020')
p2 = h.run(21, 21000, P1, 'rk-A-gaussian-000021')
h.check('two consecutive id frames prove continuity',
        p1['continuity'] == 'assumed' and p2['continuity'] == 'proven' and counted(p2))
anon = h.run(22, 22000, ANON, 'rk-A-gaussian-000022')
h.check('the anonymous frame is assumed and cannot be attributed to P1',
        anon['continuity'] == 'assumed' and anon['person']['person_id'] is None)
p3 = h.run(23, 23000, P1, 'rk-A-gaussian-000023')
h.check('P1 after an anonymous frame must re-establish, not jump back to proven',
        p3['continuity'] == 'assumed' and p3['person']['tracking']['frames_with_id'] == 1,
        (p3['continuity'], p3['person']['tracking']['frames_with_id']))
h.check('and it cannot count on that frame', not counted(p3) and p3['rep_count'] is None)
p4 = h.run(24, 24000, P1, 'rk-A-gaussian-000024')
h.check('only the next consecutive id frame proves it again',
        p4['continuity'] == 'proven' and p4['person']['tracking']['frames_with_id'] == 2 and counted(p4))

# ---------------------------------------------------------------- C. duplicates do not lengthen the chain
print('== C. a duplicate frame does not lengthen frames_with_id ==')
h = Harness(session_id='s-r5-len')
q1 = h.run(30, 30000, P1, 'rk-A-gaussian-000030')
q2 = h.run(31, 31000, P1, 'rk-A-gaussian-000031')
q3 = h.run(31, 31000, P1, 'rk-A-gaussian-000031')      # re-delivery of the same record
h.check('the chain had two verified frames before the repeat',
        q1['person']['tracking']['frames_with_id'] == 1 and
        q2['person']['tracking']['frames_with_id'] == 2 and q2['continuity'] == 'proven')
h.check('the repeat is duplicate_frame and does not claim a third frame',
        q3['counter_reason'] == 'duplicate_frame' and
        q3['person']['tracking']['frames_with_id'] == 2,
        q3['person']['tracking']['frames_with_id'])
h.check('the repeat breaks the proof (broken continuity)', q3['continuity'] == 'broken')
q4 = h.run(32, 32000, P1, 'rk-A-gaussian-000032')
h.check('the frame after a duplicate starts a fresh chain at one frame',
        q4['continuity'] == 'assumed' and q4['person']['tracking']['frames_with_id'] == 1)
q5 = h.run(33, 33000, P1, 'rk-A-gaussian-000033')
h.check('then proves again on the second frame of the new chain',
        q5['continuity'] == 'proven' and q5['person']['tracking']['frames_with_id'] == 2)

# ---------------------------------------------------------------- D. whole-identity isolation
print('== D. the six identity fields isolate state completely ==')
h = Harness(session_id='s-r5-epoch', boot_id='boot-1')
e1 = h.run(40, 40000, P1, 'rk-A-gaussian-000040')
e2 = h.run(41, 41000, P1, 'rk-A-gaussian-000041')
h.check('boot-1 proves the chain', e2['continuity'] == 'proven' and counted(e2))
e3 = h.run(40, 40000, P1, 'rk-A-gaussian-000040', boot_id='boot-2')
h.check('a new boot re-using the same sequence and timestamp is not a duplicate',
        e3['counter_reason'] != 'duplicate_frame', e3['counter_reason'])
h.check('a new boot starts a fresh chain and is reported once as session_switch',
        e3['continuity'] == 'assumed' and e3['person']['tracking']['frames_with_id'] == 1
        and e3['counter_reset_detail'] == 'session_switch',
        (e3['continuity'], e3['counter_reset_detail']))
h.check('the new boot gets its own counter state', len(h.worker.states) == 2
        and all(st['reps'] == 0 for st in h.worker.states.values()))

bit = Harness(session_id='s-r5-bit', boot_id='boot-1')
f1 = bit.run(50, 50000, P1, 'rk-A-gaussian-000050')
f2 = bit.run(51, 51000, P1, 'rk-A-gaussian-000051')
f3 = bit.run(52, 52000, P1, 'rk-A-gaussian-000052', fpga_bit_sha256='1' * 64)
f4 = bit.run(53, 53000, P1, 'rk-A-gaussian-000053', fpga_bit_sha256='1' * 64)
bit.check('the old bitstream proved its chain', f2['continuity'] == 'proven')
bit.check('a reloaded bitstream starts a fresh chain (identity includes fpga_bit_sha256)',
          f3['continuity'] == 'assumed' and f3['person']['tracking']['frames_with_id'] == 1,
          (f3['continuity'], f3['person']['tracking']['frames_with_id']))
bit.check('the new bitstream proves its own chain on its second frame',
          f4['continuity'] == 'proven' and f4['person']['tracking']['frames_with_id'] == 2)
bit.check('no fake mode switch is reported for a bitstream boundary', bit.worker.stats['mode_switch'] == 0)

proc = Harness(session_id='s-r5-proc', processor='fpga')
g1 = proc.run(60, 60000, P1, 'rk-A-gaussian-000060')
g2 = proc.run(61, 61000, P1, 'rk-A-gaussian-000061')
g3 = proc.run(60, 60000, P1, 'rk-A-gaussian-000060', processor='simulation')
proc.check('a simulation replay of the same frames does not inherit the FPGA chain',
           g2['continuity'] == 'proven' and g3['continuity'] == 'assumed'
           and g3['person']['tracking']['frames_with_id'] == 1, g3['continuity'])
proc.check('and the replay is a different scope, not a duplicate record',
           g3['counter_reason'] != 'duplicate_frame')
proc.check('the two processors keep separate counters', len(proc.worker.states) == 2)

# ---------------------------------------------------------------- E. interleaving still safe
print('== E. per-source interleaving is still not a switch ==')
h = Harness(session_id='s-r5-ab')
h.run(70, 70000, P1, 'rk-A-gaussian-000070', camera='A')
h.run(70, 70000, P1, 'rk-B-gaussian-000070', camera='B')
a2 = h.run(71, 71000, P1, 'rk-A-gaussian-000071', camera='A')
b2 = h.run(71, 71000, P1, 'rk-B-gaussian-000071', camera='B')
h.run(72, 72000, P1, 'rk-A-gaussian-000072', camera='A')
h.run(72, 72000, P1, 'rk-B-gaussian-000072', camera='B')
h.check('alternating A/B still reports no mode or session switch',
        h.worker.stats['mode_switch'] == 0 and h.worker.stats['session_switch'] == 0)
h.check('both cameras still prove their own chains',
        a2['continuity'] == 'proven' and b2['continuity'] == 'proven')
h.check('counters stay per (session, boot, camera, mode, bit, processor, person)',
        len(h.worker.states) == 2)
h.run(73, 73000, P1, 'rk-A-gaussian-000073', camera='A', mode='gray')
h.check('a real mode change on one camera is still reported exactly once',
        h.worker.stats['mode_switch'] == 1)
h.check('and it does not disturb the other camera', h.worker.stats['session_switch'] == 0)

print()
print('VERDICT', 'IDENTITY_CONTINUITY_OK' if not h.fails else
      'IDENTITY_CONTINUITY_FAILED: ' + ','.join(h.fails[:4]))
sys.exit(0 if not h.fails else 1)