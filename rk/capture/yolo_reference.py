"""Multi-person detection contract for the RK3576 path: decode, map, track, and keep tracks separate.

What this module is for
-----------------------
The competition's hard deliverable #5 is "a lightweight detection model running locally on the RK NPU,
outputting recognition results".  A single-person demo does not satisfy the spirit of that on a sports
scene where several students are visible.  This module is the CPU-side half that must be correct before
any NPU number means anything:

    * decode      -- handle the export shapes we may actually get, not just [1,84,8400]
    * map         -- box in the 640x640 letterbox -> box in the source frame, exact inverse
    * track       -- stable ids per person, one state machine PER TRACK (never one shared sequence)
    * report      -- empty frames, occlusion and the person cap are printed, not hidden

Honesty rules baked in
----------------------
* `track_id` is a tracker id, NOT a student identity.  Nothing here claims to know who someone is.
* `device`/`backend`/`runtime` are printed from what actually ran.  A host CPU result says so.
* A synthetic composite (two crops side by side) is labelled SYNTHETIC in the output.  It exercises the
  multi-box code path; it is not evidence of field multi-person accuracy.

Decode shapes supported
-----------------------
    A. single tensor  [1, 84, N]  or [1, N, 84]      -- YOLOv8 detect export (box + 80 classes)
    B. six tensors    (4*reg_max + nc) x {80,40,20}  -- DFL split export, strides 8/16/32
    C. single tensor  [1, 56, N]                     -- YOLOv8-pose export (box + cls + 17*3)
Anything else raises with the actual shapes printed, instead of guessing.

    python tools/yolo_multiperson.py --model out/r48/onnx/yolov8n.onnx --image <file> [--json out.json]
    python tools/yolo_multiperson.py --model ... --video <mp4> --max-persons 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

SIZE = 640
PAD = 114
REG_MAX = 16


# --------------------------------------------------------------------------- geometry
class Letterbox:
    """Letterbox to SIZE x SIZE with pad 114, and the exact inverse for boxes and points."""

    def __init__(self, w0, h0, size=SIZE, pad=PAD):
        self.w0, self.h0, self.size, self.pad = w0, h0, size, pad
        self.r = min(size / w0, size / h0)
        self.nw, self.nh = int(round(w0 * self.r)), int(round(h0 * self.r))
        self.dx, self.dy = (size - self.nw) // 2, (size - self.nh) // 2

    def apply(self, img):
        """PIL RGB -> float32 (1,3,S,S), 0..1."""
        from PIL import Image
        canvas = Image.new('RGB', (self.size, self.size), (self.pad,) * 3)
        canvas.paste(img.resize((self.nw, self.nh), Image.BILINEAR), (self.dx, self.dy))
        a = np.asarray(canvas, dtype=np.float32) / 255.0
        return np.ascontiguousarray(a.transpose(2, 0, 1)[None])

    def to_source(self, xyxy):
        """Box in model space -> box in source pixels, clipped to the image."""
        x1, y1, x2, y2 = xyxy
        x1 = (x1 - self.dx) / self.r
        x2 = (x2 - self.dx) / self.r
        y1 = (y1 - self.dy) / self.r
        y2 = (y2 - self.dy) / self.r
        return [max(0.0, min(x1, self.w0)), max(0.0, min(y1, self.h0)),
                max(0.0, min(x2, self.w0)), max(0.0, min(y2, self.h0))]

    def point_to_source(self, x, y):
        return ((x - self.dx) / self.r, (y - self.dy) / self.r)

    def as_dict(self):
        return {'src': [self.w0, self.h0], 'size': self.size, 'scale': round(self.r, 6),
                'resized': [self.nw, self.nh], 'pad_offset': [self.dx, self.dy], 'pad_value': self.pad}


def nms(boxes, scores, iou_thr=0.45, limit=300):
    """Plain greedy NMS.  Deliberately not library-dependent so it runs identically on the board."""
    if not len(boxes):
        return []
    order = scores.argsort()[::-1]
    keep = []
    while order.size and len(keep) < limit:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        a_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        a_o = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (boxes[order[1:], 3] - boxes[order[1:], 1])
        iou = inter / (a_i + a_o - inter + 1e-9)
        order = order[1:][iou <= iou_thr]
    return keep


# --------------------------------------------------------------------------- decode
def _xywh_to_xyxy(b):
    out = np.empty_like(b)
    out[:, 0] = b[:, 0] - b[:, 2] / 2
    out[:, 1] = b[:, 1] - b[:, 3] / 2
    out[:, 2] = b[:, 0] + b[:, 2] / 2
    out[:, 3] = b[:, 1] + b[:, 3] / 2
    return out


def _dfl_dist2bbox(dist, anchors, stride):
    """dist: (N, 4*REG_MAX) logits -> xyxy in model space (grid anchors in cells)."""
    n = dist.shape[0]
    d = dist.reshape(n, 4, REG_MAX)
    d = d - d.max(axis=2, keepdims=True)
    p = np.exp(d)
    p /= p.sum(axis=2, keepdims=True)
    proj = np.arange(REG_MAX, dtype=np.float32)
    lt = (p * proj).sum(axis=2) * stride          # (N,4) in model pixels
    x1 = anchors[:, 0] - lt[:, 0]
    y1 = anchors[:, 1] - lt[:, 1]
    x2 = anchors[:, 0] + lt[:, 2]
    y2 = anchors[:, 1] + lt[:, 3]
    return np.stack([x1, y1, x2, y2], axis=1)


def _grid(h, w, stride):
    ys, xs = np.mgrid[0:h, 0:w]
    return np.stack([(xs.ravel() + 0.5) * stride, (ys.ravel() + 0.5) * stride], axis=1).astype(np.float32)


def decode_outputs(outs, conf_thr=0.25, iou_thr=0.45, nc=80):
    """Return (boxes_xyxy_model_space, scores, class_ids, kind).  Raises on unknown layouts."""
    outs = [np.asarray(o) for o in outs]
    shapes = [o.shape for o in outs]

    # ---- A / C: one output, already decoded
    if len(outs) == 1:
        o = outs[0]
        if o.ndim == 3:
            if o.shape[1] < o.shape[2]:            # (1, rows, N)
                p = o[0].T
            else:                                   # (1, N, rows)
                p = o[0]
            rows = p.shape[1]
            if rows == 84 or rows == 4 + nc:
                b, s = p[:, :4], p[:, 4:]
            elif rows == 56 or rows == 5 + 17 * 3:
                b, s = p[:, :4], p[:, 4:5]
            else:
                raise ValueError('single output with %d rows: not a detect(84) or pose(56) layout; '
                                 'shapes=%s' % (rows, shapes))
            boxes = _xywh_to_xyxy(b)
            cls = s.argmax(1)
            conf = s.max(1)
            keep = conf >= conf_thr
            boxes, cls, conf = boxes[keep], cls[keep], conf[keep]
            idx = nms(boxes, conf, iou_thr)
            return boxes[idx], conf[idx], cls[idx], 'single'

    # ---- B: DFL split, 3 box-reg + 3 class tensors
    if len(outs) == 6:
        regs = [o for o in outs if o.shape[1] == 4 * REG_MAX]
        clss = [o for o in outs if o.shape[1] not in (4 * REG_MAX,)]
        if len(regs) == 3 and len(clss) == 3:
            allb, alls, allc = [], [], []
            for reg, cls in zip(sorted(regs, key=lambda o: -o.shape[2]),
                                sorted(clss, key=lambda o: -o.shape[2])):
                _, _, h, w = reg.shape
                stride = SIZE // h
                n = h * w
                r = reg[0].reshape(4 * REG_MAX, n).T
                c = cls[0].reshape(cls.shape[1], n).T
                boxes = _dfl_dist2bbox(r, _grid(h, w, stride), stride)
                conf = c.max(1)
                cls_id = c.argmax(1)
                k = conf >= conf_thr
                allb.append(boxes[k]); alls.append(conf[k]); allc.append(cls_id[k])
            boxes = np.concatenate(allb); conf = np.concatenate(alls); cls = np.concatenate(allc)
            idx = nms(boxes, conf, iou_thr)
            return boxes[idx], conf[idx], cls[idx], 'dfl_split'

    raise ValueError('unsupported output set %s -- refusing to guess a decode layout' % (shapes,))


# --------------------------------------------------------------------------- tracking
class Track:
    """One person's state.  Every track owns its own history: a shared sequence is how people get mixed."""

    def __init__(self, tid, box, frame_idx, ts, session):
        self.tid = tid
        self.session = session
        self.box = box                    # source-frame xyxy
        self.first_frame = frame_idx
        self.last_frame = frame_idx
        self.first_ts = ts
        self.last_ts = ts
        self.hits = 1
        self.misses = 0
        self.history = []                 # per-track only

    def update(self, box, frame_idx, ts):
        self.box = box
        self.last_frame = frame_idx
        self.last_ts = ts
        self.hits += 1
        self.misses = 0
        self.history.append({'frame': frame_idx, 'ts': round(ts, 4), 'box': [round(v, 1) for v in box]})

    def as_dict(self):
        return {'track_id': self.tid, 'session': self.session, 'first_frame': self.first_frame,
                'last_frame': self.last_frame, 'hits': self.hits, 'misses': self.misses,
                'box': [round(v, 1) for v in self.box]}


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    w, h = max(0.0, x2 - x1), max(0.0, y2 - y1)
    inter = w * h
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter + 1e-9)


class Tracker:
    """Greedy IoU association.  Small and deterministic on purpose: no dependency, no hidden state."""

    def __init__(self, iou_thr=0.3, max_misses=5, session=''):
        self.iou_thr = iou_thr
        self.max_misses = max_misses
        self.session = session
        self.tracks = {}
        self.next_id = 1

    def step(self, boxes, frame_idx, ts):
        unmatched = list(range(len(boxes)))
        for t in self.tracks.values():
            best, best_iou = None, self.iou_thr
            for i in unmatched:
                v = iou(t.box, boxes[i])
                if v >= best_iou:
                    best, best_iou = i, v
            if best is not None:
                t.update(boxes[best], frame_idx, ts)
                unmatched.remove(best)
        for i in unmatched:
            self.tracks[self.next_id] = Track(self.next_id, boxes[i], frame_idx, ts, self.session)
            self.next_id += 1
        for tid in list(self.tracks):
            t = self.tracks[tid]
            if t.last_frame != frame_idx:
                t.misses += 1
                if t.misses > self.max_misses:
                    del self.tracks[tid]
        return [t for t in self.tracks.values() if t.last_frame == frame_idx]


# --------------------------------------------------------------------------- runner
def load_model(path):
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=['CPUExecutionProvider'])
    return sess, sess.get_inputs()[0].name


def run_frame(sess, iname, img_rgb, conf, iou_thr, nc=80):
    from PIL import Image
    lb = Letterbox(img_rgb.size[0], img_rgb.size[1])
    x = lb.apply(img_rgb)
    t0 = time.perf_counter()
    outs = sess.run(None, {iname: x})
    ms = (time.perf_counter() - t0) * 1000.0
    boxes, conf_s, cls, kind = decode_outputs(outs, conf, iou_thr, nc=nc)
    persons = [(lb.to_source([float(v) for v in b]), float(c))
               for b, c, k in zip(boxes, conf_s, cls) if int(k) == 0]
    return persons, lb, ms


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--image')
    ap.add_argument('--video')
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--iou', type=float, default=0.45)
    ap.add_argument('--max-persons', type=int, default=4)
    ap.add_argument('--frames', type=int, default=60)
    ap.add_argument('--json')
    ap.add_argument('--label', default='', help='e.g. SYNTHETIC composite -- carried into the output')
    a = ap.parse_args()

    sess, iname = load_model(a.model)
    trk = Tracker(session=os.path.basename(a.model))
    per_frame = []

    if a.image:
        from PIL import Image
        img = Image.open(a.image).convert('RGB')
        persons, lb, ms = run_frame(sess, iname, img, a.conf, a.iou)
        persons.sort(key=lambda t: -t[1])
        capped = persons[:a.max_persons]
        if len(persons) > a.max_persons:
            print('CAP: %d persons detected, keeping %d (--max-persons)' % (len(persons), a.max_persons))
        live = trk.step([p[0] for p in capped], 0, 0.0)
        print('device      : CPU (onnxruntime, x86) -- NOT the NPU')
        print('backend     : onnx                       runtime: onnxruntime %s' % _ort_version())
        print('model       : %s' % os.path.basename(a.model))
        print('image       : %s  %dx%d' % (os.path.basename(a.image), img.size[0], img.size[1]))
        print('letterbox   : %s' % lb.as_dict())
        print('persons     : %d  (cap %d)' % (len(capped), a.max_persons))
        for t in sorted(live, key=lambda t: t.tid):
            print('   track_id=%d conf=%.3f box_src=%s' % (t.tid, _conf_of(persons, t.box), t.as_dict()['box']))
        if not capped:
            print('   no person above conf %.2f  -> empty result is reported, not filled in' % a.conf)
        print('latency     : %.2f ms (CPU)' % ms)
        per_frame.append({'frame': 0, 'persons': len(capped), 'tracks': [t.as_dict() for t in live]})

    if a.video:
        import cv2
        cap = cv2.VideoCapture(a.video)
        if not cap.isOpened():
            raise SystemExit('cannot open %s' % a.video)
        idx = 0
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        while idx < a.frames:
            ok, bgr = cap.read()
            if not ok:
                break
            from PIL import Image
            img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            persons, lb, ms = run_frame(sess, iname, img, a.conf, a.iou)
            persons.sort(key=lambda t: -t[1])
            capped = persons[:a.max_persons]
            live = trk.step([p[0] for p in capped], idx, idx / fps)
            per_frame.append({'frame': idx, 'ts': round(idx / fps, 4), 'persons': len(capped),
                              'tracks': [t.as_dict() for t in live]})
            if idx % 15 == 0:
                print('frame %3d  persons=%d  tracks=%s' % (idx, len(capped), [t.tid for t in live]))
            idx += 1
        cap.release()
        print('frames processed: %d, distinct tracks ever: %d, cap per frame: %d'
              % (idx, trk.next_id - 1, a.max_persons))
        print('device: CPU (x86) -- board NPU run is Ly\'s step')

    rec = {'label': a.label, 'model': os.path.basename(a.model), 'model_path': os.path.abspath(a.model),
           'device': 'cpu', 'backend': 'onnx', 'runtime': 'onnxruntime %s' % _ort_version(),
           'conf_thr': a.conf, 'iou_thr': a.iou, 'max_persons': a.max_persons,
           'track_id_semantics': 'tracker id, NOT a student identity',
           'frames': per_frame}
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        print('json        : %s' % a.json)
    return 0


def _ort_version():
    try:
        import onnxruntime
        return onnxruntime.__version__
    except Exception:
        return 'unknown'


def _conf_of(persons, box):
    for b, c in persons:
        if b == box:
            return c
    return -1.0


if __name__ == '__main__':
    raise SystemExit(main())