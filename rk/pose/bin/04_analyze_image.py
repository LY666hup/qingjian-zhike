#!/usr/bin/env python3
"""Step 4 (v3) - analyse one saved image through the same path the resident worker uses.

One image in, one panel record out (qjzc.pose.panel.v1), optionally with the qjzc.analysis.v1 compatibility
message next to it.  The point of this file is that it is not a second implementation: it builds a job, hands it
to the same Worker used by `05_analyze_stream.py`, and writes what comes back.  So the panel a single image
produces is byte-for-byte the panel the daemon would produce for that image.

The job must say which capture it belongs to: the six identity fields and an `input_record_id`.  The hash is
computed from the file and compared with the declared one, and the YOLO detections must carry the same record id,
hash, camera and processing mode - `pose/binding.py` refuses anything else.

Exit codes: 0 record written (pose present or legitimately absent), 2 usage/refused input, 3 backend/model
problem, 4 contract or adapter violation.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from pose import backend as B            # noqa: E402
from pose import binding as G            # noqa: E402
from pose import contract as C           # noqa: E402
from pose import analysis_v1 as A        # noqa: E402


def parse_size(text):
    w, h = text.lower().split('x')
    return int(w), int(h)


def _gate_load(path):
    if path and os.path.isfile(path):
        try:
            return json.load(io.open(path, encoding='utf-8'))
        except (ValueError, OSError):
            return {}
    return {}


def _gate_save(path, data):
    if not path:
        return
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f)


def build_job(a):
    """Turn the CLI into the same job object the daemon consumes."""
    if a.job:
        with io.open(a.job, encoding='utf-8') as f:
            job = json.load(f)
    else:
        if not a.image:
            raise G.BindingError('need --job FILE or --image')
        if a.identity:
            ident = json.load(io.open(a.identity, encoding='utf-8'))
        else:
            missing = [n for n, v in (('--session-id', a.session_id), ('--boot-id', a.boot_id),
                                      ('--fpga-bit-sha256', a.fpga_bit_sha256)) if not v]
            if missing:
                raise G.BindingError('an image without provenance is not accepted: pass --identity FILE or '
                                     + ', '.join(missing))
            ident = {'session_id': a.session_id, 'boot_id': a.boot_id,
                     'camera_id': a.camera_id, 'processing_mode': a.processing_mode,
                     'fpga_bit_sha256': a.fpga_bit_sha256, 'processor': a.processor}
        if a.camera_id:
            ident['camera_id'] = a.camera_id
        if a.processing_mode:
            ident['processing_mode'] = a.processing_mode
        if a.processor:
            ident['processor'] = a.processor

        if a.detections:
            det = json.load(io.open(a.detections, encoding='utf-8'))
            if isinstance(det, list):                     # bare list: no provenance, refuse to guess it
                raise G.BindingError('--detections must be a YOLO record object with input_record_id, '
                                     'jpeg_sha256, camera_id, processing_mode and items; a bare list cannot be '
                                     'bound to an image')
        elif a.box:
            det = {'input_record_id': a.input_record_id or 'manual',
                   'jpeg_sha256': B.sha256_file(a.image),
                   'camera_id': ident['camera_id'], 'processing_mode': ident['processing_mode'],
                   'coordinate_space': 'preview_pixels',
                   'items': [{'person_id': a.person_id or 'manual',
                              'xyxy': [float(v) for v in a.box.split(',')], 'score': None}]}
        else:
            raise G.BindingError('need --detections RECORD or --box x0,y0,x1,y1')

        job = {'image': a.image,
               'jpeg_sha256': a.jpeg_sha256 or B.sha256_file(a.image),
               'input_record_id': det.get('input_record_id'),
               'identity': ident,
               'preview_sequence': a.preview_sequence,
               'timestamp_ms': a.timestamp_ms,
               'time_source': a.time_source,
               'source_frame_id': a.source_frame_id,
               'capture_group_id': a.capture_group_id,
               'detections': det}
        if a.person_id:
            job['person_id'] = a.person_id
        if a.action_type:
            job['action'] = a.action_type
    return job


def main(argv=None):
    ap = argparse.ArgumentParser(description='analyse one saved image (panel + optional analysis channel)')
    ap.add_argument('--job', help='a v3 job file; the other input flags are then ignored')
    ap.add_argument('--image')
    ap.add_argument('--detections', help='YOLO record JSON (object with input_record_id / jpeg_sha256 / items)')
    ap.add_argument('--box', help='x0,y0,x1,y1 in image pixels, when there is no YOLO record')
    ap.add_argument('--person-id')
    ap.add_argument('--identity', help='six-field identity JSON')
    ap.add_argument('--session-id')
    ap.add_argument('--boot-id')
    ap.add_argument('--camera-id')
    ap.add_argument('--processing-mode')
    ap.add_argument('--fpga-bit-sha256')
    ap.add_argument('--processor')
    ap.add_argument('--input-record-id')
    ap.add_argument('--jpeg-sha256', help='assert the image hash instead of only computing it')
    ap.add_argument('--capture-group-id', help='only if the board really supplies a shared capture identity')
    ap.add_argument('--preview-sequence', type=int)
    ap.add_argument('--timestamp-ms', type=int)
    ap.add_argument('--time-source', help="e.g. 'board_monotonic' - required for counting")
    ap.add_argument('--source-frame-id')
    ap.add_argument('--preview-size', default='auto',
                    help="'auto' (default) reads the real size; an explicit WxH is only an assertion")
    ap.add_argument('--model', default=os.path.join(ROOT, 'models', B.MODEL_NAME))
    ap.add_argument('--min-detection-confidence', type=float, default=0.3)
    ap.add_argument('--pad', type=float, default=0.0)
    ap.add_argument('--state', default=os.path.join(ROOT, 'logs', 'worker_state.json'),
                    help='persist the last timestamp/sequence per scope so a re-run sees duplicates and '
                         'clock regressions')
    ap.add_argument('--analysis-out')
    ap.add_argument('--action-type')
    ap.add_argument('--frame-id', type=int)
    ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)

    try:
        job = build_job(a)
    except (G.BindingError, ValueError, OSError) as e:
        print('ANALYZE FAIL: %s' % e)
        return 2

    fp = B.backend_fingerprint()
    if not fp.get('available'):
        print('ANALYZE FAIL: backend unavailable :: %s' % fp.get('error'))
        print('  hint: this step must run inside the module venv (see bin/03_verify_import.py)')
        return 3

    real = B.image_size(job['image'])
    if not real:
        print('ANALYZE FAIL: image not decodable :: %s' % job['image'])
        return 2
    if a.preview_size.lower() != 'auto':
        want = parse_size(a.preview_size)
        if want != tuple(real):
            print('ANALYZE FAIL: preview_size %dx%d disagrees with the image %dx%d :: the size must come from '
                  'the file' % (want[0], want[1], real[0], real[1]))
            return 2

    class Args:
        model = a.model
        min_detection_confidence = a.min_detection_confidence
        pad = a.pad
        max_gap_ms = 3000
        track_iou = 0.3
        out_ndjson = None
        latest_panel = None
        latest_analysis = None
        http = 0
        http_host = '127.0.0.1'

    import importlib.util
    spec = importlib.util.spec_from_file_location('pose_worker', os.path.join(HERE, '05_analyze_stream.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    worker = mod.Worker(Args)
    if worker.landmarker is None:
        print('ANALYZE FAIL: backend unavailable :: %s' % worker._model_error)
        return 3
    if worker.landmarker.model_sha256 != B.MODEL_SHA256:
        print('ANALYZE WARN: model sha256 %s != expected %s' % (worker.landmarker.model_sha256, B.MODEL_SHA256))

    # seed the previous record identity from disk, so a separate run still sees duplicates and time regressions
    gate = _gate_load(a.state)
    scope = None
    try:
        bound_probe = G.bind(job, tuple(int(v) for v in real))
        ident = bound_probe['identity']
        scope = '%s|%s|%s' % (ident['session_id'], ident['camera_id'], ident['processing_mode'])
        g = gate.get(scope) or {}
        pid0 = (job.get('person_id')
                or (bound_probe['detections'][0]['person_id'] if bound_probe['detections'] else None))
        if pid0 is not None:
            key = (ident['session_id'], ident['camera_id'], ident['processing_mode'], pid0)
            st = worker._state(key)
            st['last_ts'] = g.get('last_ts')
            st['last_seq'] = g.get('last_seq')
            st['last_sha'] = g.get('last_sha')
    except (G.BindingError, ValueError) as e:
        print('ANALYZE FAIL: %s' % e)
        return 2

    try:
        out = worker.process(job)
    except (G.BindingError, ValueError, KeyError, OSError) as e:
        print('ANALYZE FAIL: %s' % e)
        return 2

    rec = out['panel']
    try:
        C.validate(rec)
    except ValueError as e:
        print('ANALYZE FAIL: contract violation :: %s' % e)
        return 4

    if scope:
        g = gate.setdefault(scope, {})
        ts = rec['timing']['timestamp_ms']
        if rec['counting'] == 'enabled' or rec['counter_reason'] == 'duplicate_frame':
            g['last_ts'], g['last_seq'], g['last_sha'] = ts, rec['preview_sequence'], rec['jpeg_sha256']
        g.setdefault('person_id', rec['person']['person_id'])
        _gate_save(a.state, gate)

    out_path = a.out or os.path.join(ROOT, 'logs', 'panel.json')
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with io.open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

    if a.analysis_out or a.frame_id is not None or a.identity:
        fid = a.frame_id if a.frame_id is not None else rec['preview_sequence']
        if fid is None:
            print('ANALYZE FAIL: the analysis channel needs --frame-id or --preview-sequence')
            return 4
        try:
            amsg = A.to_analysis_v1(rec, rec['identity'], fid, person_id=a.person_id, image_path=rec['image'],
                                    action_type=a.action_type, panel_identity=True)
        except (ValueError, OSError) as e:
            print('ANALYZE FAIL: analysis adapter :: %s' % e)
            return 4
        apath = a.analysis_out or (out_path + '.analysis.json')
        with io.open(apath, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(amsg, f, ensure_ascii=False, indent=2)
        print('  analysis: schema=%s kind=%s frame_id=%d persons=%d -> %s'
              % (amsg['schema'], amsg['kind'], amsg['frame_id'], len(amsg['persons']), apath))

    print('ANALYZE OK: image=%s sha256=%s' % (os.path.basename(rec['image']), rec['jpeg_sha256'][:16]))
    print('  binding: input_record_id=%s mode=%s camera=%s' %
          (rec['input_record_id'], rec['processing_mode'], rec['camera_id']))
    print('  person: selection=%s person_id=%s box=%s' %
          (rec['person']['selection'], rec['person']['person_id'], rec['person']['box']))
    print('  pose: present=%s keypoints=%d clipped=%d inference_ms=%.1f' %
          (rec['pose_present'], len(rec['pose']['keypoints']), rec['pose']['keypoints_clipped'],
           rec['pose']['inference_ms']))
    print('  counting: %s reason=%s rep_count=%s phase=%s reset=%s' %
          (rec['counting'], rec['counter_reason'], rec['rep_count'], rec['action_phase'],
           rec['counter_reset_reason']))
    print('  record: %s' % out_path)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                    # noqa: BLE001
        traceback.print_exc()
        print('ANALYZE FAIL: unexpected error (see traceback above)')
        raise SystemExit(1)