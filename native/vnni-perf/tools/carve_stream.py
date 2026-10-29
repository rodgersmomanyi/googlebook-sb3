"""Read-only carve of logical partitions from lapis.bin's super, streamed to stdout (for `| ssh builder 'cat > x'`).

Our own code (same on-disk formats as C:\\GB-x86\\carve_lp.py / extract_slot.py, which are NOT imported, so nothing is
written under C:\\GB-x86). Differences from carve_lp.py: it picks the LP metadata slot that belongs to the requested A/B
slot (a -> metadata slot 0, b -> 1, as liblp's SlotNumberForSlotSuffix does) instead of the first metadata slot that
names the partition, and it reports the active slot from the vbmeta_<x> GPT attributes.

  python -I carve_stream.py info                 GPT slot attributes, boot slot, every metadata slot's partitions
  python -I carve_stream.py stream NAME          partition NAME (e.g. system_b) to stdout; size + sha256 on stderr
"""
import hashlib, os, struct, sys

LAPIS = r"C:\GB-x86\lapis.bin"
SECTOR = 512


def gpt(f):
    f.seek(SECTOR); hdr = f.read(92)
    assert hdr[:8] == b"EFI PART", "no GPT"
    ent_lba, n, esz = struct.unpack_from("<QII", hdr, 72)
    f.seek(ent_lba * SECTOR); raw = f.read(n * esz)
    parts = {}
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == b"\0" * 16:
            continue
        first, last, attr = struct.unpack_from("<QQQ", e, 32)
        parts[e[56:128].decode("utf-16-le").rstrip("\0")] = (first * SECTOR, (last - first + 1) * SECTOR, attr)
    return parts


def slot_state(parts):
    out = {}
    for x in "ab":
        a = parts["vbmeta_" + x][2]
        out[x] = dict(priority=(a >> 48) & 15, tries=(a >> 52) & 15, successful=(a >> 56) & 1)
    return out


def boot_slot(st):
    for ok in (lambda s: s["priority"] and s["successful"], lambda s: s["priority"] and s["tries"]):
        cand = [x for x in "ab" if ok(st[x])]
        if cand:
            return max(cand, key=lambda x: (st[x]["priority"], x == "a"))
    return "a"


def lp_metadata(f, base):
    f.seek(base + 4096); geo = f.read(4096)
    assert struct.unpack_from("<I", geo, 0)[0] == 0x616C4467, "bad LP geometry magic"
    max_size, slot_count, _ = struct.unpack_from("<III", geo, 40)
    slots = []
    for s in range(slot_count):
        f.seek(base + 3 * 4096 + s * max_size); m = f.read(max_size)
        magic, _maj, _min, hsize = struct.unpack_from("<IHHI", m, 0)
        if magic != 0x414C5030:
            slots.append({}); continue
        t = m[hsize:]
        p_off, p_n, p_sz, e_off, e_n, e_sz = struct.unpack_from("<6I", m, 80)
        parts = {}
        for i in range(p_n):
            pe = t[p_off + i * p_sz:p_off + (i + 1) * p_sz]
            name = pe[:36].split(b"\0")[0].decode()
            attr, first, cnt, _g = struct.unpack_from("<IIII", pe, 36)
            exts = []
            for j in range(first, first + cnt):
                ee = t[e_off + j * e_sz:e_off + (j + 1) * e_sz]
                n_sec, ttype, tdata, _src = struct.unpack_from("<QIQI", ee, 0)
                exts.append((n_sec, ttype, tdata))
            parts[name] = (attr, exts)
        slots.append(parts)
    return slots


def main():
    with open(LAPIS, "rb") as f:
        parts = gpt(f)
        base = parts["super"][0]
        md = lp_metadata(f, base)
        if sys.argv[1] == "info":
            st = slot_state(parts)
            print("GPT vbmeta slot state:", st, "-> boot slot", boot_slot(st))
            for i, p in enumerate(md):
                print("metadata slot %d:" % i)
                for name, (attr, exts) in p.items():
                    print("   %-22s attr=%#x %6d MiB  extents=%s" % (name, attr, sum(e[0] for e in exts) * SECTOR >> 20,
                          [(e[0], e[1], e[2]) for e in exts][:4]))
            return
        assert sys.argv[1] == "stream"
        name = sys.argv[2]
        slot = name[-1]
        assert name[-2] == "_" and slot in "ab", "name must end in _a or _b"
        attr, exts = md["ab".index(slot)][name]
        out = sys.stdout.buffer
        h = hashlib.sha256(); total = 0
        for n_sec, ttype, tdata in exts:
            left = n_sec * SECTOR
            if ttype == 0:
                f.seek(base + tdata * SECTOR)
            while left:
                k = min(left, 16 << 20)
                chunk = f.read(k) if ttype == 0 else bytes(k)
                assert len(chunk) == k, "short read"
                out.write(chunk); h.update(chunk); left -= k; total += k
        out.flush()
        print("%s: metadata slot %d, %d extent(s), %d bytes, sha256 %s" % (name, "ab".index(slot), len(exts), total,
              h.hexdigest()), file=sys.stderr)


if __name__ == "__main__":
    main()
