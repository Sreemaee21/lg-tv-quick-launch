"""Tiny PNG decoder: just enough to find an icon's brand colour at install time.

Doing this on the PC keeps the TV launcher free of any image processing.
"""

from __future__ import annotations

import struct
import zlib


def _decode(data: bytes) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a png")
    pos, idat, plte, trns = 8, b"", None, None
    w = h = depth = ctype = interlace = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
    if depth != 8 or interlace or ctype not in (2, 3, 6):
        raise ValueError("unsupported png")
    bpp = {2: 3, 3: 1, 6: 4}[ctype]
    raw, stride = zlib.decompress(idat), w * bpp
    prev, out, i = bytearray(stride), [], 0
    for _ in range(h):
        f, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        for x in range(0, stride, bpp):
            if ctype == 6:
                out.append(tuple(line[x:x + 4]))
            elif ctype == 2:
                out.append((*line[x:x + 3], 255))
            else:
                idx = line[x]
                alpha = trns[idx] if trns and idx < len(trns) else 255
                out.append((*plte[idx], alpha))
        prev = line
    return w, h, out


def accent(data: bytes) -> str | None:
    """Most characteristic colour of an icon as '#rrggbb', or None."""
    try:
        _, _, px = _decode(data)
    except Exception:
        return None
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for r, g, b, a in px[::3]:
        if a < 160:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        sat = (mx - mn) / mx if mx else 0
        if sat < 0.35 or mx < 60:  # skip greys, whites, near-black
            continue
        key = (r >> 5, g >> 5, b >> 5)
        acc = buckets.setdefault(key, [0, 0, 0, 0])
        acc[0] += r; acc[1] += g; acc[2] += b; acc[3] += 1
    if not buckets:
        return None
    r, g, b, n = max(buckets.values(), key=lambda v: v[3])
    if n < 20:
        return None
    return "#%02x%02x%02x" % (r // n, g // n, b // n)
