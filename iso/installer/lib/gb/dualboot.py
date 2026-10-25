"""Mode 2: install next to Windows into unallocated space (4.2). Device side."""
import os

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
from . import state
from . import stick
from . import sysfs
from .errors import Back, Failed, Internal, Refused
from .util import fmt_bytes, gib2, sha256, utc_iso

FLOW = "dualboot"
NEED_MIB = 22358


def run_flow(sess):
    m = sess.m
    db = m["dual_boot"]
    parts = m["db_parts"]
    disks = flowlib.list_disks(sess, "Option 2: install Googlebook NEXT TO WINDOWS (into unallocated space)")
    if not disks:
        log.say("  no disks found")
        raise Back()
    d = flowlib.pick_disk("db.disk", disks, "Windows disk")
    log.say("")
    log.say("Disk %s: %s, %s" % (d.kname, d.model, fmt_bytes(d.size)))
    flowlib.guard_flow_start(d.kname, disks, sess.protected)
    g = d.g
    c = flowlib.Checks()
    flowlib.check_incomplete(c, d)
    efi_ok = efivars.available()
    c.report("not_efi", "R_NOT_EFI", None if efi_ok else "no UEFI variables (efivarfs) - no boot entry possible")
    c.report("payload_disk", "R_PAYLOAD_DISK", "this disk holds the installer" if "payload" in d.flags else None)
    c.report("readonly", "R_READONLY", "the disk is read-only" if d.ro else None)
    c.report("in_use", "R_IN_USE", flowlib.in_use(d, sess.protected))
    c.report("sector_size", "R_SECTOR_SIZE", "logical sector size %d" % d.sector if d.sector != 512 else None)
    nogpt = gpt.check_no_gpt(g)
    c.report("no_gpt", "R_NO_GPT", nogpt)
    gpt_ok = nogpt is None
    if gpt_ok:
        inv = gpt.check_gpt_invalid(g)
        c.report("gpt_invalid", "R_GPT_INVALID", "; ".join(inv[:4]) if inv else None)
        geo = gpt.check_gpt_geometry(g)
        c.report("gpt_geometry", "R_GPT_GEOMETRY", "; ".join(geo[:4]) if geo else None)
        gpt_ok = not inv and not geo
    else:
        c.report("gpt_invalid", "R_GPT_INVALID", None, skipped=True)
        c.report("gpt_geometry", "R_GPT_GEOMETRY", None, skipped=True)
    c.report("bitlocker", "R_BITLOCKER", flowlib.bitlocker_problem(d))
    code, prob, enc = flowlib.hibernation(d)
    if code is None and enc:
        code, prob = "R_NTFS_UNCHECKED", flowlib.encrypted_problem(enc)
    c.report("hibernated", code or "R_HIBERNATED", prob)
    db_guids = set(p["guid"] for p in parts)
    hits = []
    for o in disks:
        if o.all_guids & db_guids:
            hits.append("%s (this ISO's partition GUIDs)" % o.kname)
        elif o.all_names & set(const.GB_NAMES):
            hits.append("%s (Googlebook partition names)" % o.kname)
    unread = flowlib.unreadable_problem(disks)
    if unread and not hits:
        c.report("already_installed", "R_DISK_UNREADABLE", unread)
    else:
        c.report("already_installed", "R_ALREADY_INSTALLED", ("Googlebook partitions found on %s: uninstall first, "
                                                              "or unplug a Googlebook USB stick" % ", ".join(hits))
                 if hits else None)
    if efi_ok:
        named = flowlib.named_entries()
        c.report("boot_entry_exists", "R_BOOT_ENTRY_EXISTS", "a boot entry '%s' already exists (Boot%s)" % (
            const.BOOT_LABEL, ",".join("%04X" % n for n in named)) if named else None)
    else:
        c.report("boot_entry_exists", "R_BOOT_ENTRY_EXISTS", None, skipped=True)
    regions = []
    h = None
    if gpt_ok:
        hdr = g["primary"]["hdr"]
        h = gpt.highest_used_slot(g["primary"]["entries_raw"], hdr["n"], hdr["esz"])
        c.report("table_full", "R_TABLE_FULL", "fewer than 18 free partition table slots after slot %d" % h
                 if h + 18 > hdr["n"] - 1 else None)
        regions = plan.dualboot_regions(g["primary"]["entries"], hdr["first"], hdr["last"], parts,
                                        db["reserve_bytes"], d.sector, db["userdata_min_bytes"])
        forbid = [(r["first"] * d.sector, (r["first"] + r["count"]) * d.sector) for r in d.ranges]
        regions = [rg for rg in regions if not any(rg["start"] < fe and fs < rg["usable_end"] for fs, fe in forbid)]
        c.report("no_region", "R_NO_REGION", None if regions else
                 "no unallocated space of at least %s MiB after a data partition: shrink D: in Windows (Disk "
                 "Management > Shrink Volume by >= 22,400 MB)" % "{:,}".format(NEED_MIB))
    else:
        c.report("table_full", "R_TABLE_FULL", None, skipped=True)
        c.report("no_region", "R_NO_REGION", None, skipped=True)
    w = None
    if efi_ok and gpt_ok:
        if efivars.boot_order() is None:
            c.report("no_windows_entry", "R_NO_WINDOWS_ENTRY", "the firmware has no BootOrder")
        else:
            w = flowlib.windows_entry(g)
            c.report("no_windows_entry", "R_NO_WINDOWS_ENTRY", None if w else
                     "Windows Boot Manager is not in the firmware boot order: in Windows run "
                     "'bcdboot C:\\Windows /f UEFI' (elevated) first")
    else:
        c.report("no_windows_entry", "R_NO_WINDOWS_ENTRY", None, skipped=True)
    dyn = [i + 1 for i, r in enumerate(d.ranges) if any(t in const.DYNAMIC_TYPES for t in r["gpt_types"])]
    c.report("windows_dynamic", "R_WINDOWS_DYNAMIC", "dynamic disk / Storage Spaces partitions (%s): unsupported"
             % ",".join(map(str, dyn)) if dyn else None)
    if gpt_ok:
        types = set(e["type"] for e in g["primary"]["entries"])
        missing = [n for t, n in ((const.TYPE_ESP, "EFI system"), (const.TYPE_MSR, "Microsoft reserved"),
                                  (const.TYPE_BASIC, "Basic data")) if t not in types]
        c.report("no_windows", "R_NO_WINDOWS", "not a Windows system disk (missing: %s)" % ", ".join(missing)
                 if missing else None)
    else:
        c.report("no_windows", "R_NO_WINDOWS", None, skipped=True)
    flowlib.check_power(c)
    c.raise_first()

    # region, userdata, off-disk copy, plan
    for i, rg in enumerate(regions):
        log.say("  region %d: after partition #%d, %s .. %s, userdata up to %s" % (
            i + 1, rg["anchor_ordinal"], rg["start"], rg["region_end"], fmt_bytes(rg["fill"])))
        log.marker("REGION", ("n", i + 1), ("anchor", rg["anchor_ordinal"]), ("start", rg["start"]),
                   ("end", rg["region_end"]), ("fill", rg["fill"]))
    rg = regions[0] if len(regions) == 1 else flowlib.pick("db.region", regions, "Region")
    while True:
        r = log.prompt("db.userdata", "userdata size in GiB (8 .. %d), Enter = all %s GiB:" % (
            rg["fill"] // const.GIB, gib2(rg["fill"])))
        ud = plan.userdata_from_reply(r, rg["fill"])
        if ud is not None:
            break
        log.say("  invalid size")
    placement = plan.dualboot_placement(rg, parts, ud)
    backup_item = choose_gptbackup(sess, d)
    first_slot = h + 1
    efi_partno = first_slot + 1
    log.say("")
    log.say("PLAN for %s (%s): %d partitions from byte %d, partition table slots %d..%d" % (
        d.kname, d.model, len(placement), rg["start"], first_slot, first_slot + 17))
    for p in placement:
        log.say("  %-26s start %15d  size %9s GiB" % (p["name"], p["offset"], gib2(p["length"])))
    end = placement[-1]["offset"] + placement[-1]["length"]
    log.say("  space left unallocated before the next partition: %s" % fmt_bytes(rg["region_end"] - end))
    log.say("  existing partitions are not changed; Windows stays the default boot entry")
    log.marker("PLAN", ("flow", FLOW), ("disk", d.kname), ("start", rg["start"]), ("userdata", ud), ("grow", "n"))
    flowlib.confirm("db.confirm", "INSTALL " + d.kname, "Googlebook will be written into the unallocated space.")
    with flowlib.destructive():
        execute(sess, d, rg, placement, first_slot, efi_partno, w, backup_item)


def choose_gptbackup(sess, d):
    items = stick.eligible(sess.disks, sess.protected, d.kname, 1 << 20)
    if not items:
        return None
    log.say("")
    log.say("Optional: save the current partition table of %s to a USB stick first:" % d.kname)
    stick.show_eligible(items)
    while True:
        r = log.prompt("gptbackup.part", "partition number, or s to skip:")
        if r == "s":
            return None
        if r.isdigit() and 1 <= int(r) <= len(items):
            return items[int(r) - 1]
        log.say("  invalid choice")


def execute(sess, d, rg, placement, first_slot, efi_partno, w, backup_item):
    m = sess.m
    db = m["dual_boot"]
    g = d.g
    S = d.sector
    N = d.n_lbas
    hdr_p = g["primary"]["hdr"]
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
        bdir = flowlib.backup_dir()
        entries0 = g["primary"]["entries"]
        target_desc = "iso:/dev/%s" % d.kname
        lay_pre = plan.layout_json(hdr_p, entries0, d.size, target_desc, utc_iso(), "pre_install", pre_p, pre_b, N, S)
        flowlib.write_file(os.path.join(bdir, "gpt_pre_install_primary.bin"), pre_p)
        flowlib.write_file(os.path.join(bdir, "gpt_pre_install_backup.bin"), pre_b)
        flowlib.write_json(os.path.join(bdir, "layout_pre_install.json"), lay_pre)
        snap_pre = efivars.snapshot()
        flowlib.write_json(os.path.join(bdir, "efivars_pre.json"), efivars.snapshot_json(snap_pre))
        # 4.0.3: before the first write the GPT areas must still be the bytes the plan was made from
        a, b = t.read_gpt_areas()
        if (a, b) != (pre_p, pre_b) or not t.recheck_geometry():
            raise Refused("R_DISK_CHANGED", "the partition table changed since it was read; nothing was written")
        if backup_item is not None:
            files = {"gpt_pre_install_primary.bin": pre_p, "gpt_pre_install_backup.bin": pre_b,
                     "layout_pre_install.json": flowlib_json(lay_pre)}
            try:
                stick.gptbackup(backup_item, d.kname, files)
            except Failed as e:
                raise Failed("F_BACKUP", "the off-disk copy failed (%s); nothing was written to %s" % (e.msg, d.kname))
            j.phase("offdisk_backup_written")

        setup_gate(t, placement, d.ranges, N, S)
        write_data(t, sess.payload, placement, j)
        a, b, g2 = write_table(t, g, placement, first_slot, pre_p, pre_b, N, S, j)
        ok, _ = run.table_verify(t.node)
        if not ok:
            log.warn("the partition table check reported problems (see the log)")
        blockio.rescan(t, g2["primary"]["entries"])
        esp_lba = placement[0]["offset"] // S
        kp = [p for p in sysfs.kernel_partitions(d.kname) if p["start512"] == esp_lba * (S // 512)]
        if len(kp) != 1 or kp[0]["partno"] != efi_partno:
            raise Failed("F_KERNEL_RESCAN", "the kernel numbers the new ESP %s, expected %d: reboot the installer" % (
                kp[0]["partno"] if kp else None, efi_partno))
        esp_kname = kp[0]["kname"]
        new_knames = [p["kname"] for p in sysfs.kernel_partitions(d.kname)
                      if p["partno"] in range(first_slot + 1, first_slot + 19)]
        flowlib.guard_after_rescan(d.kname, sess.protected, new_knames)
        post_p, post_b = a, b
        lay_post = plan.layout_json(g2["primary"]["hdr"], g2["primary"]["entries"], d.size, target_desc, utc_iso(),
                                    "post_install", post_p, post_b, N, S)
        flowlib.write_file(os.path.join(bdir, "gpt_post_install_primary.bin"), post_p)
        flowlib.write_file(os.path.join(bdir, "gpt_post_install_backup.bin"), post_b)
        flowlib.write_json(os.path.join(bdir, "layout_post_install.json"), lay_post)

        # EFI boot entry (4.2.10)
        log.marker("STEP", ("flow", FLOW), ("step", "efi"), ("part", "-"))
        boot_num, efi_err, snap_post = None, None, None
        esp_len = placement[0]["length"] // S
        try:
            boot_num, snap_post = efi_create(t, j, w, efi_partno, esp_lba, esp_len, db["esp_guid"], snap_pre)
        except (Failed, OSError) as e:
            efi_err = e if isinstance(e, Failed) else Failed("F_EFI", "EFI step: %s (the partitions stay installed)" % e)
            snap_post = efivars.snapshot()
        seq_now = sysfs.get_diskseq(t.fd)
        if seq_now != d.diskseq:
            log.info("diskseq of %s is now %s (listed %s)" % (d.kname, seq_now, d.diskseq))
        # the whole-disk exclusive open would block the ESP mount: release it now (all disk writes are done)
        t.close()

        # state (4.2.11)
        plan_doc = plan.plan_json(hdr_p["disk"], d.size, d.kname, rg, entries0, placement, S)
        ords = gpt.ordinals(entries0)
        st = state.install_state(hdr_p["disk"], rg, ords, sess.build_id, sha256(sess.payload.manifest_bytes),
                                 db["esp_guid"], db["super_guid"], boot_num, efi_partno, d.kname,
                                 "complete" if efi_err is None else "no_boot_entry")
        docs = {"install_state.json": st, "plan.json": plan_doc, "layout_pre_install.json": lay_pre,
                "layout_post_install.json": lay_post, "gpt_pre_install_primary.bin": pre_p,
                "gpt_pre_install_backup.bin": pre_b, "gpt_post_install_primary.bin": post_p,
                "gpt_post_install_backup.bin": post_b, "manifest_installed.json": export_manifest(sess),
                "efivars_pre.json": efivars.snapshot_json(snap_pre),
                "efivars_post.json": efivars.snapshot_json(snap_post)}
        log.marker("STEP", ("flow", FLOW), ("step", "state"), ("part", "EFI-SYSTEM"))
        files = state.files_for_esp(docs, logs_text(), sess.build_id, db["esp_guid"])
        write_state_ram(files)
        write_state_esp(d, seq_now, esp_kname, esp_lba, esp_len, efi_partno, db["esp_guid"], files)
        j.phase("state_written")
        if efi_err is not None:
            raise efi_err
    finally:
        t.close()
    log.say("")
    log.say("Done. Googlebook OS is installed next to Windows on %s; Windows stays the default." % d.kname)
    log.say("  Googlebook ESP GUID: %s" % db["esp_guid"])
    log.say("  In Windows (elevated PowerShell, C:\\GB-build\\native\\dualboot):")
    log.say("    .\\Import-GooglebookIsoInstall.ps1 -EspId %s -Apply" % db["esp_guid"])
    log.say("  Boot Googlebook once with Restart-ToGooglebook.ps1 (or Surface UEFI > Boot configuration).")
    log.say("  Import the state before using uninstall_dualboot.ps1 or install_dualboot.ps1 -UpdateEspOnly.")
    log.say("  After the first Googlebook boot, check Windows' partitions: installer tools 'c'.")


def setup_gate(t, placement, ranges, N, S):
    """4.0.2 mode 2: the 18 partitions inside the planned span, which lies outside every existing range (and the
    GPT areas); GPT phase: LBA [1, 34) and [N-33, N), never LBA 0."""
    span = (placement[0]["offset"], placement[-1]["offset"] + placement[-1]["length"])
    t.gate.forbid = [(r["first"] * S, (r["first"] + r["count"]) * S) for r in ranges]
    t.gate.forbid.append((0, 34 * S))
    t.gate.forbid.append(((N - 33) * S, N * S))
    for p in placement:
        if p["offset"] < span[0] or p["offset"] + p["length"] > span[1]:
            raise Internal("partition outside the planned span")
        t.gate.allow("data", p["offset"], p["offset"] + p["length"], p["name"])
    t.gate.areas["gpt"] = [(1 * S, 34 * S, "primary GPT"), ((N - 33) * S, N * S, "backup GPT")]


def write_data(t, payload, placement, j):
    """4.2.8: every partition into the unallocated span, then read back."""
    parts = [dict(name=p["name"], offset=p["offset"], length=p["length"], object=p["object"]) for p in placement]
    try:
        zrs = {}
        for p in parts:
            log.progress("  writing %s ..." % p["name"])
            zrs[p["name"]] = blockio.write_partition(t, payload, p)
        t.sync()
        j.phase("data_written")
        t.flush_cache()
        log.marker("STEP", ("flow", FLOW), ("step", "verify"), ("part", "-"))
        for p in parts:
            blockio.verify_partition(t, payload, p, zrs[p["name"]])
        j.phase("data_verified")
    except Failed as e:
        raise Failed(e.code, e.msg + " - the partition table was not changed")


def write_table(t, g, placement, first_slot, pre_p, pre_b, N, S, j):
    """4.2.9: re-read, put the 18 entries into slots first_slot.., write per 4.0.7, re-read and verify.
    Returns (primary area, backup area, parsed new GPT)."""
    hdr_p, hdr_b = g["primary"]["hdr"], g["backup"]["hdr"]
    a, b = t.read_gpt_areas()
    if (a, b) != (pre_p, pre_b) or not t.recheck_geometry():
        raise Failed("F_GPT", "the partition table changed during the data write; the partition table was not "
                              "changed")
    log.marker("STEP", ("flow", FLOW), ("step", "gpt"), ("part", "-"))
    new_slots = plan.new_entries_for_slots(placement, first_slot, S)
    ents_new = gpt.put_entries(g["primary"]["entries_raw"], new_slots, hdr_p["esz"])
    try:
        upd = plan.gpt_area_update(pre_p, pre_b, hdr_p, hdr_b, ents_new, N, S)
    except (ValueError, AssertionError) as e:
        raise Internal("GPT update: %s" % e)
    blockio.write_gpt(t, upd["pieces"], j, ("gpt_backup_written", "gpt_primary_written"))
    a, b = t.read_gpt_areas()
    if a != upd["primary"] or b != upd["backup"] or a[:S] != pre_p[:S]:
        raise Failed("F_GPT", "the partition table read back differs from what was written")
    with sysfs.DirectReader(t.node) as rd:
        g2 = gpt.read_gpt(rd.read, N, S)
    if gpt.check_gpt_invalid(g2) or gpt.check_no_gpt(g2):
        raise Failed("F_GPT", "the new partition table does not validate")
    for e in g["primary"]["entries"]:
        if g2["primary"]["entries_raw"][e["slot"] * 128:(e["slot"] + 1) * 128] != e["raw"]:
            raise Failed("F_GPT", "a pre-existing entry changed")
    want = set(new_slots)
    got = set(e["slot"] for e in g2["primary"]["entries"]) - set(e["slot"] for e in g["primary"]["entries"])
    if got != want:
        raise Failed("F_GPT", "the new entries are not in the planned slots")
    return a, b, g2


def flowlib_json(obj):
    import json
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def export_manifest(sess):
    db = sess.m["dual_boot"]
    try:
        with open(sess.payload.path(db["export_manifest"]), "rb") as f:
            b = f.read(16 << 20)
    except OSError as e:
        raise Failed("F_ESP_STATE", "export manifest: %s" % e)
    if sha256(b) != db["export_manifest_sha256"]:
        raise Failed("F_ESP_STATE", "the export manifest does not match the payload manifest")
    return b


def logs_text():
    out = []
    for p in ("/run/gb/log/installer.log", "/run/gb/log/init.log"):
        try:
            with open(p, "r", errors="replace") as f:
                out.append("==== %s\n" % os.path.basename(p) + f.read())
        except OSError:
            pass
    return "\n".join(out)


def write_state_ram(files):
    os.makedirs(const.STATE_DIR, exist_ok=True)
    for n in os.listdir(const.STATE_DIR):
        p = os.path.join(const.STATE_DIR, n)
        if os.path.isfile(p):
            os.unlink(p)
    for n, b in files.items():
        flowlib.write_file(os.path.join(const.STATE_DIR, n), b)


def write_state_esp(d, diskseq, esp_kname, esp_lba, esp_len, partno, esp_guid, files):
    """4.2.11: verified node, read-write mount, /gbinstall/, sync, unmount, FAT check."""
    try:
        node = stick.verify_node(esp_kname, d.kname, diskseq, esp_lba * (d.sector // 512),
                                 esp_len * (d.sector // 512), partno, esp_guid)
    except (ValueError, OSError) as e:
        raise Failed("F_ESP_STATE", "the new ESP does not match the plan (%s); the install itself is complete" % e)
    run.PERMITS.grant("rw-mount", (node, const.ESP_MNT))
    ok, err = run.mount_esp_rw(node)
    run.PERMITS.revoke("rw-mount")
    if not ok:
        raise Failed("F_ESP_STATE", "mount of the new ESP failed (%s); the install itself is complete" % err)
    if not scan.mounted_rw(const.ESP_MNT):
        run.umount(const.ESP_MNT)
        raise Failed("F_ESP_STATE", "the new ESP was mounted read-only; the install itself is complete")
    try:
        base = os.path.join(const.ESP_MNT, "gbinstall")
        os.makedirs(base, exist_ok=True)
        for n, b in sorted(files.items()):
            flowlib.write_file(os.path.join(base, n), b)
        run.sync()
    except OSError as e:
        run.umount(const.ESP_MNT)
        raise Failed("F_ESP_STATE", "writing \\gbinstall\\ failed (%s); the install itself is complete" % e)
    ok, err = run.umount(const.ESP_MNT)
    if not ok:
        raise Failed("F_ESP_STATE", "unmount of the new ESP failed: %s" % err)
    run.PERMITS.grant("fsck", node)
    ok, note = run.esp_check(node)
    if not ok:
        raise Failed("F_ESP_STATE", "the FAT check of the new ESP failed (%s); the install itself is complete" % note)
    sysfs.guard([esp_kname], 1, "roguard(esp)")


# ------------------------------------------------------------------------------------------------ EFI (4.2.10)
def efi_create(t, j, w, partno, esp_lba, esp_len, esp_guid, snap_pre):
    """Create the boot entry and append it to BootOrder after Windows Boot Manager. Returns (number, snapshot).
    Raises Failed(F_EFI) after rolling back."""
    created = None
    with efivars.rw_window():
        snap = efivars.snapshot()
        snap_pre.clear()
        snap_pre.update(snap)
        old = efivars.boot_order()
        if old is None:
            raise Failed("F_EFI", "BootOrder unreadable; nothing changed (the partitions stay installed)")
        wn, wopt, wraw = w
        if efivars.read_raw(efi.boot_name(wn)) != wraw or wn not in old:
            raise Failed("F_EFI", "the Windows Boot Manager entry changed since the checks; nothing changed")
        nxt0 = efivars.boot_next()
        log.marker("EFI", ("phase", "before"), ("bootorder", efi.fmt_order(old)), ("new", "-"), ("removed", "-"))
        before = set(efivars.boot_numbers())
        try:
            run.PERMITS.grant("efi-create", (t.node, str(partno)))
            ok = run.bootmgr_create(t.node, partno)
            run.PERMITS.revoke("efi-create")
            new = sorted(set(efivars.boot_numbers()) - before)
            if len(new) == 1:
                created = new[0]
            if not ok or len(new) != 1:
                raise Failed("F_EFI", "creating the boot entry failed")
            j.phase("efi_created")
            order = old + [created]
            run.PERMITS.grant("efi-order", efi.fmt_order(order))
            if not run.bootmgr_set_order(efi.fmt_order(order)):
                raise Failed("F_EFI", "setting BootOrder failed")
            j.phase("efi_ordered")
            probs = []
            now = efivars.boot_order()
            if now != order:
                probs.append("BootOrder is %s" % (efi.fmt_order(now) if now else None))
            elif now.index(wn) >= now.index(created):
                probs.append("Windows Boot Manager is not before the new entry")
            for k, v in snap.items():
                if k.startswith("Boot") and k not in ("BootOrder", "BootNext") and efivars.read_raw(k) != v:
                    probs.append("%s changed" % k)
            if efivars.boot_next() != nxt0:
                probs.append("BootNext changed")
            opt, raw = efivars.load_option(created)
            if opt is None:
                probs.append("the new entry cannot be parsed")
            else:
                probs += efi.check_new_entry(opt, partno, esp_lba, esp_len, esp_guid)
                if opt["nodes_before_hd"]:
                    log.info("the new entry has %d device path node(s) before HD()" % opt["nodes_before_hd"])
            if probs:
                raise Failed("F_EFI", "boot entry verification failed: %s" % "; ".join(probs))
            log.marker("EFI", ("phase", "after"), ("bootorder", efi.fmt_order(now)), ("new", efi.fmt_num(created)),
                       ("removed", "-"))
            run.bootmgr_list_log()
            return created, efivars.snapshot()
        except BaseException as e:
            # BaseException: an interrupt (KeyboardInterrupt) or exit must not skip the rollback either
            rollback(created, old, snap, esp_guid)
            if not isinstance(e, Exception):
                raise
            msg = e.msg if isinstance(e, Failed) else "%s: %s" % (type(e).__name__, e)
            raise Failed("F_EFI", msg + " (rolled back; the partitions stay installed: use option 3 to remove "
                                        "them, or retry later)")


def rollback(created, old, snap, esp_guid):
    if created is not None:
        opt, raw = efivars.load_option(created)
        if opt is not None and efi.is_ours(opt, esp_guid):
            run.PERMITS.grant("efi-delete", efi.fmt_num(created))
            run.bootmgr_delete(efi.fmt_num(created))
        if efivars.boot_next() == created:
            run.PERMITS.grant("efi-delete-next")
            run.bootmgr_delete_next()
    if old and efivars.boot_order() != old:
        run.PERMITS.grant("efi-order", efi.fmt_order(old))
        run.bootmgr_set_order(efi.fmt_order(old))
    now = efivars.snapshot()
    if now != snap:
        diff = sorted(k for k in set(now) | set(snap) if now.get(k) != snap.get(k))
        log.error("EFI rollback incomplete: %s differ from the snapshot" % ", ".join(diff))
        log.say("ERROR: the boot variables could not be restored exactly (%s); see the log" % ", ".join(diff))
    else:
        log.info("EFI rollback: all variables byte-identical to the snapshot")
    log.marker("EFI", ("phase", "after"), ("bootorder", efi.fmt_order(efivars.boot_order() or [])), ("new", "-"),
               ("removed", efi.fmt_num(created) if created is not None else "-"))
