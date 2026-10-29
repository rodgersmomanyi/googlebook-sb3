"""gb_vmbackup.py -- back up, verify, compare and restore the user's Googlebook VMware VM (W4 VM_UPGRADE.md).

  python -I -B gb_vmbackup.py backup  DEST [--src DIR] [--vmx NAME] [--need-gb N] [--foreground] [--no-disk]
  python -I -B gb_vmbackup.py verify  DEST [--foreground]
  python -I -B gb_vmbackup.py compare DEST [--src DIR] [--foreground]
  python -I -B gb_vmbackup.py restore DEST --yes [--esp-only] [--src DIR] [--vmx NAME]

DIR defaults to C:\\GB-x86, NAME to lapis-boot.vmx. DEST is a new or empty folder (D:\\GB-vm-backup\\<date>).
backup   refuses while VMware runs the VM (vmrun list, VMware lock folders); refuses unless DEST's drive has N GB
         (default 26) + 3 GB floor + 1 GB margin free, and aborts (deleting the partial copy) if that drive falls
         below 4 GB during the copy. Copies the small VM files, the raw EFI-SYSTEM partition and both GPT regions,
         then lapis.bin as an NTFS sparse file (only non-zero 64 KiB chunks are written); sha256 of the source is
         taken while copying and the copy is re-read and compared; lapis.bin must not change during the copy.
         Writes SHA256SUMS + manifest.json and makes every backup file read-only. Runs in Windows background
         processing mode (very low I/O priority) unless --foreground. --no-disk: everything except the lapis.bin
         copy (the live lapis.bin is still hashed into manifest.json, to pair these extras with an existing full copy).
verify   re-hashes every file against SHA256SUMS ("<sha> *<name>" or "<sha>  <name>" lines). verify and compare also
         run in background processing mode unless --foreground.
compare  lists which GPT partitions of the live lapis.bin differ from DEST\\lapis.bin (read-only).
restore  VM must be off. Verifies the backup first. --esp-only (needs DEST\\manifest.json + lapis-esp.img) writes only
         the saved EFI-SYSTEM image back (checks the partition still has the saved offset/size). Full (needs
         DEST\\SHA256SUMS + lapis.bin, so it also works on a plain copy): rewrites only the 4 MiB blocks of lapis.bin
         that differ from the backup (in place: no extra space on C:), re-hashes lapis.bin against SHA256SUMS, restores the small
         files (the current ones are kept in DEST\\..\\pre-restore-<time>\\ first) and makes them writable.
Reads lapis.bin "rb" except restore. Standard library only; Windows only (sparse files, background mode).
"""
import ctypes, hashlib, json, msvcrt, os, shutil, stat, struct, subprocess, sys, time, zlib
from ctypes import wintypes

VMRUN = r"C:\Program Files\VMware\VMware Workstation\vmrun.exe"
DISK = "lapis.bin"
SMALL = ["lapis-boot.vmx", "lapis-boot.vmxf", "lapis-boot.nvram", "lapis-boot.vmsd", "lapis.vmdk", "lapis-data.vmdk",
         "lapis.bin.gptbak", "serial0.log"]
REQUIRED = {"lapis-boot.vmx", "lapis-boot.nvram", "lapis.vmdk"}
RESTORE_SMALL = ["lapis-boot.vmx", "lapis-boot.vmxf", "lapis-boot.nvram", "lapis.vmdk"]
CH, BLK = 64 << 10, 4 << 20            # NTFS sparse granularity (16 x 4 KiB clusters); read block
GB = 10 ** 9
FLOOR, MARGIN = 3 * GB, 1 * GB
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.GetCurrentProcess.restype = wintypes.HANDLE
K32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
K32.GetCompressedFileSizeW.restype = wintypes.DWORD
K32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
K32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]


def die(msg):
    sys.exit(f"gb_vmbackup: {msg}")


def opt(args, name, default=None):
    if name not in args:
        return default
    i = args.index(name)
    if i + 1 >= len(args):
        die(f"{name} needs a value")
    return args[i + 1]


def background():
    K32.SetPriorityClass(K32.GetCurrentProcess(), 0x00100000)        # PROCESS_MODE_BACKGROUND_BEGIN


def set_sparse(f):
    br = wintypes.DWORD()
    if not K32.DeviceIoControl(msvcrt.get_osfhandle(f.fileno()), 0x000900C4, None, 0, None, 0, ctypes.byref(br), None):
        raise OSError(ctypes.get_last_error(), "FSCTL_SET_SPARSE failed")


def allocated(path):
    hi = wintypes.DWORD()
    lo = K32.GetCompressedFileSizeW(path, ctypes.byref(hi))
    if lo == 0xFFFFFFFF and ctypes.get_last_error():
        return None
    return (hi.value << 32) + lo


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb", buffering=0) as f:
        for b in iter(lambda: f.read(BLK), b""):
            h.update(b)
    return h.hexdigest()


def gpt(path):
    """{name: (start_byte, end_byte, attrs)} from the primary GPT (header and entry CRCs checked)."""
    with open(path, "rb") as f:
        f.seek(512)
        h = f.read(512)
        if h[:8] != b"EFI PART":
            die(f"{path}: no GPT header at LBA 1")
        hsz, crc = struct.unpack_from("<II", h, 12)
        hh = bytearray(h[:hsz]); hh[16:20] = bytes(4)
        if zlib.crc32(hh) != crc:
            die(f"{path}: GPT header CRC mismatch")
        ent_lba, n, esz, ecrc = struct.unpack_from("<QIII", h, 72)
        f.seek(ent_lba * 512)
        ents = f.read(n * esz)
        if zlib.crc32(ents) != ecrc:
            die(f"{path}: GPT entry array CRC mismatch")
    parts = {}
    for i in range(n):
        e = ents[i * esz:(i + 1) * esz]
        if e[:16] == bytes(16):
            continue
        first, last, attrs = struct.unpack_from("<QQQ", e, 32)
        parts[e[56:128].decode("utf-16-le").split("\0")[0]] = (first * 512, (last + 1) * 512, attrs)
    return parts


def slot_attrs(parts):
    """vbmeta_<x> GPT attributes as (priority, tries, successful), the android-desktop boot_control encoding."""
    out = {}
    for x in "ab":
        a = parts.get("vbmeta_" + x, (0, 0, 0))[2]
        out[x] = ((a >> 48) & 15, (a >> 52) & 15, (a >> 56) & 1)
    return out


def vm_guard(src, vmx):
    """(problems, stale REDO lock folders). Problems = VMware runs the VM or holds its locks."""
    probs = []
    try:
        out = subprocess.run([VMRUN, "-T", "ws", "list"], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        probs.append(f"vmrun list failed ({e}); cannot prove the VM is off")
    else:
        if os.path.join(src, vmx).lower() in out.lower():
            probs.append(f"VMware lists {os.path.join(src, vmx)} as running")
    for n in (vmx + ".lck", "lapis.vmdk.lck", DISK + ".lck"):
        if os.path.exists(os.path.join(src, n)):
            probs.append(f"VMware lock {n} present")
    stale = sorted(n for n in os.listdir(src) if n.lower().endswith(".lck") and ".redo_" in n.lower())
    return probs, stale


def sparse_copy(src, dst, guard_dir):
    """Copy src to a new NTFS sparse dst writing only non-zero 64 KiB chunks. Returns (sha256 of src, bytes written)."""
    h, size, written, last = hashlib.sha256(), os.path.getsize(src), 0, 0
    zero, zblk = bytes(CH), bytes(BLK)
    with open(src, "rb", buffering=0) as fi, open(dst, "wb") as fo:
        set_sparse(fo)
        off = 0
        for b in iter(lambda: fi.read(BLK), b""):
            h.update(b)
            if b != zblk[:len(b)]:
                mv, i = memoryview(b), 0
                while i < len(b):
                    if mv[i:i + CH] == zero[:len(mv[i:i + CH])]:
                        i += CH
                        continue
                    j = i + CH
                    while j < len(b) and mv[j:j + CH] != zero[:len(mv[j:j + CH])]:
                        j += CH
                    j = min(j, len(b))
                    fo.seek(off + i); fo.write(mv[i:j]); written += j - i
                    i = j
            off += len(b)
            if written - last >= GB:
                last = written
                if shutil.disk_usage(guard_dir).free < FLOOR + MARGIN:
                    raise RuntimeError("destination free space fell below the 3 GB floor + 1 GB margin")
        if off != size:
            raise RuntimeError(f"read {off} bytes, expected {size}")
        fo.truncate(size)
        fo.flush(); os.fsync(fo.fileno())
    return h.hexdigest(), written


def read_sums(dest):
    sums = {}
    for ln in open(os.path.join(dest, "SHA256SUMS"), encoding="ascii"):
        if ln.strip():
            s, n = ln.split(None, 1)
            sums[n.strip().lstrip("*")] = s
    return sums


def cmd_backup(dest, src, vmx, need_gb, fg, no_disk=False):
    if not fg:
        background()
    probs, stale = vm_guard(src, vmx)
    if probs:
        die("refused: " + "; ".join(probs))
    lapis = os.path.join(src, DISK)
    if os.path.exists(dest) and os.listdir(dest):
        die(f"{dest} exists and is not empty")
    os.makedirs(dest, exist_ok=True)
    free0 = shutil.disk_usage(dest).free
    if free0 < need_gb * GB + FLOOR + MARGIN:
        os.rmdir(dest)
        die(f"refused: {dest} drive has {free0 / GB:.2f} GB free, need {need_gb} GB + 3 GB floor + 1 GB margin")
    t0 = time.time()
    st0 = os.stat(lapis)
    parts = gpt(lapis)
    man = {"tool": "gb_vmbackup.py", "started_local": time.strftime("%Y-%m-%d %H:%M:%S"), "src": src, "vmx": vmx,
           "stale_redo_locks": stale, "dest_free_before": free0, "files": {}}
    sums = {}
    for n in SMALL:
        p = os.path.join(src, n)
        if not os.path.exists(p):
            if n in REQUIRED:
                die(f"missing {p}")
            continue
        shutil.copy2(p, os.path.join(dest, n))
        sums[n] = sha_file(os.path.join(dest, n))
        if sums[n] != sha_file(p):
            die(f"{n}: copy differs from the source")
        man["files"][n] = {"bytes": os.path.getsize(p), "mtime": os.stat(p).st_mtime}
    es, ee, _ = parts["EFI-SYSTEM"]
    with open(lapis, "rb") as f:
        f.seek(es); esp = f.read(ee - es)
        f.seek(0); g1 = f.read(34 * 512)
        f.seek(st0.st_size - 33 * 512); g2 = f.read(33 * 512)
    for n, d in (("lapis-esp.img", esp), ("gpt-primary.bin", g1), ("gpt-backup.bin", g2)):
        with open(os.path.join(dest, n), "wb") as f:
            f.write(d)
        sums[n] = hashlib.sha256(d).hexdigest()
    part = os.path.join(dest, DISK + ".part")
    if no_disk:
        s_src, written = sha_file(lapis), 0
    else:
        try:
            s_src, written = sparse_copy(lapis, part, dest)
        except BaseException as e:
            if os.path.exists(part):
                os.remove(part)
            die(f"copy aborted, partial copy deleted: {e}")
    st1 = os.stat(lapis)
    if (st0.st_size, st0.st_mtime_ns) != (st1.st_size, st1.st_mtime_ns):
        if os.path.exists(part):
            os.remove(part)
        die("lapis.bin changed during the backup (is the VM running?); partial copy deleted")
    if not no_disk:
        s_dst = sha_file(part)
        if s_dst != s_src:
            die(f"verification failed: source {s_src} != copy {s_dst} (copy kept as {part})")
        os.replace(part, os.path.join(dest, DISK))
        sums[DISK] = s_src
    man.update({"lapis_bin": {"bytes": st0.st_size, "mtime": st0.st_mtime, "sha256": s_src, "copied": not no_disk,
                              "nonzero_written": written,
                              "allocated_on_dest": None if no_disk else allocated(os.path.join(dest, DISK))},
                "esp": {"offset": es, "bytes": ee - es, "sha256": sums["lapis-esp.img"]},
                "gpt_sha256": {"primary": sums["gpt-primary.bin"], "backup": sums["gpt-backup.bin"]},
                "partitions": {k: [v[0], v[1] - v[0]] for k, v in parts.items()}, "slot_attrs": slot_attrs(parts),
                "seconds": round(time.time() - t0, 1), "finished_local": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dest_free_after": shutil.disk_usage(dest).free})
    with open(os.path.join(dest, "manifest.json"), "w", encoding="ascii") as f:
        json.dump(man, f, indent=1)
    sums["manifest.json"] = sha_file(os.path.join(dest, "manifest.json"))
    with open(os.path.join(dest, "SHA256SUMS"), "w", encoding="ascii", newline="\n") as f:
        f.write("".join(f"{s} *{n}\n" for n, s in sorted(sums.items())))
    for n in os.listdir(dest):
        os.chmod(os.path.join(dest, n), stat.S_IREAD)
    print(f"backup OK: {dest}\n  lapis.bin {st0.st_size:,} B sha256 {s_src}\n  written {written:,} B, allocated "
          f"{man['lapis_bin']['allocated_on_dest'] or 0:,} B; ESP {ee - es:,} B at {es}; slots {man['slot_attrs']}\n"
          f"  {man['seconds']} s; dest free {man['dest_free_after'] / GB:.2f} GB"
          + (f"\n  NOTE stale VMware REDO lock folders in {src}: {stale}" if stale else ""))


def cmd_verify(dest):
    bad = 0
    for n, s in sorted(read_sums(dest).items()):
        got = sha_file(os.path.join(dest, n))
        print(f"  {'ok ' if got == s else 'BAD'} {n}")
        bad += got != s
    if os.path.exists(os.path.join(dest, DISK)):
        a = allocated(os.path.join(dest, DISK))
        print(f"lapis.bin allocated on disk: {a or 0:,} B of {os.path.getsize(os.path.join(dest, DISK)):,} B")
    if bad:
        die(f"verify: {bad} file(s) differ")
    print("verify OK")


def cmd_compare(dest, src):
    live, bak = os.path.join(src, DISK), os.path.join(dest, DISK)
    if os.path.getsize(live) != os.path.getsize(bak):
        die("lapis.bin size differs from the backup")
    parts = sorted((s, e, n) for n, (s, e, _) in gpt(live).items())
    diff, off = {}, 0
    with open(live, "rb", buffering=0) as a, open(bak, "rb", buffering=0) as b:
        for x in iter(lambda: a.read(BLK), b""):
            y = b.read(len(x))
            if x != y:
                for i in range(0, len(x), CH):
                    if x[i:i + CH] != y[i:i + CH]:
                        o = off + i
                        n = next((n for s, e, n in parts if s <= o < e), "(GPT / outside partitions)")
                        diff[n] = diff.get(n, 0) + min(CH, len(x) - i)
            off += len(x)
    for n, v in sorted(diff.items()):
        print(f"  differs: {n:28s} {v / 2 ** 20:10.2f} MiB")
    print("compare: identical" if not diff else f"compare: {len(diff)} region(s) differ")
    return diff


def cmd_restore(dest, src, vmx, esp_only):
    probs, _ = vm_guard(src, vmx)
    if probs:
        die("refused: " + "; ".join(probs))
    sums, lapis = read_sums(dest), os.path.join(src, DISK)
    if esp_only:
        man = json.load(open(os.path.join(dest, "manifest.json"), encoding="ascii"))
        img = os.path.join(dest, "lapis-esp.img")
        if sha_file(img) != sums["lapis-esp.img"]:
            die("lapis-esp.img does not match SHA256SUMS")
        es, ee, _ = gpt(lapis)["EFI-SYSTEM"]
        if (es, ee - es) != (man["esp"]["offset"], man["esp"]["bytes"]):
            die(f"EFI-SYSTEM is now {es}+{ee - es}, the backup has {man['esp']['offset']}+{man['esp']['bytes']}")
        data = open(img, "rb").read()
        with open(lapis, "r+b") as f:
            f.seek(es); f.write(data); f.flush(); os.fsync(f.fileno())
        with open(lapis, "rb") as f:
            f.seek(es); back = hashlib.sha256(f.read(ee - es)).hexdigest()
        if back != sums["lapis-esp.img"]:
            die("ESP read-back does not match the backup")
        print(f"restore --esp-only OK: {ee - es:,} B at {es}, sha256 {back}")
        return
    if sha_file(os.path.join(dest, DISK)) != sums[DISK]:
        die("the backup lapis.bin does not match SHA256SUMS")
    if os.path.getsize(lapis) != os.path.getsize(os.path.join(dest, DISK)):
        die("lapis.bin size differs from the backup")
    keep = os.path.join(os.path.dirname(os.path.abspath(dest)), "pre-restore-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(keep)
    for n in RESTORE_SMALL:
        if os.path.exists(os.path.join(src, n)):
            shutil.copy2(os.path.join(src, n), os.path.join(keep, n))
    changed, off = 0, 0
    with open(lapis, "r+b", buffering=0) as a, open(os.path.join(dest, DISK), "rb", buffering=0) as b:
        for y in iter(lambda: b.read(BLK), b""):
            a.seek(off)
            if a.read(len(y)) != y:
                a.seek(off); a.write(y); changed += len(y)
            off += len(y)
        os.fsync(a.fileno())
    got = sha_file(lapis)
    if got != sums[DISK]:
        die(f"lapis.bin after restore {got} != backup {sums[DISK]}")
    for n in RESTORE_SMALL:
        if n in sums:
            p = os.path.join(src, n)
            if os.path.exists(p):
                os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
            shutil.copy2(os.path.join(dest, n), p)
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
            if sha_file(p) != sums[n]:
                die(f"{n}: restored copy does not match the backup")
    print(f"restore OK: rewrote {changed:,} B of lapis.bin (sha256 {got}); small files restored; previous ones kept in "
          f"{keep}")


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0] not in ("backup", "verify", "compare", "restore"):
        sys.exit(__doc__)
    cmd, dest = a[0], a[1]
    src, vmx = opt(a, "--src", r"C:\GB-x86"), opt(a, "--vmx", "lapis-boot.vmx")
    if cmd == "backup":
        nd = "--no-disk" in a
        cmd_backup(dest, src, vmx, float(opt(a, "--need-gb", "0.5" if nd else "26")), "--foreground" in a, nd)
    elif cmd == "verify":
        if "--foreground" not in a:
            background()
        cmd_verify(dest)
    elif cmd == "compare":
        if "--foreground" not in a:
            background()
        cmd_compare(dest, src)
    else:
        if "--yes" not in a:
            die("restore overwrites the VM image: pass --yes")
        cmd_restore(dest, src, vmx, "--esp-only" in a)


if __name__ == "__main__":
    main()
