"""Shared pieces of the TEST harness (DESIGN.md section 7): paths, safety asserts, marker parsing, sparse file I/O,
the builder ssh channel, and the per-test result record.

Host side only (Windows, python -I). Nothing here touches a disk device: every file this module creates or writes must
lie below D:\\GB-iso\\test\\ or C:\\GB-x86\\iso\\test\\ (assert_test_path), and the builder channel only runs commands
in /var/tmp/gbiso/test/.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------------------------------------ constants (8.3)
SECTOR = 512
MiB = 1 << 20
GiB = 1 << 30
ALIGN = MiB
CHUNK = 536870912
RESERVE = 1073741824
USERDATA_MIN = 8589934592
HEAD_TAIL = 64 * MiB
BIG_BLANK = 256 * MiB
TYPE_ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
TYPE_BASIC = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
TYPE_MSR = "e3c9e316-0b5c-4db8-817d-f92df00215ae"
TYPE_WINRE = "de94bba4-06d1-4d40-a16a-bfd50179d6ac"
TYPE_LINUX = "0fc63daf-8483-4772-8e79-3d69d8477de4"
EFI_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
GB_NAMES = ["EFI-SYSTEM", "boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b",
            "vbmeta_a", "vbmeta_b", "pvmfw_a", "pvmfw_b", "misc", "metadata", "frp_persist",
            "desktop_security_persist", "desktop_security_storage", "super", "userdata"]
VOLUME_LABEL = "GBSB3INST"
BOOT_LABEL = "Googlebook OS"
LOADER = "\\EFI\\BOOT\\BOOTX64.EFI"
GBINSTALL_FILES = sorted(["install_state.json", "plan.json", "layout_pre_install.json", "layout_post_install.json",
                          "gpt_pre_install_primary.bin", "gpt_pre_install_backup.bin", "gpt_post_install_primary.bin",
                          "gpt_post_install_backup.bin", "manifest_installed.json", "efivars_pre.json",
                          "efivars_post.json", "install.log", "README.txt", "SHA256SUMS"])

# ------------------------------------------------------------------------------------------------ paths (7.1)
TEST_ROOT = r"D:\GB-iso\test"
VM_DIR = os.path.join(TEST_ROOT, "vm")
VMX = os.path.join(VM_DIR, "gbiso-test.vmx")
NVRAM_NAME = "gbiso-test.nvram"
DISK_DIR = os.path.join(TEST_ROOT, "disks")
ISO_LINK_DIR = os.path.join(TEST_ROOT, "iso")
ISO = r"D:\GB-iso\googlebook-sb3-installer.iso"
ISO_LINK = os.path.join(ISO_LINK_DIR, "googlebook-sb3-installer.iso")
BUILD_RECORD = r"D:\GB-iso\googlebook-sb3-installer.build.json"
PAYLOAD_MANIFEST = r"D:\GB-iso\googlebook-sb3-installer.manifest.json"
PIPE = r"\\.\pipe\gbiso_con"
VMRUN = r"C:\Program Files\VMware\VMware Workstation\vmrun.exe"
USB_IMG = r"C:\GB-x86\out\googlebook-sb3-usb.img"
USB_SHA = "b2714a638ac329711de416285ba381946b0f83a26bd0e6d91c0f8e432936ebe3"
USB_SIZE = 28814885376
VERIFY_IMAGE = r"C:\GB-x86\verify_image.py"
DUALBOOT_DIR = r"C:\GB-build\native\dualboot"
GPT_VALIDATE = os.path.join(DUALBOOT_DIR, "tools", "gpt_validate.py")
GBDISK = os.path.join(DUALBOOT_DIR, "gbdisk.exe")
EXPORT_DIR = r"C:\GB-x86\iso\work\export"
EXPORT_MANIFEST = os.path.join(EXPORT_DIR, "manifest.json")
FORBIDDEN_SCAN = r"C:\GB-x86\iso\build\forbidden_scan.py"
WORK = os.path.join(HERE, "work")
LOGS = os.path.join(HERE, "logs")
WRITABLE_ROOTS = (TEST_ROOT, HERE)

# never written, never opened for writing, never passed to a VM
PROTECTED_PATHS = (USB_IMG, r"C:\GB-x86\lapis.bin", r"C:\GB-x86\lapis-boot.vmx", r"C:\GB-x86\gbsh.py")

# ------------------------------------------------------------------------------------------------ builder
BUILDER = "builder@192.168.115.132"
BUILDER_KEY = "C:/GB-build/builder_key"
REMOTE_TEST = "/var/tmp/gbiso/test"
REMOTE_ISO = "/var/tmp/gbiso/last/googlebook-sb3-installer.iso"
SSH_BASE = ["ssh", "-i", BUILDER_KEY, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30"]
SCP_BASE = ["scp", "-q", "-i", BUILDER_KEY, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]


class HarnessError(Exception):
    """A harness/safety precondition failed (the test is FAIL with this reason, nothing was started)."""


def norm(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def is_below(path, root):
    r = norm(root).rstrip("\\/") + os.sep
    return norm(path).startswith(os.path.normcase(r))


def assert_test_path(path, roots=WRITABLE_ROOTS):
    """Every file the harness creates/writes/deletes must be below a TEST-owned root and never a protected file."""
    if any(norm(path) == norm(p) for p in PROTECTED_PATHS):
        raise HarnessError("refusing to write a protected file: " + path)
    s = str(path)
    for bad in ("\\\\.\\", "\\\\?\\", "physicaldrive", "globalroot"):
        if bad in s.lower():
            raise HarnessError("refusing a device path: " + s)
    if not any(is_below(path, r) or norm(path) == norm(r) for r in roots):
        raise HarnessError("refusing a path outside the TEST roots %s: %s" % (", ".join(roots), path))
    return path


def mkdir(p):
    assert_test_path(p)
    os.makedirs(p, exist_ok=True)
    return p


def remove_file(p):
    """Delete a TEST-owned file (missing is fine)."""
    assert_test_path(p)
    try:
        os.remove(p)
    except FileNotFoundError:
        pass


def free_bytes(path):
    return shutil.disk_usage(os.path.splitdrive(os.path.abspath(path))[0] + "\\").free


def utc_stamp():
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def utc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------------------------------------ markers (5.2)
_KV = re.compile(r'([A-Za-z_][A-Za-z0-9_.-]*)=("([^"]*)"|\S*)')


def parse_marker(line):
    """'@@GB TAG k=v k="a b"' -> (TAG, {k: v}) or None. Values keep their order in the dict."""
    line = line.rstrip("\r\n")
    if not line.startswith("@@GB "):
        return None
    rest = line[5:]
    m = re.match(r"([A-Z][A-Z0-9-]*)", rest)
    if not m:
        return None
    tag = m.group(1)
    kv = {}
    for k, raw, quoted in _KV.findall(rest[m.end():]):
        kv[k] = quoted if raw.startswith('"') else raw
    return tag, kv


def markers_of(lines, tag=None, **match):
    """Markers (tag, kv) from lines, filtered by tag and exact key values."""
    out = []
    for ln in lines:
        m = parse_marker(ln)
        if not m:
            continue
        if tag and m[0] != tag:
            continue
        if all(m[1].get(k) == v for k, v in match.items()):
            out.append(m)
    return out


# ------------------------------------------------------------------------------------------------ sparse files
FSCTL_SET_SPARSE = 0x000900C4
FSCTL_QUERY_ALLOCATED_RANGES = 0x000940CF
_ZERO_16M = bytes(16 * MiB)


def _k32():
    import ctypes
    return ctypes.windll.kernel32


def set_sparse(f):
    """Mark an open file sparse (NTFS). Returns True on success."""
    if os.name != "nt":
        return False
    import ctypes
    import msvcrt
    from ctypes import wintypes
    h = msvcrt.get_osfhandle(f.fileno())
    ret = wintypes.DWORD()
    return bool(_k32().DeviceIoControl(wintypes.HANDLE(h), FSCTL_SET_SPARSE, None, 0, None, 0,
                                       ctypes.byref(ret), None))


def allocated_ranges(path):
    """[(offset, length)] of the allocated ranges of a file (FSCTL_QUERY_ALLOCATED_RANGES); the whole file if the
    query is unsupported."""
    size = os.path.getsize(path)
    if os.name != "nt" or size == 0:
        return [(0, size)] if size else []
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class RANGE(ctypes.Structure):
        _fields_ = [("off", ctypes.c_longlong), ("len", ctypes.c_longlong)]
    out, start = [], 0
    with open(path, "rb") as f:
        h = wintypes.HANDLE(msvcrt.get_osfhandle(f.fileno()))
        while start < size:
            q = RANGE(start, size - start)
            buf = (RANGE * 512)()
            ret = wintypes.DWORD()
            ok = _k32().DeviceIoControl(h, FSCTL_QUERY_ALLOCATED_RANGES, ctypes.byref(q), ctypes.sizeof(q),
                                        ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(ret), None)
            err = ctypes.GetLastError() if not ok else 0
            if not ok and err != 234:                      # ERROR_MORE_DATA
                return [(0, size)]
            n = ret.value // ctypes.sizeof(RANGE)
            for i in range(n):
                out.append((buf[i].off, buf[i].len))
            if ok or n == 0:
                break
            start = buf[n - 1].off + buf[n - 1].len
    return [(o, min(l, size - o)) for o, l in out if o < size]


def _feed_zeros(h, n):
    mv = memoryview(_ZERO_16M)
    while n > 0:
        k = min(n, len(_ZERO_16M))
        h.update(mv[:k])
        n -= k


def sparse_sha256(path, progress=None):
    """sha256 of the whole file; unallocated ranges are hashed as zeros without reading them (7.1)."""
    size = os.path.getsize(path)
    h = hashlib.sha256()
    pos = 0
    with open(path, "rb", buffering=0) as f:
        for off, ln in allocated_ranges(path):
            if off > pos:
                _feed_zeros(h, off - pos)
            f.seek(off)
            left = ln
            while left > 0:
                b = f.read(min(left, 16 * MiB))
                if not b:
                    raise IOError("short read in " + path)
                h.update(b)
                left -= len(b)
            pos = off + ln
            if progress:
                progress(pos, size)
    if size > pos:
        _feed_zeros(h, size - pos)
    return h.hexdigest()


def create_sparse(path, size):
    """A blank sparse raw file of exactly `size` bytes: FSCTL_SET_SPARSE first, extended by writing the last sector
    (never by CRT truncate, 7.1)."""
    assert_test_path(path)
    if size % SECTOR:
        raise HarnessError("disk size not a sector multiple: %d" % size)
    with open(path, "xb") as f:
        if not set_sparse(f):
            raise HarnessError("FSCTL_SET_SPARSE failed for " + path)
        f.seek(size - SECTOR)
        f.write(bytes(SECTOR))
    if os.path.getsize(path) != size:
        raise HarnessError("size of %s is %d, wanted %d" % (path, os.path.getsize(path), size))
    return path


def physical_size(path):
    return sum(l for _, l in allocated_ranges(path))


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(16 * MiB), b""):
            h.update(b)
    return h.hexdigest()


def read_at(path, off, n):
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(n)


# ------------------------------------------------------------------------------------------------ builder channel
def ssh(cmd, input=None, timeout=600, check=True, stdout=None):
    """Run a command on the builder. The command must stay inside REMOTE_TEST (callers pass fixed script lines).
    Returns CompletedProcess (stdout bytes unless `stdout` is a file object)."""
    p = subprocess.run(SSH_BASE + [BUILDER, cmd], input=input, timeout=timeout,
                       stdout=stdout if stdout is not None else subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise HarnessError("builder command failed (%d): %s\n%s" % (p.returncode, cmd,
                                                                     p.stderr.decode(errors="replace")[-2000:]))
    return p


def scp_to_builder(local, remote_name):
    if "/" in remote_name or remote_name.startswith("."):
        raise HarnessError("remote name must be a plain file name: " + remote_name)
    p = subprocess.run(SCP_BASE + [local, "%s:%s/%s" % (BUILDER, REMOTE_TEST, remote_name)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if p.returncode:
        raise HarnessError("scp %s failed: %s" % (local, p.stderr.decode(errors="replace")))


def builder_prepare(files):
    """mkdir REMOTE_TEST and copy the harness's builder-side scripts there (shell scripts must be LF-only; Python
    accepts CRLF)."""
    ssh("mkdir -p %s %s/mnt && chmod 0755 %s" % (REMOTE_TEST, REMOTE_TEST, REMOTE_TEST))
    for f in files:
        with open(f, "rb") as fh:
            if f.endswith(".sh") and b"\r" in fh.read():
                raise HarnessError("CR bytes in builder shell script " + f)
        scp_to_builder(f, os.path.basename(f))


# ------------------------------------------------------------------------------------------------ results
class Result:
    """One test's outcome: status PASS|FAIL|SKIPPED|INCONCLUSIVE, a reason, and evidence."""

    def __init__(self, tid):
        self.id = tid
        self.status = None
        self.reason = ""
        self.checks = []          # [(ok: bool|None, text)]
        self.evidence = {}
        self.started = utc_iso()
        self.ended = None

    def check(self, ok, text):
        self.checks.append((bool(ok), text))
        return bool(ok)

    def info(self, text):
        self.checks.append((None, text))

    def fail(self, reason):
        if self.status not in ("FAIL",):
            self.status, self.reason = "FAIL", reason
        else:
            self.reason += "; " + reason

    def finish(self):
        self.ended = utc_iso()
        if self.status is None:
            bad = [t for ok, t in self.checks if ok is False]
            if bad:
                self.status, self.reason = "FAIL", bad[0] + ("" if len(bad) == 1 else " (+%d more)" % (len(bad) - 1))
            else:
                self.status, self.reason = "PASS", "%d checks passed" % sum(1 for ok, _ in self.checks if ok)
        return self

    def to_json(self):
        return {"id": self.id, "status": self.status, "reason": self.reason, "started": self.started,
                "ended": self.ended,
                "checks": [{"ok": ok, "text": t} for ok, t in self.checks], "evidence": self.evidence}


def write_json(path, obj):
    assert_test_path(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def log(msg):
    sys.stdout.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()
