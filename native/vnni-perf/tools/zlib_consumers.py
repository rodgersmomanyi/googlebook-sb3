#!/usr/bin/env python3
"""Which ELFs reach libz's adler32 (directly or through inflate/deflate with a zlib wrapper)?

For every x86-64 ELF under the ROOTs (read-only): DT_NEEDED libz.so, and the undefined dynamic symbols it imports from
the zlib API (adler32*, inflate*, deflate*, compress*, uncompress*, crc32*, gz*). Also flags ELFs that DEFINE
adler32_z themselves (a static zlib copy). Writes OUT_JSON.

usage: zlib_consumers.py OUT_JSON ROOT [ROOT...]
"""
import json, os, re, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_vnni import sections   # noqa: E402

API = re.compile(r"^(adler32\w*|inflate\w*|deflate\w*|compress\w*|uncompress\w*|crc32\w*|gz\w+|zlibVersion|zError)$")


def dyn(d):
    secs = sections(d)
    needed, und, defs = [], [], []
    for name, typ, _fl, _ad, off, size, link, _info, _al, ent in secs:
        if typ == 6:                                   # SHT_DYNAMIC
            stroff = secs[link][4]
            for k in range(size // 16):
                tag, val = struct.unpack_from("<qQ", d, off + k * 16)
                if tag == 1:
                    e = d.find(b"\0", stroff + val)
                    needed.append(d[stroff + val:e].decode("latin1"))
        elif typ == 11:                                # SHT_DYNSYM
            stroff = secs[link][4]
            for k in range(size // 24):
                nm, info, _o, shndx, val, sz = struct.unpack_from("<IBBHQQ", d, off + k * 24)
                e = d.find(b"\0", stroff + nm)
                s = d[stroff + nm:e].decode("latin1").split("@")[0]
                if API.match(s):
                    (und if shndx == 0 else defs).append(s)
    return needed, sorted(set(und)), sorted(set(defs))


def main():
    out = []
    for root in sys.argv[2:]:
        for dp, dn, fn in os.walk(root):
            dn.sort()
            for n in sorted(fn):
                p = os.path.join(dp, n)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                with open(p, "rb") as f:
                    if f.read(4) != b"\x7fELF":
                        continue
                    f.seek(0); d = f.read()
                if d[4] != 2 or struct.unpack_from("<H", d, 0x12)[0] != 0x3e:
                    continue
                try:
                    needed, und, defs = dyn(d)
                except (struct.error, ValueError, IndexError):
                    continue
                if "libz.so" in needed or und or defs:
                    out.append(dict(path=p, needs_libz="libz.so" in needed, imports=und, defines=defs))
    with open(sys.argv[1], "w") as f:
        json.dump(out, f, indent=1)
    print(len(out), "ELFs touch the zlib API;", sum(1 for o in out if o["needs_libz"]), "need libz.so;",
          sum(1 for o in out if "adler32" in o["imports"] or "adler32_z" in o["imports"]), "import adler32/adler32_z")


if __name__ == "__main__":
    main()
