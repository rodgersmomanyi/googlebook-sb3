"""Stream an image FILE from the builder to the Windows harness, data extents only (DESIGN.md 7.1 "Builder media":
transferred over ssh and written sparse on Windows).

  python3 -I builder_send.py /var/tmp/gbiso/test/<file>      (runs on the builder; read-only)

Stream format (all integers little endian):
  b"GBSPARSE1\\n"  +  JSON line {"size": <bytes>, "name": "<basename>"}\\n
  frames: <u64 offset> <u64 length> <length bytes>       (ascending, non-overlapping)
  end frame: offset 0xFFFFFFFFFFFFFFFF, length 0
  trailer: 64 hex chars = sha256 over every frame header and frame data incl. the end frame header, then "\\n"
Holes (SEEK_HOLE) are not sent; the receiver leaves them unallocated (they read as zeros on both sides).
Refuses anything that is not a regular file below /var/tmp/gbiso/test/.
"""
import hashlib
import json
import os
import stat
import struct
import sys

ROOT = "/var/tmp/gbiso/test/"
END = 0xFFFFFFFFFFFFFFFF
PIECE = 4 << 20


def extents(fd, size):
    """[(offset, length)] of the data extents (SEEK_DATA/SEEK_HOLE; the whole file without them)."""
    if not hasattr(os, "SEEK_DATA"):
        return [(0, size)] if size else []
    out, pos = [], 0
    while pos < size:
        try:
            d = os.lseek(fd, pos, os.SEEK_DATA)
        except OSError:
            break
        h = os.lseek(fd, d, os.SEEK_HOLE)
        out.append((d, h - d))
        pos = h
    return out


def encode(f, size, ext, out, name="image"):
    """Write the stream for file object f (size bytes, data extents ext) to the binary stream out."""
    out.write(b"GBSPARSE1\n")
    out.write((json.dumps({"size": size, "name": name}) + "\n").encode())
    h = hashlib.sha256()
    for off, ln in ext:
        pos, end = off, off + ln
        while pos < end:
            n = min(PIECE, end - pos)
            f.seek(pos)
            data = f.read(n)
            if len(data) != n:
                raise IOError("short read at %d" % pos)
            hdr = struct.pack("<QQ", pos, n)
            h.update(hdr)
            h.update(data)
            out.write(hdr)
            out.write(data)
            pos += n
    hdr = struct.pack("<QQ", END, 0)
    h.update(hdr)
    out.write(hdr)
    out.write(h.hexdigest().encode() + b"\n")
    out.flush()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: builder_send.py <file below %s>" % ROOT)
    p = os.path.realpath(sys.argv[1])
    if not p.startswith(ROOT):
        sys.exit("refusing %s: not below %s" % (p, ROOT))
    st = os.lstat(p)
    if not stat.S_ISREG(st.st_mode):
        sys.exit("refusing %s: not a regular file" % p)
    with open(p, "rb") as f:
        encode(f, st.st_size, extents(f.fileno(), st.st_size), sys.stdout.buffer, os.path.basename(p))


if __name__ == "__main__":
    main()
