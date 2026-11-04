#!/usr/bin/env python3
"""Tests for bin/06_prepare_pose_job.py - the YOLO -> v3 job entry.

No mediapipe: this entry only reads a JPEG, hashes it, maps boxes and refuses what cannot be proven.
Run from the package root: python tests/test_prepare_job.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'bin'))

import importlib.util
spec = importlib.util.spec_from_file_location('prepare', os.path.join(ROOT, 'bin', '06_prepare_pose_job.py'))
PREP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PREP)

from pose import binding as G   # noqa: E402

fails = []
SAMPLES = os.path.join(ROOT, 'samples')
FULL = os.path.join(SAMPLES, 'frame_0001_seq_000012.jpg')
SMALL = os.path.join(SAMPLES, 'frame_640x360.jpg')
DETS = os.path.join(SAMPLES, 'detections.json')
IDENT = {'session_id': '1df3f2e6e59d4f0097e93ae13dae7d79',
         'boot_id': '35095b5a-c6e6-42c5-9dc3-0874124d3bef',
         'camera_id': 'A', 'processing_mode': 'gaussian',
         'fpga_bit_sha256': '0' * 64, 'processor': 'fpga'}
IDENT_GRAY = dict(IDENT, processing_mode='gray')


def check(label, cond, detail=''):
    print('  %-64s %s%s' % (label, 'PASS' if cond else 'FAIL', ('  ' + str(detail)[:56]) if detail else ''))
    if not cond:
        fails.append(label)


def refuse(label, fn, needle=''):
    try:
        fn()
        check(label, False, 'accepted, should have been refused')
    except (PREP.JobError, G.BindingError) as e:
        check(label, needle in str(e), str(e)[:56])


def det_list():
    return json.load(io.open(DETS, encoding='utf-8'))['detections']


print('== A. the normal path: FPGA JPEG + YOLO record -> a job the worker accepts ==')
job, notes = PREP.build_job(FULL, det_list(), IDENT, input_record_id='rk-A-gaussian-000123',
                            preview_sequence=123, timestamp_ms=1762000000123, time_source='board_monotonic')
payload = open(FULL, 'rb').read()
check('hash comes from the bytes on disk', job['jpeg_sha256'] == hashlib.sha256(payload).hexdigest())
check('the JPEG is not re-encoded (bytes identical)', open(FULL, 'rb').read() == payload)
check('size comes from the SOF header', PREP.jpeg_size(FULL) == (960, 540))
check('identity is carried unchanged', job['identity'] == IDENT)
check('the detection record repeats its own provenance',
      job['detections']['input_record_id'] == 'rk-A-gaussian-000123'
      and job['detections']['jpeg_sha256'] == job['jpeg_sha256']
      and job['detections']['camera_id'] == 'A'
      and job['detections']['coordinate_space'] == 'preview_pixels'
      and 'geometry' not in job['detections'])
check('no frame id or group is invented', job['source_frame_id'] is None and job['capture_group_id'] is None
      and 'frame_id' not in job)
bound = G.bind(job, PREP.jpeg_size(FULL))
check('pose/binding.py accepts the job', bound['image_size'] == [960, 540] and len(bound['detections']) == 1)
check('the box is still in preview pixels', bound['detections'][0]['box'] == [237.75, 0.0, 720.75, 540.0])
check('a job without person_id leaves the selector open', 'person_id' not in job)
check('notes say what happened', any('boxes 1' in n for n in notes), notes)

print('== B. refusal: the image is not what the caller claims ==')
refuse('declared hash that is not the file', lambda: PREP.build_job(
    FULL, det_list(), IDENT, input_record_id='r1', declared_sha256='a' * 64), 'refusing to relabel')
refuse('declared preview size that is not the file', lambda: PREP.build_job(
    FULL, det_list(), IDENT, input_record_id='r1', declared_preview_size=[640, 360]), 'disagrees with the decoded')
refuse('a file that is not a JPEG', lambda: PREP.build_job(
    os.path.join(ROOT, 'README.md'), det_list(), IDENT, input_record_id='r1'), 'not a JPEG')
refuse('an image that does not exist', lambda: PREP.build_job(
    os.path.join(SAMPLES, 'nope.jpg'), det_list(), IDENT, input_record_id='r1'), 'does not exist')

print('== C. refusal: identity ==')
for key in G.IDENTITY_KEYS:
    short = {k: v for k, v in IDENT.items() if k != key}
    refuse('missing %s' % key, lambda s=short: PREP.build_job(
        FULL, det_list(), s, input_record_id='r1'), key)
refuse('camera that is not A/B', lambda: PREP.build_job(
    FULL, det_list(), dict(IDENT, camera_id='front'), input_record_id='r1'), 'camera_id')
refuse('processing mode outside the four', lambda: PREP.build_job(
    FULL, det_list(), dict(IDENT, processing_mode='sepia'), input_record_id='r1'), 'processing_mode')
refuse('processor outside the three', lambda: PREP.build_job(
    FULL, det_list(), dict(IDENT, processor='gpu'), input_record_id='r1'), 'processor')
refuse('bitstream hash that is not 64 hex', lambda: PREP.build_job(
    FULL, det_list(), dict(IDENT, fpga_bit_sha256='zz'), input_record_id='r1'), 'fpga_bit_sha256')
refuse('no input_record_id', lambda: PREP.build_job(FULL, det_list(), IDENT), 'input_record_id')
refuse('record id with a space', lambda: PREP.build_job(
    FULL, det_list(), IDENT, input_record_id='not a record'), 'input_record_id')

print('== D. refusal: boxes that cannot be trusted ==')
refuse('a box outside the 960x540 image', lambda: PREP.build_job(
    FULL, [{'person_id': 'P1', 'xyxy': [0, 0, 1000, 540]}], IDENT, input_record_id='r1'), 'outside the actual')
r = det_list(); r[0] = dict(r[0], xyxy=[0, 0, 100])
refuse('a box that is not four numbers', lambda: PREP.build_job(
    FULL, r, IDENT, input_record_id='r1'), 'four numbers')
refuse('a negative box', lambda: PREP.build_job(
    FULL, [{'person_id': 'P1', 'xyxy': [-5, 0, 100, 100]}], IDENT, input_record_id='r1'), 'outside the actual')
refuse('duplicate person ids', lambda: PREP.build_job(
    FULL, [det_list()[0], det_list()[0]], IDENT, input_record_id='r1'), 'duplicated')
refuse('a non-string person id', lambda: PREP.build_job(
    FULL, [{'person_id': 7, 'xyxy': [10, 10, 100, 100]}], IDENT, input_record_id='r1'), 'person_id')
refuse('an empty person id', lambda: PREP.build_job(
    FULL, [{'person_id': '', 'xyxy': [10, 10, 100, 100]}], IDENT, input_record_id='r1'), 'person_id')
anon, anotes = PREP.build_job(FULL, [{'xyxy': [10, 10, 100, 100]}], IDENT, input_record_id='r1')
check('a detection with no person id is accepted as anonymous',
      anon['detections']['items'] == [{'person_id': None, 'xyxy': [10.0, 10.0, 100.0, 100.0], 'score': None}])
check('and it binds without inventing an id',
      G.bind(anon, PREP.jpeg_size(FULL))['detections'][0]['person_id'] is None)
anon2, _ = PREP.build_job(FULL, [{'person_id': None, 'xyxy': [10, 10, 100, 100]},
                                {'person_id': None, 'xyxy': [200, 10, 400, 300]}],
                          IDENT, input_record_id='r1')
check('two anonymous boxes are allowed; null ids are not duplicates',
      [i['person_id'] for i in anon2['detections']['items']] == [None, None])
check('anonymous detections are reported, not hidden',
      any('no upstream person id' in n for n in anotes))
refuse('score above one', lambda: PREP.build_job(
    FULL, [{'person_id': 'P1', 'xyxy': [10, 10, 100, 100], 'score': 1.4}], IDENT, input_record_id='r1'), 'score')
refuse('more than 32 detections', lambda: PREP.build_job(
    FULL, [{'person_id': 'P%d' % i, 'xyxy': [10, 10, 20 + i, 100]} for i in range(33)], IDENT,
    input_record_id='r1'), 'cap is 32')
refuse('a record whose shape is unknown', lambda: PREP.build_job(
    FULL, {'people': []}, IDENT, input_record_id='r1'), 'items/detections')

print('== E. mosaic boxes: transformed once, with the geometry recorded ==')
# the whole 1920x1080 mosaic scaled down to the 960x540 preview
mosaic_dets = [{'person_id': 'P1', 'score': 0.8, 'xyxy': [475.5, 0.0, 1441.5, 1080.0]}]
mj, mnotes = PREP.build_job(FULL, mosaic_dets, IDENT, input_record_id='r1', space='mosaic_pixels',
                            mosaic_size=[1920, 1080], roi=[0, 0, 1920, 1080])
mg = mj['detections']['geometry']
check('boxes are halved into the preview', mj['detections']['items'][0]['xyxy'] == [237.75, 0.0, 720.75, 540.0])
check('the geometry record says what was done',
      mg['source_space'] == 'mosaic_pixels' and mg['mosaic_size'] == [1920, 1080]
      and mg['roi'] == [0.0, 0.0, 1920.0, 1080.0] and mg['target_size'] == [960, 540]
      and mg['scale'] == [0.5, 0.5])
check('the transformed job still binds', G.bind(mj, PREP.jpeg_size(FULL))['detections'][0]['box']
      == [237.75, 0.0, 720.75, 540.0])
# one 960x540 tile of the mosaic shown at 640x360
m2, _ = PREP.build_job(SMALL, [{'person_id': 'P1', 'xyxy': [1200.0, 540.0, 1680.0, 1080.0]}], IDENT_GRAY,
                       input_record_id='r2', space='mosaic_pixels', mosaic_size=[1920, 1080],
                       roi=[960, 540, 960, 540])
check('the bottom-right tile maps onto 640x360 too',
      m2['detections']['items'][0]['xyxy'] == [160.0, 0.0, 480.0, 360.0]
      and m2['detections']['geometry']['target_size'] == [640, 360])
refuse('mosaic boxes without the mosaic size and roi', lambda: PREP.build_job(
    FULL, mosaic_dets, IDENT, input_record_id='r1', space='mosaic_pixels'), 'needs --mosaic-size and --roi')
refuse('a mosaic box from the other camera tile', lambda: PREP.build_job(
    FULL, [{'person_id': 'P1', 'xyxy': [1500.0, 0.0, 1800.0, 500.0]}], IDENT, input_record_id='r1',
    space='mosaic_pixels', mosaic_size=[1920, 1080], roi=[0, 0, 960, 540]), 'another camera region')
refuse('a roi that leaves the mosaic', lambda: PREP.build_job(
    FULL, mosaic_dets, IDENT, input_record_id='r1', space='mosaic_pixels',
    mosaic_size=[1920, 1080], roi=[1400, 0, 960, 540]), 'not inside the mosaic')
refuse('an unknown coordinate space', lambda: PREP.build_job(
    FULL, mosaic_dets, IDENT, input_record_id='r1', space='normalized'), 'coordinate space')

print('== F. time: allowed to be absent, never invented ==')
jt, notes_t = PREP.build_job(FULL, det_list(), IDENT, input_record_id='r1')
check('no timestamp is legal and says so', jt['timestamp_ms'] is None and jt['time_source'] is None
      and any('paused' in n for n in notes_t))
refuse('--strict-time refuses the same job', lambda: PREP.build_job(
    FULL, det_list(), IDENT, input_record_id='r1', strict_time=True), 'strict-time')
refuse('a time_source without a timestamp is not usable', lambda: PREP.build_job(
    FULL, det_list(), IDENT, input_record_id='r1', time_source='board_monotonic'), 'without timestamp_ms')
refuse('a negative timestamp', lambda: PREP.build_job(
    FULL, det_list(), IDENT, input_record_id='r1', timestamp_ms=-1), 'timestamp_ms')
refuse('source_frame_id that the caller did not really have', lambda: PREP.build_job(
    FULL, det_list(), IDENT, input_record_id='r1', source_frame_id=''), 'source_frame_id')

print('== G. the CLI produces the same job, and refuses with a readable line ==')
tmp = tempfile.mkdtemp(prefix='prepare_job_')
try:
    out = os.path.join(tmp, 'job.json')
    cmd = [sys.executable, os.path.join(ROOT, 'bin', '06_prepare_pose_job.py'),
           '--image', FULL, '--detections', DETS, '--out', out,
           '--input-record-id', 'rk-A-gaussian-000123', '--session-id', IDENT['session_id'],
           '--boot-id', IDENT['boot_id'], '--camera-id', 'A', '--processing-mode', 'gaussian',
           '--fpga-bit-sha256', IDENT['fpga_bit_sha256'], '--processor', 'fpga',
           '--preview-sequence', '123', '--timestamp-ms', '1762000000123',
           '--time-source', 'board_monotonic']
    p = subprocess.run(cmd, capture_output=True, text=True)
    check('exit 0 and a job on disk', p.returncode == 0 and os.path.isfile(out), p.stdout.strip()[:56])
    check('JOB OK names image, hash, mode and record', 'JOB OK:' in p.stdout
          and 'sha256=' in p.stdout and 'mode=gaussian' in p.stdout and 'rk-A-gaussian-000123' in p.stdout)
    written = json.load(io.open(out, encoding='utf-8'))
    check('the written job binds', G.bind(written, PREP.jpeg_size(FULL))['detections'][0]['person_id'] == 'P1')
    bad = os.path.join(tmp, 'bad.json')
    i = cmd.index('--boot-id')
    broken = cmd[:i] + cmd[i + 2:] + ['--out', bad]
    p2 = subprocess.run(broken, capture_output=True, text=True)
    check('a truncated identity fails loudly with rc=2',
          p2.returncode == 2 and p2.stdout.startswith('JOB FAIL:') and not os.path.exists(bad),
          p2.stdout.strip()[:56])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print('VERDICT', 'PREPARE_JOB_OK' if not fails else 'PREPARE_JOB_FAILED: ' + ','.join(fails[:3]))
sys.exit(0 if not fails else 1)