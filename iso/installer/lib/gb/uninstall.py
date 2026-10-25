"""Mode 3: remove what mode 2 added (4.3). Device side."""
import os
import shutil

from . import blockio
from . import const
from . import efi
from . import efivars
from . import flowlib
from . import gpt
from . import journal
from . import log
from . import plan
from . import run
from . import scan
from . import stick
from . import sysfs
from .errors import Failed, Refused
from .util import utc_iso

FLOW = "uninstall"


class EfiOnly:
    kname = "efi"


def find_installs(sess):
    """Disks (not protected) with partitions carrying the dual-boot GUIDs: list of (disk, matched entries)."""
    db = set(p["guid"] for p in sess.m["db_parts"])
    out = []
    for d in sess.disks:
        if d.kname in sess.protected or d.g is None:
            continue
        seen = {}
        for copy in (d.g["primary"], d.g["backup"]):
            for e in copy["entries"]:
                if e["guid"] in db and e["guid"] not in seen:
                    seen[e["guid"]] = e
        if seen:
            out.append((d, list(seen.values())))
    return out


def run_flow(sess):
    m = sess.m
    db = m["dual_boot"]
    flowlib.list_disks(sess, "Option 3: uninstall what option 2 added")
    unread = flowlib.unreadable_problem([d for d in sess.disks if d.kname not in sess.protected])
    if unread:
        raise Refused("R_DISK_UNREADABLE", unread)
    installs = find_installs(sess)
    efi_ok = efivars.available()
    ours = flowlib.our_entries(db["esp_guid"]) if efi_ok else []
    items = []
    log.say("")
    for d, ents in installs:
        items.append((d, ents))
        log.say("  %d  %s: %d/18 partitions of this ISO's install" % (len(items), d.kname, len(ents)))
    if not installs and ours:
        items.append((EfiOnly(), []))
        log.say("  %d  boot entry only (%s)" % (len(items), ",".join("Boot%04X" % n for n, _, _ in ours)))
    if not items:
        raise Refused("R_NOT_FOUND", "no install of this ISO's partition set was found (installs made by "
                                     "install_dualboot.ps1 or by another ISO build are removed with "
                                     "uninstall_dualboot.ps1 on Windows)")
    d, matched = flowlib.pick("un.disk", items, "Install to remove")
    if isinstance(d, EfiOnly):
        flowlib.confirm("un.confirm", "REMOVE efi", "The boot entry '%s' will be removed." % const.BOOT_LABEL)
        with flowlib.destructive():
            j = journal.Journal(FLOW, "efi", None, sess.build_id)
            sess.journal = j
            j.phase("planned")
            efi_remove(sess, j)
        log.say("Done: the boot entry was removed.")
        return
    flowlib.guard_flow_start(d.kname, sess.disks, sess.protected)
    g = d.g
    mp = {p["guid"]: p for p in m["db_parts"]}
    vb = [mp_["guid"] for mp_ in m["db_parts"] if mp_["name"] in ("vbmeta_a", "vbmeta_b")]
    c = flowlib.Checks()
    flowlib.check_incomplete(c, d)
    c.report("readonly", "R_READONLY", "the disk is read-only" if d.ro else None)
    c.report("in_use", "R_IN_USE", flowlib.in_use(d, sess.protected))
    c.report("sector_size", "R_SECTOR_SIZE", "logical sector size %d" % d.sector if d.sector != 512 else None)
    nogpt = gpt.check_no_gpt(g)
    c.report("no_gpt", "R_NO_GPT", nogpt)
    tolerated = False
    gpt_ok = nogpt is None
    if gpt_ok:
        inv, tolerated = gpt.check_gpt_invalid(g, vbmeta_guids=vb)
        if inv:
            c.report("gpt_invalid", "R_GPT_INVALID", "; ".join(inv[:4]))
        elif tolerated:
            c.report("gpt_invalid", "R_GPT_INVALID", "the two GPT copies differ only in the vbmeta slot bits (an "
                     "interrupted Android A/B update): the primary copy is used", warn=True)
        else:
            c.report("gpt_invalid", "R_GPT_INVALID", None)
        geo = gpt.check_gpt_geometry(g)
        c.report("gpt_geometry", "R_GPT_GEOMETRY", "; ".join(geo[:4]) if geo else None)
        gpt_ok = not inv and not geo
    else:
        c.report("gpt_invalid", "R_GPT_INVALID", None, skipped=True)
        c.report("gpt_geometry", "R_GPT_GEOMETRY", None, skipped=True)
    mism = [e for e in matched if e["name"] != mp[e["guid"]]["name"] or e["type"] != mp[e["guid"]]["type"]]
    c.report("mismatch", "R_MISMATCH", "partition(s) %s carry this install's GUIDs with another name or type"
             % ",".join(e["name"] for e in mism) if mism else None)
    lo = min(e["first"] for e in matched)
    hi = max(e["last"] for e in matched)
    foreign = []
    for copy in (g["primary"], g["backup"]):
        for e in copy["entries"]:
            if e["guid"] not in mp and e["first"] <= hi and e["last"] >= lo and e["guid"] not in foreign:
                foreign.append(e["guid"])
    c.report("foreign_between", "R_FOREIGN_BETWEEN", "%d other partition(s) lie between this install's partitions"
             % len(foreign) if foreign else None)
    code, prob, enc = flowlib.hibernation(d)
    if code is None and enc:
        c.report_ack("hibernated", "R_NTFS_UNCHECKED", flowlib.encrypted_problem(enc), "hiber.ack",
                     "FASTSTARTUP-OFF", flowlib.ENCRYPTED_ACK_TEXT)
    else:
        c.report("hibernated", code or "R_HIBERNATED", prob)
    flowlib.check_power(c)
    c.report("bitlocker", "-", "BitLocker is on: keep your recovery key at hand; Windows may ask for it at the next "
             "boot" if "bitlocker" in d.flags else None, warn=True)
    c.report("not_efi", "-", None if efi_ok else "no UEFI variables: the boot entry step is skipped", warn=True)
    c.raise_first()

    kept = [e for e in g["primary"]["entries"] if e["name"] in const.GB_NAMES and e["guid"] not in mp]
    for e in kept:
        log.say("  kept: slot %d '%s' (not this install's GUID)" % (e["slot"], e["name"]))
    for e in sorted(matched, key=lambda e: e["first"]):
        log.say("  remove: slot %d %-26s start %d" % (e["slot"], e["name"], e["first"] * d.sector))
    from .dualboot import choose_gptbackup
    backup_item = choose_gptbackup(sess, d)
    flowlib.confirm("un.confirm", "REMOVE " + d.kname, "The %d partitions above will be removed from the partition "
                    "table of %s (their data is not overwritten)." % (len(matched), d.kname))
    with flowlib.destructive():
        execute(sess, d, matched, tolerated, backup_item, efi_ok)


def copy_previous_state(d, matched):
    """If the matched EFI-SYSTEM holds /gbinstall/, copy it (read-only mount) to /run/gb/state/previous/."""
    esp = [e for e in matched if e["name"] == "EFI-SYSTEM"]
    if not esp:
        return None
    e = esp[0]
    f = d.sector // 512
    kp = [p for p in sysfs.kernel_partitions(d.kname) if p["start512"] == e["first"] * f and
          p["size512"] == (e["last"] - e["first"] + 1) * f]
    if len(kp) != 1:
        return None
    k = kp[0]["kname"]
    mp, err, busy = scan.mount_probe_ro(k, "vfat")
    if mp is None:
        log.warn("the Googlebook ESP could not be mounted read-only: %s" % err)
        return None
    dst = os.path.join(const.STATE_DIR, "previous")
    try:
        src = scan.ci_path(mp, "gbinstall")
        if src is None or not os.path.isdir(src):
            return None
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        os.makedirs(dst)
        for n in os.listdir(src):
            p = os.path.join(src, n)
            if os.path.isfile(p):
                with open(p, "rb") as fi:
                    flowlib.write_file(os.path.join(dst, n), fi.read(64 << 20))
        log.info("copied \\gbinstall\\ of the installed ESP to %s" % dst)
        return dst
    except OSError as ex:
        log.warn("copying \\gbinstall\\: %s" % ex)
        return None
    finally:
        scan.unmount(mp)


def execute(sess, d, matched, tolerated, backup_item, efi_ok):
    g = d.g
    S = d.sector
    N = d.n_lbas
    hdr_p = g["primary"]["hdr"]
    prev = copy_previous_state(d, matched)
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
        pre_p, pre_b = d.gpt_areas
        a, b = t.read_gpt_areas()
        if (a, b) != (pre_p, pre_b) or not t.recheck_geometry():
            raise Refused("R_DISK_CHANGED", "the partition table changed since it was read; nothing was written")
        bdir = flowlib.backup_dir()
        lay = plan.layout_json(hdr_p, g["primary"]["entries"], d.size, "iso:/dev/%s" % d.kname, utc_iso(),
                               "pre_uninstall", pre_p, pre_b, N, S)
        flowlib.write_file(os.path.join(bdir, "gpt_pre_uninstall_primary.bin"), pre_p)
        flowlib.write_file(os.path.join(bdir, "gpt_pre_uninstall_backup.bin"), pre_b)
        flowlib.write_json(os.path.join(bdir, "layout_pre_uninstall.json"), lay)
        if efi_ok:
            flowlib.write_json(os.path.join(bdir, "efivars_pre_uninstall.json"),
                               efivars.snapshot_json(efivars.snapshot()))
        if backup_item is not None:
            import json
            files = {"gpt_pre_uninstall_primary.bin": pre_p, "gpt_pre_uninstall_backup.bin": pre_b,
                     "layout_pre_uninstall.json": json.dumps(lay, separators=(",", ":")).encode()}
            try:
                stick.gptbackup(backup_item, d.kname, files)
            except Failed as e:
                raise Failed("F_BACKUP", "the off-disk copy failed (%s); nothing was changed" % e.msg)
            j.phase("offdisk_backup_written")

        # EFI first (4.3.5)
        if efi_ok:
            efi_remove(sess, j)
        else:
            log.say("  no UEFI variables: boot entry step skipped")
            j.phase("efi_removed")

        # GPT (4.3.6)
        db = set(p["guid"] for p in sess.m["db_parts"])
        slots = sorted(e["slot"] for e in g["primary"]["entries"] if e["guid"] in db)
        a, b, g2 = remove_table(t, g, slots, pre_p, pre_b, N, S, j)
        blockio.rescan(t, g2["primary"]["entries"])
        flowlib.guard_after_rescan(d.kname, sess.protected, [])

        # 4.3.7 compare with the pre-install copy
        res = "unknown"
        diffs = []
        if prev and os.path.exists(os.path.join(prev, "gpt_pre_install_primary.bin")):
            try:
                with open(os.path.join(prev, "gpt_pre_install_primary.bin"), "rb") as f:
                    pp = f.read()
                with open(os.path.join(prev, "gpt_pre_install_backup.bin"), "rb") as f:
                    pb = f.read()
                res = "yes" if (pp == a and pb == b) else "no"
                if res == "no":
                    diffs = compare_areas(pp, pb, a, b, N, S)
            except OSError as e:
                log.warn("pre-install copy: %s" % e)
        log.marker("UNINSTALL", ("gpt_matches_pre_install", res))
        for x in diffs:
            log.say("  warning: differs from the pre-install table: %s" % x)
    finally:
        t.close()
    log.say("")
    log.say("Done. The space is unallocated again: in Windows use Disk Management > D: > Extend Volume to take it "
            "back.")
    log.say("A state imported into C:\\GB-backup is now stale (uninstall_dualboot.ps1 will report 'no Googlebook "
            "partitions').")


def remove_table(t, g, slots, pre_p, pre_b, N, S, j):
    """4.3.6: re-read, zero the matched slots of the primary array, write both copies per 4.0.7, verify.
    Returns (primary area, backup area, parsed new GPT)."""
    hdr_p, hdr_b = g["primary"]["hdr"], g["backup"]["hdr"]
    a, b = t.read_gpt_areas()
    if (a, b) != (pre_p, pre_b) or not t.recheck_geometry():
        raise Failed("F_GPT", "the partition table changed; it was not changed by the installer")
    log.marker("STEP", ("flow", FLOW), ("step", "gpt"), ("part", "-"))
    ents_new = gpt.zero_slots(g["primary"]["entries_raw"], slots, hdr_p["esz"])
    try:
        upd = plan.gpt_area_update(pre_p, pre_b, hdr_p, hdr_b, ents_new, N, S)
    except (ValueError, AssertionError) as e:
        raise Failed("F_GPT", "GPT update not possible (%s); nothing was written" % e)
    t.gate.areas["gpt"] = [(1 * S, 34 * S, "primary GPT"), ((N - 33) * S, N * S, "backup GPT")]
    blockio.write_gpt(t, upd["pieces"], j, ("gpt_backup_written", "gpt_primary_written"))
    a, b = t.read_gpt_areas()
    if a != upd["primary"] or b != upd["backup"] or a[:S] != pre_p[:S]:
        raise Failed("F_GPT", "the partition table read back differs from what was written")
    for e in g["primary"]["entries"]:
        if e["slot"] not in slots and ents_new[e["slot"] * 128:(e["slot"] + 1) * 128] != e["raw"]:
            raise Failed("F_GPT", "an unrelated entry changed")
    with sysfs.DirectReader(t.node) as rd:
        g2 = gpt.read_gpt(rd.read, N, S)
    if gpt.check_gpt_invalid(g2) or gpt.check_no_gpt(g2):
        raise Failed("F_GPT", "the partition table does not validate after the removal")
    if any(e["slot"] in slots for e in g2["primary"]["entries"]):
        raise Failed("F_GPT", "removed slots are not empty")
    return a, b, g2


def compare_areas(pp, pb, a, b, N, S):
    out = []
    h0, h1 = gpt.parse_header(pp[S:2 * S]), gpt.parse_header(a[S:2 * S])
    if h0 and h1:
        out += plan.header_diffs(h0, h1, "primary")
    hb0, hb1 = gpt.parse_header(pb[32 * S:33 * S]), gpt.parse_header(b[32 * S:33 * S])
    if hb0 and hb1:
        out += plan.header_diffs(hb0, hb1, "backup")
    for slot, kind in plan.slot_diffs(pp[2 * S:34 * S], a[2 * S:34 * S]):
        out.append("slot %d %s" % (slot, kind))
    if pp[:S] != a[:S]:
        out.append("sector 0")
    return out


def efi_remove(sess, j):
    """4.3.5: delete every boot entry that is ours (re-verified right before), BootNext if it points at one."""
    db = sess.m["dual_boot"]
    with efivars.rw_window():
        snap = efivars.snapshot()
        old = efivars.boot_order() or []
        log.marker("EFI", ("phase", "before"), ("bootorder", efi.fmt_order(old)), ("new", "-"), ("removed", "-"))
        removed = []
        for n in efivars.boot_numbers():
            opt, raw = efivars.load_option(n)
            if opt is None:
                continue
            if opt["description"] == const.BOOT_LABEL and not efi.is_ours(opt, db["esp_guid"]):
                log.say("  kept: Boot%04X '%s' points at another partition" % (n, const.BOOT_LABEL))
                continue
            if not efi.is_ours(opt, db["esp_guid"]):
                continue
            opt2, raw2 = efivars.load_option(n)
            if opt2 is None or raw2 != raw or not efi.is_ours(opt2, db["esp_guid"]):
                raise Failed("F_EFI", "Boot%04X changed while being checked" % n)
            run.PERMITS.grant("efi-delete", efi.fmt_num(n))
            if not run.bootmgr_delete(efi.fmt_num(n)):
                raise Failed("F_EFI", "deleting Boot%04X failed" % n)
            removed.append(n)
            if efivars.boot_next() == n:
                run.PERMITS.grant("efi-delete-next")
                run.bootmgr_delete_next()
        now_order = efivars.boot_order() or []
        want = [x for x in old if x not in removed]
        probs = []
        if now_order != want:
            probs.append("BootOrder is %s, expected %s" % (efi.fmt_order(now_order), efi.fmt_order(want)))
        now = efivars.snapshot()
        for k, v in snap.items():
            if k in ("BootOrder", "BootNext") or k in [efi.boot_name(x) for x in removed]:
                continue
            if now.get(k) != v:
                probs.append("%s changed" % k)
        for x in removed:
            if efi.boot_name(x) in now:
                probs.append("%s still present" % efi.boot_name(x))
        log.marker("EFI", ("phase", "after"), ("bootorder", efi.fmt_order(now_order)), ("new", "-"),
                   ("removed", efi.fmt_order(removed) if removed else "-"))
        if probs:
            raise Failed("F_EFI", "; ".join(probs))
    j.phase("efi_removed")
