r"""W5 VNNI measurement: the TWO throwaway VMs below D:\gb-w5test\ (adapted from the W4 rehearsal's w4vm.py).

Instances (select with --inst off|on as the first argument; default off):
  off  D:\gb-w5test\vm\gbw5.vmx      pipe \\.\pipe\gbw5_con    ESP = esp\vnni-off.img (gb.vnni=0, the baseline)
  on   D:\gb-w5test\vmon\gbw5on.vmx  pipe \\.\pipe\gbw5on_con  ESP = esp\vnni-on.img (== the integrate disk's own ESP)
Never the user's VM: the only vmx files this tool starts/stops are those two, their console pipes are their own (the
user's \\.\pipe\lapis_con is refused), disk writes go only into <instance dir>\disk.img (a build_usb.py --vm-test
--vm-test-w5 image or a verified sparse clone of it, never lapis.bin). 4096 MiB RAM, 2 vCPUs each.

  w5vm.py clone                     (--inst on) sparse copy of vm\disk.img (VM off, == pristine) to vmon\disk.img;
                                    whole-file sha256 of the source while copying and of the copy read back
  w5vm.py mkvm                      write the instance's vmx, disk.vmdk, empty serial0.log (VM off)
  w5vm.py guard                     pre-start checks (read-only); exit 0 = GUARD OK
  w5vm.py start | stop [soft|hard] | state
  w5vm.py pristine                  (once, before the first boot) record the GPT bytes and the sha256 of every region
                                    (GPT head/tail, gaps, every partition) and the whole file (must equal disk.img.sha256)
  w5vm.py reset                     back to the pristine record (VM off): the blank partitions (misc, metadata, frp,
                                    desktop_security_*, userdata) zeroed + deallocated (FSCTL_SET_ZERO_DATA), the GPT
                                    bytes restored, every region except EFI-SYSTEM re-hashed and compared
  w5vm.py state                     re-hash every region and compare with the pristine record (read-only)
  w5vm.py esp IMG                   write IMG (below D:\gb-w5test) into disk.img's EFI-SYSTEM (VM off), read back
  w5vm.py espsha | slot             sha256 of the EFI-SYSTEM | vbmeta_a/_b attrs (read-only)
  w5vm.py sh [-t SECS] CMD...       run CMD on the guest's root console (gb_console on ttyS1 = serial1 pipe)
  w5vm.py shf [-t SECS] FILE        run a local script's text there
  w5vm.py push LOCAL REMOTE | pull REMOTE LOCAL
  w5vm.py waitadb IP [SECS]         poll sys.boot_completed via adb IP:5555 (never the user VM's address)
Python -I -B. Every action is appended to D:\gb-w5test\w5vm.log.
"""
import base64, ctypes, hashlib, json, os, re, struct, subprocess, sys, time, uuid

ROOT = "D:\\gb-w5test"
VMRUN = r"C:\Program Files\VMware\VMware Workstation\vmrun.exe"
LOG = ROOT + "\\w5vm.log"
PRISTINE_GPT = ROOT + "\\state\\pristine-gpt.bin"
BLANK = ("misc", "metadata", "frp_persist", "desktop_security_persist", "desktop_security_storage", "userdata")
USER_VMX = r"C:\GB-x86\lapis-boot.vmx"
INSTANCES = {
    "off": {"dir": ROOT + "\\vm", "vmx": "gbw5.vmx", "pipe": r"\\.\pipe\gbw5_con",
            "pristine": ROOT + "\\state\\pristine.json",
            "name": "gb-w5test off (throwaway VNNI measurement, baseline gb.vnni=0)"},
    "on": {"dir": ROOT + "\\vmon", "vmx": "gbw5on.vmx", "pipe": r"\\.\pipe\gbw5on_con",
           "pristine": ROOT + "\\state\\pristine-on.json",
           "name": "gb-w5test on (throwaway VNNI measurement, patch on)"},
}
ALL_VMX = [os.path.normcase(i["dir"] + "\\" + i["vmx"]) for i in INSTANCES.values()]
INST = None


def select(inst):
    global INST, VMDIR, VMX, DISK, PIPE, PRISTINE, DISPLAY
    c = INSTANCES[inst]
    INST, VMDIR, VMX, DISK, PIPE, PRISTINE, DISPLAY = (inst, c["dir"], c["dir"] + "\\" + c["vmx"], c["dir"] + "\\disk.img",
                                                       c["pipe"], c["pristine"], c["name"])
    assert "lapis" not in PIPE.lower() and "lapis" not in VMX.lower() and "lapis" not in DISK.lower()


select(os.environ.get("GBW5_INST", "off"))


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S") + f" [{INST}] " + msg + "\n")


def running():
    out = subprocess.run([VMRUN, "-T", "ws", "list"], capture_output=True, text=True, timeout=60).stdout
    return [l.strip() for l in out.splitlines()[1:] if l.strip()]


def is_running():
    return any(os.path.normcase(p) == os.path.normcase(VMX) for p in running())


def user_vm_running():
    return any(os.path.normcase(p) == os.path.normcase(USER_VMX) for p in running())


# ---------------------------------------------------------------- VM files
def mkvm():
    if is_running():
        sys.exit("mkvm: the throwaway VM is running")
    sz = os.path.getsize(DISK)
    assert sz % 512 == 0
    vmx = "\n".join([
        '.encoding = "windows-1252"', 'config.version = "8"', 'virtualHW.version = "21"',
        'displayName = "' + DISPLAY + '"', 'guestOS = "other6xlinux-64"', 'firmware = "efi"',
        'uefi.secureBoot.enabled = "FALSE"', 'memSize = "4096"', 'numvcpus = "2"', 'uuid.action = "create"',
        'msg.autoAnswer = "TRUE"', 'rtc.diffFromUTC = "0"',
        # the user VM's CPUID masks (lapis-boot.vmx): AVX-VNNI hidden, so VEX VNNI code traps into k15's emulator
        'cpuid.7.1.eax = "---------------------------0----"', 'cpuid.7.ecx = "-----------------------0--------"',
        'pciBridge0.present = "TRUE"', 'pciBridge4.present = "TRUE"', 'pciBridge4.virtualDev = "pcieRootPort"',
        'pciBridge4.functions = "8"', 'nvme0.present = "TRUE"', 'nvme0:0.present = "TRUE"',
        'nvme0:0.fileName = "disk.vmdk"', 'nvme0:0.mode = "independent-persistent"',
        'serial0.present = "TRUE"', 'serial0.fileType = "file"', 'serial0.fileName = "serial0.log"',
        # root console (gb_console on ttyS1): this VM's OWN pipe, never \\.\pipe\lapis_con
        'serial1.present = "TRUE"', 'serial1.fileType = "pipe"', 'serial1.fileName = "' + PIPE + '"',
        'serial1.pipe.endPoint = "server"', 'serial1.tryNoRxLoss = "TRUE"', 'serial1.yieldOnMsrRead = "TRUE"',
        'serial1.startConnected = "TRUE"',
        'svga.present = "TRUE"', 'mks.enable3d = "TRUE"', 'svga.graphicsMemoryKB = "1048576"',
        'svga.vramSize = "268435456"', 'usb.present = "FALSE"', 'usb_xhci.present = "FALSE"', 'ehci.present = "FALSE"',
        'sound.present = "FALSE"', 'floppy0.present = "FALSE"',
        'ethernet0.present = "TRUE"', 'ethernet0.connectionType = "nat"', 'ethernet0.virtualDev = "vmxnet3"',
        'ethernet0.addressType = "generated"']) + "\n"
    vmdk = ("# Disk DescriptorFile\nversion=1\nCID=fffffffe\nparentCID=ffffffff\ncreateType=\"monolithicFlat\"\n\n"
            f"RW {sz // 512} FLAT \"disk.img\" 0\n\nddb.adapterType = \"lsilogic\"\nddb.virtualHWVersion = \"21\"\n")
    open(VMX, "w", encoding="ascii", newline="\n").write(vmx)
    open(VMDIR + "\\disk.vmdk", "w", encoding="ascii", newline="\n").write(vmdk)
    open(VMDIR + "\\serial0.log", "wb").close()
    print(f"vm files written: {VMX}; disk {sz:,} B = {sz // 512} sectors; console pipe {PIPE}")
    log("mkvm")


REPARSE = 0x400


def guard():
    import stat
    probs = []
    r = os.path.normcase(ROOT)

    def below(p):
        rp = os.path.normcase(os.path.abspath(p))
        if not rp.startswith(r + os.sep):
            return False
        cur = rp
        while len(cur) > len(r):
            st = os.lstat(cur)
            if st.st_file_attributes & REPARSE or stat.S_ISLNK(st.st_mode):
                probs.append(f"reparse point / link: {cur}"); return False
            cur = os.path.dirname(cur)
        return True

    txt = open(VMX, encoding="windows-1252").read()
    kv = {}
    for ln in txt.splitlines():
        m = re.match(r'\s*([^=#\s]+)\s*=\s*"(.*)"\s*$', ln)
        if m:
            kv[m.group(1).lower()] = m.group(2)
    low = txt.lower()
    for f in ("physicaldrive", "globalroot", "rawdevice", "fulldevice", "partitioneddevice", "lapis"):
        if f in low:
            probs.append(f"vmx: forbidden text {f!r}")
    for k in ("usb.present", "usb_xhci.present", "ehci.present"):
        if kv.get(k, "FALSE").upper() != "FALSE":
            probs.append(f"{k} = {kv.get(k)}")
    if int(kv.get("memsize", "0")) > 4096 or int(kv.get("numvcpus", "1")) > 2:
        probs.append(f"memsize {kv.get('memsize')} / numvcpus {kv.get('numvcpus')} above 4096 / 2")
    for k, v in sorted(kv.items()):
        if not k.endswith(".filename"):
            continue
        dev = k.rsplit(".", 1)[0]
        if kv.get(dev + ".filetype") == "pipe":
            if v != PIPE:
                probs.append(f"{k}: pipe {v} is not this VM's own {PIPE}")
            else:
                print(f"  {k} -> {v} (this VM's own console pipe)")
            continue
        if "\\\\.\\" in v or "\\\\?\\" in v:
            probs.append(f"{k}: device path {v}"); continue
        p = v if os.path.isabs(v) else os.path.join(VMDIR, v)
        if not os.path.lexists(p) or not below(p) or not stat.S_ISREG(os.lstat(p).st_mode):
            probs.append(f"{k}: {p} not a regular file below {ROOT}"); continue
        print(f"  {k} -> {p} ({os.path.getsize(p):,} B)")
        if p.lower().endswith(".vmdk"):
            d = open(p, encoding="ascii", errors="replace").read()
            ext = [ln for ln in d.splitlines() if re.match(r"\s*(RW|RDONLY|NOACCESS)\s", ln)]
            if 'createType="monolithicFlat"' not in d or len(ext) != 1:
                probs.append(f"{p}: not a one-extent monolithicFlat descriptor"); continue
            m = re.fullmatch(r'RW (\d+) FLAT "([^"\\/:]+)" 0', ext[0].strip())
            e = os.path.join(VMDIR, m.group(2)) if m else None
            if not m or os.path.normcase(e) != os.path.normcase(DISK) or not below(e):
                probs.append(f"{p}: extent is not {DISK}"); continue
            if os.path.getsize(e) != int(m.group(1)) * 512:
                probs.append("extent size mismatch")
            print(f"  extent {e}: {int(m.group(1)):,} sectors, file {os.path.getsize(e):,} B")
    others = [p for p in running() if os.path.normcase(p).startswith(r + os.sep) and os.path.normcase(p) not in ALL_VMX]
    if others:
        probs.append(f"an unknown test VM runs: {others}")
    if user_vm_running():
        print("  note: the user's VM is running (never touched by this tool)")
    for p in probs:
        print("PROBLEM:", p)
    print("GUARD OK" if not probs else f"GUARD FAILED: {len(probs)} problem(s)")
    return 0 if not probs else 1


def start():
    if guard():
        sys.exit("start refused by the guard")
    if is_running():
        print("already running"); return 0
    p = subprocess.run([VMRUN, "-T", "ws", "start", VMX, "nogui"], capture_output=True, text=True, timeout=300)
    print(f"start exit {p.returncode} at {time.strftime('%Y-%m-%dT%H:%M:%S')} {p.stdout.strip()} {p.stderr.strip()}")
    log(f"start rc={p.returncode}")
    return p.returncode


def stop(mode="soft"):
    assert mode in ("soft", "hard")
    if not is_running():
        print("not running"); return 0
    p = subprocess.run([VMRUN, "-T", "ws", "stop", VMX, mode], capture_output=True, text=True, timeout=300)
    t = time.time()
    while is_running() and time.time() - t < 180:
        time.sleep(2)
    print(f"stop {mode} exit {p.returncode} at {time.strftime('%Y-%m-%dT%H:%M:%S')}; running={is_running()} "
          f"{p.stdout.strip()} {p.stderr.strip()}")
    log(f"stop {mode} rc={p.returncode}")
    return p.returncode


# ---------------------------------------------------------------- disk
def gpt(disk=DISK):
    """Primary GPT header fields + every named partition (first, last LBA, attrs)."""
    with open(disk, "rb") as f:
        f.seek(512); h = f.read(92)
        assert h[:8] == b"EFI PART"
        alt, first_usable, last_usable = struct.unpack_from("<QQQ", h, 32)
        elba, n, esz = struct.unpack_from("<QII", h, 72)
        f.seek(elba * 512); raw = f.read(n * esz)
    parts = {}
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        name = e[56:128].decode("utf-16-le").rstrip("\0")
        if name:
            a, b = struct.unpack_from("<QQ", e, 32)
            parts[name] = (a, b, struct.unpack_from("<Q", e, 48)[0])
    return {"alt": alt, "first_usable": first_usable, "last_usable": last_usable, "parts": parts}


def _part(name, disk=DISK):
    p = gpt(disk)["parts"].get(name)
    if not p:
        sys.exit(f"no {name} partition")
    return p


def regions(disk=DISK):
    """Ordered, gap-free list of (name, start_byte, end_byte) covering the whole file."""
    g = gpt(disk)
    size = os.path.getsize(disk)
    ps = sorted((a * 512, (b + 1) * 512, n) for n, (a, b, _) in g["parts"].items())
    out, cur = [], 0
    for a, e, n in ps:
        assert a >= cur, (n, a, cur)
        if a > cur:
            out.append(("gpt-head" if cur == 0 else f"gap@{cur}", cur, a))
        out.append((n, a, e)); cur = e
    if cur < size:
        out.append(("gpt-tail", cur, size))
    return out


def hash_regions(whole=True):
    regs = regions()
    res, wh = {}, hashlib.sha256()
    t0 = time.time()
    with open(DISK, "rb", buffering=0) as f:
        for name, a, e in regs:
            h = hashlib.sha256(); f.seek(a); left = e - a
            while left:
                b = f.read(min(left, 16 << 20))
                if not b:
                    raise IOError("short read")
                h.update(b)
                if whole:
                    wh.update(b)
                left -= len(b)
            res[name] = h.hexdigest()
    return regs, res, (wh.hexdigest() if whole else None), time.time() - t0


def allocated():
    hi = ctypes.c_ulong(0)
    k = ctypes.windll.kernel32
    k.GetCompressedFileSizeW.restype = ctypes.c_ulong
    lo = k.GetCompressedFileSizeW(ctypes.c_wchar_p(DISK), ctypes.byref(hi))
    return (hi.value << 32) | lo


def is_sparse():
    a = ctypes.windll.kernel32.GetFileAttributesW(ctypes.c_wchar_p(DISK))
    return bool(a & 0x200)


def pristine():
    _off_and_unlocked()
    if os.path.exists(PRISTINE):
        sys.exit(f"{PRISTINE} exists already (pristine is recorded once)")
    os.makedirs(os.path.dirname(PRISTINE), exist_ok=True)
    want = open(DISK + ".sha256", encoding="ascii").read().split()[0]
    regs, res, whole, dt = hash_regions(True)
    print(f"hashed {len(regs)} regions in {dt:.0f} s; whole file {whole}")
    if whole != want:
        sys.exit(f"disk.img sha256 {whole} != disk.img.sha256 {want}: not the integrate step's image, stop")
    g = gpt()
    head = [r for r in regs if r[0] == "gpt-head"][0]
    tail = [r for r in regs if r[0] == "gpt-tail"][0]
    with open(DISK, "rb") as f:
        f.seek(head[1]); hb = f.read(head[2] - head[1])
        f.seek(tail[1]); tb = f.read(tail[2] - tail[1])
    with open(PRISTINE_GPT, "wb") as f:
        f.write(hb + tb)
    rec = {"disk": DISK, "size": os.path.getsize(DISK), "whole_sha256": whole, "regions": [list(r) for r in regs],
           "sha256": res, "gpt_head": [head[1], head[2]], "gpt_tail": [tail[1], tail[2]],
           "gpt_bytes_sha256": hashlib.sha256(hb + tb).hexdigest(),
           "attrs": {n: v[2] for n, v in g["parts"].items()}, "sparse": is_sparse(), "allocated": allocated(),
           "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    json.dump(rec, open(PRISTINE, "w", encoding="utf-8"), indent=1)
    print(f"pristine recorded: {PRISTINE} (+ {PRISTINE_GPT}, {len(hb) + len(tb)} B); sparse={rec['sparse']} "
          f"allocated={rec['allocated'] / 2**30:.2f} GiB")
    log(f"pristine whole={whole[:16]}")
    return 0


def compare_state(quiet=False):
    rec = json.load(open(PRISTINE, encoding="utf-8"))
    regs, res, whole, dt = hash_regions(True)
    assert [list(r) for r in regs] == rec["regions"], "region layout changed"
    diff = [n for n in res if res[n] != rec["sha256"][n]]
    non_esp = [n for n in diff if n != "EFI-SYSTEM"]
    print(f"state: hashed in {dt:.0f} s; whole {whole}; regions differing from pristine: {diff or 'none'}; "
          f"EFI-SYSTEM {res['EFI-SYSTEM'][:16]} (pristine {rec['sha256']['EFI-SYSTEM'][:16]}); "
          f"whole == pristine ({rec['whole_sha256'][:16]}): {whole == rec['whole_sha256']}")
    log(f"state whole={whole[:16]} diff={diff}")
    return non_esp, res, whole


def _zero_range(f, off, end):
    import msvcrt
    from ctypes import wintypes

    class FZDI(ctypes.Structure):
        _fields_ = [("FileOffset", ctypes.c_longlong), ("BeyondFinalZero", ctypes.c_longlong)]
    k = ctypes.windll.kernel32
    k.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.DeviceIoControl.restype = wintypes.BOOL
    info = FZDI(off, end)
    br = wintypes.DWORD(0)
    ok = k.DeviceIoControl(ctypes.c_void_p(msvcrt.get_osfhandle(f.fileno())), 0x000980C8, ctypes.byref(info),
                           ctypes.sizeof(info), None, 0, ctypes.byref(br), None)
    if not ok:
        raise OSError(f"FSCTL_SET_ZERO_DATA failed: {ctypes.GetLastError()}")


def reset():
    _off_and_unlocked()
    rec = json.load(open(PRISTINE, encoding="utf-8"))
    gb = open(PRISTINE_GPT, "rb").read()
    assert hashlib.sha256(gb).hexdigest() == rec["gpt_bytes_sha256"]
    regs = {r[0]: (r[1], r[2]) for r in rec["regions"]}
    before = allocated()
    with open(DISK, "r+b") as f:
        for n in BLANK:
            a, e = regs[n]
            _zero_range(f, a, e)
        h0, h1 = rec["gpt_head"]; t0, t1 = rec["gpt_tail"]
        f.seek(h0); f.write(gb[:h1 - h0])
        f.seek(t0); f.write(gb[h1 - h0:])
        f.flush(); os.fsync(f.fileno())
    print(f"reset: zeroed {', '.join(BLANK)}; GPT head/tail restored; allocated {before / 2**30:.2f} -> "
          f"{allocated() / 2**30:.2f} GiB")
    log("reset")
    non_esp, res, whole = compare_state()
    if non_esp:
        print(f"RESET FAILED: regions still differing from pristine: {non_esp}")
        return 1
    print("RESET OK: every region except EFI-SYSTEM equals the pristine record")
    return 0


def _set_sparse(f):
    import msvcrt
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.DeviceIoControl.restype = wintypes.BOOL
    br = wintypes.DWORD(0)
    if not k.DeviceIoControl(ctypes.c_void_p(msvcrt.get_osfhandle(f.fileno())), 0x000900C4, None, 0, None, 0,
                             ctypes.byref(br), None):
        raise OSError(f"FSCTL_SET_SPARSE failed: {ctypes.GetLastError()}")


def _sha_file(path, size):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        left = size
        while left:
            b = f.read(min(left, 16 << 20))
            if not b:
                raise IOError("short read")
            h.update(b); left -= len(b)
    return h.hexdigest()


def clone():
    """(--inst on) sparse copy of the off instance's disk.img, which must be off and still == its pristine record."""
    assert INST == "on", "clone is for --inst on"
    off = INSTANCES["off"]
    src = off["dir"] + "\\disk.img"
    if any(os.path.normcase(p) == os.path.normcase(off["dir"] + "\\" + off["vmx"]) for p in running()):
        sys.exit("clone: the off VM is running")
    if [n for n in os.listdir(off["dir"]) if n.lower().endswith(".lck")]:
        sys.exit("clone: lock folders in the off VM's folder")
    os.makedirs(VMDIR, exist_ok=True)
    _off_and_unlocked()
    if os.path.exists(DISK):
        sys.exit(f"clone: {DISK} exists already")
    rec = json.load(open(off["pristine"], encoding="utf-8"))
    want = open(src + ".sha256", encoding="ascii").read().split()[0]
    assert want == rec["whole_sha256"], "disk.img.sha256 != pristine record"
    size = os.path.getsize(src)
    assert size == rec["size"]
    tmp = DISK + ".partial"
    CH = 16 << 20
    Z = bytes(CH)
    t0 = time.time()
    hs = hashlib.sha256()
    written = 0
    with open(tmp, "wb") as fo:
        _set_sparse(fo)
        fo.truncate(size)
    with open(src, "rb") as fi, open(tmp, "r+b") as fo:
        pos = 0
        while pos < size:
            b = fi.read(min(CH, size - pos))
            if not b:
                raise IOError("short read")
            hs.update(b)
            if b != Z[:len(b)]:
                fo.seek(pos); fo.write(b); written += len(b)
            pos += len(b)
        fo.flush(); os.fsync(fo.fileno())
    src_sha = hs.hexdigest()
    print(f"clone: source read + copied in {time.time() - t0:.0f} s; {written / 2**30:.2f} GiB of non-zero chunks written; "
          f"source sha256 {src_sha}")
    if src_sha != want:
        sys.exit(f"clone: the source changed: {src_sha} != {want} (copy left as {tmp})")
    back = _sha_file(tmp, size)
    print(f"clone: copy read back: sha256 {back} ({'MATCH' if back == want else 'MISMATCH'})")
    if back != want:
        sys.exit("clone: copy mismatch (left as .partial)")
    os.rename(tmp, DISK)
    open(DISK + ".sha256", "w", encoding="ascii", newline="\n").write(f"{want}  disk.img\n")
    r2 = dict(rec)
    r2["disk"] = DISK
    r2["cloned_from"] = src
    r2["allocated"] = allocated()
    r2["sparse"] = is_sparse()
    r2["time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    json.dump(r2, open(PRISTINE, "w", encoding="utf-8"), indent=1)
    print(f"clone OK: {DISK} sparse={r2['sparse']} allocated {r2['allocated'] / 2**30:.2f} GiB; pristine record {PRISTINE}")
    log(f"clone from {src} sha={want[:16]} back={back[:16]}")
    return 0


def _off_and_unlocked():
    if is_running():
        sys.exit("the throwaway VM is running: power it off first")
    lck = [n for n in os.listdir(VMDIR) if n.lower().endswith(".lck")]
    if lck:
        sys.exit(f"lock folders present in {VMDIR}: {lck}")


def write_esp(img):
    _off_and_unlocked()
    assert os.path.normcase(os.path.abspath(img)).startswith(os.path.normcase(ROOT) + os.sep), img
    esp = open(img, "rb").read()
    a, b, _ = _part("EFI-SYSTEM")
    size = (b - a + 1) * 512
    assert size == len(esp), (size, len(esp))
    with open(DISK, "r+b") as f:
        f.seek(a * 512); old = hashlib.sha256(f.read(size)).hexdigest()
        f.seek(a * 512); f.write(esp); f.flush(); os.fsync(f.fileno())
        f.seek(a * 512); back = hashlib.sha256(f.read(size)).hexdigest()
    want = hashlib.sha256(esp).hexdigest()
    print(f"EFI-SYSTEM LBA {a}..{b}: old {old[:16]}, wrote {os.path.basename(img)} {want}, read back {back}: "
          f"{'MATCH' if back == want else 'MISMATCH'}")
    log(f"esp {img} old={old[:16]} new={want[:16]} back={back[:16]}")
    return 0 if back == want else 1


def esp_sha():
    a, b, _ = _part("EFI-SYSTEM")
    with open(DISK, "rb") as f:
        f.seek(a * 512); d = f.read((b - a + 1) * 512)
    print(f"disk EFI-SYSTEM sha256 {hashlib.sha256(d).hexdigest()}")


def slot():
    for s in ("a", "b"):
        _, _, at = _part("vbmeta_" + s)
        print(f"vbmeta_{s}: priority={(at >> 48) & 0xf} tries={(at >> 52) & 0xf} successful={(at >> 56) & 1} (attrs {at:#018x})")


# ---------------------------------------------------------------- console (gbsh.py's protocol, own pipe)
k32 = ctypes.windll.kernel32


def _open():
    for _ in range(50):
        try:
            return open(PIPE, "r+b", buffering=0)
        except OSError:
            time.sleep(0.2)
    raise OSError("cannot open " + PIPE)


def _avail(f):
    import msvcrt
    n = ctypes.c_ulong(0)
    if not k32.PeekNamedPipe(ctypes.c_void_p(msvcrt.get_osfhandle(f.fileno())), None, 0, None, ctypes.byref(n), None):
        raise OSError("PeekNamedPipe failed")
    return n.value


def run(cmd, timeout=60, echo=True):
    f = _open()
    tag = uuid.uuid4().hex[:10]
    beg, end = ("__GBBEG_%s__" % tag).encode(), ("__GBEND_%s__" % tag).encode()
    data = ('echo __GBB""EG_%s__\n%s\necho __GBE""ND_%s__ $?\n' % (tag, cmd.rstrip("\n"), tag)).encode()
    for i in range(0, len(data), 2048):
        f.write(data[i:i + 2048])
    buf, deadline, shown = b"", time.time() + timeout, 0
    while time.time() < deadline:
        n = _avail(f)
        if not n:
            time.sleep(0.03); continue
        buf += f.read(n)
        b = buf.find(beg + b"\n")
        if b >= 0 and echo:
            body = buf[b + len(beg) + 1:]
            e = body.find(end)
            out = body[:e] if e >= 0 else body[:max(0, len(body) - len(end) - 8)]
            if len(out) > shown:
                sys.stdout.buffer.write(out[shown:].replace(b"\r", b"")); sys.stdout.flush(); shown = len(out)
        e = buf.find(end + b" ")
        if e >= 0 and buf.find(b"\n", e) >= 0:
            st = buf[e + len(end) + 1:buf.find(b"\n", e)].strip()
            f.close()
            body = buf[buf.find(beg + b"\n") + len(beg) + 1:e] if b >= 0 else b""
            return int(st or b"0"), body.replace(b"\r", b"")
    f.close()
    sys.stderr.write("\nw5vm: console TIMEOUT after %ss\n" % timeout)
    return 124, buf


def push(local, remote, timeout=900):
    raw = open(local, "rb").read()
    b64 = base64.b64encode(raw).decode()
    lines = "\n".join(b64[i:i + 1000] for i in range(0, len(b64), 1000))
    cmd = ("/system/bin/toybox base64 -d > '%s.gbtmp' <<'GBEOF'\n%s\nGBEOF\n"
           "/system/bin/toybox mv -f '%s.gbtmp' '%s' && /system/bin/toybox md5sum '%s'") % (remote, lines, remote, remote, remote)
    st, out = run(cmd, timeout, echo=False)
    ok = hashlib.md5(raw).hexdigest().encode() in out
    print("push %s -> %s: %d bytes, md5 %s" % (local, remote, len(raw), "OK" if ok else "MISMATCH"))
    return 0 if ok else 1


def pull(remote, local, timeout=900):
    st, out = run("/system/bin/toybox base64 -w 0 '%s'; echo" % remote, timeout, echo=False)
    if st:
        sys.exit("pull failed: " + out.decode(errors="replace"))
    open(local, "wb").write(base64.b64decode(out.strip()))
    print("pull %s -> %s: %d bytes" % (remote, local, os.path.getsize(local)))
    return 0


ADB = r"C:\GB-x86\host\scrcpy-win64-v5.0.1\adb.exe"
USER_VM_ADB = "192.168.115.133"          # the user's VM: never addressed by this tool


def adb(ip, *args, timeout=60):
    assert ip != USER_VM_ADB and not ip.startswith(USER_VM_ADB + ":"), "refusing the user's VM"
    return subprocess.run([ADB, "-s", f"{ip}:5555"] + list(args), capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def waitboot_adb(ip, limit=900):
    """Wait for sys.boot_completed=1 through adb only."""
    assert ip != USER_VM_ADB
    t0 = time.time()
    while time.time() - t0 < limit:
        if not is_running():
            print("VM not running"); return 2
        subprocess.run([ADB, "connect", f"{ip}:5555"], capture_output=True, text=True, timeout=30)
        try:
            p = adb(ip, "shell", "getprop sys.boot_completed; cat /proc/uptime", timeout=20)
            parts = p.stdout.split()
        except subprocess.TimeoutExpired:
            parts = []
        if parts[:1] == ["1"]:
            print(f"boot_completed=1 after {time.time() - t0:.0f} s of waiting; guest uptime {parts[1]} s")
            log(f"boot_completed (adb) uptime={parts[1]}")
            return 0
        time.sleep(5)
    print(f"no boot_completed within {limit} s")
    return 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--inst"]:
        select(a[1]); a = a[2:]
    if not a:
        sys.exit(__doc__)
    c = a.pop(0)
    if c == "clone":
        sys.exit(clone())
    elif c == "mkvm":
        mkvm()
    elif c == "guard":
        sys.exit(guard())
    elif c == "start":
        sys.exit(start())
    elif c == "stop":
        sys.exit(stop(a[0] if a else "soft"))
    elif c == "state":
        if a[:1] == ["vm"]:
            print("running" if is_running() else "off", running())
        else:
            ne, _, _ = compare_state()
            sys.exit(1 if ne else 0)
    elif c == "pristine":
        sys.exit(pristine())
    elif c == "reset":
        sys.exit(reset())
    elif c == "esp":
        sys.exit(write_esp(a[0]))
    elif c == "espsha":
        esp_sha()
    elif c == "slot":
        slot()
    elif c in ("sh", "shf"):
        t = 60
        if a[:1] == ["-t"]:
            t = float(a[1]); a = a[2:]
        cmd = open(a[0], encoding="utf-8").read() if c == "shf" else " ".join(a)
        st, _ = run(cmd, t)
        sys.exit(st)
    elif c == "push":
        sys.exit(push(a[0], a[1]))
    elif c == "pull":
        sys.exit(pull(a[0], a[1]))
    elif c == "waitadb":
        sys.exit(waitboot_adb(a[0], float(a[1]) if len(a) > 1 else 900))
    else:
        sys.exit(__doc__)
