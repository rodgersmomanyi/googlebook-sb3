"""The flow target: exclusive open, identity check, the write gate, payload writes, read-back verification, GPT
writes and the kernel rescan (4.0.1-4.0.7). Device side."""
import errno
import hashlib
import mmap
import os
import time

from . import const
from . import log
from . import run
from . import scan
from . import sysfs
from .errors import Failed, Refused, Internal

ZERO = bytes(const.IO_BLOCK)


class Gate:
    """4.0.2: every write must lie inside one allowed area of the current phase ('data' or 'gpt')."""

    def __init__(self):
        self.areas = {"data": [], "gpt": []}
        self.phase = "data"
        self.forbid = []          # byte ranges that no area may overlap (mode 2: the original disk's ranges)

    def allow(self, phase, start, end, why=""):
        if not (0 <= start < end):
            raise Internal("empty gate area %s" % why)
        for fs, fe in self.forbid:
            if start < fe and fs < end:
                raise Internal("gate area %s [%d,%d) overlaps an existing partition [%d,%d)" % (why, start, end, fs, fe))
        self.areas[phase].append((start, end, why))

    def check(self, off, n):
        if n <= 0:
            raise Internal("empty write")
        for s, e, _ in self.areas[self.phase]:
            if s <= off and off + n <= e:
                return
        raise Internal("write [%d,%d) outside the areas allowed in phase %s" % (off, off + n, self.phase))


class Target:
    """The whole-disk device of the flow, opened O_RDWR|O_EXCL after the typed confirmation."""

    def __init__(self, disk):
        self.d = disk
        self.kname = disk.kname
        self.node = sysfs.node(disk.kname)
        self.fd = None
        self.gate = Gate()
        self.sector = disk.sector
        self.n_lbas = disk.n_lbas
        self.flow = None

    # ------------------------------------------------------------------------------------------ open / identity
    def prepare(self):
        """Unmount every installer mount on this disk; clear the whole device's read-only flag only if the installer
        itself set it on this very disk (same kname AND diskseq); any other read-only flag -> R_READONLY."""
        names = [self.kname] + [p["kname"] for p in sysfs.kernel_partitions(self.kname)]
        nums = set(sysfs.devnum(k) for k in names)
        for m in sysfs.installer_mounts():
            if m["devnum"] in nums:
                scan.unmount(m["mountpoint"])
        try:
            fd = os.open(self.node, os.O_RDONLY | os.O_CLOEXEC)
            try:
                ro = sysfs.get_ro(fd)
            finally:
                os.close(fd)
        except OSError as e:
            log.warn("target read-only flag: %s" % e)
            return
        if not ro:
            return
        if not sysfs.ro_by_installer(self.kname, self.d.diskseq):
            raise Refused("R_READONLY", "%s is read-only, and not because of the installer: nothing was written"
                          % self.kname)
        log.info("clearing the read-only flag the installer set on the target's whole device %s (diskseq %s)"
                 % (self.kname, self.d.diskseq))
        try:
            sysfs.set_ro(self.node, 0)
        except OSError as e:
            log.warn("target read-only flag: %s" % e)

    def open_excl(self):
        try:
            self.fd = os.open(self.node, os.O_RDWR | os.O_EXCL | os.O_CLOEXEC)
        except OSError as e:
            if e.errno == errno.EBUSY:
                raise Refused("R_IN_USE", "%s is in use (exclusive open refused)" % self.kname)
            raise Failed("F_WRITE", "cannot open %s: %s" % (self.kname, e))
        log.info("opened %s O_RDWR|O_EXCL" % self.node)

    def identity_check(self):
        """4.0.1: kname, major:minor, size, sector size, model, diskseq (ioctl), headtail sha256 == the listing."""
        d = self.d
        probs = []
        if sysfs.devnum(self.kname) != d.devnum:
            probs.append("major:minor")
        if sysfs.get_size64(self.fd) != d.size:
            probs.append("size")
        if sysfs.get_ssz(self.fd) != d.sector:
            probs.append("sector size")
        if sysfs.model(self.kname) != d.model:
            probs.append("model")
        try:
            seq = sysfs.get_diskseq(self.fd)
        except OSError as e:
            seq = None
            log.warn("BLKGETDISKSEQ: %s" % e)
        if seq != d.diskseq:
            probs.append("diskseq %s != %s" % (seq, d.diskseq))
        with sysfs.DirectReader(self.node) as rd:
            ht = hashlib.sha256(sysfs.headtail_bytes(rd)).hexdigest()
        if ht != d.headtail_sha256:
            probs.append("first/last sectors")
        if probs:
            raise Refused("R_DISK_CHANGED", "the disk changed since it was listed (%s); nothing was written"
                          % ", ".join(probs))
        log.info("identity of %s confirmed (diskseq %s)" % (self.kname, seq))

    def recheck_geometry(self):
        if sysfs.get_size64(self.fd) != self.d.size or sysfs.get_ssz(self.fd) != self.d.sector or \
                sysfs.get_diskseq(self.fd) != self.d.diskseq:
            return False
        return True

    def read_gpt_areas(self):
        """LBA 0..33 and the last 33 LBAs (O_DIRECT)."""
        with sysfs.DirectReader(self.node) as rd:
            return rd.read(0, 34), rd.read(self.n_lbas - 33, 33)

    def read_headtail(self):
        with sysfs.DirectReader(self.node) as rd:
            return sysfs.headtail_bytes(rd)

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        run.PERMITS.revoke("target")

    # ------------------------------------------------------------------------------------------ writes
    def write(self, off, data):
        n = len(data)
        self.gate.check(off, n)
        if off % 512 or n % 512:
            raise Internal("unaligned write %d+%d" % (off, n))
        done = 0
        mv = memoryview(data)
        while done < n:
            try:
                k = os.pwrite(self.fd, mv[done:], off + done)
            except OSError as e:
                raise Failed("F_WRITE", "write error on %s at byte %d: %s" % (self.kname, off + done, e))
            if k <= 0:
                raise Failed("F_WRITE", "short write on %s at byte %d" % (self.kname, off + done))
            done += k

    def zero(self, off, n):
        while n > 0:
            k = min(n, const.IO_BLOCK)
            self.write(off, memoryview(ZERO)[:k])
            off += k
            n -= k

    def sync(self):
        try:
            os.fsync(self.fd)
        except OSError as e:
            raise Failed("F_WRITE", "fsync of %s failed: %s" % (self.kname, e))

    def flush_cache(self):
        self.sync()
        try:
            sysfs.flsbuf(self.fd)
        except OSError as e:
            log.warn("BLKFLSBUF: %s" % e)


# ------------------------------------------------------------------------------------------------ payload objects
def write_object(t, payload, oid, part_off, part_len, label):
    """4.0.4: write object `oid` at part_off: per chunk check the compressed sha256 first, decompress, hash the raw
    stream; the object sha256 at the end. Returns the object sha256."""
    o = payload.m["objects"][oid]
    if o["raw_size"] > part_len:
        raise Internal("object %s larger than its partition" % oid)
    h_obj = hashlib.sha256()
    n_chunks = len(o["chunks"])
    for i, c in enumerate(o["chunks"]):
        try:
            path = payload.path(c["path"])
            zh = scan.file_sha256(path)
        except OSError as e:
            raise Failed("F_PAYLOAD", "%s: %s" % (c["path"], e))
        if zh != c["zst_sha256"]:
            raise Failed("F_PAYLOAD", "%s: compressed sha256 mismatch (medium damaged?)" % c["path"])
        try:
            s = run.zstd_stream(path)
        except OSError as e:
            raise Failed("F_PAYLOAD", "decompressor: %s" % e)
        h = hashlib.sha256()
        got = 0
        try:
            while got < c["raw_size"]:
                want = min(const.IO_BLOCK, c["raw_size"] - got)
                data = s.read_exact(want)
                if len(data) != want:
                    got += len(data)          # premature end of the stream: nothing of it is written
                    break
                h.update(data)
                h_obj.update(data)
                t.write(part_off + c["raw_offset"] + got, data)
                got += len(data)
            extra = s.read(1) if got == c["raw_size"] else b""
        except OSError as e:
            s.close(terminate=True)
            raise Failed("F_PAYLOAD", "%s: decompression read: %s" % (c["path"], e))
        rc = s.close(terminate=bool(extra))
        if got != c["raw_size"] or extra or rc != 0:
            raise Failed("F_PAYLOAD", "%s: decompressed %d bytes (expected %d), exit %s"
                         % (c["path"], got, c["raw_size"], rc))
        if h.hexdigest() != c["raw_sha256"]:
            raise Failed("F_PAYLOAD", "%s: raw sha256 mismatch" % c["path"])
        log.progress("  %s: chunk %d/%d written" % (label, i + 1, n_chunks))
    if h_obj.hexdigest() != o["sha256"]:
        raise Failed("F_PAYLOAD", "object %s: sha256 mismatch" % oid)
    return o["sha256"]


def write_partition(t, payload, part):
    """part: dict(name, offset, length, object|None). Object + zero tail, or the blank zero ranges (4.0.5)."""
    from .plan import zero_ranges
    log.marker("STEP", ("flow", t.flow), ("step", "write"), ("part", part["name"]))
    if part.get("object"):
        write_object(t, payload, part["object"], part["offset"], part["length"], part["name"])
        raw = payload.m["objects"][part["object"]]["raw_size"]
        zr = zero_ranges(part["length"], raw)
    else:
        zr = zero_ranges(part["length"])
    for rel, n in zr:
        t.zero(part["offset"] + rel, n)
    return zr


# ------------------------------------------------------------------------------------------------ read-back (4.0.6)
class Verifier:
    def __init__(self, node):
        self.fd = os.open(node, os.O_RDONLY | os.O_DIRECT | os.O_CLOEXEC)
        self.buf = mmap.mmap(-1, const.IO_BLOCK)

    def _chunks(self, off, n):
        while n > 0:
            k = min(n, const.IO_BLOCK)
            view = memoryview(self.buf)[:k]
            try:
                try:
                    got = os.preadv(self.fd, [view], off)
                except OSError as e:
                    raise Failed("F_VERIFY", "read-back error at byte %d: %s" % (off, e))
                if got != k:
                    raise Failed("F_VERIFY", "short read-back at byte %d" % off)
                yield view
            finally:
                view.release()
            off += k
            n -= k

    def sha256(self, off, n):
        h = hashlib.sha256()
        for v in self._chunks(off, n):
            h.update(v)
        return h.hexdigest()

    def is_zero(self, off, n):
        for v in self._chunks(off, n):
            if v != memoryview(ZERO)[:len(v)]:
                return False
        return True

    def read(self, off, n):
        out = bytearray()
        for v in self._chunks(off, n):
            out += v
        return bytes(out)

    def close(self):
        self.buf.close()
        os.close(self.fd)


def verify_partition(t, payload, part, zr):
    """Read back one written partition. Prints @@GB VERIFY. Raises Failed(F_VERIFY) on a mismatch."""
    log.progress("  verifying %s ..." % part["name"])
    v = Verifier(t.node)
    try:
        sha = "zero"
        ok = True
        if part.get("object"):
            o = payload.m["objects"][part["object"]]
            sha = v.sha256(part["offset"], o["raw_size"])
            ok = sha == o["sha256"]
        for rel, n in zr:
            if ok and not v.is_zero(part["offset"] + rel, n):
                ok = False
    finally:
        v.close()
    log.marker("VERIFY", ("part", part["name"]), ("result", "ok" if ok else "fail"), ("sha256", sha))
    if not ok:
        raise Failed("F_VERIFY", "read-back of %s differs from what was written" % part["name"])


# ------------------------------------------------------------------------------------------------ GPT write (4.0.7)
def write_gpt(t, pieces, journal=None, flow_phases=None):
    """pieces: dict with 'backup_entries' (lba, bytes), 'backup_hdr', 'primary_entries', 'primary_hdr' and optional
    'mbr'. Order: backup entries, backup header, fsync, primary entries, primary header, (MBR), fsync."""
    t.gate.phase = "gpt"
    for key in ("backup_entries", "backup_hdr"):
        lba, data = pieces[key]
        t.write(lba * t.sector, data)
    t.sync()
    if journal is not None and flow_phases:
        journal.phase(flow_phases[0])
    for key in ("primary_entries", "primary_hdr", "mbr"):
        if key in pieces:
            lba, data = pieces[key]
            t.write(lba * t.sector, data)
    t.sync()
    if journal is not None and flow_phases and len(flow_phases) > 1:
        journal.phase(flow_phases[1])
    t.flush_cache()


def expected_kernel_parts(entries, sector):
    """(partno, start512, size512) for every used GPT slot (Linux numbers partitions slot + 1)."""
    f = sector // 512
    return sorted((e["slot"] + 1, e["first"] * f, (e["last"] - e["first"] + 1) * f) for e in entries)


def rescan(t, entries, timeout=10.0):
    """BLKRRPART (the kernel partition update tool on EBUSY), then wait until the kernel partitions match."""
    want = expected_kernel_parts(entries, t.sector)
    log.marker("STEP", ("flow", t.flow), ("step", "rescan"), ("part", "-"))
    try:
        sysfs.rrpart(t.fd)
        log.info("BLKRRPART ok")
    except OSError as e:
        log.warn("BLKRRPART: %s" % e)
        if e.errno == errno.EBUSY:
            run.PERMITS.grant("target", t.node)
            run.kernel_partitions_update(t.node)
    t_end = time.time() + timeout
    have = None
    while time.time() < t_end:
        have = sorted((p["partno"], p["start512"], p["size512"]) for p in sysfs.kernel_partitions(t.kname))
        if have == want:
            log.info("kernel partitions match the new table (%d)" % len(want))
            return True
        time.sleep(0.5)
    log.error("kernel partitions %s != table %s" % (have, want))
    raise Failed("F_KERNEL_RESCAN", "the partition table on disk is correct, but the kernel did not pick it up: "
                                    "reboot the installer")
