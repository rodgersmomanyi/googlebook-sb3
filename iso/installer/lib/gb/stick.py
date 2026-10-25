"""Savelog / gptbackup (4.4): the only places the installer writes to a disk other than the flow target, and the
partition-node verification before read-write mounts (4.0.13). Device side."""
import hashlib
import os

from . import const
from . import log
from . import run
from . import scan
from . import sysfs
from .errors import Failed
from .util import gib2, sha256, utc_stamp

BAD_DISK_FLAGS = ("windows", "bitlocker", "winmedia", "gbbackup", "ventoy", "unscanned", "googlebook", "usbimage",
                  "isodualboot", "unreadable")


def eligible(disks, protected, target_kname, min_free):
    """Partitions eligible for savelog/gptbackup: list of dict(disk, range, kname, start512, size512, partno, uuid,
    fs, free)."""
    out = []
    # gb.stick_any=1 (TEST only, honoured only on VMware, 2.3): a non-USB test disk may stand in for the stick
    any_disk = sysfs.params()["stick_any"] and sysfs.is_vmware()
    for d in disks:
        if not (d.usb or any_disk) or d.kname in protected or d.kname == target_kname:
            continue
        if d.flags & set(BAD_DISK_FLAGS):
            continue
        for r in d.ranges:
            k = r.get("kname")
            if not k or r.get("fs") not in ("vfat", "exfat"):
                continue
            if const.TYPE_ESP in r["gpt_types"] or 0xEF in r["mbr_types"] or \
                    (r.get("part_entry_type") or "").lower() in (const.TYPE_ESP, "0xef"):
                continue
            if r.get("free") is None or r["free"] < min_free or not r.get("part_entry_uuid"):
                continue
            kp = [p for p in sysfs.kernel_partitions(d.kname) if p["kname"] == k]
            if not kp:
                continue
            out.append(dict(disk=d, range=r, kname=k, start512=kp[0]["start512"], size512=kp[0]["size512"],
                            partno=kp[0]["partno"], uuid=r["part_entry_uuid"].lower(), fs=r["fs"], free=r["free"]))
    return out


def show_eligible(items):
    for i, it in enumerate(items):
        log.say("  %d  %s on %s (%s), %s GiB, %s GiB free" % (i + 1, it["kname"], it["disk"].kname,
                                                           it["disk"].model, gib2(it["size512"] * 512),
                                                           gib2(it["free"])))
        if it["range"].get("label"):
            log.say("       label: %s" % it["range"]["label"][:32], screen_only=True)


def verify_node(kname, disk_kname, diskseq, start512, size512, partno, uuid):
    """4.0.13: the partition node matches the plan. Returns the node path or raises ValueError."""
    if sysfs.parent_disk(kname) != disk_kname or not sysfs.is_partition(kname):
        raise ValueError("%s is not a partition of %s" % (kname, disk_kname))
    if sysfs.diskseq(disk_kname) != diskseq:
        raise ValueError("disk %s changed (diskseq)" % disk_kname)
    base = "/sys/class/block/%s/" % kname
    if sysfs.read_int(base + "start") != start512 or sysfs.read_int(base + "size") != size512 or \
            sysfs.read_int(base + "partition") != partno:
        raise ValueError("%s start/size/number differ from the plan" % kname)
    node = sysfs.node(kname)
    b = run.blkid_probe(node)
    if (b.get("PART_ENTRY_UUID") or "").lower() != uuid.lower() or \
            str(b.get("PART_ENTRY_OFFSET")) != str(start512) or str(b.get("PART_ENTRY_SIZE")) != str(size512):
        raise ValueError("%s: partition entry (GUID/offset/size) differs from the plan" % kname)
    return node


def write_dir(item, relroot, files, code="F_BACKUP"):
    """Write files (dict relpath -> bytes) into <partition>/<relroot>/ plus SHA256SUMS, then verify them from a
    fresh read-only mount. Returns the directory path on the partition."""
    d = item["disk"]
    k = item["kname"]
    sysfs.guard([k], 0, "roguard(savelog)")
    mounted = False
    try:
        try:
            node = verify_node(k, d.kname, d.diskseq, item["start512"], item["size512"], item["partno"], item["uuid"])
        except (ValueError, OSError) as e:
            raise Failed(code, "the chosen partition does not match the listing: %s" % e)
        run.PERMITS.grant("rw-mount", (node, const.SAVELOG_MNT))
        ok, err = run.mount_savelog_rw(item["fs"], node)
        run.PERMITS.revoke("rw-mount")
        if not ok:
            raise Failed(code, "mount failed: %s" % err)
        mounted = True
        if not scan.mounted_rw(const.SAVELOG_MNT):
            raise Failed(code, "the partition was mounted read-only (write-protected?)")
        base = os.path.join(const.SAVELOG_MNT, relroot.strip("/"))
        sums = []
        for rel in sorted(files):
            p = os.path.join(base, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(files[rel])
                f.flush()
                os.fsync(f.fileno())
            sums.append("%s  %s\n" % (sha256(files[rel]), rel))
        with open(os.path.join(base, "SHA256SUMS"), "w") as f:
            f.write("".join(sums))
            f.flush()
            os.fsync(f.fileno())
        run.sync()
        ok, err = run.umount(const.SAVELOG_MNT)
        mounted = not ok
        if not ok:
            raise Failed(code, "unmount failed: %s" % err)
    except OSError as e:
        raise Failed(code, "write failed: %s" % e)
    finally:
        if mounted:
            run.umount(const.SAVELOG_MNT)
        sysfs.guard([k], 1, "roguard(savelog)")
    # read back from a fresh read-only mount
    mp, err, busy = scan.mount_probe_ro(k, item["fs"])
    if mp is None:
        raise Failed(code, "read-back mount failed: %s" % err)
    try:
        base = os.path.join(mp, relroot.strip("/"))
        for rel in sorted(files):
            with open(os.path.join(base, rel), "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != sha256(files[rel]):
                    raise Failed(code, "read-back of %s differs" % rel)
    except OSError as e:
        raise Failed(code, "read-back failed: %s" % e)
    finally:
        scan.unmount(mp)
    return "/" + relroot.strip("/")


def collect_tree(root, prefix):
    """Files below a directory -> dict prefix/relpath -> bytes (small files only; logs and state)."""
    out = {}
    if not os.path.isdir(root):
        return out
    for dp, dn, fn in os.walk(root):
        for n in fn:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, root)
            try:
                with open(p, "rb") as f:
                    out[prefix + "/" + rel] = f.read(64 << 20)
            except OSError:
                pass
    return out


def gptbackup(item, target_kname, files):
    """Off-disk GPT copy (4.2.7 / 4.3.4) in /gbsb3-gptbackup/<UTC stamp>-<kname>/."""
    rel = "gbsb3-gptbackup/%s-%s" % (utc_stamp(), target_kname)
    try:
        path = write_dir(item, rel, files)
    except Failed:
        log.marker("GPTBACKUP", ("part", item["kname"]), ("dir", "/" + rel), ("result", "fail"))
        raise
    log.marker("GPTBACKUP", ("part", item["kname"]), ("dir", path), ("result", "ok"))
    return path
