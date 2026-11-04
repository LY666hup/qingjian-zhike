"""Bind one job to the JPEG it names and to the YOLO record that produced its boxes.

The failure this exists to prevent: the FPGA branch produces several images from the same source frame
(original / gray / gaussian / histogram).  If a gaussian image is analysed with the original image's detection
boxes, every number downstream is wrong while every field still looks plausible.  So the binding is checked,
not assumed:

  * the actual JPEG is hashed here and compared with the declared `jpeg_sha256`;
  * the detections record must carry the same `input_record_id`, the same `jpeg_sha256`, the same `camera_id`
    and the same `processing_mode`;
  * boxes must be pixel coordinates inside the decoded image - no rescaling is attempted, because a box from a
    different mode/size would silently land on the wrong body part;
  * `capture_group_id` is only ever copied from the input, never synthesised;
  * `source_frame_id` stays null unless the caller really has one.

Everything raises `BindingError`, and every rule has a test.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re

IDENTITY_KEYS = ('session_id', 'boot_id', 'camera_id', 'processing_mode', 'fpga_bit_sha256', 'processor')
CAMERAS = ('A', 'B')
MODES = ('original', 'gray', 'gaussian', 'histogram')
PROCESSORS = ('fpga', 'rk', 'simulation')
COORDINATE_SPACES = ('preview_pixels',)
GEOMETRY_SPACES = ('mosaic_pixels',)
HEX64 = re.compile(r'^[0-9a-f]{64}$')
ID_RE = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')


class BindingError(ValueError):
    pass


def _need(cond, msg):
    if not cond:
        raise BindingError(msg)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def check_identity(identity, where='identity'):
    _need(isinstance(identity, dict), '%s must be an object' % where)
    for key in IDENTITY_KEYS:
        v = identity.get(key)
        _need(isinstance(v, str) and 0 < len(v) <= 128, '%s.%s must be a string 1..128 chars' % (where, key))
    _need(identity['camera_id'] in CAMERAS, '%s.camera_id must be A or B' % where)
    _need(identity['processing_mode'] in MODES,
          '%s.processing_mode must be one of %s' % (where, ', '.join(MODES)))
    _need(identity['processor'] in PROCESSORS,
          '%s.processor must be one of %s' % (where, ', '.join(PROCESSORS)))
    _need(bool(HEX64.match(identity['fpga_bit_sha256'].lower())),
          '%s.fpga_bit_sha256 must be 64 hex chars' % where)
    return {k: identity[k] for k in IDENTITY_KEYS}


def _check_id(value, field):
    _need(isinstance(value, str) and bool(ID_RE.match(value)),
          '%s must be 1..128 chars of [A-Za-z0-9._:-], got %r' % (field, value))
    return value


def bind(job, image_size):
    """Validate one job against the file on disk and against its detection record.

    image_size is (width, height) decoded from the actual file - the caller has to decode first, because the
    point of the check is that the declared and real sizes agree.
    """
    _need(isinstance(job, dict), 'job must be an object')

    image = job.get('image')
    _need(isinstance(image, str) and image.strip(), 'job.image is required')
    _need(os.path.isfile(image), 'job.image does not exist: %s' % image)
    actual = sha256_file(image)
    declared = job.get('jpeg_sha256')
    _need(isinstance(declared, str) and bool(HEX64.match(declared)),
          'job.jpeg_sha256 must be the 64-hex hash of the image (declared %r)' % (declared,))
    _need(actual == declared,
          'jpeg_sha256 mismatch: the file hashes to %s, the job declares %s' % (actual, declared))

    identity = check_identity(job.get('identity'), 'identity')
    _need(identity['session_id'] == identity['session_id'].strip(), 'identity.session_id has stray whitespace')

    input_record_id = _check_id(job.get('input_record_id'), 'input_record_id')

    seq = job.get('preview_sequence')
    _need(seq is None or (type(seq) is int and seq >= 0), 'preview_sequence must be a non-negative int or null')

    ts = job.get('timestamp_ms')
    _need(ts is None or type(ts) is int, 'timestamp_ms must be an int or null')
    ts_src = job.get('time_source')
    _need(ts_src is None or (isinstance(ts_src, str) and 0 < len(ts_src) <= 64),
          'time_source must be a short string or null')
    _need(ts is not None or ts_src is None, 'time_source is set but there is no timestamp')

    group = job.get('capture_group_id')
    _need(group is None or (isinstance(group, str) and 0 < len(group) <= 128),
          'capture_group_id must be a string or null, and is never invented here')

    src_frame = job.get('source_frame_id')
    _need(src_frame is None or (isinstance(src_frame, str) and 0 < len(src_frame) <= 128),
          'source_frame_id must be a string or null')

    det = job.get('detections')
    _need(isinstance(det, dict), 'job.detections must be the YOLO record object, not a path or a bare list')
    _need(det.get('input_record_id') == input_record_id,
          'detections.input_record_id %r does not belong to this job (%r)'
          % (det.get('input_record_id'), input_record_id))
    _need(det.get('jpeg_sha256') == declared,
          'detections.jpeg_sha256 %r does not match the image hash %r - boxes from another image'
          % (det.get('jpeg_sha256'), declared))
    for key in ('camera_id', 'processing_mode'):
        _need(det.get(key) == identity[key],
              'detections.%s %r differs from the job identity %r (a box from one mode must not be applied to '
              'another)' % (key, det.get(key), identity[key]))
    if det.get('session_id') is not None:
        _need(det['session_id'] == identity['session_id'],
              'detections.session_id %r differs from the job session %r'
              % (det['session_id'], identity['session_id']))
    _need(det.get('coordinate_space') in COORDINATE_SPACES,
          'detections.coordinate_space must be %r, got %r' % (COORDINATE_SPACES[0], det.get('coordinate_space')))
    if det.get('capture_group_id') is not None:
        _need(group is not None and det['capture_group_id'] == group,
              'detections.capture_group_id %r does not match the job (%r)'
              % (det['capture_group_id'], group))

    geometry = det.get('geometry')
    if geometry is not None:
        _need(isinstance(geometry, dict), 'detections.geometry must be an object or null')
        for key in ('source_space', 'mosaic_size', 'roi', 'scale', 'target_size'):
            _need(key in geometry, 'detections.geometry.%s is required' % key)
        _need(geometry['source_space'] in GEOMETRY_SPACES,
              'detections.geometry.source_space must be one of %s' % ', '.join(GEOMETRY_SPACES))
        ms, roi, sc, ts_ = geometry['mosaic_size'], geometry['roi'], geometry['scale'], geometry['target_size']
        _need(isinstance(ms, list) and len(ms) == 2 and all(type(v) is int and v > 0 for v in ms),
              'detections.geometry.mosaic_size must be [width, height]')
        _need(isinstance(roi, list) and len(roi) == 4 and all(type(v) in (int, float) for v in roi),
              'detections.geometry.roi must be four numbers')
        _need(isinstance(sc, list) and len(sc) == 2 and all(type(v) in (int, float) and v > 0 for v in sc),
              'detections.geometry.scale must be two positive numbers')
        _need(isinstance(ts_, list) and len(ts_) == 2 and list(ts_) == [image_size[0], image_size[1]],
              'detections.geometry.target_size %r is not the actual image %r' % (ts_, list(image_size)))
        _need(abs(roi[2] * sc[0] - image_size[0]) <= 1 and abs(roi[3] * sc[1] - image_size[1]) <= 1,
              'detections.geometry does not map the roi onto the actual image (roi %r x scale %r != %r)'
              % (roi, sc, list(image_size)))
    else:
        _need(det.get('coordinate_space') == 'preview_pixels',
              'boxes claim a coordinate space but carry no geometry explaining it')

    items = det.get('items')
    _need(isinstance(items, list), 'detections.items must be a list (it may be empty)')
    _need(len(items) <= 32, 'detections.items has %d entries, the cap is 32' % len(items))
    w, h = image_size
    _need(type(w) is int and type(h) is int and w > 0 and h > 0, 'image_size must be positive ints')
    boxes = []
    seen = set()
    for i, item in enumerate(items):
        _need(isinstance(item, dict), 'detections.items[%d] must be an object' % i)
        pid = item.get('person_id')
        _need(pid is None or (isinstance(pid, str) and 0 < len(pid) <= 128),
              'items[%d].person_id must be null or 1..128 chars (a stable id from the tracker), got %r'
              % (i, pid))
        if pid is not None:
            _need(pid not in seen, 'items[%d].person_id %r is duplicated' % (i, pid))
            seen.add(pid)
        box = item.get('xyxy')
        _need(isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) for v in box),
              'items[%d].xyxy must be four numbers' % i)
        x0, y0, x1, y1 = [float(v) for v in box]
        _need(0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h,
              'items[%d].xyxy %r is outside the actual %dx%d image - a box in another coordinate space'
              % (i, box, w, h))
        score = item.get('score')
        _need(score is None or (type(score) in (int, float) and 0 <= float(score) <= 1),
              'items[%d].score must be 0..1 or null' % i)
        boxes.append({'person_id': pid, 'box': [x0, y0, x1, y1],
                      'score': None if score is None else float(score),
                      'upstream_id': pid is not None})

    return {
        'image': os.path.abspath(image),
        'jpeg_sha256': actual,
        'input_record_id': input_record_id,
        'identity': identity,
        'preview_sequence': seq,
        'timestamp_ms': ts,
        'time_source': ts_src,
        'capture_group_id': group,
        'source_frame_id': src_frame,
        'image_size': [w, h],
        'detections': boxes,
        'geometry': geometry,
        'raw_detections': det,
    }


def load(path):
    with io.open(path, encoding='utf-8') as f:
        job = json.load(f)
    _need(isinstance(job, dict), 'job file must contain a JSON object')
    return job