#!/usr/bin/env python3
"""Step 6 - turn upstream FPGA JPEG + YOLO detections into one v3 pose job.

This is the only integration point with Ly's pipeline.  It reads, it never captures:

  * a JPEG that the FPGA branch already produced (bytes are hashed, never re-encoded);
  * the YOLO detection record for that same image (boxes, optional person ids, optional score);
  * the six identity fields of the capture branch, the camera, the processing mode and the input record id;
  * a timestamp and its source when the upstream really has them.

and writes a job that `bin/05_analyze_stream.py` (or `bin/04_analyze_image.py`) consumes unchanged.  It is
deliberately strict, because a wrong box is worse than no box:

  * the hash is computed from the file; a declared hash that differs is refused, not overwritten;
  * a declared preview size that differs from the decoded JPEG is refused;
  * boxes are accepted in the JPEG's own pixel space (`preview_pixels`) or in the 1920x1080 mosaic
    (`mosaic_pixels` only together with `--mosaic-size` and `--roi`), and are transformed once, here, with the
    geometry recorded in the job; a box outside the declared space is refused rather than clamped;
  * no timestamp or no `time_source` is allowed (the worker then pauses counting) unless `--strict-time` is
    given, in which case the job is refused;
  * `source_frame_id` and `capture_group_id` are only ever copied from the caller - never derived from
    `preview_sequence`, the mosaic position, or the clock.

Nothing here imports mediapipe, opens a socket, or touches Ly's code.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pose import binding as G      # noqa: E402  (stdlib only)

SPACES = ('preview_pixels', 'mosaic_pixels')
MOSAIC_CAP = (1920, 1080)


class JobError(ValueError):
    pass


def _need(cond, msg):
    if not cond:
        raise JobError(msg)


def jpeg_size(path):
    """Width/height from the JPEG header.  The size must come from the file, not from a constant."""
    with open(path, 'rb') as f:
        data = f.read()
    _need(len(data) > 4 and data[:2] == b'\xff\xd8', 'not a JPEG file: %s' % path)
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h = (data[i + 5] << 8) | data[i + 6]
            w = (data[i + 7] << 8) | data[i + 8]
            _need(w > 0 and h > 0, 'JPEG has no positive dimensions')
            return int(w), int(h)
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = (data[i + 2] << 8) | data[i + 3]
        _need(seg >= 2, 'corrupt JPEG segment at %d' % i)
        i += 2 + seg
    raise JobError('JPEG has no SOF marker: %s' % path)


def _norm_items(raw):
    """Accept the shapes upstreams actually produce: a bare list, or an object with items/detections."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ('items', 'detections', 'boxes'):
            value = raw.get(key)
            if isinstance(value, list):
                return value
    raise JobError('detections must be a list or an object with an items/detections list')


def _to_preview(box, space, size, mosaic, roi):
    """One explicit transformation, applied once.  Returns (box, scale)."""
    w, h = size
    if space == 'preview_pixels':
        return [float(v) for v in box], None
    _need(mosaic is not None and roi is not None,
          'mosaic_pixels needs --mosaic-size and --roi; without them the box cannot be placed on this image')
    mx, my = mosaic
    x, y, rw, rh = [float(v) for v in roi]
    _need(0 <= x and 0 <= y and rw > 0 and rh > 0 and x + rw <= mx and y + rh <= my,
          'roi %r is not inside the mosaic %r' % (roi, mosaic))
    sx, sy = w / rw, h / rh
    _need(abs(rw * sx - w) <= 1 and abs(rh * sy - h) <= 1, 'roi cannot map onto the image')
    x0, y0, x1, y1 = [float(v) for v in box]
    _need(x <= x0 < x1 <= x + rw and y <= y0 < y1 <= y + rh,
          'box %r is outside roi %r - the box belongs to another camera region' % (box, roi))
    out = [(x0 - x) * sx, (y0 - y) * sy, (x1 - x) * sx, (y1 - y) * sy]
    _need(0 <= out[0] < out[2] <= w and 0 <= out[1] < out[3] <= h,
          'transformed box %r falls outside the %dx%d image' % (out, w, h))
    return [round(v, 4) for v in out], [round(sx, 6), round(sy, 6)]


def build_job(image, detections, identity, *, input_record_id=None, preview_sequence=None, timestamp_ms=None,
              time_source=None, source_frame_id=None, capture_group_id=None, space='preview_pixels',
              mosaic_size=None, roi=None, declared_sha256=None, declared_preview_size=None, person_id=None,
              action=None, strict_time=False):
    """Validate everything, then return (job, notes).  Raises JobError with the exact reason."""
    _need(isinstance(image, str) and image.strip(), 'image path is required')
    _need(os.path.isfile(image), 'image does not exist: %s' % image)
    image = os.path.abspath(image)
    with open(image, 'rb') as f:
        payload = f.read()
    actual_sha = hashlib.sha256(payload).hexdigest()
    _need(declared_sha256 is None or declared_sha256 == actual_sha,
          'declared jpeg_sha256 %r is not the file hash %r - refusing to relabel the image'
          % (declared_sha256, actual_sha))
    w, h = jpeg_size(image)
    if declared_preview_size is not None:
        _need(list(declared_preview_size) == [w, h],
              'declared preview size %r disagrees with the decoded JPEG %r' % (declared_preview_size, [w, h]))

    ident = G.check_identity(identity)
    rid = G._check_id(input_record_id, 'input_record_id') if input_record_id is not None else None
    _need(rid is not None, 'input_record_id is required; it is the identity of this capture record')
    _need(space in SPACES, 'coordinate space must be one of %s' % ', '.join(SPACES))
    _need(preview_sequence is None or (type(preview_sequence) is int and preview_sequence >= 0),
          'preview_sequence must be a non-negative int or null')
    _need(timestamp_ms is None or (type(timestamp_ms) is int and timestamp_ms >= 0),
          'timestamp_ms must be a non-negative int or null')
    _need(time_source is None or (isinstance(time_source, str) and 0 < len(time_source) <= 64),
          'time_source must be a short string or null')
    _need(timestamp_ms is not None or time_source is None, 'time_source without timestamp_ms is not usable')
    if strict_time:
        _need(timestamp_ms is not None and time_source is not None,
              '--strict-time was given but there is no reliable timestamp + time_source')
    _need(source_frame_id is None or (isinstance(source_frame_id, str) and 0 < len(source_frame_id) <= 128),
          'source_frame_id must be a string or null; it is never derived here')
    _need(capture_group_id is None or (isinstance(capture_group_id, str) and 0 < len(capture_group_id) <= 128),
          'capture_group_id must be a string or null; it is never invented here')

    items = _norm_items(detections)
    _need(len(items) <= 32, 'detections carry %d entries; the panel cap is 32' % len(items))

    out_items, seen, geometry, scale = [], set(), None, None
    for i, item in enumerate(items):
        _need(isinstance(item, dict), 'detections[%d] must be an object' % i)
        pid = item.get('person_id', item.get('id'))
        if pid is not None and not (isinstance(pid, str) and 0 < len(pid) <= 128):
            raise JobError('detections[%d].person_id must be null or 1..128 chars '
                           '(a stable id from the tracker), got %r' % (i, pid))
        if pid is not None:
            _need(pid not in seen, 'detections[%d].person_id %r is duplicated' % (i, pid))
            seen.add(pid)
        box = item.get('xyxy', item.get('box'))
        _need(isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) for v in box),
              'detections[%d].xyxy must be four numbers' % i)
        mapped, scale = _to_preview(box, space, (w, h), mosaic_size, roi)
        _need(0 <= mapped[0] < mapped[2] <= w and 0 <= mapped[1] < mapped[3] <= h,
              'box %r is outside the actual %dx%d image' % (mapped, w, h))
        score = item.get('score', item.get('confidence'))
        _need(score is None or (type(score) in (int, float) and 0 <= float(score) <= 1),
              'detections[%d].score must be 0..1 or null' % i)
        out_items.append({'person_id': pid, 'xyxy': mapped, 'score': None if score is None else float(score)})
        if space == 'mosaic_pixels':
            geometry = {'source_space': space,
                        'mosaic_size': [int(mosaic_size[0]), int(mosaic_size[1])],
                        'roi': [float(v) for v in roi],
                        'scale': scale,
                        'target_size': [w, h]}

    det_out = {'input_record_id': rid,
               'jpeg_sha256': actual_sha,
               'camera_id': ident['camera_id'],
               'processing_mode': ident['processing_mode'],
               'coordinate_space': 'preview_pixels',
               'items': out_items}
    if geometry is not None:
        det_out['geometry'] = geometry

    job = {'image': image,
           'jpeg_sha256': actual_sha,
           'input_record_id': rid,
           'identity': ident,
           'preview_sequence': preview_sequence,
           'source_frame_id': source_frame_id,
           'timestamp_ms': timestamp_ms,
           'time_source': time_source,
           'capture_group_id': capture_group_id,
           'detections': det_out}
    if person_id is not None:
        _need(isinstance(person_id, str) and 0 < len(person_id) <= 128, 'person_id must be 1..128 chars')
        job['person_id'] = person_id
    if action is not None:
        job['action'] = action

    notes = ['image %dx%d' % (w, h), 'boxes %d' % len(out_items), 'space %s' % space]
    if geometry is not None:
        notes.append('mapped through mosaic %r roi %r scale %r' % (geometry['mosaic_size'], geometry['roi'],
                                                                  geometry['scale']))
    if timestamp_ms is None:
        notes.append('no timestamp: counting will stay paused until one is supplied')
    elif time_source is None:
        notes.append('no time_source: counting will stay paused until one is supplied')
    if not out_items:
        notes.append('no detections: this job produces a legal pose_absent record')
    anonymous = sum(1 for it in out_items if it['person_id'] is None)
    if anonymous:
        notes.append('%d/%d boxes have no upstream person id: keypoints will be emitted, but continuity stays '
                     'assumed and no score is counted for those people' % (anonymous, len(out_items)))
    return job, notes


def _identity_from_args(a):
    return {k: getattr(a, k) for k in G.IDENTITY_KEYS}


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        """Keeps every refusal on the same line format: JOB FAIL: <reason>, exit 2."""

        def error(self, message):
            print('JOB FAIL: %s' % message)
            raise SystemExit(2)

    ap = Parser(description='turn one FPGA JPEG + YOLO record into a v3 pose job')
    ap.add_argument('--image', required=True, help='JPEG produced by the selected processing branch')
    ap.add_argument('--detections', required=True,
                    help='JSON file with the YOLO record: a list, or {"items": [...]}')
    ap.add_argument('--out', required=True, help='where to write the job JSON (written atomically)')
    ap.add_argument('--input-record-id', required=True)
    ap.add_argument('--session-id', required=True)
    ap.add_argument('--boot-id', required=True)
    ap.add_argument('--camera-id', required=True, choices=['A', 'B'])
    ap.add_argument('--processing-mode', required=True,
                    choices=['original', 'gray', 'gaussian', 'histogram'])
    ap.add_argument('--fpga-bit-sha256', required=True)
    ap.add_argument('--processor', required=True, choices=['fpga', 'rk', 'simulation'])
    ap.add_argument('--preview-sequence', type=int)
    ap.add_argument('--timestamp-ms', type=int)
    ap.add_argument('--time-source')
    ap.add_argument('--source-frame-id')
    ap.add_argument('--capture-group-id')
    ap.add_argument('--person-id', help='select one person when the record has several')
    ap.add_argument('--action', help='action type to carry into the job (e.g. SQUAT)')
    ap.add_argument('--space', default='preview_pixels', choices=list(SPACES),
                    help='coordinate space of the boxes you pass in')
    ap.add_argument('--mosaic-size', help='WxH of the 1920x1080 mosaic, required for mosaic_pixels')
    ap.add_argument('--roi', help='X,Y,W,H of this camera preview inside the mosaic, required for mosaic_pixels')
    ap.add_argument('--declared-sha256', help='hash the upstream claims for this JPEG; it must match the file')
    ap.add_argument('--declared-preview-size', help='WxH the upstream claims; it must match the decoded file')
    ap.add_argument('--strict-time', action='store_true',
                    help='refuse the job instead of emitting one whose counting must stay paused')
    a = ap.parse_args(argv)

    def pair(text, label):
        if text is None:
            return None
        parts = [p for p in text.replace('X', 'x').replace(',', 'x').split('x') if p.strip()]
        _need(len(parts) in (2, 4), '%s must look like WxH or X,Y,W,H' % label)
        return [int(v) for v in parts]

    try:
        mosaic = None
        if a.mosaic_size:
            parts = [p for p in a.mosaic_size.lower().split('x') if p.strip()]
            _need(len(parts) == 2, '--mosaic-size must look like 1920x1080')
            mosaic = [int(parts[0]), int(parts[1])]
            _need(mosaic[0] <= MOSAIC_CAP[0] and mosaic[1] <= MOSAIC_CAP[1],
                  'mosaic %r is larger than the 1920x1080 candidate frame' % (mosaic,))
        roi = pair(a.roi, '--roi')
        size = pair(a.declared_preview_size, '--declared-preview-size')
        if a.space == 'mosaic_pixels':
            _need(mosaic is not None and roi is not None,
                  '--space mosaic_pixels needs --mosaic-size and --roi')
        with io.open(a.detections, encoding='utf-8') as f:
            detections = json.load(f)
        job, notes = build_job(
            a.image, detections, _identity_from_args(a),
            input_record_id=a.input_record_id, preview_sequence=a.preview_sequence,
            timestamp_ms=a.timestamp_ms, time_source=a.time_source, source_frame_id=a.source_frame_id,
            capture_group_id=a.capture_group_id, space=a.space, mosaic_size=mosaic, roi=roi,
            declared_sha256=a.declared_sha256, declared_preview_size=size, person_id=a.person_id,
            action=a.action, strict_time=a.strict_time)
        tmp = a.out + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(job, f, indent=1, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, a.out)
    except (JobError, G.BindingError) as e:
        print('JOB FAIL: %s' % e)
        return 2
    except (OSError, ValueError) as e:
        print('JOB FAIL: %s: %s' % (type(e).__name__, e))
        return 2
    print('JOB OK: image=%s sha256=%s mode=%s camera=%s record=%s -> %s'
          % (os.path.basename(job['image']), job['jpeg_sha256'][:16],
             job['identity']['processing_mode'], job['identity']['camera_id'],
             job['input_record_id'], a.out))
    for note in notes:
        print('  note: %s' % note)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())