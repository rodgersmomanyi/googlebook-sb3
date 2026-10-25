"""Read-only EROFS lister for a local image file (NP-21): prints every entry of the image, sorted, as
"<type> <mode> <uid> <gid> <size> <path>" (type f/d/l/c/b/p/s, mode octal without the file-type bits).

Usage: python3 -I erofs_ls.py <image.erofs>

Same on-disk parsing as nvresearch/erofs_ls.py (superblock at 1024, 4 KiB blocks, compact/extended inodes, FLAT_PLAIN
and FLAT_INLINE directories), but it reads the file directly and walks the whole tree; nothing is written."""
import struct
import sys

BS = 4096
TYPES = {1: "f", 2: "d", 3: "c", 4: "b", 5: "p", 6: "s", 7: "l"}


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    with open(sys.argv[1], "rb") as f:
        img = f.read()

    def rd(off, ln):
        if off < 0 or off + ln > len(img):
            raise ValueError("read past the end of the image")
        return img[off:off + ln]

    sb = rd(1024, 128)
    magic, = struct.unpack_from("<I", sb, 0)
    if magic != 0xE0F5E1E2:
        raise ValueError("not an EROFS image (magic %#x)" % magic)
    if sb[12] != 12:
        raise ValueError("block size 2^%d unsupported" % sb[12])
    root_nid, = struct.unpack_from("<H", sb, 14)
    meta_blkaddr, = struct.unpack_from("<I", sb, 40)

    def inode(nid):
        off = meta_blkaddr * BS + nid * 32
        h = rd(off, 64)
        fmt, xic, mode = struct.unpack_from("<HHH", h, 0)
        if fmt & 1:                                    # extended inode
            size, = struct.unpack_from("<Q", h, 8)
            u, = struct.unpack_from("<I", h, 16)
            uid, gid = struct.unpack_from("<II", h, 24)
            isz = 64
        else:                                          # compact inode
            size, = struct.unpack_from("<I", h, 8)
            u, = struct.unpack_from("<I", h, 16)
            uid, gid = struct.unpack_from("<HH", h, 24)
            isz = 32
        xsz = 0 if xic == 0 else 12 + (xic - 1) * 4
        return dict(off=off, mode=mode, size=size, layout=(fmt >> 1) & 7, u=u, uid=uid, gid=gid, isz=isz, xsz=xsz)

    def data(ino):
        size, lay = ino["size"], ino["layout"]
        if lay == 0:                                   # FLAT_PLAIN
            return rd(ino["u"] * BS, size)
        if lay == 2:                                   # FLAT_INLINE
            nfull = size // BS
            tail = size - nfull * BS
            d = rd(ino["u"] * BS, nfull * BS) if nfull else b""
            if tail:
                d += rd(ino["off"] + ino["isz"] + ino["xsz"], tail)
            return d
        raise ValueError("directory layout %d unsupported" % lay)

    def readdir(ino):
        d = data(ino)
        out = []
        for bo in range(0, len(d), BS):
            blk = d[bo:bo + BS]
            nameoff0, = struct.unpack_from("<H", blk, 8)
            n = nameoff0 // 12
            ents = [struct.unpack_from("<QHBB", blk, i * 12) for i in range(n)]
            for i, (nid, no, ft, _) in enumerate(ents):
                end = ents[i + 1][1] if i + 1 < n else len(blk)
                name = blk[no:end].split(b"\0")[0].decode("utf-8", "replace")
                out.append((name, nid, ft))
        return out

    lines = []

    def walk(ino, path, depth):
        if depth > 32:
            raise ValueError("directory tree too deep")
        for name, nid, ft in readdir(ino):
            if name in (".", ".."):
                continue
            ci = inode(nid)
            p = path + "/" + name if path else name
            lines.append("%s %04o %d %d %d %s" % (TYPES.get(ft, "?"), ci["mode"] & 0o7777, ci["uid"], ci["gid"],
                                                   ci["size"], p))
            if ft == 2:
                walk(ci, p, depth + 1)

    root = inode(root_nid)
    lines.append("d %04o %d %d %d ." % (root["mode"] & 0o7777, root["uid"], root["gid"], root["size"]))
    walk(root, "", 0)
    for line in sorted(lines, key=lambda s: s.split(" ", 5)[5]):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
