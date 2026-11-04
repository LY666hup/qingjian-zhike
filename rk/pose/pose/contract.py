"""Pose record contract (qjzc.pose.panel.v1), aligned with the Windows Round-2 panel入口.

One record per processed frame, carrying the full provenance of the image it analysed: the six identity fields of
the capture branch, the input record it belongs to, the processing mode, the JPEG hash, the real preview size, the
crop geometry, and whether the person's continuity is proven strongly enough for the number to count as a result.

`validate()` is written to refuse, not to accept:
  * an identity field that is missing, out of enum, or disagrees with its mirror;
  * a backend that is not MediaPipe on the CPU, or a device/host that overclaims;
  * counting that is enabled without a number, a timestamp, a time source, or proven person continuity;
  * an absent pose that still has points, or a selected person without a box;
  * keypoints outside [0,1], a box outside the real preview size, a hash that is not 64 hex;
  * a selection or reset reason outside the vocabulary the Windows Round-2 receiver accepts.

Every rule below is exercised by the mutation test (`src/_pose_mod_mutation.py`) and the panel suite.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re

SCHEMA = 'qjzc.pose.panel.v1'
LAYOUT = 'mediapipe33'
COORDINATE_SPACE = 'preview_pixels_normalized'
BACKEND = 'mediapipe_cpu'
INFERENCE_DEVICE = 'RK3576J'
MODEL_SHA256 = '4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad'

IDENTITY_KEYS = ('session_id', 'boot_id', 'camera_id', 'processing_mode', 'fpga_bit_sha256', 'processor')
CAMERAS = ('A', 'B')
PROCESSING_MODES = ('original', 'gray', 'gaussian', 'histogram')
PROCESSORS = ('fpga', 'rk', 'simulation')

# Field name -> where it comes from.  Keeping this table next to the validator is what makes the mapping auditable.
TOP_LEVEL = {
    'schema': 'constant: qjzc.pose.panel.v1',
    'state': 'pose_ready when a pose exists, pose_absent otherwise',
    'identity': 'the six fields of the capture branch (required, mirrored below)',
    'session_id': 'identity.session_id (mirror, must agree)',
    'boot_id': 'identity.boot_id (mirror, must agree)',
    'camera_id': 'identity.camera_id (mirror, must agree)',
    'processing_mode': 'identity.processing_mode (mirror, must agree)',
    'fpga_bit_sha256': 'identity.fpga_bit_sha256 (mirror, must agree)',
    'processor': 'identity.processor (mirror, must agree) - the SOURCE processor, not ours',
    'input_record_id': 'the upstream record this JPEG and its boxes belong to',
    'capture_group_id': 'only when the caller supplies a real one, otherwise null',
    'preview_sequence': 'caller (from the detection record)',
    'source_frame_id': 'caller or null -> never synthesised',
    'jpeg_sha256': 'computed from the analysed file (required)',
    'image': 'path of the analysed file (required)',
    'person': 'person selector (see PERSON_FIELDS)',
    'person_selection': 'mirror of person.selection',
    'pose': 'model output (see POSE_FIELDS)',
    'pose_present': 'model output',
    'pose_available': 'same as pose_present',
    'rep_count': 'counter, or null when the number is not a result',
    'action_phase': 'counter or unknown',
    'counting': 'enabled only when a number is safe to show as a result',
    'counter_reason': '"ok" when a number is shown, otherwise why not',
    'counter_note': 'optional detail that does not block counting (e.g. low_visibility_23)',
    'counter_reset_reason': 'person_switch | none (the only values Windows Round-2 accepts)',
    'counter_reset_detail': 'none | person_switch | session_switch | mode_switch (audit detail)',
    'continuity': 'proven | assumed | unknown | broken - can this person be the same person as before?',
    'score_eligible': 'true only when continuity is proven and all counting gates pass',
    'timing': 'timestamp and where it came from',
    'backend': 'constant: mediapipe_cpu (ours)',
    'inference_device': 'constant: RK3576J (target of this inference)',
    'inference_host': 'actual host string, so an x86 run is never mistaken for an RK run',
    'geometry': 'how the boxes got into preview pixels, or null when they were already there',
    'model': 'model identity',
    'preview_size': 'coordinate space of the keypoints, from the decoded file',
}

PERSON_FIELDS = {
    'key': 'display key, e.g. upstream:P1',
    'person_id': 'upstream id, or null when the upstream record has none',
    'selection': 'by_id | by_box | single | max_score_ambiguous | person_id_not_found | no_person',
    'mode': 'selected | max_score',
    'box': 'preview pixels [x0, y0, x1, y1], or null',
    'score': 'upstream score or null',
    'candidates': 'how many detections the record carried',
    'reason': 'why this person was or was not chosen',
    'tracking': 'method (upstream_id | iou_follow | none), iou, frames_with_id',
}

POSE_FIELDS = {
    'layout': LAYOUT,
    'coordinate_space': COORDINATE_SPACE,
    'keypoints': '33 x [x, y, visibility], normalised to preview_size; [] when absent',
    'keypoints_clipped': 'number of points clamped into the preview rectangle',
    'pose_score': 'model confidence, 0.0 when absent',
    'inference_ms': 'wall time of the landmarker call',
    'crop': 'crop rectangle in preview pixels: [x0, y0, x1, y1], null when nothing was cropped',
    'crop_source': 'detection_box | detection_box_padded | none',
}

MIN_VISIBILITY = 0.5
KEYPOINT_COUNT = 33

SELECTIONS = {'by_id', 'by_box', 'single', 'max_score_ambiguous', 'person_id_not_found', 'no_person'}
TRACKING_METHODS = {'upstream_id', 'iou_follow', 'none'}
MODES = {'selected', 'max_score'}
PHASES = {'STANDING', 'DESCENDING', 'BOTTOM', 'ASCENDING', 'UNKNOWN', 'standing', 'descending',
          'bottom', 'ascending', 'unknown'}
REASONS = {'ok', 'no_person', 'counter_unavailable', 'person_switch', 'person_ambiguous',
           'identity_missing', 'no_time_base', 'time_regression', 'gap_too_large', 'pose_absent',
           'duplicate_frame', 'model_unavailable', 'person_untracked', 'person_id_unverified',
           'continuity_unproven', 'continuity_broken',
           'no_pose_in_crop', 'crop_too_small', 'image_size_mismatch'}
CROP_SOURCES = {'detection_box', 'detection_box_padded', 'none'}
CONTINUITY = {'proven', 'assumed', 'unknown', 'broken'}
# Round-2 only understands these two; session/mode switches are separate tallies, not resets of this one.
RESET_REASONS = {'none', 'person_switch'}
RESET_DETAILS = {'none', 'person_switch', 'session_switch', 'mode_switch'}
GEOMETRY_SPACES = {'mosaic_pixels'}
HEX64 = re.compile(r'^[0-9a-f]{64}$')
VIS_REASON = re.compile(r'^low_visibility_\d+$')
RECORD_ID = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')


def required_names():
    return sorted(TOP_LEVEL) + sorted(POSE_FIELDS) + sorted(PERSON_FIELDS)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def _need(cond, msg):
    if not cond:
        raise ValueError(msg)


def _check_geometry(rec, pv):
    geo = rec['geometry']
    if geo is None:
        return
    _need(isinstance(geo, dict), 'geometry must be an object or null')
    for key in ('source_space', 'mosaic_size', 'roi', 'scale', 'target_size'):
        _need(key in geo, 'geometry.%s is required' % key)
    _need(geo['source_space'] in GEOMETRY_SPACES,
          'geometry.source_space must be one of %s' % sorted(GEOMETRY_SPACES))
    ms, roi, sc, ts = geo['mosaic_size'], geo['roi'], geo['scale'], geo['target_size']
    _need(isinstance(ms, list) and len(ms) == 2 and all(isinstance(v, int) and v > 0 for v in ms),
          'geometry.mosaic_size must be [width, height]')
    _need(isinstance(roi, list) and len(roi) == 4 and all(_is_num(v) for v in roi), 'geometry.roi must be four numbers')
    _need(0 <= roi[0] and 0 <= roi[1] and 0 < roi[2] <= ms[0] and 0 < roi[3] <= ms[1]
          and roi[0] + roi[2] <= ms[0] and roi[1] + roi[3] <= ms[1],
          'geometry.roi %r is not inside the mosaic %r' % (roi, ms))
    _need(isinstance(sc, list) and len(sc) == 2 and all(_is_num(v) and v > 0 for v in sc),
          'geometry.scale must be two positive numbers')
    _need(isinstance(ts, list) and len(ts) == 2 and list(ts) == list(pv),
          'geometry.target_size %r disagrees with preview_size %r' % (ts, pv))
    _need(abs(roi[2] * sc[0] - pv[0]) <= 1 and abs(roi[3] * sc[1] - pv[1]) <= 1,
          'geometry.scale/roi do not map onto preview_size (roi %r x scale %r != %r)' % (roi, sc, pv))


def validate(rec):
    if not isinstance(rec, dict):
        raise ValueError('pose record must be an object')
    for name in TOP_LEVEL:
        _need(name in rec, 'missing top-level field: %s' % name)
    _need(rec['schema'] == SCHEMA, 'schema must be %r, got %r' % (SCHEMA, rec['schema']))
    _need(rec['backend'] == BACKEND, 'backend must be %r, got %r' % (BACKEND, rec['backend']))
    _need(rec['inference_device'] == INFERENCE_DEVICE,
          'inference device must be %r, got %r' % (INFERENCE_DEVICE, rec['inference_device']))
    _need(isinstance(rec['inference_host'], str) and rec['inference_host'].strip(),
          'inference_host must say where the pose was actually computed')

    ident = rec['identity']
    _need(isinstance(ident, dict), 'identity must be an object with the six capture-branch fields')
    for key in IDENTITY_KEYS:
        _need(key in ident, 'identity.%s is missing' % key)
        v = ident[key]
        _need(isinstance(v, str) and 0 < len(v) <= 128, 'identity.%s must be a string 1..128 chars' % key)
        _need(rec.get(key) == v, 'top-level %s %r disagrees with identity.%s %r' % (key, rec.get(key), key, v))
    _need(ident['camera_id'] in CAMERAS, 'identity.camera_id must be A or B, got %r' % (ident['camera_id'],))
    _need(ident['processing_mode'] in PROCESSING_MODES,
          'identity.processing_mode must be one of %s, got %r' % (PROCESSING_MODES, ident['processing_mode']))
    _need(ident['processor'] in PROCESSORS,
          'identity.processor must be one of %s, got %r' % (PROCESSORS, ident['processor']))
    _need(bool(HEX64.match(ident['fpga_bit_sha256'].lower())),
          'identity.fpga_bit_sha256 must be 64 hex chars')

    rid = rec['input_record_id']
    _need(isinstance(rid, str) and bool(RECORD_ID.match(rid)),
          'input_record_id %r is not a valid record id' % (rid,))
    group = rec['capture_group_id']
    _need(group is None or (isinstance(group, str) and 0 < len(group) <= 128),
          'capture_group_id must be a short string or null')
    sha = rec['jpeg_sha256']
    _need(isinstance(sha, str) and bool(HEX64.match(sha)), 'jpeg_sha256 must be 64 lowercase hex chars')
    _need(isinstance(rec['image'], str) and rec['image'].strip(), 'image path must be a non-empty string')

    pose_ok = rec['pose_present']
    _need(isinstance(pose_ok, bool), 'pose_present must be true or false')
    _need(isinstance(rec['pose_available'], bool), 'pose_available must be true or false, not %r'
          % (rec['pose_available'],))
    _need(rec['pose_available'] == pose_ok, 'pose_available must equal pose_present')
    _need(rec['state'] in ('pose_ready', 'pose_absent'), 'state must be pose_ready or pose_absent')
    _need((rec['state'] == 'pose_ready') == pose_ok, 'state does not agree with pose_present')

    pv = rec['preview_size']
    _need(isinstance(pv, list) and len(pv) == 2 and all(isinstance(v, int) and v > 0 for v in pv),
          'preview_size must be [width, height] with positive ints')

    seq = rec['preview_sequence']
    _need(seq is None or (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0),
          'preview_sequence must be a non-negative int or null')
    sfs = rec['source_frame_id']
    _need(sfs is None or (isinstance(sfs, str) and 0 < len(sfs) <= 128),
          'source_frame_id must be a string or null')

    person = rec['person']
    _need(isinstance(person, dict), 'person must be an object')
    for k in PERSON_FIELDS:
        _need(k in person, 'person.%s is missing' % k)
    _need(person['selection'] in SELECTIONS, 'person.selection %r is not a known selection' % (person['selection'],))
    _need(person['mode'] in MODES, 'person.mode %r is not a known mode' % (person['mode'],))
    _need(rec['person_selection'] == person['selection'],
          'person_selection %r disagrees with person.selection %r'
          % (rec['person_selection'], person['selection']))
    pid = person['person_id']
    _need(pid is None or (isinstance(pid, str) and 0 < len(pid) <= 64),
          'person.person_id must be a string or null, not %r' % (pid,))
    track = person['tracking']
    _need(isinstance(track, dict) and set(track) >= {'method', 'iou', 'frames_with_id'},
          'person.tracking must carry method, iou and frames_with_id')
    _need(track['method'] in TRACKING_METHODS, 'person.tracking.method %r is not known' % (track['method'],))
    _need(track['iou'] is None or (_is_num(track['iou']) and 0.0 <= track['iou'] <= 1.0),
          'person.tracking.iou must be within [0,1] or null')
    _need(isinstance(track['frames_with_id'], int) and track['frames_with_id'] >= 0,
          'person.tracking.frames_with_id must be a non-negative int')
    _need((pid is not None and track['method'] == 'upstream_id')
          or (pid is None and track['method'] in ('none', 'iou_follow')),
          'person.tracking.method %r disagrees with person_id %r: an upstream id is the only thing that can '
          'claim upstream_id, and an anonymous person can only be none or iou_follow' % (track['method'], pid))
    _need(isinstance(person['candidates'], int) and person['candidates'] >= 0,
          'person.candidates must be a non-negative int')
    if person['box'] is None:
        _need(person['selection'] in ('person_id_not_found', 'no_person', 'max_score_ambiguous'),
              'a record without a box must say why no person was chosen')
        _need(not pose_ok, 'a pose cannot exist without the person box it was cropped from')
    else:
        _need(person['selection'] in ('by_id', 'by_box', 'single'),
              'a box cannot be shipped with the selection %r, which says no person was chosen'
              % (person['selection'],))
        box = person['box']
        _need(isinstance(box, list) and len(box) == 4 and all(_is_num(v) for v in box),
              'person.box must be four numbers')
        x0, y0, x1, y1 = box
        _need(0 <= x0 < x1 <= pv[0] and 0 <= y0 < y1 <= pv[1],
              'person.box %r is not inside the preview %r' % (box, pv))

    _check_geometry(rec, pv)

    pose = rec['pose']
    _need(isinstance(pose, dict), 'pose must be an object')
    for name in POSE_FIELDS:
        _need(name in pose, 'missing pose field: %s' % name)
    _need(pose['layout'] == LAYOUT, 'pose.layout must be %r' % LAYOUT)
    _need(pose['coordinate_space'] == COORDINATE_SPACE, 'pose.coordinate_space must be %r' % COORDINATE_SPACE)

    kp = pose['keypoints']
    _need(isinstance(kp, list), 'pose.keypoints must be a list')
    if pose_ok:
        _need(len(kp) == KEYPOINT_COUNT, 'keypoints must be %d, got %d' % (KEYPOINT_COUNT, len(kp)))
    else:
        _need(len(kp) == 0, 'absent pose must carry zero keypoints, got %d' % len(kp))
    for i, p in enumerate(kp):
        _need(isinstance(p, list) and len(p) == 3, 'keypoint %d is not [x, y, vis]' % i)
        x, y, v = p
        for v2, nm in ((x, 'x'), (y, 'y'), (v, 'visibility')):
            _need(_is_num(v2), 'keypoint %d %s is not a finite number: %r' % (i, nm, v2))
        _need(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0, 'keypoint %d outside [0,1]: %r' % (i, (x, y)))
        _need(0.0 <= v <= 1.0, 'keypoint %d visibility out of range: %r' % (i, v))
    clipped = pose['keypoints_clipped']
    _need(isinstance(clipped, int) and 0 <= clipped <= KEYPOINT_COUNT,
          'keypoints_clipped must be 0..%d, got %r' % (KEYPOINT_COUNT, clipped))
    _need(_is_num(pose['pose_score']) and 0.0 <= pose['pose_score'] <= 1.0, 'pose_score must be 0..1')
    _need(_is_num(pose['inference_ms']) and pose['inference_ms'] >= 0, 'inference_ms must be >= 0')
    crop = pose['crop']
    _need(crop is None or (isinstance(crop, list) and len(crop) == 4 and all(_is_num(v) for v in crop)),
          'crop must be four numbers or null')
    _need(pose['crop_source'] in CROP_SOURCES, 'crop_source %r is not known' % (pose['crop_source'],))

    if not pose_ok:
        _need(rec['rep_count'] in (None, 0),
              'absent pose must not carry a rep count, got %r' % (rec['rep_count'],))

    rep = rec['rep_count']
    _need(rep is None or (isinstance(rep, int) and not isinstance(rep, bool) and rep >= 0),
          'rep_count must be a non-negative int or null, got %r' % (rep,))
    counting = rec['counting']
    _need(counting in ('enabled', 'disabled'), 'counting must be enabled or disabled, got %r' % (counting,))
    _need((counting == 'enabled') == (rep is not None),
          'counting=%s contradicts rep_count=%r (disabled means null, enabled means a number)' % (counting, rep))
    reason = rec['counter_reason']
    _need(isinstance(reason, str) and (reason in REASONS or bool(VIS_REASON.match(reason))),
          'counter_reason %r is not known' % (reason,))
    _need((reason == 'ok') == (counting == 'enabled'),
          'counter_reason %r contradicts counting=%s (a shown number must say ok, a paused one must say why)'
          % (reason, counting))
    _need(rep is None or rep == 0 or reason == 'ok',
          'rep_count=%r cannot be explained by counter_reason %r' % (rep, reason))
    note = rec['counter_note']
    _need(note is None or (isinstance(note, str) and 0 < len(note) <= 128),
          'counter_note must be a short string or null')
    _need(rec['counter_reset_reason'] in RESET_REASONS,
          'counter_reset_reason %r must be one of %s (Round-2 vocabulary)'
          % (rec['counter_reset_reason'], sorted(RESET_REASONS)))
    detail = rec['counter_reset_detail']
    _need(detail in RESET_DETAILS,
          'counter_reset_detail %r must be one of %s' % (detail, sorted(RESET_DETAILS)))
    _need((rec['counter_reset_reason'] == 'person_switch') == (detail == 'person_switch'),
          'counter_reset_reason %r disagrees with detail %r' % (rec['counter_reset_reason'], detail))
    _need(rec['action_phase'] in PHASES, 'action_phase %r is not known' % (rec['action_phase'],))

    continuity = rec['continuity']
    _need(continuity in CONTINUITY, 'continuity %r must be one of %s' % (continuity, sorted(CONTINUITY)))
    eligible = rec['score_eligible']
    _need(isinstance(eligible, bool), 'score_eligible must be true or false')
    _need(eligible == (counting == 'enabled'),
          'score_eligible=%r disagrees with counting=%s' % (eligible, counting))
    if eligible:
        _need(continuity == 'proven', 'a counted result needs proven continuity, got %r' % (continuity,))
        _need(pid is not None, 'a counted result needs a stable person id from upstream')
        _need(track['method'] == 'upstream_id', 'a counted result needs an upstream person id, not IoU tracking')
        _need(track['frames_with_id'] >= 1, 'a counted result needs at least one frame with the id')
    if not pose_ok:
        _need(continuity in ('unknown', 'broken'),
              'an absent pose cannot claim %r continuity' % (continuity,))
        _need(not eligible, 'an absent pose cannot be a result')

    timing = rec['timing']
    _need(isinstance(timing, dict) and 'timestamp_ms' in timing, 'timing.timestamp_ms is required (may be null)')
    ts = timing['timestamp_ms']
    _need(ts is None or (isinstance(ts, int) and not isinstance(ts, bool) and ts >= 0),
          'timing.timestamp_ms must be a non-negative int or null')
    src = timing.get('time_source')
    _need(src is None or (isinstance(src, str) and 0 < len(src) <= 64),
          'timing.time_source must be a short string or null')
    if counting == 'enabled':
        _need(ts is not None, 'counting cannot be enabled without a timestamp')
        _need(src is not None, 'counting cannot be enabled without saying where the timestamp came from')
        _need(sfs is not None or rec['preview_sequence'] is not None,
              'counting cannot be enabled without a record identity (source_frame_id or preview_sequence)')

    model = rec['model']
    _need(isinstance(model, dict) and isinstance(model.get('name'), str) and model['name'].strip(),
          'model.name is required')
    _need(isinstance(model.get('sha256'), str) and bool(HEX64.match(model['sha256'])),
          'model.sha256 must be 64 lowercase hex chars')
    return True


def load_contracts(path):
    spec = importlib.util.spec_from_file_location('display_contracts', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_against_contracts(path):
    """Compare our emitted names with the names the display side defines.  Report only; never rewrite."""
    mod = load_contracts(path)
    found = {n for n in dir(mod) if not n.startswith('_')}
    ours = set(required_names())
    report = {'contracts_path': os.path.abspath(path), 'contract_symbols': len(found),
              'we_emit': len(ours), 'missing_in_contracts': sorted(ours - found),
              'extra_in_contracts': sorted(found - ours)[:40]}
    for name in ('FIELDS', 'POSE_FIELDS', 'REQUIRED', 'SCHEMA'):
        if hasattr(mod, name):
            val = getattr(mod, name)
            if isinstance(val, dict):
                report.setdefault('contract_dicts', {})[name] = sorted(val)
            elif isinstance(val, (list, tuple, set)):
                report.setdefault('contract_lists', {})[name] = sorted(val)
    return report


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='contract self-check')
    ap.add_argument('--contracts', help='path to the display side contracts.py')
    ap.add_argument('--sample', help='path to a pose record to validate')
    a = ap.parse_args(argv)
    rc = 0
    if a.contracts:
        rep = check_against_contracts(a.contracts)
        missing = rep['missing_in_contracts']
        print('CONTRACTS %s: %d symbols, %d names we emit, %d not found there'
              % (rep['contracts_path'], rep['contract_symbols'], rep['we_emit'], len(missing)))
        if missing:
            print('MISMATCH: display contract does not define: %s' % ', '.join(missing[:12]))
            rc = 1
        else:
            print('MATCH: the display contract defines every name we emit')
    if a.sample:
        rec = json.load(io.open(a.sample, encoding='utf-8'))
        try:
            validate(rec)
            print('SAMPLE OK: %s validates against %s' % (a.sample, SCHEMA))
        except ValueError as e:
            print('SAMPLE FAIL: %s' % e)
            rc = 1
    if not a.contracts and not a.sample:
        print('nothing to do; pass --contracts and/or --sample')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())