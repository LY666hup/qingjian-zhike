"""Map the pose panel record (qjzc.pose.panel.v1) onto the Windows analysis channel (qjzc.analysis.v1).

The panel is our primary channel; this adapter is the compatibility edge for the Windows receiver that currently
ships (`Windows新版/pose_input.py`), which refuses anything that is not `qjzc.analysis.v1`, whose per-person
`coordinate_space` is not `normalized_image`, whose `kind` is not `pose|absent`, or that lacks the six identity
fields from `profiles.IDENTITY`.  The panel keeps more provenance than that channel carries, so this is a
deliberate narrowing rather than a rename:

  * only what the receiver validates is emitted at the top level (plus `pose_detail`, which it ignores);
  * the identity is copied from the panel record, so it cannot drift from what was analysed;
  * `rep_count` is null whenever the counter is paused - the adapter will not turn "unknown" into 0;
  * `quality_score` is null: this project has no validated 0-100 scoring, and inventing one would be a lie the
    display side cannot detect;
  * `counts_by_action` / `durations_ms` are empty for the same reason;
  * image size is taken from the analysed JPEG, never from a constant.

Everything the adapter refuses raises `AdapterError`, and every refusal is exercised in tests.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re

SCHEMA = 'qjzc.analysis.v1'
PANEL_SCHEMA = 'qjzc.pose.panel.v1'
LAYOUT = 'mediapipe33'
COORDINATE_SPACE = 'normalized_image'

# profiles.IDENTITY - the receiver compares these against its own profile and rejects any mismatch
IDENTITY_KEYS = ('session_id', 'boot_id', 'camera_id', 'processing_mode', 'fpga_bit_sha256', 'processor')
CAMERAS = ('A', 'B')
PROCESSORS = ('fpga', 'rk', 'simulation')
MODES = ('original', 'gray', 'gaussian', 'histogram')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


class AdapterError(ValueError):
    pass


def _need(cond, msg):
    if not cond:
        raise AdapterError(msg)


def check_identity(identity):
    """The six fields the Windows side will compare byte for byte."""
    _need(isinstance(identity, dict), 'identity must be an object with the six board fields')
    for key in IDENTITY_KEYS:
        v = identity.get(key)
        _need(isinstance(v, str) and 0 < len(v) <= 128, 'identity.%s must be a string 1..128 chars' % key)
    _need(identity['camera_id'] in CAMERAS, 'identity.camera_id must be A or B (the panel calls it front/rear)')
    _need(identity['processing_mode'] in MODES, 'identity.processing_mode must be one of %s' % (MODES,))
    _need(identity['processor'] in PROCESSORS, 'identity.processor must be one of %s' % (PROCESSORS,))
    _need(bool(HEX64.match(identity['fpga_bit_sha256'].lower())),
          'identity.fpga_bit_sha256 must be 64 hex chars')
    return {k: identity[k] for k in IDENTITY_KEYS}


def _person_id(rec, override):
    """The upstream id, an explicit override, or None.  None means anonymous: never a made-up P1."""
    if override is not None:
        _need(isinstance(override, str) and 0 < len(override) <= 128, 'person_id override must be 1..128 chars')
        return override
    person = rec.get('person') or {}
    upstream = person.get('person_id')
    if isinstance(upstream, str) and 0 < len(upstream) <= 128:
        return upstream
    key = person.get('key')
    if isinstance(key, str) and 0 < len(key) <= 128:
        return key
    return None


def image_size_from_jpeg(path):
    """Real size, read from the file.  Supports 640x360 as well as 960x540; no default anywhere."""
    import cv2
    img = cv2.imread(path)
    _need(img is not None, 'cannot decode image: %s' % path)
    h, w = img.shape[:2]
    return int(w), int(h)


def panel_identity(panel_rec):
    """The identity a panel record carries; falls back to the mirrored top-level fields for older records."""
    ident = panel_rec.get('identity')
    if isinstance(ident, dict):
        return check_identity(ident)
    return check_identity({k: panel_rec.get(k) for k in IDENTITY_KEYS})


def to_analysis_v1(panel_rec, identity, frame_id, person_id=None, image_b64=None, image_path=None,
                   action_type=None, profile_data=None, panel_identity=False):
    """panel record + board identity -> one analysis message the Windows PoseReceiver accepts.

    `panel_identity=True` means "the panel record already carries the identity; verify it against the argument
    instead of trusting either one", which is how the worker calls it.
    """
    ident = check_identity(identity)
    _need(type(frame_id) is int and frame_id >= 0, 'frame_id must be a non-negative int')

    # the panel record has already been through pose.contract.validate(); re-check the parts we depend on
    _need(panel_rec.get('schema') == PANEL_SCHEMA,
          'adapter expects a %r record, got %r' % (PANEL_SCHEMA, panel_rec.get('schema')))
    size = panel_rec.get('preview_size')
    _need(isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and v > 0 for v in size),
          'preview_size (the analysed image size) is missing from the panel record')

    if panel_identity:
        inner = panel_identity_of(panel_rec)
        for k in IDENTITY_KEYS:
            _need(inner[k] == ident[k],
                  'panel identity.%s %r differs from the adapter identity %r - refusing to relabel an image'
                  % (k, inner[k], ident[k]))

    if image_path:
        w, h = image_size_from_jpeg(image_path)
        _need([w, h] == list(size),
              'preview_size %r disagrees with the decoded image %r; the image size must come from the file'
              % (size, [w, h]))
        with open(image_path, 'rb') as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        _need(actual == panel_rec.get('jpeg_sha256'),
              'jpeg_sha256 %r does not match the file %s (it hashes to %s) - the panel would name an image it '
              'did not analyse' % (panel_rec.get('jpeg_sha256'), os.path.basename(image_path), actual))

    pose_ok = panel_rec.get('pose_present') is True
    counting_on = panel_rec.get('counting') == 'enabled'
    rep = panel_rec.get('rep_count')
    _need(not counting_on or (type(rep) is int and rep >= 0),
          'counting is on but rep_count is %r; refusing to send a number we cannot back up' % (rep,))
    _need(counting_on or rep is None, 'counting is off but rep_count is %r' % (rep,))

    pid = _person_id(panel_rec, person_id) if pose_ok else None
    msg = dict(ident)
    msg.update({
        'schema': SCHEMA,
        # the legacy receiver needs a stable person_id per person; an anonymous pose cannot be named there,
        # so this compatibility channel reports it as absent while /panel.json still carries the 33 points
        'kind': 'pose' if (pose_ok and pid is not None) else 'absent',
        'frame_id': frame_id,
        'pose_present': bool(pose_ok and pid is not None),
        'persons': [],
    })

    pose = panel_rec['pose']
    if pose_ok and pid is not None:
        person = panel_rec['person']
        kps = pose['keypoints']
        _need(len(kps) == 33, 'mediapipe33 needs 33 points, got %d' % len(kps))
        # the panel normalises to the analysed image; that is exactly what normalized_image means here
        msg['persons'] = [{
            'person_id': pid,
            'layout': LAYOUT,
            'coordinate_space': COORDINATE_SPACE,
            'keypoints': [[round(float(x), 6), round(float(y), 6), round(float(v), 6)] for x, y, v in kps],
            'rep_count': rep if counting_on else None,
            'correct_count': None,
            'incorrect_count': None,
            'counts_by_action': {},
            'durations_ms': {},
            'action_type': action_type,
            'quality_score': None,
            'profile': dict(profile_data or {}),
        }]
        # the receiver rejects a pose message whose person list is empty; it also rejects absent ones that carry data
        _need(bool(msg['persons']), 'pose present but no person could be named')

    if image_b64 is not None:
        _need(isinstance(image_b64, str), 'image_b64 must be a base64 string')
        try:
            payload = base64.b64decode(image_b64, validate=True)
        except (ValueError, TypeError):
            raise AdapterError('image_b64 is not valid base64') from None
        _need(hashlib.sha256(payload).hexdigest() == panel_rec.get('jpeg_sha256'),
              'image_b64 does not hash to the record jpeg_sha256')
        msg['image_base64'] = image_b64

    # extras the receiver ignores; kept so nothing is lost between the two sides
    msg['pose_detail'] = {
        'source_schema': panel_rec.get('schema'),
        'jpeg_sha256': panel_rec.get('jpeg_sha256'),
        'input_record_id': panel_rec.get('input_record_id'),
        'processing_mode': panel_rec.get('processing_mode'),
        'capture_group_id': panel_rec.get('capture_group_id'),
        'preview_size': size,
        'session_id_reported': panel_rec.get('session_id'),
        'preview_sequence': panel_rec.get('preview_sequence'),
        'source_frame_id': panel_rec.get('source_frame_id'),
        'person_selection': (panel_rec.get('person') or {}).get('selection'),
        'person_mode': (panel_rec.get('person') or {}).get('mode'),
        'person_person_id': (panel_rec.get('person') or {}).get('person_id'),
        'person_box': (panel_rec.get('person') or {}).get('box'),
        'person_candidates': (panel_rec.get('person') or {}).get('candidates'),
        'keypoints_clipped': pose['keypoints_clipped'] if pose_ok else 0,
        'pose_score': pose['pose_score'] if pose_ok else None,
        'inference_ms': pose['inference_ms'] if pose_ok else None,
        'crop': pose['crop'] if pose_ok else None,
        'absent_reason': ('person_id_unverified' if (pose_ok and pid is None)
                          else (pose.get('absent_reason') if not pose_ok else None)),
        'anonymous': bool(pose_ok and pid is None),
        'anonymous_keypoints_on_panel': len(pose['keypoints']) if (pose_ok and pid is None) else 0,
        'counting': panel_rec.get('counting'),
        'counter_reason': panel_rec.get('counter_reason'),
        'counter_note': panel_rec.get('counter_note'),
        'counter_reset_reason': panel_rec.get('counter_reset_reason'),
        'counter_reset_detail': panel_rec.get('counter_reset_detail'),
        'continuity': panel_rec.get('continuity'),
        'score_eligible': panel_rec.get('score_eligible'),
        'person_tracking': (panel_rec.get('person') or {}).get('tracking'),
        'geometry': panel_rec.get('geometry'),
        'action_phase': panel_rec.get('action_phase'),
        'timestamp_ms': (panel_rec.get('timing') or {}).get('timestamp_ms'),
        'time_source': (panel_rec.get('timing') or {}).get('time_source'),
        'model_sha256': (panel_rec.get('model') or {}).get('sha256'),
        'inference_device': panel_rec.get('inference_device'),
        'inference_host': panel_rec.get('inference_host'),
    }
    return msg


# alias kept for callers that already import `panel_identity` as a function name
panel_identity_of = panel_identity


def read_panel(path):
    with io.open(path, encoding='utf-8') as f:
        return json.load(f)