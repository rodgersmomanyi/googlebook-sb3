"""fat_add.py - TEST HELPER (image files only): change the FAT32 EFI-SYSTEM partition inside a raw disk image in place,
so the -UpdateEspOnly image tests (wave 3, item 7) have an installed ESP that differs from the export's.

  python -I fat_add.py IMAGE add ESP_PATH HOST_FILE        add a file (directories are created), e.g. gblog/x.txt
  python -I fat_add.py IMAGE replace OLD NEW                replace a byte string (same length, exactly once) in the ESP
  python -I fat_add.py IMAGE sha                            sha256 of the whole EFI-SYSTEM partition

The partition is found by its GPT name EFI-SYSTEM. Names that do not fit 8.3 get long-name entries. Device paths are
refused. The FSInfo free-cluster hint is set to "unknown" after an add.
"""
import hashlib, struct, sys

SECTOR = 512


def esp_range(f):
    f.seek(SECTOR)
    h = f.read(SECTOR)
    if h[:8] != b"EFI PART":
        f.seek(0)
        b0 = f.read(SECTOR)
        if b0[82:90] == b"FAT32   ":                  # a bare ESP image (the helpers' own unit test)
            f.seek(0, 2)
            return 0, f.tell()
        sys.exit("fat_add: no GPT")
    ent_lba, num, esz = struct.unpack_from("<QII", h, 72)
    f.seek(ent_lba * SECTOR)
    ents = f.read(num * esz)
    for i in range(num):
        e = ents[i * esz:(i + 1) * esz]
        if e[56:128].decode("utf-16-le", "replace").split("\0")[0] == "EFI-SYSTEM":
            first, last = struct.unpack_from("<QQ", e, 32)
            return first * SECTOR, (last - first + 1) * SECTOR
    sys.exit("fat_add: no EFI-SYSTEM partition")


class Fat:
    def __init__(self, f, off):
        self.f, self.off = f, off
        b = self.rd(0, 512)
        self.bps, self.spc, self.res, self.nf = struct.unpack_from("<HBHB", b, 11)
        self.fatsz = struct.unpack_from("<I", b, 36)[0]
        self.root = struct.unpack_from("<I", b, 44)[0]
        self.fsinfo = struct.unpack_from("<H", b, 48)[0]
        tot = struct.unpack_from("<I", b, 32)[0]
        assert b[82:90] == b"FAT32   ", "not FAT32"
        self.cs = self.bps * self.spc
        self.data0 = (self.res + self.nf * self.fatsz) * self.bps
        self.clusters = (tot - self.res - self.nf * self.fatsz) // self.spc
        n = min(self.fatsz * self.bps // 4, self.clusters + 2)
        self.fat = list(struct.unpack_from("<%dI" % n, self.rd(self.res * self.bps, n * 4)))

    def rd(self, o, n):
        self.f.seek(self.off + o)
        return self.f.read(n)

    def wr(self, o, b):
        self.f.seek(self.off + o)
        self.f.write(b)

    def cl(self, c):
        return self.data0 + (c - 2) * self.cs

    def chain(self, c):
        out = []
        while 2 <= c < 0x0FFFFFF8:
            out.append(c)
            c = self.fat[c] & 0x0FFFFFFF
        return out

    def alloc(self, k):
        got = [c for c in range(2, self.clusters + 2) if (self.fat[c] & 0x0FFFFFFF) == 0][:k]
        assert len(got) == k, "ESP full"
        for a, b in zip(got, got[1:] + [0x0FFFFFFF]):
            self.setfat(a, b)
        return got

    def setfat(self, c, v):
        self.fat[c] = (self.fat[c] & 0xF0000000) | v
        for i in range(self.nf):
            self.wr((self.res + i * self.fatsz) * self.bps + 4 * c, struct.pack("<I", self.fat[c]))

    def entries(self, dc):
        """[(cluster, index, 32-byte entry)] of a directory"""
        out = []
        for c in self.chain(dc):
            b = self.rd(self.cl(c), self.cs)
            out += [(c, i, b[i * 32:(i + 1) * 32]) for i in range(self.cs // 32)]
        return out

    def names(self, dc):
        """{lower-case long or short name: (first cluster, is_dir)}, plus the set of short names"""
        res, shorts, lfn = {}, set(), []
        for _, _, e in self.entries(dc):
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = []
                continue
            if e[11] == 0x0F:
                if e[0] & 0x40:
                    lfn = []
                lfn.insert(0, (e[1:11] + e[14:26] + e[28:32]).decode("utf-16-le", "replace"))
                continue
            shorts.add(bytes(e[:11]))
            base, ext = e[:8].decode("ascii").rstrip(), e[8:11].decode("ascii").rstrip()
            sn = (base + ("." + ext if ext else "")).lower()
            ln = "".join(lfn).split("\0")[0].lower() if lfn else sn
            c = (struct.unpack_from("<H", e, 20)[0] << 16) | struct.unpack_from("<H", e, 26)[0]
            res[ln] = res[sn] = (c, bool(e[11] & 0x10))
            lfn = []
        return res, shorts

    def put_entry(self, dc, name, cluster, size, isdir):
        _, shorts = self.names(dc)
        base, _, ext = name.rpartition(".") if "." in name[1:] else (name, "", "")
        ok83 = (len(base) <= 8 and len(ext) <= 3 and base and all(ch.isalnum() or ch in "_-" for ch in base + ext)
                and (base.islower() or base.isupper() or base.isdigit()) and (not ext or ext.islower() or ext.isupper()))
        nt = 0
        if ok83:
            sn = base.upper().ljust(8).encode() + ext.upper().ljust(3).encode()
            nt = (0x08 if base.islower() else 0) | (0x10 if ext.islower() else 0)
            lfn_parts = []
        else:
            stem = "".join(ch for ch in base.upper() if ch.isalnum())[:6] or "X"
            for k in range(1, 100):
                sn = (stem + "~%d" % k).ljust(8).encode()[:8] + "".join(ch for ch in ext.upper() if ch.isalnum())[:3].ljust(3).encode()
                if sn not in shorts:
                    break
            u = name.encode("utf-16-le") + b"\0\0"
            u += b"\xff" * (-len(u) % 26)
            lfn_parts = [u[i:i + 26] for i in range(0, len(u), 26)]
        s = 0
        for ch in sn:
            s = (((s & 1) << 7) + (s >> 1) + ch) & 0xFF
        recs = []
        for i in range(len(lfn_parts), 0, -1):
            p = lfn_parts[i - 1]
            seq = i | (0x40 if i == len(lfn_parts) else 0)
            recs.append(bytes([seq]) + p[0:10] + bytes([0x0F, 0, s]) + p[10:22] + b"\0\0" + p[22:26])
        recs.append(sn + bytes([0x10 if isdir else 0x20, nt]) + b"\0" * 7 + struct.pack("<H", cluster >> 16)
                    + b"\0" * 4 + struct.pack("<HI", cluster & 0xFFFF, size))
        ents = self.entries(dc)
        run = 0
        for k, (_, _, e) in enumerate(ents):
            run = run + 1 if e[0] in (0, 0xE5) else 0
            if run == len(recs):
                start = k - len(recs) + 1
                break
        else:
            last = self.chain(dc)[-1]
            nc = self.alloc(1)[0]
            self.setfat(last, nc)
            self.wr(self.cl(nc), b"\0" * self.cs)
            return self.put_entry(dc, name, cluster, size, isdir)
        for j, r in enumerate(recs):
            c, i, _ = ents[start + j]
            self.wr(self.cl(c) + i * 32, r)

    def mkdir(self, parent, name):
        c = self.alloc(1)[0]
        pc = 0 if parent == self.root else parent
        blk = bytearray(self.cs)
        blk[0:32] = b".          " + bytes([0x10, 0]) + b"\0" * 7 + struct.pack("<H", c >> 16) + b"\0" * 4 + struct.pack("<HI", c & 0xFFFF, 0)
        blk[32:64] = b"..         " + bytes([0x10, 0]) + b"\0" * 7 + struct.pack("<H", pc >> 16) + b"\0" * 4 + struct.pack("<HI", pc & 0xFFFF, 0)
        self.wr(self.cl(c), bytes(blk))
        self.put_entry(parent, name, c, 0, True)
        return c

    def add(self, path, data):
        parts = path.strip("/").split("/")
        d = self.root
        for p in parts[:-1]:
            n, _ = self.names(d)
            d = n[p.lower()][0] if p.lower() in n and n[p.lower()][1] else self.mkdir(d, p)
        n, _ = self.names(d)
        assert parts[-1].lower() not in n, f"{path} exists"
        k = max(1, -(-len(data) // self.cs))
        cl = self.alloc(k) if data else []
        for i, c in enumerate(cl):
            self.wr(self.cl(c), data[i * self.cs:(i + 1) * self.cs].ljust(self.cs, b"\0"))
        self.put_entry(d, parts[-1], cl[0] if cl else 0, len(data), False)
        fs = self.rd(self.fsinfo * self.bps, 512)
        if fs[:4] == b"RRaA":
            self.wr(self.fsinfo * self.bps + 488, b"\xff" * 8)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    img, cmd = sys.argv[1], sys.argv[2]
    if img.startswith(("\\\\.\\", "\\\\?\\", "//./", "/dev/")) or "PhysicalDrive" in img:
        sys.exit("fat_add: image files only")
    with open(img, "r+b" if cmd != "sha" else "rb") as f:
        off, ln = esp_range(f)
        if cmd == "add":
            Fat(f, off).add(sys.argv[3], open(sys.argv[4], "rb").read())
            print(f"added {sys.argv[3]}")
        elif cmd == "replace":
            old, new = sys.argv[3].encode().decode("unicode_escape").encode("latin-1"), \
                sys.argv[4].encode().decode("unicode_escape").encode("latin-1")
            assert len(old) == len(new), "same length only"
            f.seek(off)
            esp = f.read(ln)
            assert esp.count(old) == 1, f"{old!r} found {esp.count(old)} times"
            f.seek(off + esp.index(old))
            f.write(new)
            print(f"replaced {old!r} -> {new!r}")
        elif cmd == "sha":
            f.seek(off)
            h = hashlib.sha256()
            left = ln
            while left:
                b = f.read(min(left, 1 << 22))
                h.update(b)
                left -= len(b)
            print(h.hexdigest())
        else:
            sys.exit(__doc__)


if __name__ == "__main__":
    main()
