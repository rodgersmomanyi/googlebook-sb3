"""esp_extract.py - read-only look into a FAT32 EFI-SYSTEM image for install_dualboot.ps1 -UpdateEspOnly (wave 3,
item 7). Image mode / test harness only: on a real disk the installer copies the same files from a temporary drive
letter instead (no Python needed there).

  python -I esp_extract.py --src FILE [--offset BYTES --length BYTES] [--out DIR]

--src is a raw ESP image (the backup esp-before-<stamp>.img) or, with --offset/--length, a disk image whose ESP lies at
that byte range (the dry run reads the target image in place). Without --out nothing is written. With --out DIR (which
must not exist yet) the files that a whole-partition ESP rewrite would remove are copied there: everything under
gblog\\, gbinstall\\ and gb\\overlay\\, and gb\\gb.cfg (names matched without regard to case, like FAT), plus
DIR\\SHA256SUMS.txt. Prints one JSON object: loader_defaults (every "default" line of loader/loader.conf), the
preserved files (path, size, sha256), skipped names, gb_cfg / gb_overlay (present or not).

The FAT32 reader follows verify_image.py fat_parse (same project, same licence: Apache-2.0); names from the ESP are
untrusted, so a file is written only when every path component is a plain name and the result stays inside DIR.
"""
import argparse, hashlib, json, mmap, os, re, struct, sys

KEEP_DIRS = ("gblog", "gbinstall", "gb/overlay")
KEEP_FILES = ("gb/gb.cfg",)
SAFE = re.compile(r"[A-Za-z0-9 ._+()~-]{1,255}")


def fat_parse(esp):
    """{path: bytes} of every file of a FAT32 image (paths with '/', case as stored). ValueError if not FAT32."""
    if len(esp) < 1024 or esp[510:512] != b"\x55\xaa":
        raise ValueError("no boot sector signature")
    bps, spc, res, nf, rootent, tot16 = struct.unpack_from("<HBHBHH", esp, 11)
    fatsz16 = struct.unpack_from("<H", esp, 22)[0]
    tot32, fatsz = struct.unpack_from("<II", esp, 32)
    root = struct.unpack_from("<I", esp, 44)[0]
    if bps not in (512, 1024, 2048, 4096) or spc == 0 or spc & (spc - 1) or nf == 0 or res == 0:
        raise ValueError("bad BPB")
    if rootent != 0 or fatsz16 != 0 or fatsz == 0 or esp[82:90] != b"FAT32   ":
        raise ValueError("not FAT32")
    total = tot32 or tot16
    if total * bps > len(esp):
        raise ValueError("the FAT is larger than the image")
    data0 = (res + nf * fatsz) * bps
    cs = bps * spc
    clusters = (total - res - nf * fatsz) // spc
    nent = min(fatsz * bps // 4, clusters + 2)
    fat = struct.unpack_from("<%dI" % nent, esp, res * bps)

    def chain(c):
        out = []
        while 2 <= c < 0x0FFFFFF8:
            if c >= clusters + 2 or c >= nent or len(out) > clusters:
                raise ValueError("bad cluster chain")
            out.append(c)
            c = fat[c] & 0x0FFFFFFF
        return out

    def read(c, size=None):
        b = b"".join(esp[data0 + (x - 2) * cs:data0 + (x - 1) * cs] for x in (chain(c) if c else []))
        if size is not None:
            if size > len(b):
                raise ValueError("file larger than its clusters")
            b = b[:size]
        return b

    files = {}

    def walk(c, path, depth):
        if depth > 16:
            raise ValueError("directory tree too deep")
        raw = read(c)
        lfn, lfn_sum = [], None
        for i in range(0, len(raw) - 31, 32):
            e = raw[i:i + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = []
                continue
            attr = e[11]
            if attr == 0x0F:
                if e[0] & 0x40:
                    lfn, lfn_sum = [], e[13]
                lfn.insert(0, (e[1:11] + e[14:26] + e[28:32]).decode("utf-16-le", "replace"))
                continue
            if attr & 0x08:
                lfn = []
                continue
            s = 0
            for ch in e[0:11]:
                s = (((s & 1) << 7) + (s >> 1) + ch) & 0xFF
            if lfn and lfn_sum == s:
                name = "".join(lfn).split("\0")[0]
            else:
                base, ext = e[:8].decode("ascii", "replace").rstrip(), e[8:11].decode("ascii", "replace").rstrip()
                if e[12] & 0x08:
                    base = base.lower()
                if e[12] & 0x10:
                    ext = ext.lower()
                name = base + ("." + ext if ext else "")
            lfn = []
            if name in (".", ".."):
                continue
            cl = (struct.unpack_from("<H", e, 20)[0] << 16) | struct.unpack_from("<H", e, 26)[0]
            full = path + name
            if attr & 0x10:
                walk(cl, full + "/", depth + 1)
            else:
                files[full] = read(cl, struct.unpack_from("<I", e, 28)[0])

    walk(root, "", 0)
    return files


def kept(path):
    low = path.lower()
    return low in KEEP_FILES or any(low.startswith(d + "/") for d in KEEP_DIRS)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--length", type=int, default=0)
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.out and os.path.exists(a.out):
        sys.exit(f"esp_extract: {a.out} already exists")
    with open(a.src, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            n = a.length or (len(mm) - a.offset)
            if a.offset < 0 or n <= 0 or a.offset + n > len(mm):
                sys.exit("esp_extract: the byte range is outside the file")
            esp = mm[a.offset:a.offset + n]
        finally:
            mm.close()
    try:
        files = fat_parse(esp)
    except ValueError as e:
        print(json.dumps({"error": f"not a readable FAT32 ESP: {e}"}))
        return 3
    lc = next((v for k, v in files.items() if k.lower() == "loader/loader.conf"), None)
    defaults = []
    if lc is not None:
        for line in lc.decode("ascii", "replace").splitlines():
            m = re.match(r"^default(?:[ \t]+(.*?))?[ \t\r]*$", line)
            if m:
                defaults.append(m.group(1) or "")
    res = {"loader_conf": lc is not None, "loader_defaults": defaults, "preserved": [], "skipped": [],
           "gb_cfg": any(k.lower() == "gb/gb.cfg" for k in files),
           "gb_overlay": any(k.lower().startswith("gb/overlay/") for k in files)}
    if a.out:
        os.makedirs(a.out)
        top = os.path.realpath(a.out)
    sums = []
    for path in sorted(files):
        if not kept(path):
            continue
        parts = path.split("/")
        if not all(SAFE.fullmatch(p) and p not in (".", "..") and not p.endswith((".", " ")) for p in parts):
            res["skipped"].append(path.encode("ascii", "backslashreplace").decode())
            continue
        data = files[path]
        h = hashlib.sha256(data).hexdigest()
        if a.out:
            dst = os.path.realpath(os.path.join(a.out, *parts))
            if not dst.startswith(top + os.sep):
                res["skipped"].append(path)
                continue
        res["preserved"].append({"path": path, "size": len(data), "sha256": h})
        if a.out:
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "xb") as o:          # a name that differs only in case from one already written: skip
                    o.write(data)
            except (FileExistsError, NotADirectoryError):
                res["preserved"].pop()
                res["skipped"].append(path)
                continue
            sums.append(f"{h}  {path}\n")
    if a.out:
        with open(os.path.join(a.out, "SHA256SUMS.txt"), "x", encoding="ascii", newline="\n") as o:
            o.writelines(sums)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
