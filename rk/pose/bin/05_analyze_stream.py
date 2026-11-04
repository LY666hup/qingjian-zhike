#!/usr/bin/env python3
"""Step 5 (v3) - resident pose worker bound to an upstream record.

One image in, one panel record out.  The job says exactly which capture it belongs to (the six identity fields
and an `input_record_id`), which YOLO record produced the boxes, and the hash of the JPEG itself; `pose/binding.py`
refuses the job if any of that does not line up with the file on disk.

What the worker keeps between frames (and never leaks between people, cameras, modes, boots, bitstreams or
source processors):

    states[scope + (person_id,)] = counter + last timestamp
    last[scope]                  = last selected box/person + the continuity chain (chain_pid, chain_len)
    records[scope]               = the last 16 record identities, so a re-delivery is a duplicate

  where scope = (session_id, boot_id, camera_id, processing_mode, fpga_bit_sha256, processor).

Rules, each covered by a test in `tests/` and a captured log in `samples/expected/`:

  * no detections             -> a legal pose_absent record (reason no_person), not an exception;
  * one detection, no id      -> that person, id taken from upstream (never a made-up "P1");
  * several detections, no id -> follow the previous box when it overlaps (selection single, tracking iou_follow),
                                 otherwise max_score_ambiguous with reason person_ambiguous;
  * several detections, id    -> that one, by id;
  * person change             -> the new person's own count, counter_reset_reason=person_switch;
  * session / boot change     -> that state is isolated and reported once as session_switch (detail);
  * camera / mode / bitstream / processor change -> that source is a different scope, so its tally starts at 0;
  * repeated record identity  -> duplicate_frame, counting disabled, rep_count null (anonymous records included);
  * identical bytes, new id   -> counted normally (a still person must not be rejected);
  * no timestamp, missing time_source or time going backwards -> counting disabled with the reason recorded.

Counting is a claim about a person over time, so it needs identity, not just geometry:

  * continuity=proven requires an upstream person_id that repeats frame after frame with no gap, no duplicate and
    no time regression.  Only then is counting enabled and score_eligible true;
  * a single detection without an id, or one followed by IoU, is continuity=assumed: the 33 landmarks are still
    emitted, but rep_count is null and score_eligible is false - an unproven person never writes a result;
  * any frame that does not verify the id (absent, anonymous, duplicate, broken time, missing pose) ends the chain,
    so the next frame must re-establish it: the first frame is assumed(1), the second proven(2).  An old
    person_id left in the state map never proves a chain by itself.

Output channels: the panel (qjzc.pose.panel.v1) is primary - written to `--out-ndjson`, to `--latest-panel`, and
served at `/panel.json`.  qjzc.analysis.v1 stays as a compatibility output for older Windows receivers and is
served at `/analysis.json`.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pose import analysis_v1 as A           # noqa: E402
from pose import backend as B               # noqa: E402
from pose import binding as G               # noqa: E402
from pose import contract as C              # noqa: E402
from pose.squat import SquatCounter         # noqa: E402

NO_PERSON_REASON = 'no_person'


def host_string():
    return '%s/%s/python%s' % (platform.system() or '?', platform.machine() or '?',
                               platform.python_version())


class Worker:
    def __init__(self, args):
        self.args = args
        self.landmarker = None
        self._model_error = None
        try:
            self.landmarker = B.PoseLandmarker(args.model, args.min_detection_confidence)
        except Exception as e:                     # noqa: BLE001
            self._model_error = '%s: %s' % (type(e).__name__, e)
        self.states = {}
        self.last = {}                             # full scope -> last chosen box + person + chain
        self.records = {}                          # full scope -> recently seen record identities
        self._seen_epochs = set()                  # (session, boot) pairs, so a new boot is reported once
        self._source_mode = {}                     # full scope minus mode -> last mode seen for that source
        self.frame_counter = 0
        self.latest_panel = None
        self.latest_analysis = None
        self.lock = threading.Lock()
        self.stats = {'jobs': 0, 'ok': 0, 'failed': 0, 'absent': 0, 'duplicate': 0,
                      'person_switch': 0, 'session_switch': 0, 'mode_switch': 0, 'paused': 0,
                      'no_model': 0}
        self.fingerprint = B.backend_fingerprint() if hasattr(B, 'backend_fingerprint') else {}

    # ---- state ---------------------------------------------------------
    def _state(self, key):
        st = self.states.get(key)
        if st is None:
            st = {'counter': SquatCounter(), 'reps': 0, 'phase': 'unknown',
                  'last_ts': None, 'last_seq': None, 'last_sha': None, 'frames': 0}
            self.states[key] = st
        return st

    def _scope(self, identity):
        """The full capture identity: every field that makes two frames the same source.

        Counting state, continuity chains and record history are all keyed by this tuple, so a new boot, a
        reloaded bitstream or a different source processor can never inherit another source's counters.
        """
        return (identity['session_id'], identity['boot_id'], identity['camera_id'],
                identity['processing_mode'], identity['fpga_bit_sha256'], identity['processor'])

    def _switches(self, scope):
        """Return the isolation event caused by this job, if any.

        State itself is keyed by the full scope, so interleaved sources never share counters.  A switch is only
        reported when *that source* really changed: alternating A/B frames is not a mode switch, and a mode change
        in A does not touch B.  A session/boot epoch is remembered so the first frame of a new epoch is reported
        once; a new bitstream or processor is simply a new scope (no previous state, so nothing to switch from).
        """
        session, boot, camera, mode = scope[0], scope[1], scope[2], scope[3]
        source = (session, boot, camera, scope[4], scope[5])       # everything except mode
        epoch = (session, boot)
        fresh_epoch = epoch not in self._seen_epochs
        self._seen_epochs.add(epoch)
        prev_mode = self._source_mode.get(source)
        self._source_mode[source] = mode
        if fresh_epoch and len(self._seen_epochs) > 1:
            self.stats['session_switch'] += 1
            return 'session_switch'
        if prev_mode is not None and prev_mode != mode:
            self.stats['mode_switch'] += 1
            return 'mode_switch'
        return 'none'

    # ---- person selection ----------------------------------------------
    def _choose(self, dets, requested, scope):
        """Return (detection, selection, reason, tracking).

        `selection` uses the vocabulary the Windows Round-2 receiver accepts.  IoU following is real but it is not
        an identity: it is reported as selection=single with tracking.method=iou_follow, and it never counts.
        """
        if not dets:
            return None, 'no_person', NO_PERSON_REASON, {'method': 'none', 'iou': None}
        if requested:
            matches = [d for d in dets if d['person_id'] == requested]
            if not matches:
                return None, 'person_id_not_found', 'person_id_not_found', {'method': 'none', 'iou': None}
            m = matches[0]
            return m, 'by_id', 'ok', {'method': 'upstream_id' if m['person_id'] else 'none', 'iou': None}
        if len(dets) == 1:
            d = dets[0]
            return d, 'single', 'ok', {'method': 'upstream_id' if d['person_id'] else 'none', 'iou': None}
        prev = self.last.get(scope)
        if prev is not None and prev.get('box') is not None:
            best, best_iou = None, 0.0
            for d in dets:
                iou = _iou(prev['box'], d['box'])
                if iou > best_iou:
                    best, best_iou = d, iou
            if best is not None and best_iou >= self.args.track_iou:
                # the overlap chose WHICH detection; the identity still comes from upstream, or not at all
                if best['person_id']:
                    return best, 'single', 'ok', {'method': 'upstream_id', 'iou': round(best_iou, 4)}
                return best, 'single', 'ok', {'method': 'iou_follow', 'iou': round(best_iou, 4)}
        return None, 'max_score_ambiguous', 'person_ambiguous', {'method': 'none', 'iou': None}

    # ---- one job -------------------------------------------------------
    def process(self, job):
        self.stats['jobs'] += 1
        w, h = B.image_size(job['image'])
        if not w or not h:
            raise G.BindingError('image not decodable: %s' % job['image'])
        bound = G.bind(job, (int(w), int(h)))
        ident = bound['identity']
        scope = self._scope(ident)
        reset = self._switches(scope)

        dets = bound['detections']
        chosen, selection, sel_reason, tracking = self._choose(dets, job.get('person_id'), scope)

        # the person is known only now, so a switch can only be detected here
        pid = chosen['person_id'] if chosen else None
        key = scope + (pid,)
        prev_scope = self.last.get(scope, {})
        prev_pid = prev_scope.get('person_id')
        chain_pid = prev_scope.get('chain_pid')
        prev_continuity = prev_scope.get('continuity')
        chain_len = int(prev_scope.get('chain_len', 0))
        moved = pid is not None and prev_pid is not None and pid != prev_pid
        if moved:
            self.stats['person_switch'] += 1
            reset = 'person_switch'

        pose_ok = False
        res = {}
        if chosen is not None and self.landmarker is not None:
            res = self.landmarker.infer(bound['image'], chosen['box'], (int(w), int(h)), self.args.pad)
            pose_ok = bool(res.get('ok'))
        kps = (res.get('keypoints') or []) if pose_ok else []

        st = self._state(key) if pid is not None else None
        ts, ts_src = bound['timestamp_ms'], bound['time_source']

        # Record identity is checked per *source*, before any person is involved: an anonymous delivery that is
        # repeated must be refused just like one with a person_id.  Recent record ids are remembered (not only the
        # immediately previous one) because a spool re-delivery may arrive after another frame.
        record_id = bound['input_record_id']
        seq, pair = bound['preview_sequence'], (bound['preview_sequence'], ts)
        seen = self.records.get(scope)
        duplicate = bool(seen is not None and (record_id in seen['ids'] or (
            seq is not None and ts is not None and pair in seen['pairs'])))
        same_bytes = bool(seen is not None and seen['sha'] == bound['jpeg_sha256'] and not duplicate)
        hist = seen or {'ids': [], 'pairs': [], 'sha': None}
        if not duplicate:
            self.records[scope] = {'ids': (hist['ids'] + [record_id])[-16:],
                                   'pairs': (hist['pairs'] + [pair])[-16:] if seq is not None and ts is not None
                                   else hist['pairs'],
                                   'sha': bound['jpeg_sha256']}

        time_missing = bool(ts is None or ts_src is None)
        regression = bool(st and ts is not None and st['last_ts'] is not None and ts <= st['last_ts'])
        gap = bool(st and ts is not None and st['last_ts'] is not None
                   and ts - st['last_ts'] > self.args.max_gap_ms)
        broken_by_time = time_missing or regression or gap

        # Continuity is a claim about the person, not about the counter: it decides whether a number is a result.
        # chain_len is the number of consecutive frames in which this id was actually verified.  Any frame that
        # does not verify the id (absent, anonymous, duplicate, broken time, missing pose) ends the chain.
        if chosen is None:
            continuity = 'broken' if (chain_pid is not None or prev_pid is not None) else 'unknown'
            chain_pid, chain_len = None, 0
        elif tracking['method'] != 'upstream_id':
            continuity = 'assumed'
            chain_pid, chain_len = None, 0
        elif duplicate or broken_by_time or not pose_ok:
            continuity = 'broken'
            chain_pid, chain_len = None, 0
        elif chain_pid == pid and prev_continuity in ('proven', 'assumed'):
            continuity = 'proven'
            chain_len += 1
        else:
            continuity = 'assumed'
            chain_pid, chain_len = pid, 1
        reported_len = int(prev_scope.get('chain_len', 0)) if duplicate else chain_len

        rep, phase = None, 'unknown'
        counting, reason, note = 'disabled', None, None
        if chosen is None:
            reason = sel_reason
        elif self.landmarker is None:
            reason = 'model_unavailable'
            self.stats['no_model'] += 1
        elif not pose_ok:
            reason = res.get('reason', 'pose_absent')
        elif duplicate:
            reason = 'duplicate_frame'
            self.stats['duplicate'] += 1
        elif bound['preview_sequence'] is None and bound['source_frame_id'] is None:
            reason = 'identity_missing'
        elif time_missing:
            reason = 'no_time_base'
        elif regression:
            reason = 'time_regression'
        elif gap:
            reason = 'gap_too_large'
        elif continuity == 'assumed':
            reason = 'person_id_unverified' if tracking['method'] != 'upstream_id' else 'continuity_unproven'
        elif continuity != 'proven':
            reason = 'continuity_broken'
        else:
            counting, reason = 'enabled', 'ok'
            lm = [{'i': i, 'x': p[0], 'y': p[1], 'visibility': p[2]} for i, p in enumerate(kps)]
            d = st['counter'].update(lm, ts)
            st['reps'] = int(d.get('reps', 0))
            st['phase'] = d.get('phase', 'unknown')
            # a low-visibility frame pauses the squat FSM but does not invalidate who the person is
            note = None if d.get('reason') in (None, 'ok') else d.get('reason')
            rep, phase = st['reps'], st['phase']

        if st is not None and not duplicate:
            st['frames'] += 1
            st['last_sha'] = bound['jpeg_sha256']
            if ts is not None and (st['last_ts'] is None or ts > st['last_ts']):
                st['last_ts'], st['last_seq'] = ts, bound['preview_sequence']

        if counting != 'enabled':
            self.stats['paused'] += 1
        if chosen is None:
            self.stats['absent'] += 1

        # keep the chain for the next frame; an absent or unverified frame ends it
        self.last[scope] = {'person_id': pid if pid is not None else prev_pid,
                            'box': list(chosen['box']) if chosen else prev_scope.get('box'),
                            'continuity': continuity,
                            'chain_pid': chain_pid,
                            'chain_len': chain_len}

        self.frame_counter += 1
        fid = bound['preview_sequence'] if bound['preview_sequence'] is not None else self.frame_counter
        reset_reason = 'person_switch' if reset == 'person_switch' else 'none'

        panel = {
            'schema': C.SCHEMA,
            'state': 'pose_ready' if pose_ok else 'pose_absent',
            'identity': ident,
            'session_id': ident['session_id'],
            'boot_id': ident['boot_id'],
            'camera_id': ident['camera_id'],
            'processing_mode': ident['processing_mode'],
            'fpga_bit_sha256': ident['fpga_bit_sha256'],
            'processor': ident['processor'],
            'input_record_id': bound['input_record_id'],
            'capture_group_id': bound['capture_group_id'],
            'preview_sequence': bound['preview_sequence'],
            'source_frame_id': bound['source_frame_id'],
            'jpeg_sha256': bound['jpeg_sha256'],
            'image': bound['image'],
            'preview_size': [int(w), int(h)],
            'person': {'key': None if pid is None else ('upstream:%s' % pid),
                       'person_id': pid,
                       'selection': selection,
                       'mode': 'selected',
                       'box': list(chosen['box']) if chosen else None,
                       'score': None if not chosen else chosen['score'],
                       'candidates': len(dets),
                       'reason': None if chosen else sel_reason,
                       'tracking': {'method': tracking['method'], 'iou': tracking['iou'],
                                    'frames_with_id': reported_len}},
            'person_selection': selection,
            'pose': {'layout': B.LAYOUT, 'coordinate_space': B.COORDINATE_SPACE,
                     'keypoints': kps,
                     'keypoints_clipped': int(res.get('keypoints_clipped', 0)) if pose_ok else 0,
                     'pose_score': float(res.get('pose_score', 0.0)) if pose_ok else 0.0,
                     'inference_ms': float(res.get('inference_ms', 0.0)) if pose_ok else 0.0,
                     'crop': res.get('crop') if pose_ok else None,
                     'crop_source': res.get('crop_source', 'none') if pose_ok else 'none'},
            'pose_present': pose_ok,
            'pose_available': pose_ok,
            'rep_count': rep,
            'action_phase': phase,
            'counting': counting,
            'counter_reason': reason,
            'counter_note': note,
            'counter_reset_reason': reset_reason,
            'counter_reset_detail': reset,
            'continuity': continuity,
            'score_eligible': counting == 'enabled',
            'timing': {'timestamp_ms': ts, 'time_source': ts_src},
            'geometry': bound.get('geometry'),
            'backend': C.BACKEND,
            'inference_device': C.INFERENCE_DEVICE,
            'inference_host': host_string(),
            'model': {'name': B.MODEL_NAME, 'sha256': B.MODEL_SHA256,
                      'runtime': self.fingerprint.get('mediapipe'),
                      'numpy': self.fingerprint.get('numpy'),
                      'opencv': self.fingerprint.get('opencv')},
        }
        if not pose_ok:
            panel['pose']['absent_reason'] = 'no_person' if chosen is None else res.get('reason')
        C.validate(panel)

        analysis = A.to_analysis_v1(panel, ident, fid, image_path=bound['image'],
                                    action_type=job.get('action'), panel_identity=True)
        self.stats['ok'] += 1
        return {'ok': True, 'panel': panel, 'analysis': analysis,
                'flags': {'same_bytes_as_previous': same_bytes, 'person_switch': moved,
                          'selection': selection, 'scope_reset': reset, 'continuity': continuity}}

    # ---- plumbing ------------------------------------------------------
    def emit(self, line):
        text = json.dumps(line, ensure_ascii=False)
        print(text, flush=True)
        if self.args.out_ndjson:
            with io.open(self.args.out_ndjson, 'a', encoding='utf-8', newline='\n') as f:
                f.write(text + '\n')
        if line.get('ok') and line.get('panel'):
            with self.lock:
                self.latest_panel = line['panel']
                self.latest_analysis = line.get('analysis')
            if self.args.latest_panel:
                _atomic_json(self.args.latest_panel, line['panel'])
            if self.args.latest_analysis:
                _atomic_json(self.args.latest_analysis, line.get('analysis'))

    def run_once(self, jobs):
        for job in jobs:
            try:
                out = self.process(job)
            except (G.BindingError, ValueError, KeyError, OSError) as e:
                self.stats['failed'] += 1
                out = {'ok': False, 'error': str(e),
                       'image': job.get('image') if isinstance(job, dict) else None}
            self.emit(out)
        return self.stats

    def health(self):
        return {'schema': 'qjzc.pose.worker.v1', 'state': 'ready' if self.landmarker else 'degraded',
                'model_loaded': self.landmarker is not None, 'model_error': self._model_error,
                'model_sha256': B.MODEL_SHA256, 'backend': C.BACKEND,
                'inference_device': C.INFERENCE_DEVICE, 'inference_host': host_string(),
                'pid': os.getpid(), 'stats': dict(self.stats),
                'note': 'pose worker health only; the video capture status is owned by Ly'}

    def serve(self):
        """Local HTTP.  Default bind is 127.0.0.1: exposing a port is a deployment decision, not a default."""
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                payload = json.dumps(obj, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                path = self.path.split('?')[0]
                if path in ('/health', '/health.json'):
                    return self._send(worker.health())
                if path in ('/panel', '/panel.json'):
                    with worker.lock:
                        body = worker.latest_panel
                    if body is None:
                        return self._send({'schema': 'qjzc.pose.panel.v1', 'state': 'no_frame_yet',
                                           'error': 'no frame processed yet'}, 503)
                    return self._send(body)
                if path in ('/analysis', '/analysis.json'):
                    with worker.lock:
                        body = worker.latest_analysis
                    if body is None:
                        return self._send({'error': 'no analysis yet'}, 503)
                    return self._send(body)
                if path in ('/status', '/status.json'):
                    return self._send(
                        {'error': 'capture status belongs to the video service, not the pose worker',
                         'see': '/health.json'}, 404)
                return self._send({'error': 'not found',
                                   'paths': ['/panel.json', '/analysis.json', '/health.json']}, 404)

        srv = ThreadingHTTPServer((self.args.http_host, self.args.http), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        print(json.dumps({'http': 'http://%s:%d/panel.json' % (self.args.http_host, self.args.http),
                          'bind': self.args.http_host}), flush=True)
        return srv


def _atomic_json(path, obj):
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def read_spool(directory, done_dir, limit=None):
    jobs = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith('.json'):
            continue
        path = os.path.join(directory, name)
        try:
            with io.open(path, encoding='utf-8') as f:
                job = json.load(f)
        except (ValueError, OSError) as e:
            yield {'image': None, 'bad_job': str(e), 'path': path}
            continue
        job.setdefault('_job_path', path)
        jobs.append(job)
        if limit and len(jobs) >= limit:
            break
    for j in jobs:
        yield j
        if done_dir:
            os.makedirs(done_dir, exist_ok=True)
            try:
                os.replace(j['_job_path'], os.path.join(done_dir, os.path.basename(j['_job_path'])))
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(description='resident pose worker bound to an upstream capture record')
    ap.add_argument('--jobs', help='spool directory of job JSON files')
    ap.add_argument('--done', help='where processed jobs are moved (default: <jobs>/done)')
    ap.add_argument('--stdin', action='store_true', help='read jobs as NDJSON lines on stdin')
    ap.add_argument('--model', default=os.path.join(ROOT, 'models', B.MODEL_NAME))
    ap.add_argument('--out-ndjson', help='append one line per job here')
    ap.add_argument('--latest-panel', help='atomically keep the newest panel record here (primary channel)')
    ap.add_argument('--latest-analysis', help='atomically keep the newest qjzc.analysis.v1 message here')
    ap.add_argument('--http', type=int, default=0, help='serve /panel.json, /analysis.json, /health.json')
    ap.add_argument('--http-host', default='127.0.0.1',
                    help='bind address; keep 127.0.0.1 unless a reverse proxy is in front of it')
    ap.add_argument('--pad', type=float, default=0.0)
    ap.add_argument('--min-detection-confidence', type=float, default=0.3)
    ap.add_argument('--max-gap-ms', type=int, default=3000)
    ap.add_argument('--track-iou', type=float, default=0.3,
                    help='overlap needed to follow the same person when no id is supplied')
    ap.add_argument('--once', action='store_true', help='drain the current jobs and exit')
    ap.add_argument('--idle-exit-s', type=float, default=0.0, help='exit after N idle seconds (0 = never)')
    a = ap.parse_args(argv)

    if not a.jobs and not a.stdin:
        print('STREAM FAIL: need --jobs DIR or --stdin')
        return 2
    if not (0.0 <= a.track_iou <= 1.0):
        print('STREAM FAIL: --track-iou must be within [0,1]')
        return 2
    worker = Worker(a)
    if worker.landmarker is None:
        print(json.dumps({'warning': 'model unavailable, every job will be refused',
                          'error': worker._model_error}), flush=True)
    if a.http:
        worker.serve()
    idle_since = None
    while True:
        if a.stdin:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                job = json.loads(line)
            except ValueError as e:
                worker.emit({'ok': False, 'error': 'bad json: %s' % e})
                continue
            worker.run_once([job])
            continue
        batch = list(read_spool(a.jobs, a.done or os.path.join(a.jobs, 'done')))
        if batch:
            idle_since = None
            worker.run_once(batch)
        else:
            if a.once:
                break
            if a.idle_exit_s:
                idle_since = idle_since or time.time()
                if time.time() - idle_since >= a.idle_exit_s:
                    break
            time.sleep(0.2)
    worker.emit({'ok': True, 'final': True, 'stats': worker.stats, 'health': worker.health()})
    return 0 if worker.stats['failed'] == 0 else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                        # noqa: BLE001
        traceback.print_exc()
        print('STREAM FAIL: unexpected error (see traceback above)')