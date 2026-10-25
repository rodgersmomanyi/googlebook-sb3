"""Pixel-double a PSF console font (DESIGN.md 3.1/3.5): Lat15-TerminusBold32x16 (16 wide, 32 tall) -> PSF2 32x64.

Usage: python3 -I -B psf_double.py <in.psf[.gz]> <out.psf>
Every pixel becomes a 2x2 block. Input PSF1 or PSF2 (gzip or plain); output is an uncompressed PSF2 with the input's
Unicode table (PSF1 tables are converted to the PSF2 UTF-8 form). Prints one line: "psf_double: <in WxH> -> <out WxH>
glyphs=<n> unicode=<yes|no> bytes=<n>".
"""
import gzip
import struct
import sys

PSF1_MAGIC = b"\x36\x04"
PSF2_MAGIC = b"\x72\xb5\x4a\x86"
PSF1_MODE512, PSF1_MODEHASTAB, PSF1_MODESEQ = 0x01, 0x02, 0x04
PSF2_HAS_UNICODE_TABLE = 0x01


def load(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if raw[:4] == PSF2_MAGIC:
        _magic, version, hsize, flags, n, csize, h, w = struct.unpack_from("<4sIIIIIII", raw, 0)
        if version != 0:
            raise SystemExit(f"psf_double: unsupported PSF2 version {version}")
        rowbytes = (w + 7) // 8
        if csize != h * rowbytes:
            raise SystemExit("psf_double: PSF2 charsize does not match height x row bytes")
        glyphs = [raw[hsize + i * csize: hsize + (i + 1) * csize] for i in range(n)]
        table = raw[hsize + n * csize:] if flags & PSF2_HAS_UNICODE_TABLE else None
        return w, h, glyphs, table
    if raw[:2] == PSF1_MAGIC:
        mode, csize = raw[2], raw[3]
        n = 512 if mode & PSF1_MODE512 else 256
        glyphs = [raw[4 + i * csize: 4 + (i + 1) * csize] for i in range(n)]
        table = None
        if mode & (PSF1_MODEHASTAB | PSF1_MODESEQ):
            table = psf1_table_to_psf2(raw[4 + n * csize:], n)
        return 8, csize, glyphs, table
    raise SystemExit("psf_double: not a PSF1/PSF2 font")


def psf1_table_to_psf2(tab, n):
    """PSF1 table: per glyph u16 code points, 0xFFFE starts a sequence, 0xFFFF ends the glyph."""
    out = bytearray()
    pos = 0
    for _ in range(n):
        while pos + 2 <= len(tab):
            (u,) = struct.unpack_from("<H", tab, pos)
            pos += 2
            if u == 0xFFFF:
                break
            if u == 0xFFFE:
                out.append(0xFE)
            else:
                out += chr(u).encode("utf-8")
        out.append(0xFF)
    return bytes(out)


def double_glyph(g, w, h):
    rowbytes = (w + 7) // 8
    w2 = 2 * w
    rowbytes2 = (w2 + 7) // 8
    out = bytearray()
    for y in range(h):
        row = int.from_bytes(g[y * rowbytes:(y + 1) * rowbytes], "big") >> (rowbytes * 8 - w)
        wide = 0
        for x in range(w):            # bit x counted from the left
            if row & (1 << (w - 1 - x)):
                wide |= 3 << (w2 - 2 - 2 * x)
        b = (wide << (rowbytes2 * 8 - w2)).to_bytes(rowbytes2, "big")
        out += b + b
    return bytes(out)


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    src, dst = argv
    w, h, glyphs, table = load(src)
    w2, h2 = 2 * w, 2 * h
    if w2 > 32 or h2 > 64:
        raise SystemExit(f"psf_double: {w2}x{h2} exceeds the kernel console font limit 32x64")
    data = b"".join(double_glyph(g, w, h) for g in glyphs)
    csize = h2 * ((w2 + 7) // 8)
    flags = PSF2_HAS_UNICODE_TABLE if table is not None else 0
    hdr = struct.pack("<4sIIIIIII", PSF2_MAGIC, 0, 32, flags, len(glyphs), csize, h2, w2)
    blob = hdr + data + (table or b"")
    with open(dst, "wb") as f:
        f.write(blob)
    print(f"psf_double: {w}x{h} -> {w2}x{h2} glyphs={len(glyphs)} unicode={'yes' if table is not None else 'no'} "
          f"bytes={len(blob)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
