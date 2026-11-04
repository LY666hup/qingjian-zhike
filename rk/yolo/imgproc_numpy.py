"""uint8 RGB imaging with zero third-party dependencies -- resize, letterbox, inverse map.

Why this file exists
--------------------
The board has no Pillow, and the previous board scripts quietly fell into a PIL resize branch for any
image that was not already 640x640.  "PPM needs no Pillow" was therefore only half true.  This module
removes the dependency instead of documenting around it:

    resize_bilinear   fixed-point bilinear that follows OpenCV's INTER_LINEAR_8u arithmetic
    letterbox         scale + pad with an explicit pad value, returns the exact inverse mapping
    resize_pil        reference path, used only in tests when Pillow happens to exist

OpenCV's INTER_LINEAR for 8-bit uses 11-bit coefficients (INTER_RESIZE_COEF_BITS = 11,
INTER_RESIZE_COEF_SCALE = 2048).  The source coordinate is (dst + 0.5) * scale - 0.5, and the final
combine is a two-stage fixed-point accumulate with rounding, which this module reproduces as

    (Σ v_i * a_i * b_i + (1 << 21)) >> 22

Anything else (PIL's triangle filter, area averaging, numpy's own interpolation) samples different
source positions or rounds differently, so mixing them changes pixels by 1-3 levels and, near strong
edges, visibly moves a box.  The test module measures that difference instead of assuming zero.
"""
from __future__ import annotations

import sys

import numpy as np

COEF_BITS = 11
COEF_SCALE = 1 << COEF_BITS          # 2048
ROUND = 1 << (2 * COEF_BITS - 1)     # 1 << 21


def _coeffs(dst_len, src_len):
    """1-D source indices and 11-bit weights, exactly as OpenCV builds them."""
    scale = src_len / dst_len
    d = np.arange(dst_len, dtype=np.float64)
    s = (d + 0.5) * scale - 0.5
    s = np.maximum(s, 0.0)
    s0 = np.floor(s).astype(np.int64)
    s0 = np.minimum(s0, src_len - 1)
    s1 = np.minimum(s0 + 1, src_len - 1)
    frac = s - s0
    b1 = np.rint(frac * COEF_SCALE).astype(np.int64)      # cvRound(alpha * 2048)
    b1 = np.clip(b1, 0, COEF_SCALE)
    b0 = COEF_SCALE - b1
    return s0, s1, b0, b1


def resize_bilinear(img, out_w, out_h, band=64):
    """img: (H,W,3|4) uint8 -> (out_h,out_w,C) uint8.  Bit-comparable with cv2.resize(INTER_LINEAR).

    Banded fused resize with a flat gather (see the module docstring history in the repo: the middle
    axis gather was the bottleneck, not the arithmetic).  int32 is safe: a horizontal result is at
    most 255*2048 = 522,240 and the vertical sum uses b0 + b1 == 2048 -> at most 1.070e9.
    """
    a = np.asarray(img)
    if a.dtype != np.uint8:
        raise TypeError('expected uint8, got %s' % a.dtype)
    if a.ndim == 2:
        a = a[:, :, None]
    h, w, c = a.shape
    if (w, h) == (out_w, out_h):
        return a.copy()

    x0, x1, xb0, xb1 = _coeffs(out_w, w)
    y0, y1, yb0, yb1 = _coeffs(out_h, h)

    # flat gather indices for the three (or four) channels, and matching weight vectors
    chan = np.arange(c, dtype=np.int64)
    ix0 = (x0[:, None] * c + chan[None, :]).ravel()
    ix1 = (x1[:, None] * c + chan[None, :]).ravel()
    wb0 = np.repeat(xb0, c).astype(np.int32)[None, :]
    wb1 = np.repeat(xb1, c).astype(np.int32)[None, :]
    width = out_w * c

    out = np.empty((out_h, width), dtype=np.int32)
    for lo in range(0, out_h, band):
        hi = min(lo + band, out_h)
        n = hi - lo
        idx = np.concatenate([y0[lo:hi], y1[lo:hi]])
        rows = a.reshape(h, w * c)[idx]                       # (2n, w*c) uint8
        rows = rows.astype(np.int32)
        hb = rows[:, ix0] * wb0 + rows[:, ix1] * wb1          # (2n, out_w*c)
        acc = hb[:n] * yb0[lo:hi].astype(np.int32)[:, None] \
            + hb[n:] * yb1[lo:hi].astype(np.int32)[:, None]
        acc += ROUND
        acc >>= (2 * COEF_BITS)
        out[lo:hi] = acc

    np.clip(out, 0, 255, out=out)
    return out.reshape(out_h, out_w, c).astype(np.uint8)


def resize_pil(img, out_w, out_h):
    """Reference only: PIL's bilinear.  Present so tests can measure the gap, never used on the board."""
    from PIL import Image
    return np.asarray(Image.fromarray(np.asarray(img)).resize((out_w, out_h), Image.BILINEAR),
                      dtype=np.uint8)


def letterbox(img, size=640, pad=0, upscale=True, rounding='trunc'):
    """Scale so the long side fits `size`, centre it, fill with `pad`.

    Returns (canvas HxWx3 uint8, geom) where geom carries the numbers needed to map boxes back:
    ratio, the float pad offsets, and the integer borders that were actually used.

    Rounding of the borders follows the vendor demo (round(dh-0.1) / round(dh+0.1)) so the geometry
    matches the vendor model's expectations, which is what the inverse mapping assumes.

    `rounding` controls the resized extent: 'trunc' reproduces the vendor's `int(w*r)` exactly, 'round'
    is the friendlier choice for arbitrary sizes.  They differ by one pixel on some odd sizes
    (1918x1078 -> 639 vs 640), which shifts the pad borders; pick per model, never per convenience.
    """
    a = np.asarray(img)
    if a.ndim == 2:
        a = np.repeat(a[:, :, None], 3, axis=2)
    if a.shape[2] == 4:
        a = a[:, :, :3]
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)
    h, w = a.shape[:2]
    r = min(size / h, size / w)
    if not upscale:
        r = min(r, 1.0)
    if rounding == 'trunc':
        nw, nh = max(1, int(w * r)), max(1, int(h * r))
    elif rounding == 'round':
        nw, nh = max(1, int(round(w * r))), max(1, int(round(h * r)))
    else:
        raise ValueError("rounding must be 'trunc' or 'round'")
    resized = resize_bilinear(a, nw, nh)
    dw, dh = (size - nw) / 2.0, (size - nh) / 2.0
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    canvas = np.full((top + nh + bottom, left + nw + right, 3), pad, dtype=np.uint8)
    canvas[top:top + nh, left:left + nw] = resized
    geom = {'src': [w, h], 'dst': [canvas.shape[1], canvas.shape[0]], 'ratio': r,
            'resized': [nw, nh], 'pad_offset': [dw, dh], 'borders': [top, bottom, left, right],
            'pad_value': pad, 'rounding': rounding,
            'resize': 'fixed_point_bilinear(opencv-compatible)'}
    return canvas, geom


def boxes_to_source(boxes, geom, clip_to=None):
    """Model-space xyxy -> source pixels, using the same numbers the letterbox produced."""
    b = np.asarray(boxes, dtype=np.float64).copy()
    if b.size == 0:
        return b.reshape(0, 4)
    r = geom['ratio']
    dw, dh = geom['pad_offset']
    b[:, [0, 2]] = (b[:, [0, 2]] - dw) / r
    b[:, [1, 3]] = (b[:, [1, 3]] - dh) / r
    if clip_to is None:
        clip_to = geom['src']
    w, h = clip_to
    b[:, 0] = np.clip(b[:, 0], 0, w)
    b[:, 1] = np.clip(b[:, 1], 0, h)
    b[:, 2] = np.clip(b[:, 2], 0, w)
    b[:, 3] = np.clip(b[:, 3], 0, h)
    return b


_WARNED = set()


def _warn_slow_filter(ft):
    if ft not in _WARNED:
        _WARNED.add(ft)
        sys.stderr.write('imgproc_numpy: PNG filter type %d is inherently sequential; large images '
                         'decode slowly (use PPM for the fast path)\n' % ft)


def gray_or_rgb_from_png(path):
    """Minimal PNG reader (8-bit, non-interlaced) so PNG input needs no Pillow.

    JPEG still needs a real decoder; that is what the shipped Pillow wheel is for.
    """
    import struct
    import zlib

    with open(path, 'rb') as f:
        data = f.read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    pos = 8
    idat = []
    w = h = bitdepth = colortype = interlace = None
    palette = None
    while pos < len(data):
        ln = struct.unpack('>I', data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b'IHDR':
            w, h, bitdepth, colortype, _comp, _filt, interlace = struct.unpack('>IIBBBBB', body)
        elif typ == b'PLTE':
            palette = np.frombuffer(body, dtype=np.uint8).reshape(-1, 3)
        elif typ == b'IDAT':
            idat.append(body)
        elif typ == b'IEND':
            break
    if bitdepth != 8 or interlace != 0:
        raise ValueError('only 8-bit non-interlaced PNG is supported (bitdepth=%s interlace=%s)'
                         % (bitdepth, interlace))
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colortype)
    if ch is None:
        raise ValueError('unsupported PNG colour type %s' % colortype)
    raw = zlib.decompress(b''.join(idat))
    stride = w * ch
    out = np.empty((h, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.uint8)
    p = 0
    for y in range(h):
        ft = raw[p]
        p += 1
        line = np.frombuffer(raw[p:p + stride], dtype=np.uint8).copy()
        p += stride
        if ft == 0:
            cur = line
        elif ft == 1:                                  # Sub: running sum per channel
            cur = np.cumsum(line.reshape(w, ch).astype(np.int32), axis=0) & 0xFF
            cur = cur.astype(np.uint8).reshape(stride)
        elif ft == 2:                                  # Up
            cur = (line.astype(np.int32) + prev).astype(np.uint8)
        elif ft == 3:                                  # Average (sequential by construction)
            _warn_slow_filter(ft)
            cur = line
            for x in range(stride):
                left = int(cur[x - ch]) if x >= ch else 0
                cur[x] = (int(cur[x]) + ((left + int(prev[x])) >> 1)) & 0xFF
        elif ft == 4:                                  # Paeth (sequential by construction)
            _warn_slow_filter(ft)
            cur = line
            for x in range(stride):
                a = int(cur[x - ch]) if x >= ch else 0
                b = int(prev[x])
                c = int(prev[x - ch]) if x >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[x] = (int(cur[x]) + pr) & 0xFF
        else:
            raise ValueError('bad PNG filter type %s on row %d' % (ft, y))
        out[y] = cur
        prev = cur
    img = out.reshape(h, w, ch)
    if colortype == 3:
        img = palette[img[:, :, 0]]
    elif colortype == 6:
        img = img[:, :, :3]
    elif colortype == 4:
        img = np.repeat(img[:, :, :1], 3, axis=2)
    elif colortype == 0:
        img = np.repeat(img, 3, axis=2)
    return img, (w, h)