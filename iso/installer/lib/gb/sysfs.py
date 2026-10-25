"""Block devices through sysfs, device nodes, the allowed ioctls, O_DIRECT sector reads and the read-only guard
(3.6-3.10). Device side: used only inside the installer environment. No process code."""
import fcntl
import mmap
import os
import re
import stat
import struct

from . import const
from . import log

SYS_BLOCK = "/sys/block"
SYS_CLASS_BLOCK = "/sys/class/block"
PROBE_EXCLUDE = re.compile(r"^(loop|ram|zram|nbd|mtdblock)|^mmcblk[0-9]+boot|^mmcblk[0-9]+rpmb")
CAND_EXCLUDE = re.compile(r"^(loop|ram|zram|sr|dm-|md|nbd|mtdblock)|^mmcblk[0-9]+boot|^mmcblk[0-9]+rpmb")


def read_text(path, default=None):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path, default=None):
    t = read_text(path)
    try:
        return int(t)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------------------------------------ kernel parameters
_PARAMS = None


def params():
    """gb.* kernel parameters (2.3) parsed from /proc/cmdline."""
    global _PARAMS
    if _PARAMS is None:
        p = {"serial": False, "discover": None, "font": "auto", "debug": False, "ntfs_any": False, "build": None,
             "stick_any": False, "warnings": []}
        for w in (read_text("/proc/cmdline", "") or "").split():
            if w == "gb.serial=1":
                p["serial"] = True
            elif w.startswith("gb.discover="):
                p["discover"] = w.split("=", 1)[1]
            elif w.startswith("gb.font="):
                p["font"] = w.split("=", 1)[1]
            elif w == "gb.debug=1":
                p["debug"] = True
            elif w == "gb.ntfs_any=1":
                p["ntfs_any"] = True
            elif w == "gb.stick_any=1":
                p["stick_any"] = True
            elif w.startswith("gb.build="):
                p["build"] = w.split("=", 1)[1]
            elif w.startswith("gb.autotest"):
                p["warnings"].append("gb.autotest is reserved and ignored in this version")
        methods = []
        if p["discover"] is not None:
            for m in p["discover"].split(","):
                if m in ("iso9660", "marker", "isofile"):
                    if m not in methods:
                        methods.append(m)
                elif m:
                    p["warnings"].append("gb.discover: unknown method %r ignored" % m)
        p["methods"] = methods or ["iso9660", "marker", "isofile"]
        if p["ntfs_any"] and not is_vmware():
            p["warnings"].append("gb.ntfs_any=1 ignored (not a VMware VM)")
            p["ntfs_any"] = False
        if p["stick_any"] and not is_vmware():
            p["warnings"].append("gb.stick_any=1 ignored (not a VMware VM)")
            p["stick_any"] = False
        _PARAMS = p
    return _PARAMS


def is_vmware():
    v = read_text("/sys/class/dmi/id/sys_vendor", "") or ""
    return v.rstrip() == const.VMWARE_VENDOR


# ------------------------------------------------------------------------------------------------ names and nodes
def class_names():
    try:
        return sorted(os.listdir(SYS_CLASS_BLOCK))
    except OSError:
        return []


def disk_names():
    try:
        return sorted(os.listdir(SYS_BLOCK))
    except OSError:
        return []


def is_whole(k):
    return os.path.isdir(os.path.join(SYS_BLOCK, k))


def is_partition(k):
    return os.path.exists(os.path.join(SYS_CLASS_BLOCK, k, "partition"))


def parent_disk(k):
    """Whole disk of a partition (the device itself for a whole device)."""
    if not is_partition(k):
        return k
    return os.path.basename(os.path.dirname(os.path.realpath(os.path.join(SYS_CLASS_BLOCK, k))))


def devnum(k):
    return read_text(os.path.join(SYS_CLASS_BLOCK, k, "dev"))


def size512(k):
    return read_int(os.path.join(SYS_CLASS_BLOCK, k, "size"), 0)


class NodeError(OSError):
    pass


def node(k):
    """/dev node of a kernel block name, verified against sysfs major:minor."""
    if not re.match(r"^[a-z][a-z0-9_.!-]*$", k):
        raise NodeError("unexpected block device name %r" % k)
    path = "/dev/" + k.replace("!", "/")
    want = devnum(k)
    try:
        st = os.stat(path)
    except OSError as e:
        raise NodeError("%s: %s" % (path, e))
    if not stat.S_ISBLK(st.st_mode) or "%d:%d" % (os.major(st.st_rdev), os.minor(st.st_rdev)) != want:
        raise NodeError("%s is not the block device %s (%s)" % (path, k, want))
    return path


def name_of_devnum(mm):
    for k in class_names():
        if devnum(k) == mm:
            return k
    return None


def kernel_partitions(disk):
    """Partitions the kernel created for a whole disk: list of dict(kname, partno, start512, size512)."""
    out = []
    base = os.path.join(SYS_BLOCK, disk)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for x in names:
        pf = os.path.join(base, x, "partition")
        if os.path.exists(pf):
            out.append(dict(kname=x, partno=read_int(pf, 0), start512=read_int(os.path.join(base, x, "start"), 0),
                            size512=read_int(os.path.join(base, x, "size"), 0)))
    return out


def has_kernel_partitions(disk):
    return bool(kernel_partitions(disk))


def holders(k):
    try:
        return sorted(os.listdir(os.path.join(SYS_CLASS_BLOCK, k, "holders")))
    except OSError:
        return []


def slaves(k):
    try:
        return sorted(os.listdir(os.path.join(SYS_CLASS_BLOCK, k, "slaves")))
    except OSError:
        return []


def loop_backing(k):
    return read_text(os.path.join(SYS_BLOCK, k, "loop", "backing_file"))


def disk_attr(disk, name, default=None):
    return read_text(os.path.join(SYS_BLOCK, disk, name), default)


def transport(disk):
    p = os.path.realpath(os.path.join(SYS_BLOCK, disk))
    if "/usb" in p:
        return "usb"
    if disk.startswith("nvme") or "/nvme/" in p:
        return "nvme"
    if "/ata" in p:
        return "sata"
    if "virtio" in p:
        return "virtio"
    if "/host" in p or "/target" in p:
        return "scsi"
    return "unknown"


def model(disk):
    m = read_text(os.path.join(SYS_BLOCK, disk, "device", "model"))
    return m.strip() if m and m.strip() else "unknown"


def diskseq(disk):
    return read_int(os.path.join(SYS_BLOCK, disk, "diskseq"))


def is_usb(disk):
    return read_int(os.path.join(SYS_BLOCK, disk, "removable"), 0) == 1 or transport(disk) == "usb"


# ------------------------------------------------------------------------------------------------ leaf disks (3.7)
def mount_devnum_of_path(path):
    """major:minor of the file system holding path (from /proc/self/mountinfo, longest mount point prefix)."""
    rp = os.path.realpath(path)
    best = None
    for m in mountinfo():
        mp = m["mountpoint"]
        if rp == mp or rp.startswith(mp.rstrip("/") + "/") or mp == "/":
            if best is None or len(mp) > len(best["mountpoint"]):
                best = m
    return best["devnum"] if best else None


def leaf_disks(k, depth=0):
    """Whole disks under a block device: partition -> parent; dm/md -> slaves recursively; loop -> backing file's
    file system device -> its leaf disks; sr -> itself."""
    if depth > 16:
        return set()
    if is_partition(k):
        return leaf_disks(parent_disk(k), depth + 1)
    if k.startswith("loop"):
        bf = loop_backing(k)
        if not bf:
            return set()
        mm = mount_devnum_of_path(bf)
        name = name_of_devnum(mm) if mm else None
        return leaf_disks(name, depth + 1) if name else set()
    sl = slaves(k)
    if sl:
        out = set()
        for s in sl:
            out |= leaf_disks(s, depth + 1)
        return out
    return {k}


def ntfs_allowed_for_node(path):
    """ntfs_allowed (3.6): every leaf disk of the node is USB, or gb.ntfs_any=1 on VMware."""
    k = os.path.basename(path)
    if params()["ntfs_any"] and is_vmware():
        return True
    ld = leaf_disks(k)
    return bool(ld) and all(is_whole(d) and is_usb(d) for d in ld)


# ------------------------------------------------------------------------------------------------ mounts
def mountinfo():
    out = []
    try:
        with open("/proc/self/mountinfo") as f:
            for ln in f:
                parts = ln.split()
                if len(parts) < 10:
                    continue
                sep = parts.index("-") if "-" in parts else None
                if sep is None:
                    continue
                mp = parts[4].replace("\\040", " ")
                out.append(dict(devnum=parts[2], mountpoint=mp, fstype=parts[sep + 1], source=parts[sep + 2],
                                options=parts[5], super=parts[sep + 3] if len(parts) > sep + 3 else ""))
    except OSError:
        pass
    return out


def installer_mounts():
    """Mounts below /run/gb/ (deepest first)."""
    ms = [m for m in mountinfo() if m["mountpoint"].startswith("/run/gb/")]
    return sorted(ms, key=lambda m: -len(m["mountpoint"]))


def mounts_on(names):
    """Mounts whose source device is one of the given kernel names."""
    nums = set(devnum(k) for k in names)
    return [m for m in mountinfo() if m["devnum"] in nums]


# ------------------------------------------------------------------------------------------------ ioctls
def ioctl(fd, req, arg):
    if req not in const.ALLOWED_IOCTLS:
        raise AssertionError("ioctl 0x%x is not allowed" % req)
    return fcntl.ioctl(fd, req, arg)


def get_size64(fd):
    return struct.unpack("Q", ioctl(fd, const.BLKGETSIZE64, b"\0" * 8))[0]


def get_ssz(fd):
    return struct.unpack("i", ioctl(fd, const.BLKSSZGET, b"\0" * 4))[0]


def get_diskseq(fd):
    return struct.unpack("Q", ioctl(fd, const.BLKGETDISKSEQ, b"\0" * 8))[0]


def get_ro(fd):
    return struct.unpack("i", ioctl(fd, const.BLKROGET, b"\0" * 4))[0]


def rrpart(fd):
    ioctl(fd, const.BLKRRPART, 0)


def flsbuf(fd):
    ioctl(fd, const.BLKFLSBUF, 0)


def set_ro(path, value=1):
    """BLKROSET on a node, confirmed with BLKROGET (3.10). Returns True on success."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        ioctl(fd, const.BLKROSET, struct.pack("i", 1 if value else 0))
        return get_ro(fd) == (1 if value else 0)
    finally:
        os.close(fd)


RO_LIST = "/run/gb/roguard.list"


def ro_by_installer(k, seq):
    """True if the installer itself set the read-only flag of the whole disk k while that disk had diskseq `seq`
    (the list is keyed by kname AND diskseq: another disk that later gets the same kname is never covered)."""
    if seq is None:
        return False
    try:
        with open(RO_LIST) as f:
            for ln in f:
                w = ln.split()
                if len(w) == 2 and w[0] == k and w[1] == str(seq):
                    return True
    except OSError:
        pass
    return False


def _note_ro(k):
    seq = diskseq(k)
    if seq is None:
        log.warn("roguard: %s has no diskseq; its read-only flag is not recorded as set by the installer" % k)
        return
    try:
        with open(RO_LIST, "a") as f:
            f.write("%s %d\n" % (k, seq))
    except OSError:
        pass


def guard(knames, value=1, what="roguard"):
    """Set (or clear) the kernel read-only flag on each name; logs 'roguard set=<n> cleared=<n>'."""
    n_set = n_clear = 0
    for k in sorted(set(knames)):
        try:
            p = node(k)
            if value and is_whole(k):
                fd = os.open(p, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    was = get_ro(fd)
                finally:
                    os.close(fd)
                if not was:
                    _note_ro(k)
            if set_ro(p, value):
                if value:
                    n_set += 1
                else:
                    n_clear += 1
            else:
                log.warn("%s: read-only flag on %s not confirmed" % (what, k))
        except (OSError, AssertionError) as e:
            log.warn("%s: %s: %s" % (what, k, e))
    log.info("%s set=%d cleared=%d" % (what, n_set, n_clear))
    return n_set


# ------------------------------------------------------------------------------------------------ power, input
POWER_SUPPLY = "/sys/class/power_supply"


def power_supplies():
    """[(name, type, online int|None, status str|None)] of /sys/class/power_supply (type/online/status only; serial
    numbers and the like are never read)."""
    out = []
    try:
        names = sorted(os.listdir(POWER_SUPPLY))
    except OSError:
        names = []
    for n in names:
        b = os.path.join(POWER_SUPPLY, n)
        out.append((n, read_text(os.path.join(b, "type"), "") or "", read_int(os.path.join(b, "online")),
                    read_text(os.path.join(b, "status"))))
    return out


def input_devices():
    """[(name, handlers)] of /proc/bus/input/devices (names and handlers only)."""
    from .util import parse_input_devices
    return parse_input_devices(read_text("/proc/bus/input/devices", "") or "")


# ------------------------------------------------------------------------------------------------ O_DIRECT reads
class DirectReader:
    """Aligned O_DIRECT reads of a block device (or regular file) node: read(lba, count) -> bytes."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_DIRECT | os.O_CLOEXEC)
        st = os.fstat(self.fd)
        if stat.S_ISBLK(st.st_mode):
            self.sector = get_ssz(self.fd)
            self.size = get_size64(self.fd)
        else:
            self.sector = 512
            self.size = st.st_size
        self.n_lbas = self.size // self.sector
        self._buf = mmap.mmap(-1, const.IO_BLOCK)

    def read_bytes(self, off, n):
        if off % self.sector or n % self.sector or off < 0 or off + n > self.size:
            raise OSError("unaligned or out-of-range read %d+%d" % (off, n))
        out = bytearray()
        while n:
            k = min(n, const.IO_BLOCK)
            view = memoryview(self._buf)[:k]
            try:
                got = os.preadv(self.fd, [view], off)
            finally:
                view.release()
            if got != k:
                raise OSError("short read at %d" % off)
            out += self._buf[:k]
            off += k
            n -= k
        return bytes(out)

    def read(self, lba, count):
        return self.read_bytes(lba * self.sector, count * self.sector)

    def close(self):
        try:
            self._buf.close()
        finally:
            os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def headtail_regions(n_lbas):
    """(first LBA, count) pieces covered by headtail_sha256: LBA 0..2047 then the last 2081 LBAs (clamped)."""
    h = min(const.HEADTAIL_HEAD_LBAS, n_lbas)
    t0 = max(0, n_lbas - const.HEADTAIL_TAIL_LBAS)
    return [(0, h), (t0, n_lbas - t0)]


def headtail_bytes(reader):
    data = bytearray()
    for first, count in headtail_regions(reader.n_lbas):
        data += reader.read(first, count)
    return bytes(data)
