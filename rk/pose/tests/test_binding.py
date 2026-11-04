#!/usr/bin/env python3
"""Binding tests: a job may only be analysed against the image and YOLO record it actually names.

No mediapipe needed.  Run from the package root: python tests/test_binding.py
"""
from __future__ import annotations

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pose import binding as G   # noqa: E402

fails = []


def check(label, cond, detail=''):
    print('  %-64s %s%s' % (label, 'PASS' if cond else 'FAIL', ('  ' + str(detail)[:60]) if detail else ''))
    if not cond:
        fails.append(label)


def need_refusal(label, job, size, needle=''):
    try:
        G.bind(job, size)
        check(label, False, 'accepted, should have been refused')
    except G.BindingError as e:
        check(label, needle in str(e), str(e)[:60])


FULL = os.path.join(ROOT, 'samples', 'frame_0001_seq_000012.jpg')
SMALL = os.path.join(ROOT, 'samples', 'frame_640x360.jpg')
FULL_SHA = G.sha256_file(FULL)
SMALL_SHA = G.sha256_file(SMALL)
FULL_SIZE = (960, 540)
SMALL_SIZE = (640, 360)

IDENT = {'session_id': '1df3f2e6e59d4f0097e93ae13dae7d79',
         'boot_id': '35095b5a-c6e6-42c5-9dc3-0874124d3bef',
         'camera_id': 'A', 'processing_mode': 'gaussian',
         'fpga_bit_sha256': '0' * 64, 'processor': 'fpga'}


def job_for(mode='gaussian', camera='A', sha=FULL_SHA, image=FULL, box=(237.75, 0.0, 720.75, 540.0),
            record='rk-A-gaussian-000123', group=None, det_sha=None, det_record=None):
    return {
        'image': image,
        'jpeg_sha256': sha,
        'input_record_id': record,
        'identity': dict(IDENT, processing_mode=mode, camera_id=camera),
        'preview_sequence': 123,
        'source_frame_id': None,
        'timestamp_ms': 1762000000000,
        'time_source': 'board_monotonic',
        'capture_group_id': group,
        'detections': {
            'input_record_id': det_record or record,
            'jpeg_sha256': det_sha or sha,
            'camera_id': camera,
            'processing_mode': mode,
            'coordinate_space': 'preview_pixels',
            'items': [{'person_id': 'P1', 'xyxy': list(box), 'score': 0.95}],
        },
    }


print('== A. the well-formed job is accepted ==')
b = G.bind(job_for(), FULL_SIZE)
check('identity copied as the six fields', tuple(b['identity']) == G.IDENTITY_KEYS)
check('hash is recomputed from the file', b['jpeg_sha256'] == FULL_SHA)
check('record id carried through', b['input_record_id'] == 'rk-A-gaussian-000123')
check('source_frame_id stays null', b['source_frame_id'] is None)
check('capture_group_id stays null when absent', b['capture_group_id'] is None)
check('one detection, upstream id kept', len(b['detections']) == 1 and b['detections'][0]['person_id'] == 'P1')

print('== B. hash binding ==')
need_refusal('declared jpeg_sha256 is not the file hash',
             job_for(sha='a' * 64), FULL_SIZE, 'jpeg_sha256 mismatch')
need_refusal('malformed declared hash', job_for(sha='nothex'), FULL_SIZE, '64-hex')
j = job_for()
j['detections']['jpeg_sha256'] = 'b' * 64
need_refusal('boxes from a different image (A-original boxes on A-gaussian image)',
             j, FULL_SIZE, 'does not match the image hash')
j = job_for()
j['detections']['input_record_id'] = 'rk-A-original-000123'
need_refusal('boxes from a different input record', j, FULL_SIZE, 'does not belong to this job')
j = job_for()
j['detections']['processing_mode'] = 'original'
need_refusal('boxes captured in another processing mode', j, FULL_SIZE, 'a box from one mode')
j = job_for()
j['detections']['camera_id'] = 'B'
need_refusal('boxes from another camera', j, FULL_SIZE, 'camera_id')
j = job_for()
j['detections']['coordinate_space'] = 'normalized'
need_refusal('boxes in a coordinate space we do not accept', j, FULL_SIZE, 'coordinate_space')

print('== C. the box must fit the real image ==')
need_refusal('960-sized box on a 640x360 image, no rescaling',
             job_for(sha=SMALL_SHA, image=SMALL), SMALL_SIZE, 'outside the actual 640x360 image')
ok = G.bind(job_for(sha=SMALL_SHA, image=SMALL, box=(158.5, 0.0, 480.5, 360.0),
                    record='rk-A-gaussian-000200'), SMALL_SIZE)
check('640x360 box accepted, size read from the file', ok['image_size'] == [640, 360])
need_refusal('zero-area box', job_for(box=(100.0, 100.0, 100.0, 200.0)), FULL_SIZE, 'xyxy')
need_refusal('negative coordinate', job_for(box=(-1.0, 0.0, 100.0, 100.0)), FULL_SIZE, 'outside')

print('== D. identity ==')
for field in G.IDENTITY_KEYS:
    j = job_for()
    del j['identity'][field]
    need_refusal('identity without %s' % field, j, FULL_SIZE, field)
need_refusal('camera front instead of A/B', job_for(camera='front'), FULL_SIZE, 'camera_id must be A or B')
j = job_for()
j['identity']['processor'] = 'gpu'
need_refusal('unknown source processor', j, FULL_SIZE, 'processor must be one of')
j = job_for()
j['identity']['processing_mode'] = 'blur'
need_refusal('unknown processing mode', j, FULL_SIZE, 'processing_mode must be one of')
j = job_for()
j['identity']['fpga_bit_sha256'] = 'xyz'
need_refusal('bitstream hash is not 64 hex', j, FULL_SIZE, 'fpga_bit_sha256')

print('== E. record identity and capture group ==')
j = job_for()
j['input_record_id'] = 'bad id with spaces'
need_refusal('input_record_id with spaces', j, FULL_SIZE, 'input_record_id')
j = job_for(group='grp-1')
j['detections']['capture_group_id'] = None
b = G.bind(j, FULL_SIZE)
check('a real capture_group_id is carried through', b['capture_group_id'] == 'grp-1')
j = job_for()
j['detections']['capture_group_id'] = 'grp-1'
need_refusal('detections claim a group the job does not have', j, FULL_SIZE, 'capture_group_id')
need_refusal('capture_group_id never invented',
             job_for(), FULL_SIZE, '') if False else None
check('no capture_group_id is invented', G.bind(job_for(), FULL_SIZE)['capture_group_id'] is None)

print('== F. time and identity for counting ==')
j = job_for()
j['time_source'] = None
check('a null time_source is allowed through binding (counting will pause)',
      G.bind(j, FULL_SIZE)['time_source'] is None)
j = job_for()
j['timestamp_ms'] = None
need_refusal('time_source without a timestamp', j, FULL_SIZE, 'time_source is set but there is no timestamp')
j = job_for()
j['timestamp_ms'] = 1.5
need_refusal('fractional timestamp', j, FULL_SIZE, 'timestamp_ms must be an int')

print('== G. the detections record must be a record ==')
j = job_for()
j['detections'] = [{'person_id': 'P1', 'xyxy': [1, 2, 3, 4]}]
need_refusal('a bare list of boxes has no provenance', j, FULL_SIZE, 'must be the YOLO record object')
j = job_for()
j['detections'] = 'samples/detections.json'
need_refusal('a path is not a bound record', j, FULL_SIZE, 'must be the YOLO record object')
j = job_for()
del j['jpeg_sha256']
need_refusal('the job does not declare a hash at all', j, FULL_SIZE, 'jpeg_sha256')
j = job_for()
j['detections']['items'] = [{'person_id': 'P1', 'xyxy': [10, 10, 100, 100], 'score': 0.9},
                            {'person_id': 'P1', 'xyxy': [20, 20, 200, 200], 'score': 0.8}]
need_refusal('two boxes with the same upstream person id', j, FULL_SIZE, 'duplicated')
j = job_for()
j['detections']['items'] = [{'person_id': None, 'xyxy': [10, 10, 100, 100], 'score': 0.9},
                            {'person_id': None, 'xyxy': [200, 20, 400, 300], 'score': 0.8}]
b = G.bind(j, FULL_SIZE)
check('two anonymous boxes are legal and stay anonymous',
      [d['person_id'] for d in b['detections']] == [None, None])
check('anonymous detections are not marked as having an upstream id',
      [d['upstream_id'] for d in b['detections']] == [False, False])
j = job_for()
j['detections']['items'] = [{'person_id': 7, 'xyxy': [10, 10, 100, 100]}]
need_refusal('a numeric person id is not an id', j, FULL_SIZE, 'person_id')

print('== H. four modes are four identities, not one ==')
seen = {}
for mode in G.MODES:
    j = job_for(mode=mode, record='rk-A-%s-000123' % mode)
    b = G.bind(j, FULL_SIZE)
    seen[mode] = (b['identity']['processing_mode'], b['input_record_id'], b['jpeg_sha256'][:8])
check('four distinct mode identities bind independently', len(seen) == 4 and
      len({v[1] for v in seen.values()}) == 4)
check('same image hash for all four (the file really is shared here)', len({v[2] for v in seen.values()}) == 1)

print('== I. the geometry a job carries must match the image it names ==')
j = job_for()
j['detections']['geometry'] = {'source_space': 'mosaic_pixels', 'mosaic_size': [1920, 1080],
                               'roi': [0, 0, 1920, 1080], 'scale': [0.5, 0.5], 'target_size': [960, 540]}
check('a correct mosaic mapping binds', G.bind(j, FULL_SIZE)['geometry']['target_size'] == [960, 540])
j = job_for()
j['detections']['geometry'] = {'source_space': 'mosaic_pixels', 'mosaic_size': [1920, 1080],
                               'roi': [0, 0, 1920, 1080], 'scale': [0.5, 0.5], 'target_size': [640, 360]}
need_refusal('a geometry that targets another image', j, FULL_SIZE, 'is not the actual image')
j = job_for()
j['detections']['geometry'] = {'source_space': 'mosaic_pixels', 'mosaic_size': [1920, 1080],
                               'roi': [0, 0, 1920, 1080], 'scale': [0.3, 0.5], 'target_size': [960, 540]}
need_refusal('a geometry whose scale does not reach the image', j, FULL_SIZE, 'does not map the roi')
j = job_for()
j['detections']['geometry'] = {'source_space': 'camera', 'mosaic_size': [1920, 1080],
                               'roi': [0, 0, 1920, 1080], 'scale': [0.5, 0.5], 'target_size': [960, 540]}
need_refusal('a geometry from a space nobody knows', j, FULL_SIZE, 'source_space')
j = job_for()
j['detections'].pop('geometry', None)
j['detections']['coordinate_space'] = 'mosaic_pixels'
need_refusal('mosaic boxes with no geometry to explain them', j, FULL_SIZE, 'coordinate_space')

print()
print('VERDICT', 'BINDING_OK' if not fails else 'BINDING_FAILED: ' + ','.join(fails[:3]))
sys.exit(0 if not fails else 1)