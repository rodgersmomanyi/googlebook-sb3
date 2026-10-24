#!/usr/bin/env python3
# TEST ONLY: convert an NV12 dump (optionally with a line stride) to PNG (full-range BT.601),
# and print basic statistics. usage: nv12topng.py in.yuv width height [stride] out.png
import struct
import sys
import zlib


def main():
    src, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    stride = int(sys.argv[4]) if len(sys.argv) > 5 else w
    out = sys.argv[-1]
    import os
    step = int(os.environ.get("STEP", "1"))
    data = open(src, "rb").read()
    ysz = stride * h
    rows = []
    ysum = 0
    usum = vsum = 0
    for y in range(0, h, step):
        row = bytearray([0])
        yl = data[y * stride:y * stride + w]
        uvl = data[ysz + (y // 2) * stride:ysz + (y // 2) * stride + w]
        for x in range(0, w, step):
            Y = yl[x]
            U = uvl[x & ~1] - 128
            V = uvl[(x & ~1) + 1] - 128
            ysum += Y
            usum += abs(U)
            vsum += abs(V)
            r = Y + 1.402 * V
            g = Y - 0.344136 * U - 0.714136 * V
            b = Y + 1.772 * U
            row += bytes(max(0, min(255, int(c + 0.5))) for c in (r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)

    ow, oh = len(range(0, w, step)), len(range(0, h, step))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", ow, oh, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    open(out, "wb").write(png)
    n = ow * oh
    print("%s: %dx%d stride %d meanY %.1f mean|U| %.1f mean|V| %.1f" %
          (src, w, h, stride, ysum / n, usum / n, vsum / n))


main()
