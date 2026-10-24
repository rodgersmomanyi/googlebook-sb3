"""Extract logical (dynamic) partitions such as vendor_dlkm_b from the super partition inside lapis.bin.

Parses liblp metadata (geometry at super+4096, metadata slots after the two geometry copies) and copies the linear
extents of the named partitions into OUTDIR/<name>.img. Read-only on lapis.bin.

Usage: python -I carve_lp.py OUTDIR NAME [NAME...]      e.g.  carve_lp.py D:\\gb-carve vendor_dlkm_b
       python -I carve_lp.py --list                      list partitions of every metadata slot
"""
import os, struct, sys

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
LAPIS = os.path.join(HERE, "lapis.bin")
SECTOR = 512


def super_offset(path=LAPIS):
    with open(path, "rb") as f:
        f.seek(SECTOR); hdr = f.read(92)
        ent_lba, n, esz = struct.unpack_from("<QII", hdr, 72)
        f.seek(ent_lba * SECTOR); raw = f.read(n * esz)
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[56:128].decode("utf-16-le").rstrip("\0") == "super":
            first, last = struct.unpack_from("<QQ", e, 32)
            return first * SECTOR, (last - first + 1) * SECTOR
    raise SystemExit("no super partition")


def read_metadata(f, base):
    f.seek(base + 4096)
    geo = f.read(4096)
    magic, _size = struct.unpack_from("<II", geo, 0)
    assert magic == 0x616C4467, "bad LP geometry magic"
    max_size, slot_count, _lbs = struct.unpack_from("<III", geo, 40)
    slots = []
    for s in range(slot_count):
        f.seek(base + 4096 + 2 * 4096 + s * max_size)
        m = f.read(max_size)
        magic, major, minor, hsize = struct.unpack_from("<IHHI", m, 0)
        if magic != 0x414C5030:
            slots.append({}); continue
        tables = m[hsize:]
        p_off, p_n, p_sz, e_off, e_n, e_sz = struct.unpack_from("<6I", m, 80)
        parts = {}
        for i in range(p_n):
            pe = tables[p_off + i * p_sz: p_off + (i + 1) * p_sz]
            name = pe[:36].split(b"\0")[0].decode()
            _attr, first_ext, n_ext, _grp = struct.unpack_from("<IIII", pe, 36)
            exts = []
            for j in range(first_ext, first_ext + n_ext):
                ee = tables[e_off + j * e_sz: e_off + (j + 1) * e_sz]
                n_sec, ttype, tdata, _src = struct.unpack_from("<QIQI", ee, 0)
                exts.append((n_sec, ttype, tdata))
            parts[name] = exts
        slots.append(parts)
    return slots


def main():
    base, _ = super_offset()
    with open(LAPIS, "rb") as f:
        slots = read_metadata(f, base)
        if sys.argv[1:] == ["--list"]:
            for i, p in enumerate(slots):
                print(f"metadata slot {i}:", ", ".join(f"{n}({sum(e[0] for e in x) * SECTOR >> 20}M)" for n, x in p.items()))
            return
        out = sys.argv[1]
        os.makedirs(out, exist_ok=True)
        for name in sys.argv[2:]:
            exts = next((p[name] for p in slots if name in p and p[name]), None)
            assert exts, f"{name} not found in any metadata slot"
            with open(os.path.join(out, name + ".img"), "wb") as o:
                for n_sec, ttype, tdata in exts:
                    if ttype == 0:          # linear
                        f.seek(base + tdata * SECTOR)
                        left = n_sec * SECTOR
                        while left:
                            chunk = f.read(min(left, 16 << 20)); o.write(chunk); left -= len(chunk)
                    else:                   # zero
                        o.write(bytes(n_sec * SECTOR))
            print(f"{name}: {sum(e[0] for e in exts) * SECTOR} bytes in {len(exts)} extent(s)")


if __name__ == "__main__":
    main()
