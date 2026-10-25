"""Payload discovery (3.6), protected-disk scan (3.7), media scan (3.9) and installer mounts. Device side."""
import json
import os
import time

from . import const
from . import log
from . import manifest as mf
from . import run
from . import sysfs

VFAT_EXFAT = ("vfat", "exfat")


# ------------------------------------------------------------------------------------------------ paths
def ci_path(root, rel):
    """Resolve a relative path case-insensitively below root (exact match preferred). None if missing."""
    cur = root
    for comp in [c for c in rel.split("/") if c]:
        cand = os.path.join(cur, comp)
        if os.path.lexists(cand):
            cur = cand
            continue
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        hit = [n for n in names if n.lower() == comp.lower()]
        if len(hit) != 1:
            return None
        cur = os.path.join(cur, hit[0])
    return cur


def read_file(path, limit=64 << 20):
    with open(path, "rb") as f:
        return f.read(limit)


# ------------------------------------------------------------------------------------------------ mounts
def probe_mountpoint(name):
    return const.PROBE_DIR + "/" + name


def mounted_at(mp):
    for m in sysfs.mountinfo():
        if m["mountpoint"] == mp:
            return m
    return None


def mounted_rw(mp):
    """True if mp is mounted read-write (a read-write mount request of a read-only device falls back to ro)."""
    m = mounted_at(mp)
    return m is not None and m["options"].split(",")[0] == "rw" and m["super"].split(",")[0] == "rw"


def installer_mount_of(k):
    """Installer mount point (below /run/gb/) of block device k, or None."""
    mm = sysfs.devnum(k)
    for m in sysfs.installer_mounts():
        if m["devnum"] == mm:
            return m["mountpoint"]
    return None


def unmount(mp):
    ok, err = run.umount(mp)
    if not ok:
        log.warn("unmount %s failed: %s" % (mp, err))
        return False
    try:
        if mp.startswith(const.PROBE_DIR + "/"):
            os.rmdir(mp)
    except OSError:
        pass
    return True


def mount_probe_ro(k, fstype):
    """Read-only probe mount of block device k (read-only guard first). Returns (mountpoint|None, error, ebusy)."""
    try:
        node = sysfs.node(k)
    except OSError as e:
        return None, str(e), False
    sysfs.guard([k], 1, "roguard(probe)")
    mp = probe_mountpoint(k)
    if mounted_at(mp):
        return mp, "", False
    ok, err = run.mount_probe(fstype, node, k)
    if ok:
        return mp, "", False
    busy = "busy" in err.lower()
    try:
        os.rmdir(mp)
    except OSError:
        pass
    return None, err, busy


def cleanup_installer_state():
    """UI start step (1): unmount every mount below /run/gb/ (loop mounts first), detach our loop devices,
    remount efivarfs read-only, forget the previous payload."""
    for attempt in range(2):
        loops = [m for m in sysfs.installer_mounts() if m["source"].startswith("/dev/loop")]
        for m in loops:
            unmount(m["mountpoint"])
        for k in sysfs.disk_names():
            if k.startswith("loop"):
                bf = sysfs.loop_backing(k)
                if bf and bf.startswith(const.PROBE_DIR + "/"):
                    run.loop_detach("/dev/" + k)
        for m in sysfs.installer_mounts():
            unmount(m["mountpoint"])
        if not sysfs.installer_mounts():
            break
    efivars_ro()
    for p in (const.PAYLOAD_LINK, const.PAYLOAD_JSON):
        try:
            if os.path.islink(p) or os.path.exists(p):
                os.unlink(p)
        except OSError:
            pass


def efivars_ro():
    if os.path.isdir(const.EFIVARS) and mounted_at(const.EFIVARS):
        m = mounted_at(const.EFIVARS)
        if not m["options"].split(",")[0] == "ro":
            if not run.efivars_remount_ro():
                log.error("efivarfs could not be remounted read-only")
                return False
    return True


# ------------------------------------------------------------------------------------------------ probe devices
def probe_devices():
    """3.6: /sys/class/block entries without loop/ram/zram/nbd/mtdblock/mmc boot+rpmb and size 0; a whole device
    with kernel partitions is not probed itself (only its partitions are)."""
    out = []
    for k in sysfs.class_names():
        if sysfs.PROBE_EXCLUDE.match(k) or sysfs.size512(k) == 0:
            continue
        if sysfs.is_whole(k) and sysfs.has_kernel_partitions(k):
            continue
        out.append(k)
    return out


def guard_pass_listing():
    """3.10.1: every partition of every candidate disk, every dm-* and md* device."""
    names = []
    for d in sysfs.disk_names():
        if d.startswith("dm-") or d.startswith("md"):
            names.append(d)
        elif not sysfs.CAND_EXCLUDE.match(d):
            names += [p["kname"] for p in sysfs.kernel_partitions(d)]
    sysfs.guard(names, 1, "roguard")


# ------------------------------------------------------------------------------------------------ walk
def pvd_label(path):
    """ISO9660 primary volume descriptor label of a file, or None."""
    try:
        with open(path, "rb") as f:
            f.seek(32768)
            b = f.read(2048)
    except OSError:
        return None
    if len(b) < 72 or b[1:6] != b"CD001":
        return None
    return b[40:72].decode("ascii", "replace").strip()


def walk(root, deadline_s=const.WALK_SECONDS, max_depth=const.WALK_DEPTH):
    """Bounded walk of a mounted file system: returns dict(iso=[(rel, size)], gbbackup=bool, timed_out=bool).
    Depth 1 = entries of the root; directories are entered down to max_depth levels; skip list case-insensitive."""
    t_end = time.time() + deadline_s
    res = dict(iso=[], gbbackup=False, timed_out=False)
    stack = [(root, "", 1)]
    while stack:
        if time.time() > t_end:
            res["timed_out"] = True
            break
        d, rel, depth = stack.pop()
        try:
            it = list(os.scandir(d))
        except OSError:
            continue
        for e in it:
            nm = e.name
            low = nm.lower()
            r = rel + "/" + nm
            try:
                is_dir = e.is_dir(follow_symlinks=False)
                is_file = e.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if low in const.WALK_SKIP:
                    continue
                if depth <= 3 and low == "gb-backup":
                    res["gbbackup"] = True
                if depth < max_depth:
                    stack.append((e.path, r, depth + 1))
            elif is_file:
                if depth <= 3 and (low in ("install_state.json", "googlebook_install.json") or
                                   (low.startswith("gpt_") and low.endswith("_primary.bin"))):
                    res["gbbackup"] = True
                if low.endswith(".iso"):
                    try:
                        size = e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    if size >= const.ISO_MIN_BYTES:
                        res["iso"].append((r, size))
    return res


WINMEDIA = ("sources/boot.wim", "sources/install.wim", "sources/install.esd", "sources/install.swm", "bootmgr",
            "efi/microsoft/boot/bcd", "efi/microsoft/boot/bootmgfw.efi")


def winmedia_check(root):
    """(winmedia, bootmgfw) for a mounted FAT/exFAT file system."""
    hit = any(ci_path(root, p) is not None for p in WINMEDIA)
    src = ci_path(root, "sources")
    if not hit and src and os.path.isdir(src):
        try:
            hit = any(n.lower().startswith("reconstruct") for n in os.listdir(src))
        except OSError:
            pass
    bm = ci_path(root, "efi/microsoft/boot/bootmgfw.efi")
    return hit, bm is not None


# ------------------------------------------------------------------------------------------------ inspector
class Inspector:
    """Per-pass cache of what each probe device / partition holds (one pass = one discovery/listing)."""

    def __init__(self):
        self.blkid = {}
        self.fs = {}

    def probe(self, k):
        if k not in self.blkid:
            try:
                self.blkid[k] = run.blkid_probe(sysfs.node(k))
            except OSError as e:
                log.warn("blkid %s: %s" % (k, e))
                self.blkid[k] = {}
        return self.blkid[k]

    def inspect(self, k, allow_ntfs, want_walk=True):
        """Mount (read-only) and inspect the file system on k. Cached. Returns dict:
        type, label, mounted, ebusy, error, gbsb3, iso_hits[(rel,label)], gbbackup, winmedia, bootmgfw, timed_out,
        used, total."""
        key = (k, allow_ntfs, want_walk)
        if key in self.fs:
            return self.fs[key]
        b = self.probe(k)
        t = b.get("TYPE")
        info = dict(type=t, label=b.get("LABEL"), mounted=False, ebusy=False, error="", gbsb3=False, iso_hits=[],
                    gbbackup=False, winmedia=False, bootmgfw=False, timed_out=False, used=None, total=None)
        fstype = {"vfat": "vfat", "exfat": "exfat", "iso9660": "iso9660"}.get(t)
        if t == "ntfs" and allow_ntfs:
            fstype = "ntfs3"
        if fstype is None:
            self.fs[key] = info
            return info
        existing = installer_mount_of(k)
        mp = existing
        ours = False
        if mp is None:
            mp, err, busy = mount_probe_ro(k, fstype)
            info["error"], info["ebusy"] = err, busy
            ours = mp is not None
        if mp is not None:
            info["mounted"] = True
            try:
                info["gbsb3"] = ci_path(mp, "gbsb3") is not None and os.path.isdir(ci_path(mp, "gbsb3"))
                if fstype != "iso9660":
                    if want_walk:
                        w = walk(mp)
                        info["timed_out"] = w["timed_out"]
                        info["gbbackup"] = w["gbbackup"]
                        for rel, size in w["iso"]:
                            lab = pvd_label(mp + rel)
                            if lab is not None:
                                info["iso_hits"].append((rel, lab))
                    if fstype in VFAT_EXFAT:
                        info["winmedia"], info["bootmgfw"] = winmedia_check(mp)
                        try:
                            sv = os.statvfs(mp)
                            info["total"] = sv.f_blocks * sv.f_frsize
                            info["used"] = (sv.f_blocks - sv.f_bfree) * sv.f_frsize
                            info["free"] = sv.f_bavail * sv.f_frsize
                        except OSError:
                            pass
            finally:
                if ours:
                    unmount(mp)
        self.fs[key] = info
        return info


# ------------------------------------------------------------------------------------------------ payload
class Payload:
    def __init__(self, kind, dev, disk, file, mount, build_id, marker, manifest_bytes, manifest, host_mount=None):
        self.kind, self.dev, self.disk, self.file, self.mount = kind, dev, disk, file, mount
        self.build_id, self.marker, self.manifest_bytes, self.m = build_id, marker, manifest_bytes, manifest
        self.host_mount = host_mount
        self.protected = []

    @property
    def root(self):
        return const.PAYLOAD_LINK

    def path(self, rel):
        p = ci_path(self.mount, rel)
        if p is None:
            raise OSError("payload file missing: %s" % rel)
        return const.PAYLOAD_LINK + p[len(self.mount):]

    def to_json(self):
        return {"kind": self.kind, "dev": self.dev, "disk": self.disk, "file": self.file, "mount": self.mount,
                "build_id": self.build_id, "protected": sorted(self.protected)}


def validate_root(mp, build_id):
    """1.2 validity of a payload root. Returns (marker, manifest bytes, parsed manifest) or raises ValueError."""
    p = ci_path(mp, "gbsb3/" + mf.marker_name(build_id))
    if p is None:
        raise ValueError("no marker for build %s" % build_id)
    marker = mf.parse_marker(read_file(p, 4096))
    mp_path = ci_path(mp, marker["manifest"])
    if mp_path is None:
        raise ValueError("manifest missing")
    mbytes = read_file(mp_path, 16 << 20)
    prob = mf.check_marker_for_build(marker, mbytes, build_id)
    if prob:
        raise ValueError(prob)
    m = mf.parse_manifest(mbytes, build_id)
    return marker, mbytes, m


def _found(kind, k, mp, build_id, val, file=None, host_mount=None, loopdev=None):
    marker, mbytes, m = val
    dev = loopdev or ("/dev/" + k)
    lk = os.path.basename(dev)
    leaf = sorted(sysfs.leaf_disks(lk))
    disk = leaf[0] if leaf else lk
    p = Payload(kind, dev, disk, file, mp, build_id, marker, mbytes, m, host_mount)
    try:
        if os.path.islink(const.PAYLOAD_LINK):
            os.unlink(const.PAYLOAD_LINK)
        os.symlink(mp, const.PAYLOAD_LINK)
    except OSError as e:
        log.error("payload link: %s" % e)
        return None
    keep = {mp} | ({host_mount} if host_mount else set())
    for mt in sysfs.installer_mounts():
        if mt["mountpoint"].startswith(const.PROBE_DIR + "/") and mt["mountpoint"] not in keep:
            unmount(mt["mountpoint"])
    log.info("payload found: kind=%s dev=%s disk=%s mount=%s" % (kind, dev, disk, mp))
    return p


def discover(build_id, methods, max_wait=30.0, interval=2.0):
    """Payload discovery (3.6): whole passes every `interval` s until found or max_wait. Returns Payload or None."""
    t0 = time.time()
    n = 0
    while True:
        n += 1
        guard_pass_listing()
        insp = Inspector()
        for method in methods:
            log.marker("PAYLOAD-SEARCH", ("method", method), ("pass", n))
            p = _discover_method(method, build_id, insp)
            if p is not None:
                return p
        if time.time() - t0 + interval > max_wait:
            return None
        time.sleep(interval)


def _discover_method(method, build_id, insp):
    for k in probe_devices():
        b = insp.probe(k)
        t = b.get("TYPE")
        if method == "iso9660":
            if t != "iso9660" or b.get("LABEL") != const.VOLUME_LABEL:
                continue
            mp = installer_mount_of(k)
            ours = mp is None
            if mp is None:
                mp, err, busy = mount_probe_ro(k, "iso9660")
                if mp is None:
                    log.warn("iso9660 probe %s: %s" % (k, err))
                    continue
            try:
                val = validate_root(mp, build_id)
            except (ValueError, OSError) as e:
                log.info("iso9660 %s: not our payload (%s)" % (k, e))
                if ours:
                    unmount(mp)
                continue
            return _found("iso9660", k, mp, build_id, val)
        fstype = _fs_for(k, t)
        if fstype is None:
            continue
        mp = installer_mount_of(k)
        ours = mp is None
        if mp is None:
            mp, err, busy = mount_probe_ro(k, fstype)
            if mp is None:
                log.info("%s probe %s: %s%s" % (method, k, err, " (held)" if busy else ""))
                continue
        if method == "marker":
            try:
                val = validate_root(mp, build_id)
            except (ValueError, OSError) as e:
                log.debug("marker %s: %s" % (k, e))
                if ours:
                    unmount(mp)
                continue
            return _found("marker", k, mp, build_id, val)
        if method == "isofile":
            w = walk(mp)
            for rel, size in sorted(w["iso"]):
                if pvd_label(mp + rel) != const.VOLUME_LABEL:
                    continue
                loopdev = run.loop_attach_ro(mp + rel)
                if not loopdev:
                    log.warn("loop setup failed for an .iso file on %s" % k)
                    continue
                lk = os.path.basename(loopdev)
                lmp, err, busy = mount_probe_ro(lk, "iso9660")
                if lmp is None:
                    log.warn("iso9660 mount of %s failed: %s" % (lk, err))
                    run.loop_detach(loopdev)
                    continue
                try:
                    val = validate_root(lmp, build_id)
                except (ValueError, OSError) as e:
                    log.info("isofile on %s: not our payload (%s)" % (k, e))
                    unmount(lmp)
                    run.loop_detach(loopdev)
                    continue
                return _found("isofile", k, lmp, build_id, val, file=rel, host_mount=mp, loopdev=loopdev)
            if ours:
                unmount(mp)
    return None


def _fs_for(k, t):
    if t in VFAT_EXFAT:
        return t
    if t == "ntfs" and sysfs.ntfs_allowed_for_node("/dev/" + k):
        return "ntfs3"
    return None


# ------------------------------------------------------------------------------------------------ protected scan
def protected_scan(payload, insp=None):
    """3.7: disks holding any installer medium or ISO file of any build, plus the payload's and every installer
    mount's leaf disks and EBUSY-held devices. Returns (sorted list of knames, Inspector)."""
    guard_pass_listing()
    insp = insp or Inspector()
    prot = set()
    why = {}

    def add(disks, reason):
        for d in disks:
            if d not in prot:
                why[d] = reason
            prot.add(d)

    for k in probe_devices():
        b = insp.probe(k)
        t = b.get("TYPE")
        if t == "iso9660" and b.get("LABEL") == const.VOLUME_LABEL:
            add(sysfs.leaf_disks(k), "installer volume")
        allow_ntfs = t == "ntfs" and sysfs.ntfs_allowed_for_node("/dev/" + k)
        if t in ("vfat", "exfat", "iso9660") or allow_ntfs:
            info = insp.inspect(k, allow_ntfs)
            if info["ebusy"]:
                held = set(sysfs.leaf_disks(k))
                for h in sysfs.holders(k):
                    held |= sysfs.leaf_disks(h)
                add(held, "held (busy)")
            if info["gbsb3"]:
                add(sysfs.leaf_disks(k), "gbsb3 folder")
            if any(lab == const.VOLUME_LABEL for _, lab in info["iso_hits"]):
                add(sysfs.leaf_disks(k), "installer .iso file")
    if payload is not None:
        add(sysfs.leaf_disks(os.path.basename(payload.dev)), "payload")
    for m in sysfs.installer_mounts():
        k = sysfs.name_of_devnum(m["devnum"])
        if k:
            add(sysfs.leaf_disks(k), "installer mount")
    for d in sysfs.disk_names():
        if d.startswith("loop"):
            bf = sysfs.loop_backing(d)
            if bf and bf.startswith(const.RUN + "/"):
                add(sysfs.leaf_disks(d), "loop backing file")
    for d in sorted(prot):
        log.info("protected: %s (%s)" % (d, why.get(d)))
    # 3.7: the read-only guard sets the whole devices of protected disks read-only
    sysfs.guard([d for d in prot if sysfs.is_whole(d)], 1, "roguard(protected)")
    return sorted(prot), insp


def write_payload_json(p):
    tmp = const.PAYLOAD_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(p.to_json(), f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, const.PAYLOAD_JSON)


def file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(4 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()
