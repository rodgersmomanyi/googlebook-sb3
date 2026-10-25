r"""Test disks for the TEST harness (DESIGN.md 7.1, 7.2): sparse raw files + monolithicFlat descriptors under
D:\GB-iso\test\disks\, built with Python on Windows or received from the builder (builder_media.sh), and the ISO hard
link D:\GB-iso\test\iso\googlebook-sb3-installer.iso.

Every file written here is below D:\GB-iso\test\ (common.assert_test_path); after writing a descriptor the pre-start
guard (vmtest.guard_vmdk) runs on it. Nothing here opens a device.

CLI (manual use):
  python -I mkdisks.py blank <name> <bytes>          blank sparse raw + descriptor
  python -I mkdisks.py make <recipe>                 one of the Python recipes (t1a t1b t1c t2a t2c t2d t2f t3 t3na t3nb blank1g)
  python -I mkdisks.py guard <file.vmdk>             run the descriptor guard
  python -I mkdisks.py delete <name>                 delete <name>.img/.vmdk (and test side files)
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I: no script dir on sys.path
import json
import os
import struct
import subprocess
import sys
import uuid
import zlib

import common as C
import vmtest

MIN_FREE_AFTER = 8 * C.GiB        # 7.1: D: free - expected physical size >= 8 GiB, else SKIPPED (space)


class Skipped(Exception):
    pass


# ------------------------------------------------------------------------------------------------ space, names
def space_check(expected_physical, drive_path=C.TEST_ROOT):
    free = C.free_bytes(drive_path)
    if free - expected_physical < MIN_FREE_AFTER:
        raise Skipped("SKIPPED (space): %s free %.1f GiB - expected %.1f GiB < %d GiB" % (
            os.path.splitdrive(drive_path)[0], free / C.GiB, expected_physical / C.GiB, MIN_FREE_AFTER // C.GiB))
    return free


def paths(name):
    if not name.replace("_", "").replace("-", "").isalnum():
        raise C.HarnessError("bad disk name " + name)
    return os.path.join(C.DISK_DIR, name + ".img"), os.path.join(C.DISK_DIR, name + ".vmdk")


# ------------------------------------------------------------------------------------------------ descriptors
def descriptor_text(extent_path, sectors, access="RW", vmdk_dir=None):
    if access not in ("RW", "RDONLY"):
        raise C.HarnessError("extent access must be RW or RDONLY")
    ref = extent_path
    if vmdk_dir and C.norm(os.path.dirname(extent_path)) == C.norm(vmdk_dir):
        ref = os.path.basename(extent_path)
    cid = uuid.uuid4().hex[:8]
    return ("# Disk DescriptorFile\nversion=1\nencoding=\"UTF-8\"\nCID=%s\nparentCID=ffffffff\n"
            "createType=\"monolithicFlat\"\n\n# Extent description\n%s %d FLAT \"%s\" 0\n\n"
            "# The Disk Data Base\n#DDB\n\nddb.adapterType = \"lsilogic\"\nddb.virtualHWVersion = \"21\"\n"
            "ddb.longContentID = \"%s\"\n" % (cid, access, sectors, ref, uuid.uuid4().hex[:24] + cid))


def write_vmdk(vmdk, extent_path, access="RW"):
    C.assert_test_path(vmdk)
    size = os.path.getsize(extent_path)
    if size % C.SECTOR:
        raise C.HarnessError("%s: size not a sector multiple" % extent_path)
    with open(vmdk, "w", encoding="utf-8", newline="\n") as f:
        f.write(descriptor_text(extent_path, size // C.SECTOR, access, os.path.dirname(vmdk)))
    probs = vmtest.guard_vmdk(vmdk)
    if probs:
        C.remove_file(vmdk)
        raise C.HarnessError("guard (descriptor): " + "; ".join(probs))
    return vmdk


# ------------------------------------------------------------------------------------------------ GPT writer
def gpt_entry(t, i, first, last, attrs, name):
    return (uuid.UUID(t).bytes_le + uuid.UUID(i).bytes_le + struct.pack("<QQQ", first, last, attrs)
            + name.encode("utf-16-le").ljust(72, b"\0"))


def gpt_header(my, alt, first, last, disk, elba, ecrc):
    h = bytearray(struct.pack("<8sIIIIQQQQ16sQIII", b"EFI PART", 0x00010000, 92, 0, 0, my, alt, first, last,
                              uuid.UUID(disk).bytes_le, elba, 128, 128, ecrc))
    struct.pack_into("<I", h, 16, zlib.crc32(bytes(h)))
    return bytes(h).ljust(C.SECTOR, b"\0")


def build_gpt(size, parts, disk_guid):
    """(primary LBA 0..33 bytes, backup last-33-LBA bytes). parts: dicts type, guid, first, last, attrs, name."""
    n = size // C.SECTOR
    ents = b"".join(gpt_entry(p["type"], p["guid"], p["first"], p["last"], p.get("attrs", 0), p["name"])
                    for p in parts).ljust(128 * 128, b"\0")
    if len(ents) != 128 * 128:
        raise C.HarnessError("too many partitions")
    for p in parts:
        if p["first"] < 34 or p["last"] > n - 34 or p["first"] > p["last"]:
            raise C.HarnessError("partition %s outside the usable LBAs" % p["name"])
    ecrc = zlib.crc32(ents)
    mbr = bytearray(C.SECTOR)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, min(n - 1, 0xFFFFFFFF))
    mbr[510:512] = b"\x55\xAA"
    prim = bytes(mbr) + gpt_header(1, n - 1, 34, n - 34, disk_guid, 2, ecrc) + ents
    back = ents + gpt_header(n - 1, 1, 34, n - 34, disk_guid, n - 33, ecrc)
    return prim, back


def write_gpt_disk(path, size, parts, disk_guid=None, writes=()):
    """Sparse raw disk with a GPT and optional (offset, bytes) writes (e.g. a boot sector)."""
    C.assert_test_path(path)
    prim, back = build_gpt(size, parts, disk_guid or str(uuid.uuid4()))
    with open(path, "xb") as f:
        if not C.set_sparse(f):
            raise C.HarnessError("FSCTL_SET_SPARSE failed: " + path)
        f.write(prim)
        for off, data in writes:
            f.seek(off)
            f.write(data)
        f.seek(size - len(back))
        f.write(back)
    if os.path.getsize(path) != size:
        raise C.HarnessError("size mismatch for " + path)
    return path


def bitlocker_sector():
    """An NTFS-like boot sector with the BitLocker OEM ID -FVE-FS- at offset 3 (T2c, T3n(b))."""
    s = bytearray(C.SECTOR)
    s[0:3] = b"\xEB\x58\x90"
    s[3:11] = b"-FVE-FS-"
    struct.pack_into("<HB", s, 11, 512, 8)            # bytes/sector, sectors/cluster
    s[21] = 0xF8                                      # media descriptor
    s[510:512] = b"\x55\xAA"
    return bytes(s)


# ------------------------------------------------------------------------------------------------ tools
def run(argv, timeout=3600, cwd=None):
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, cwd=cwd)
    return p.returncode, p.stdout.decode(errors="replace")


def gpt_validate(*args, timeout=7200):
    """python -I gpt_validate.py <args> (read-only tool except mkimage; image paths must be TEST files)."""
    for a in args:
        if a.lower().endswith((".img", ".json")):
            C.assert_test_path(a)
    return run([sys.executable, "-I", C.GPT_VALIDATE] + list(args), timeout=timeout)


def gbdisk(*args, timeout=3600):
    """gbdisk.exe in image mode only (--image <TEST file>); --disk is refused here."""
    a = list(args)
    if "--disk" in a or "--image" not in a:
        raise C.HarnessError("gbdisk only with --image in the harness")
    C.assert_test_path(a[a.index("--image") + 1])
    if "--out" in a:
        C.assert_test_path(a[a.index("--out") + 1])
    return run([C.GBDISK] + a, timeout=timeout)


def mkimage(path, c_mib, d_mib):
    C.assert_test_path(path)
    if os.path.exists(path):
        raise C.HarnessError("exists: " + path)
    rc, out = gpt_validate("mkimage", path, "--c-mib", str(c_mib), "--d-mib", str(d_mib))
    if rc:
        raise C.HarnessError("mkimage failed: " + out)
    return out


def read_gpt(path):
    """gpt_validate.read_gpt over a file (info, problems)."""
    gv = load_gpt_validate()
    with open(path, "rb") as f:
        return gv.read_gpt(f, os.path.getsize(path))


_GV = None


def load_gpt_validate():
    """Import C:\\GB-build\\native\\dualboot\\tools\\gpt_validate.py by path (7.1)."""
    global _GV
    if _GV is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("gpt_validate", C.GPT_VALIDATE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _GV = mod
    return _GV


# ------------------------------------------------------------------------------------------------ recipes
def blank(name, size, expected_physical=64 * C.MiB):
    raw, vmdk = paths(name)
    space_check(expected_physical)
    C.mkdir(C.DISK_DIR)
    C.create_sparse(raw, size)
    write_vmdk(vmdk, raw)
    return raw, vmdk


def t2c(name="t2c"):
    """32 GiB GPT, one Basic Data partition whose first sector is NTFS-like with -FVE-FS- (R_BITLOCKER)."""
    raw, vmdk = paths(name)
    space_check(64 * C.MiB)
    C.mkdir(C.DISK_DIR)
    size = 32 * C.GiB
    first, last = 2048, 2048 + (1 * C.GiB) // C.SECTOR - 1
    write_gpt_disk(raw, size, [dict(type=C.TYPE_BASIC, guid=str(uuid.uuid4()), first=first, last=last,
                                    name="Basic data partition")],
                   writes=[(first * C.SECTOR, bitlocker_sector())])
    write_vmdk(vmdk, raw)
    return raw, vmdk


def host_shaped(name, c_mib=1024, d_mib=6144, expected_physical=512 * C.MiB):
    raw, vmdk = paths(name)
    space_check(expected_physical)
    C.mkdir(C.DISK_DIR)
    mkimage(raw, c_mib, d_mib)
    write_vmdk(vmdk, raw)
    return raw, vmdk


def t3nb(name="t3nb"):
    """Host-shaped disk whose C: (slot 2) starts with a BitLocker boot sector (mode 2: R_BITLOCKER)."""
    raw, vmdk = host_shaped(name)
    info, probs = read_gpt(raw)
    if probs:
        raise C.HarnessError("mkimage GPT problems: %s" % probs)
    c = [e for e in info["entries"] if e["slot"] == 2][0]
    with open(raw, "r+b") as f:
        f.seek(c["offset"])
        f.write(bitlocker_sector())
    return raw, vmdk


def t3(work_dir, name="t3"):
    """The dual-boot disk of groups G1-G4 (7.2): mkimage C 1024 / D 26624 MiB, gbdisk resize-image #4 -> 2 GiB,
    snapshot pre.json + whole-file sha256, expected_plan.json from gbdisk plan (read-only)."""
    raw, vmdk = paths(name)
    space_check(int(13.5 * C.GiB))
    C.mkdir(C.DISK_DIR)
    C.mkdir(work_dir)
    out = mkimage(raw, 1024, 26624)
    rc, o2 = gbdisk("resize-image", "--image", raw, "--number", "4", "--length", "2147483648", "--apply")
    if rc:
        raise C.HarnessError("gbdisk resize-image failed: " + o2)
    pre = os.path.join(work_dir, "pre.json")
    C.remove_file(pre)
    rc, o3 = gpt_validate("snapshot", raw, pre)
    if rc:
        raise C.HarnessError("snapshot failed: " + o3)
    plan = os.path.join(work_dir, "expected_plan.json")
    C.remove_file(plan)
    rc, o4 = gbdisk("plan", "--manifest", C.EXPORT_MANIFEST, "--anchor", "4", "--reserve", "1073741824",
                    "--image", raw, "--out", plan)
    if rc:
        raise C.HarnessError("gbdisk plan failed: " + o4)
    sha = C.sparse_sha256(raw)
    write_vmdk(vmdk, raw)
    meta = {"raw": raw, "pre": pre, "expected_plan": plan, "sha256_after_build": sha,
            "log": [out, o2, o3, o4]}
    C.write_json(os.path.join(work_dir, "t3_build.json"), meta)
    return meta


SPOT = 64 * 1024


def pattern_block(off):
    """The deterministic 64 KiB pattern written at byte offset `off` of a T1d disk (never all zero)."""
    import hashlib
    return (hashlib.sha256(b"GBT1D-pattern" + off.to_bytes(8, "little")).digest() * (SPOT // 32))


def t1d_spots(size, man):
    """Sorted byte offsets (64 KiB aligned) of the pattern spots of a T1d disk: one every 8 MiB, the first and last
    MiB of every partition of both the image layout and the grown-userdata layout, all of every blank partition of
    <= 256 MiB, 64 MiB at both ends of userdata (both layouts) and both gap ranges after userdata. Never LBA 0..2047
    and never the last 2081 LBAs (an internal disk must be blank there for mode 1: R_INTERNAL_NOT_BLANK)."""
    N = size // C.SECTOR
    lo, hi = 2048 * C.SECTOR, (N - 2081) * C.SECTOR
    wd = man["whole_disk"]
    T = wd["total_lba"]
    spots = set(range(8 * MiB_, hi - SPOT + 1, 8 * MiB_))

    def area(a, b):
        a = max(lo, (a // SPOT) * SPOT)
        b = min(hi, b)
        for o in range(a, b - SPOT + 1, SPOT):
            spots.add(o)
    ud = [p for p in wd["partitions"] if p["growable"]][0]
    grown_last = ((N - 33 - 2048) // 2048) * 2048 - 1
    for p in wd["partitions"]:
        f, l = p["first_lba"] * C.SECTOR, (p["last_lba"] + 1) * C.SECTOR
        area(f, f + MiB_)
        area(l - MiB_, l)
        if p["blank"] and l - f <= C.BIG_BLANK:
            area(f, l)
    uf = ud["first_lba"] * C.SECTOR
    for last in (ud["last_lba"], grown_last):
        ue = (last + 1) * C.SECTOR
        area(uf, uf + C.HEAD_TAIL)
        area(ue - C.HEAD_TAIL, ue)
        area(ue, ue + 4 * MiB_)
    area((ud["last_lba"] + 1) * C.SECTOR, (T - 33) * C.SECTOR)
    area((grown_last + 1) * C.SECTOR, (N - 33) * C.SECTOR)
    return sorted(o for o in spots if lo <= o and o + SPOT <= hi)


MiB_ = 1 << 20


def t1d(name="t1d", size=34359738368):
    """T1d: a 32 GiB 'used' internal disk: blank in LBA 0..2047 and the last 2081 LBAs (so that mode 1 accepts it),
    pattern data (pattern_block) at every t1d_spots offset elsewhere."""
    import json as _json
    man = _json.load(open(C.PAYLOAD_MANIFEST, encoding="utf-8"))
    raw, vmdk = paths(name)
    spots = t1d_spots(size, man)
    space_check(int(13.5 * C.GiB) + len(spots) * SPOT)
    C.mkdir(C.DISK_DIR)
    C.assert_test_path(raw)
    with open(raw, "xb") as f:
        if not C.set_sparse(f):
            raise C.HarnessError("FSCTL_SET_SPARSE failed: " + raw)
        for o in spots:
            f.seek(o)
            f.write(pattern_block(o))
        f.seek(size - C.SECTOR)
        f.write(bytes(C.SECTOR))
    if os.path.getsize(raw) != size:
        raise C.HarnessError("size mismatch for " + raw)
    write_vmdk(vmdk, raw)
    return raw, vmdk


RECIPES = {
    # name: (function, args) ; sizes per 7.2
    "t1a": lambda: blank("t1a", C.USB_SIZE, expected_physical=int(13.5 * C.GiB)),
    "t1b": lambda: blank("t1b", 34359738368, expected_physical=int(13.5 * C.GiB)),
    "t1c": lambda: blank("t1c", 34359738368, expected_physical=int(13.5 * C.GiB)),
    "t2a": lambda: host_shaped("t2a", 1024, 28672),
    "t2c": t2c,
    "t2d": lambda: blank("t2d", 8 * C.GiB),
    "t2f": lambda: blank("t2f", 32 * C.GiB),
    "t3na": lambda: host_shaped("t3na"),
    "t3nb": t3nb,
    "blank1g": lambda: blank("blank1g", 1 * C.GiB),
    "t1d": t1d,
}


def delete(name):
    raw, vmdk = paths(name)
    for p in (vmdk, raw, raw + ".gbstate.json"):
        C.remove_file(p)
    bk = raw + ".gbbackup"
    if os.path.isdir(bk):
        C.assert_test_path(bk)
        import shutil
        shutil.rmtree(bk)


# ------------------------------------------------------------------------------------------------ builder media
def receive_stream(stream, out_path, expected_size=None):
    """Write a builder_send.py stream sparse to out_path (new file). Returns (size, frames, bytes_written)."""
    import hashlib
    C.assert_test_path(out_path)

    def readn(n):
        b = bytearray()
        while len(b) < n:
            d = stream.read(n - len(b))
            if not d:
                raise C.HarnessError("builder stream ended early")
            b += d
        return bytes(b)

    def readline():
        b = bytearray()
        while not b.endswith(b"\n"):
            d = stream.read(1)
            if not d:
                raise C.HarnessError("builder stream ended early")
            b += d
            if len(b) > 4096:
                raise C.HarnessError("builder stream header too long")
        return bytes(b)
    if readline() != b"GBSPARSE1\n":
        raise C.HarnessError("not a GBSPARSE1 stream")
    hdr = json.loads(readline())
    size = int(hdr["size"])
    if expected_size is not None and size != expected_size:
        raise C.HarnessError("builder file size %d != expected %d" % (size, expected_size))
    h = hashlib.sha256()
    zero = bytes(C.MiB)
    frames = written = 0
    last_end = 0
    with open(out_path, "xb") as f:
        if not C.set_sparse(f):
            raise C.HarnessError("FSCTL_SET_SPARSE failed: " + out_path)
        while True:
            fh = readn(16)
            h.update(fh)
            off, ln = struct.unpack("<QQ", fh)
            if off == 0xFFFFFFFFFFFFFFFF:
                break
            if off < last_end or off + ln > size:
                raise C.HarnessError("bad frame %d+%d" % (off, ln))
            data = readn(ln)
            h.update(data)
            frames += 1
            pos = 0
            while pos < ln:
                a = off + pos
                n = min(ln - pos, C.MiB - (a % C.MiB))
                piece = data[pos:pos + n]
                if piece != zero[:n]:
                    f.seek(a)
                    f.write(piece)
                    written += n
                pos += n
            last_end = off + ln
        trailer = readn(65)
        if trailer[:64].decode() != h.hexdigest():
            raise C.HarnessError("builder stream hash mismatch")
        if f.seek(0, 2) < size:
            f.seek(size - C.SECTOR)
            f.write(bytes(C.SECTOR))
    if os.path.getsize(out_path) != size:
        raise C.HarnessError("received size %d != %d" % (os.path.getsize(out_path), size))
    return size, frames, written


def fetch_builder_file(remote_name, out_path, expected_physical):
    """Stream /var/tmp/gbiso/test/<remote_name> from the builder into out_path (sparse); then delete it there."""
    if "/" in remote_name:
        raise C.HarnessError("plain remote name only")
    space_check(expected_physical)
    C.mkdir(os.path.dirname(out_path))
    cmd = "python3 -I %s/builder_send.py %s/%s" % (C.REMOTE_TEST, C.REMOTE_TEST, remote_name)
    p = subprocess.Popen(C.SSH_BASE + [C.BUILDER, cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        res = receive_stream(p.stdout, out_path)
    except Exception:
        p.kill()
        C.remove_file(out_path)
        raise
    finally:
        rc = p.wait(timeout=600)
    if rc:
        C.remove_file(out_path)
        raise C.HarnessError("builder_send failed: " + p.stderr.read().decode(errors="replace"))
    C.ssh("rm -f %s/%s" % (C.REMOTE_TEST, remote_name))
    return res


# ------------------------------------------------------------------------------------------------ ISO hard link
def link_iso():
    """(Re)create the hard link D:\\GB-iso\\test\\iso\\googlebook-sb3-installer.iso -> the ISO (7.1)."""
    if not os.path.isfile(C.ISO):
        raise C.HarnessError("no ISO at " + C.ISO)
    C.mkdir(C.ISO_LINK_DIR)
    unlink_iso()
    os.link(C.ISO, C.ISO_LINK)
    if not os.path.samefile(C.ISO, C.ISO_LINK):
        raise C.HarnessError("hard link check failed")
    return C.ISO_LINK


def unlink_iso():
    """Delete the hard link only (the ISO keeps its read-only attribute: FILE_DISPOSITION_IGNORE_READONLY)."""
    if not os.path.lexists(C.ISO_LINK):
        return
    C.assert_test_path(C.ISO_LINK)
    try:
        os.remove(C.ISO_LINK)
        return
    except PermissionError:
        pass
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetFileInformationByHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    DELETE, SHARE_ALL, OPEN_EXISTING = 0x00010000, 7, 3
    h = k32.CreateFileW(C.ISO_LINK, DELETE, SHARE_ALL, None, OPEN_EXISTING, 0x00200000, None)
    if h in (None, ctypes.c_void_p(-1).value):
        raise C.HarnessError("cannot open the ISO link for deletion")
    try:
        flags = ctypes.c_ulong(0x1 | 0x2 | 0x10)   # DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE
        if not k32.SetFileInformationByHandle(h, 21, ctypes.byref(flags), ctypes.sizeof(flags)):
            raise C.HarnessError("deleting the ISO link failed (error %d)" % ctypes.get_last_error())
    finally:
        k32.CloseHandle(h)


def main(argv):
    if len(argv) >= 3 and argv[0] == "blank":
        print(blank(argv[1], int(argv[2])))
    elif len(argv) == 2 and argv[0] == "make" and argv[1] in RECIPES:
        print(RECIPES[argv[1]]())
    elif len(argv) == 2 and argv[0] == "guard":
        probs = vmtest.guard_vmdk(argv[1])
        print("\n".join(probs) or "GUARD OK")
        return 1 if probs else 0
    elif len(argv) == 2 and argv[0] == "delete":
        delete(argv[1])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
