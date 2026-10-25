"""Disk enumeration (3.8), union partition enumeration (4.0.16) and the media scan flags (3.9). Device side."""
import hashlib

from . import const
from . import gpt
from . import log
from . import sysfs
from .util import ascii_screen, gib2


class Disk:
    def __init__(self, kname):
        self.kname = kname
        self.n = 0
        self.flags = set()
        self.ranges = []
        self.g = None
        self.error = None
        # defaults, so that a disk that could not be read is still listed (flag 'unreadable', never a target)
        self.devnum = None
        self.size = 0
        self.sector = 512
        self.sector_sysfs = 512
        self.n_lbas = 0
        self.ro = False
        self.removable = 0
        self.tran = "unknown"
        self.usb = False
        self.model = "unknown"
        self.diskseq = None
        self.headtail_sha256 = None
        self.gpt_areas = None
        self.inuse = False
        self.all_guids = set()
        self.all_names = set()

    @property
    def node(self):
        return sysfs.node(self.kname)

    def ident(self):
        return dict(kname=self.kname, devnum=self.devnum, size=self.size, sector=self.sector, model=self.model,
                    diskseq=self.diskseq, headtail=self.headtail_sha256)


def fat_like(sec):
    if len(sec) < 512 or sec[510:512] != b"\x55\xaa":
        return False
    return sec[3:11] == b"EXFAT   " or sec[54:62] in (b"FAT12   ", b"FAT16   ") or sec[82:90] == b"FAT32   "


def oem_flags(sec):
    """(ntfs, bitlocker) from sector 0 of a disk or partition."""
    return sec[3:11] == b"NTFS    ", sec[3:11] == b"-FVE-FS-"


def kernel_parts_lba(disk, sector):
    out = []
    for p in sysfs.kernel_partitions(disk):
        out.append(dict(kname=p["kname"], partno=p["partno"], first=p["start512"] * 512 // sector,
                        count=p["size512"] * 512 // sector))
    return out


def read_layout(d, reader):
    """GPT copies + union enumeration of disk d (fills d.g and d.ranges)."""
    d.g = gpt.read_gpt(reader.read, reader.n_lbas, reader.sector)
    d.ranges = gpt.union_ranges(reader.read, reader.n_lbas, reader.sector, d.g, kernel_parts_lba(d.kname, reader.sector))
    d.gpt_areas = None
    if reader.n_lbas >= 67:
        d.gpt_areas = (reader.read(0, 34), reader.read(reader.n_lbas - 33, 33))
    return d


def whole_disk_range(d, n_lbas):
    """A pseudo-range for a file system on the whole device (no partition table, a 'superfloppy')."""
    return dict(first=0, count=n_lbas, sources=["whole-disk"], gpt_types=[], mbr_types=[], guids=[], names=[],
                slots=[], attrs=None, kname=d.kname, partno=None, whole=True)


def media_scan(d, reader, insp):
    """3.9 per range: blkid, sector-0 checks, read-only mount + walk of vfat/exfat. Sets range keys and d.flags.
    A disk without kernel partitions whose whole device carries a FAT/exFAT file system (blkid TYPE, or a FAT-like
    sector 0) is scanned as one whole-disk range (first LBA 0, the whole disk)."""
    s0 = b""
    try:
        s0 = reader.read(0, 1)
        ntfs, bl = oem_flags(s0)
        if ntfs:
            d.flags.add("windows")
        if bl:
            d.flags.add("bitlocker")
    except OSError as e:
        log.warn("%s: sector 0 read: %s" % (d.kname, e))
    db = {}
    try:
        db = insp.probe(d.kname)
        if db.get("TYPE") == "BitLocker":
            d.flags.add("bitlocker")
    except OSError:
        pass
    if not sysfs.kernel_partitions(d.kname) and (db.get("TYPE") in ("vfat", "exfat") or fat_like(s0)):
        log.info("%s: a file system on the whole device (no partition table): scanned as one range" % d.kname)
        d.ranges = [whole_disk_range(d, reader.n_lbas)] + [r for r in d.ranges
                                                            if (r["first"], r["count"]) != (0, reader.n_lbas)]
    for r in d.ranges:
        r["fs"] = None
        r["label"] = None
        r["used"] = None
        r["free"] = None
        r["rflags"] = set()
        r["ntfs"] = False
        try:
            sec = reader.read(r["first"], 1)
        except OSError:
            sec = b""
        ntfs, bl = oem_flags(sec) if sec else (False, False)
        r["ntfs"] = ntfs
        if ntfs:
            d.flags.add("windows")
        if bl:
            r["rflags"].add("bitlocker")
        if any(t in const.WINDOWS_ONLY_TYPES for t in r["gpt_types"]):
            d.flags.add("windows")
        if any(t in const.DYNAMIC_TYPES for t in r["gpt_types"]):
            d.flags.add("dynamic")
        k = r.get("kname")
        if k is None:
            if fat_like(sec):
                r["rflags"].add("unscanned")
                log.info("%s: a FAT-like range at LBA %d has no kernel partition: not scanned" % (d.kname, r["first"]))
            continue
        b = insp.probe(k)
        r["fs"] = b.get("TYPE")
        r["label"] = b.get("LABEL")
        r["part_entry_type"] = b.get("PART_ENTRY_TYPE")
        r["part_entry_uuid"] = b.get("PART_ENTRY_UUID")
        if r["fs"] == "BitLocker":
            r["rflags"].add("bitlocker")
        if r["label"] == "VTOYEFI" or (r["fs"] == "exfat" and r["label"] == "Ventoy"):
            r["rflags"].add("ventoy")
        if r["fs"] in ("vfat", "exfat"):
            info = insp.inspect(k, False)
            r["used"], r["free"] = info.get("used"), info.get("free")
            if info["winmedia"]:
                r["rflags"].add("winmedia")
            if info["bootmgfw"]:
                d.flags.add("windows")
                r["bootmgfw"] = True
            if info["gbbackup"]:
                r["rflags"].add("gbbackup")
            if info["timed_out"]:
                r["rflags"].add("unscanned")
            if not info["mounted"]:
                r["rflags"].add("unscanned")
                log.info("%s: %s could not be mounted read-only for the media scan" % (d.kname, k))
        d.flags |= r["rflags"]


def enumerate_disks(payload, m, protected, insp):
    """3.8: candidate disks with identity, flags and the union enumeration. Returns a list of Disk, numbered."""
    wd_guids, db_guids = (set(), set())
    image_bytes = None
    if m is not None:
        from .manifest import all_gb_guids
        wd_guids, db_guids = all_gb_guids(m)
        image_bytes = m["wd"]["image_bytes"]
    out = []
    for k in sysfs.disk_names():
        if sysfs.CAND_EXCLUDE.match(k) or sysfs.size512(k) == 0:
            continue
        d = Disk(k)
        try:
            d.devnum = sysfs.devnum(k)
            d.diskseq = sysfs.diskseq(k)
            d.size = sysfs.size512(k) * 512
            d.ro = sysfs.read_int("/sys/block/%s/ro" % k, 0) == 1 and not sysfs.ro_by_installer(k, d.diskseq)
            d.removable = sysfs.read_int("/sys/block/%s/removable" % k, 0)
            d.tran = sysfs.transport(k)
            d.usb = sysfs.is_usb(k)
            d.model = sysfs.model(k)
            d.sector_sysfs = sysfs.read_int("/sys/block/%s/queue/logical_block_size" % k, 512)
            with sysfs.DirectReader(sysfs.node(k)) as rd:
                d.size = rd.size
                d.sector = rd.sector
                d.n_lbas = rd.n_lbas
                d.headtail_sha256 = hashlib.sha256(sysfs.headtail_bytes(rd)).hexdigest()
                read_layout(d, rd)
                d.inuse = bool(sysfs.holders(k)) or any(sysfs.holders(p["kname"]) for p in sysfs.kernel_partitions(k))
                media_scan(d, rd, insp)
        except OSError as e:
            # kept in the list (flag 'unreadable'): it can never be chosen, and the cross-disk checks (duplicate
            # GUIDs, already installed, mode 3's search) refuse while it is attached (R_DISK_UNREADABLE)
            d.error = str(e)[:200]
            log.warn("disk %s could not be read: %s" % (k, e))
            d.flags = {"unreadable"}
            d.ranges, d.g, d.gpt_areas = [], None, None
            d.all_guids, d.all_names = set(), set()
            if k in protected:
                d.flags.add("payload")
            out.append(d)
            continue
        if k in protected:
            d.flags.add("payload")
        if d.ro:
            d.flags.add("readonly")
        if d.sector != 512 or d.sector_sysfs != 512:
            d.flags.add("sector4k")
        if image_bytes and d.size < image_bytes:
            d.flags.add("small")
        if d.inuse:
            d.flags.add("inuse")
        names = set()
        guids = set()
        for r in d.ranges:
            names |= set(r["names"])
            guids |= set(r["guids"])
        for c in (d.g["primary"], d.g["backup"]):
            if c["hdr"] is not None:
                guids.add(c["hdr"]["disk"])
        d.all_guids = guids
        d.all_names = names
        if names & set(const.GB_NAMES):
            d.flags.add("googlebook")
        if guids & wd_guids:
            d.flags.add("usbimage")
        if guids & db_guids:
            d.flags.add("isodualboot")
        out.append(d)
    for i, d in enumerate(out):
        d.n = i + 1
    return out


def show_disks(disks):
    """Screen table + one @@GB DISK marker per disk (5.2)."""
    log.say("  n  disk          size GiB  transport  model                 flags")
    for d in disks:
        fl = ",".join(sorted(d.flags)) or "-"
        log.say("  %-2d %-12s %9s  %-9s  %-20s  %s" % (d.n, d.kname, gib2(d.size), d.tran,
                                                      ascii_screen(d.model, 20), fl))
        log.marker("DISK", ("n", d.n), ("kname", d.kname), ("size", d.size), ("sector", d.sector),
                   ("model", ascii_screen(d.model, 40)), ("tran", d.tran), ("flags", fl))
        if "unreadable" in d.flags:
            log.say("     %s could not be read (%s): it cannot be chosen; unplug it or rescan" % (
                d.kname, ascii_screen(d.error or "-", 60)))


def unreadable(disks, exclude=None):
    """knames of listed disks that could not be read (other than `exclude`)."""
    return [d.kname for d in disks if "unreadable" in d.flags and d is not exclude]


def show_ranges(d, title="existing partitions"):
    """Every range of the union enumeration (labels on the screen only)."""
    if not d.ranges:
        log.say("    (no partitions found)")
        return
    log.say("    %s:" % title)
    for i, r in enumerate(d.ranges):
        start = r["first"] * d.sector
        size = r["count"] * d.sector
        fs = r.get("fs") or ("ntfs" if r.get("ntfs") else "-")
        used = "-" if r.get("used") is None else "%s GiB used" % gib2(r["used"])
        if r.get("whole"):
            log.say("    %2d  whole-disk file system (no partition table), size %s GiB  %-8s %s" % (
                i + 1, gib2(size), fs, used))
        else:
            log.say("    %2d  start %15d  size %9s GiB  %-8s %s" % (i + 1, start, gib2(size), fs, used))
        if r.get("label"):
            log.say("        label: %s" % ascii_screen(r["label"], 32), screen_only=True)
