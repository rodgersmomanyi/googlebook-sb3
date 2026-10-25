"""Mode 1: write Googlebook to a whole disk (4.1). Device side."""
import hashlib

from . import blockio
from . import const
from . import disks as dk
from . import flowlib
from . import gpt
from . import journal
from . import log
from . import plan
from . import run
from . import sysfs
from .errors import Back, Failed, Refused
from .util import fmt_bytes, gib2, sha256

FLOW = "wholedisk"


def windows_reasons(d):
    why = []
    for i, r in enumerate(d.ranges):
        if any(t in const.WINDOWS_ONLY_TYPES for t in r["gpt_types"]):
            why.append("partition %d has a Windows type" % (i + 1))
        if r.get("ntfs"):
            why.append("partition %d is NTFS" % (i + 1))
        if r.get("bootmgfw"):
            why.append("partition %d holds the Windows boot manager" % (i + 1))
    return "; ".join(why) or "the disk holds Windows (NTFS boot sector)"


def run_flow(sess):
    m = sess.m
    wd = m["wd"]
    disks = flowlib.list_disks(sess, "Option 1: write Googlebook to a WHOLE disk (the disk is erased)")
    if not disks:
        log.say("  no disks found")
        raise Back()
    d = flowlib.pick_disk("wd.disk", disks, "Disk to erase")
    log.say("")
    log.say("Disk %s: %s, %s" % (d.kname, d.model, fmt_bytes(d.size)))
    flowlib.guard_flow_start(d.kname, disks, sess.protected)

    c = flowlib.Checks()
    flowlib.check_incomplete(c, d)
    c.report("payload_disk", "R_PAYLOAD_DISK",
             "this disk holds the installer or another installer medium" if "payload" in d.flags else None)
    c.report("readonly", "R_READONLY", "the disk is read-only" if d.ro else None)
    c.report("in_use", "R_IN_USE", flowlib.in_use(d, sess.protected))
    c.report("sector_size", "R_SECTOR_SIZE", "logical sector size %d (512 required)" % d.sector
             if d.sector != 512 else None)
    c.report("too_small", "R_TOO_SMALL", "the disk has %s; the image needs %s" % (fmt_bytes(d.size),
             fmt_bytes(wd["image_bytes"])) if d.size < wd["image_bytes"] else None)
    c.report("bitlocker", "R_BITLOCKER", flowlib.bitlocker_problem(d))
    c.report("windows_disk", "R_WINDOWS_DISK", windows_reasons(d) if "windows" in d.flags else None)
    wd_guids = set([wd["disk_guid"]] + [p["guid"] for p in wd["partitions"]])
    dup = [o.kname for o in disks if o is not d and o.all_guids & wd_guids]
    unread = flowlib.unreadable_problem(disks, exclude=d)
    if unread and not dup:
        c.report("duplicate_guids", "R_DISK_UNREADABLE", unread)
    else:
        c.report("duplicate_guids", "R_DUPLICATE_GUIDS", "disk %s carries the image's GUIDs (a stick made from "
                 "this image?): unplug it" % ",".join(dup) if dup else None)
    pm = [f for f in ("winmedia", "gbbackup", "ventoy", "unscanned") if f in d.flags]
    names = {"winmedia": "Windows/recovery media", "gbbackup": "a GB-backup copy", "ventoy": "a Ventoy stick",
             "unscanned": "not fully scanned"}
    c.report("protected_media", "R_PROTECTED_MEDIA",
             "the disk is %s" % ", ".join(names[f] for f in pm) if pm else None)
    blank_problem = None
    if not d.usb:
        try:
            with sysfs.DirectReader(d.node) as rd:
                if any(sysfs.headtail_bytes(rd)):
                    blank_problem = "an internal disk must be blank in its first 2048 and last 2081 sectors: " \
                                    "wipe it on purpose elsewhere first"
        except OSError as e:
            blank_problem = "cannot read the disk: %s" % e
    c.report("internal_not_blank", "R_INTERNAL_NOT_BLANK", blank_problem)
    flowlib.check_power(c, applies=not d.usb)
    c.raise_first()

    grow = False
    if d.size >= wd["image_bytes"] + const.GIB:
        grow = flowlib.ask_yes_no("wd.grow", "The disk is larger than the image. Grow userdata to the end of the "
                                             "disk?")
    n_lbas = d.n_lbas
    primary_bin, backup_bin = load_gpt_copies(sess)
    built = plan.whole_disk_gpt(primary_bin, backup_bin, wd, n_lbas, grow)
    parts = plan.whole_disk_partitions(wd, built["userdata_last"] if grow else None)
    ud = [p for p in parts if p["name"] == "userdata"][0]

    log.say("")
    log.say("PLAN for %s (%s, %s GiB = %d bytes)" % (d.kname, d.model, gib2(d.size), d.size))
    log.say("  ALL DATA ON THIS DISK WILL BE ERASED")
    dk.show_ranges(d)
    log.say("    new partitions:")
    for p in parts:
        log.say("      %-26s start %15d  size %9s GiB" % (p["name"], p["offset"], gib2(p["length"])))
    log.say("  userdata: %s%s" % (fmt_bytes(ud["length"]), " (grown to the end of the disk)" if grow else ""))
    log.marker("PLAN", ("flow", FLOW), ("disk", d.kname), ("start", parts[0]["offset"]), ("userdata", ud["length"]),
               ("grow", "y" if grow else "n"))
    flowlib.confirm("wd.confirm", "ERASE " + d.kname, "Everything on %s will be overwritten." % d.kname)
    with flowlib.destructive():
        execute(sess, d, parts, built, n_lbas)


def load_gpt_copies(sess):
    wd = sess.m["wd"]
    out = []
    for k in ("gpt_primary", "gpt_backup"):
        try:
            with open(sess.payload.path(wd[k]["path"]), "rb") as f:
                b = f.read(1 << 20)
        except OSError as e:
            raise Failed("F_PAYLOAD", "%s: %s" % (wd[k]["path"], e))
        if len(b) != wd[k]["bytes"] or sha256(b) != wd[k]["sha256"]:
            raise Failed("F_PAYLOAD", "%s does not match the manifest" % wd[k]["path"])
        out.append(b)
    return out


def execute(sess, d, parts, built, n_lbas):
    wd = sess.m["wd"]
    T = wd["total_lba"]
    t = blockio.Target(d)
    t.flow = FLOW
    sess.target = t
    try:
        t.prepare()
        t.open_excl()
        t.identity_check()
        j = journal.Journal(FLOW, d.kname, d.diskseq, sess.build_id)
        sess.journal = j
        j.phase("planned")
        run.PERMITS.grant("target", t.node)
        setup_gate(t, parts, built, n_lbas)
        # re-read before the first write (4.0.3): the bytes covered by headtail_sha256
        if not t.recheck_geometry() or hashlib.sha256(t.read_headtail()).hexdigest() != d.headtail_sha256:
            raise Refused("R_DISK_CHANGED", "the disk changed since it was listed; nothing was written")
        zrs = write_image(t, sess.payload, parts, built, n_lbas, j)
        verify_image(t, sess.payload, parts, built, zrs)
        ok, text = run.table_verify(t.node)
        if not ok:
            raise Failed("F_VERIFY", "the partition table check reported problems (see the log)")
        with sysfs.DirectReader(t.node) as rd:
            g2 = gpt.read_gpt(rd.read, n_lbas, t.sector)
        blockio.rescan(t, g2["primary"]["entries"])
        new_parts = [k["kname"] for k in sysfs.kernel_partitions(d.kname)]
        flowlib.guard_after_rescan(d.kname, sess.protected, new_parts)
        j.phase("verified")
        if n_lbas == T:
            log.info("target has exactly the image size")
    finally:
        t.close()
    log.say("")
    log.say("Done: %s now holds Googlebook OS (the USB image layout)." % d.kname)
    log.say("Boot it with the firmware's removable-media path (Surface: Volume Down + Power).")


def setup_gate(t, parts, built, n_lbas):
    """4.0.2 areas of mode 1."""
    S = t.sector
    g = t.gate
    for p in parts:
        g.allow("data", p["offset"], p["offset"] + p["length"], p["name"])
    g.allow("data", 0, const.HEADTAIL_HEAD_LBAS * S, "head")
    g.allow("data", (n_lbas - const.HEADTAIL_TAIL_LBAS) * S, n_lbas * S, "tail")
    gap = built["gap"]
    if gap[1] > gap[0]:
        g.allow("data", gap[0] * S, gap[1] * S, "gap after userdata")
    g.allow("gpt", 0, 34 * S, "primary GPT")
    g.allow("gpt", (n_lbas - 33) * S, n_lbas * S, "backup GPT")


def write_image(t, payload, parts, built, n_lbas, j):
    """4.1.6: zero head and tail, every partition in slot order, the gap after userdata, then the GPT."""
    S = t.sector
    log.marker("STEP", ("flow", FLOW), ("step", "zero"), ("part", "-"))
    t.zero(0, const.HEADTAIL_HEAD_LBAS * S)
    t.zero((n_lbas - const.HEADTAIL_TAIL_LBAS) * S, const.HEADTAIL_TAIL_LBAS * S)
    t.sync()
    j.phase("head_zeroed")
    zrs = {}
    for p in parts:
        log.progress("  writing %s ..." % p["name"])
        zrs[p["name"]] = blockio.write_partition(t, payload, p)
    gap = built["gap"]
    if gap[1] > gap[0]:
        t.zero(gap[0] * S, (gap[1] - gap[0]) * S)
    t.sync()
    j.phase("data_written")
    # re-read before the GPT write: head and tail areas must be all zero (zeroed above)
    p0, b0 = t.read_gpt_areas()
    if not t.recheck_geometry() or any(p0) or any(b0):
        raise Failed("F_GPT", "the first/last sectors changed during the write; the partition table was not "
                              "changed")
    log.marker("STEP", ("flow", FLOW), ("step", "gpt"), ("part", "-"))
    entries = built["entries"]
    pieces = {"backup_entries": (n_lbas - 33, entries), "backup_hdr": (n_lbas - 1, built["backup_hdr"]),
              "primary_entries": (2, entries), "primary_hdr": (1, built["primary_hdr"]),
              "mbr": (0, built["mbr"])}
    blockio.write_gpt(t, pieces)
    j.phase("gpt_written")
    return zrs


def verify_image(t, payload, parts, built, zrs):
    """4.0.6: every partition, then the GPT areas byte-identical to what was written."""
    log.marker("STEP", ("flow", FLOW), ("step", "verify"), ("part", "-"))
    for p in parts:
        blockio.verify_partition(t, payload, p, zrs[p["name"]])
    entries = built["entries"]
    p1, b1 = t.read_gpt_areas()
    if p1 != built["mbr"] + built["primary_hdr"] + entries or b1 != entries + built["backup_hdr"]:
        raise Failed("F_VERIFY", "the partition table read back differs from what was written")
