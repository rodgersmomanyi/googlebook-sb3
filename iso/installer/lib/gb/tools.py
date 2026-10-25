"""Mode 4: tools (4.4): shell, log, payload verify, compare, savelog. Device side."""
import hashlib
import os

from . import const
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
from .util import parse_sha256sums, parse_attr, utc_stamp


def show_log():
    st, why, text = flowlib.power_summary()
    log.say("---- power: %s (%s)" % (st, why), screen_only=True)
    log.say("     supplies: %s" % text[:400], screen_only=True)
    kb = flowlib.keyboards()
    log.say("---- keyboard devices: %d%s" % (len(kb), (" (" + "; ".join(kb)[:300] + ")") if kb else ""),
            screen_only=True)
    try:
        with open(log.LOG_FILE, "r", errors="replace") as f:
            lines = f.readlines()[-200:]
    except OSError:
        lines = []
    log.say("---- last %d lines of the installer log ----" % len(lines), screen_only=True)
    for ln in lines:
        log.say(ln.rstrip("\n")[:200], screen_only=True)
    files = journal.all_files()
    log.say("---- journal files: %s" % (", ".join(os.path.basename(p) for p in files) or "none"), screen_only=True)
    for p in files:
        try:
            with open(p) as f:
                log.say("%s: %s" % (os.path.basename(p), f.read().strip()[:600]), screen_only=True)
        except OSError:
            pass


def verify_payload(sess):
    """Every chunk: compressed sha256, decompressed sha256; every object sha256."""
    objs = sess.m["objects"]
    total = sum(len(o["chunks"]) for o in objs.values())
    k = 0
    for oid in sorted(objs):
        o = objs[oid]
        h_obj = hashlib.sha256()
        for c in o["chunks"]:
            k += 1
            try:
                path = sess.payload.path(c["path"])
                if scan.file_sha256(path) != c["zst_sha256"]:
                    raise Failed("F_PAYLOAD", "%s: compressed sha256 mismatch" % c["path"])
                s = run.zstd_stream(path)
            except OSError as e:
                raise Failed("F_PAYLOAD", "%s: %s" % (c["path"], e))
            h = hashlib.sha256()
            n = 0
            try:
                while True:
                    b = s.read(1 << 20)
                    if not b:
                        break
                    h.update(b)
                    h_obj.update(b)
                    n += len(b)
            except OSError as e:
                s.close(terminate=True)
                raise Failed("F_PAYLOAD", "%s: %s" % (c["path"], e))
            rc = s.close()
            if rc != 0 or n != c["raw_size"] or h.hexdigest() != c["raw_sha256"]:
                raise Failed("F_PAYLOAD", "%s: decompressed data does not match the manifest" % c["path"])
            log.progress("  verified %d/%d chunks (%s)" % (k, total, c["path"].split("/")[-1]))
        if h_obj.hexdigest() != o["sha256"]:
            raise Failed("F_PAYLOAD", "object %s sha256 mismatch" % oid)
    log.say("  payload verified: %d objects, %d chunks" % (len(objs), total))


def _installs(sess):
    from .uninstall import find_installs
    return find_installs(sess)


def read_gbinstall(d, matched, names):
    """Read files of /gbinstall/ on the matched ESP (read-only mount). Returns dict name -> bytes, or None."""
    esp = [e for e in matched if e["name"] == "EFI-SYSTEM"]
    if not esp:
        return None
    e = esp[0]
    f = d.sector // 512
    kp = [p for p in sysfs.kernel_partitions(d.kname) if p["start512"] == e["first"] * f and
          p["size512"] == (e["last"] - e["first"] + 1) * f]
    if len(kp) != 1:
        return None
    mp, err, busy = scan.mount_probe_ro(kp[0]["kname"], "vfat")
    if mp is None:
        log.warn("ESP mount: %s" % err)
        return None
    try:
        out = {}
        src = scan.ci_path(mp, "gbinstall")
        if src is None:
            return None
        for n in (names if names else os.listdir(src)):
            p = scan.ci_path(src, n)
            if p is None or not os.path.isfile(p):
                continue
            with open(p, "rb") as fh:
                out[n] = fh.read(64 << 20)
        return out
    except OSError as ex:
        log.warn("reading \\gbinstall\\: %s" % ex)
        return None
    finally:
        scan.unmount(mp)


def compare(sess):
    """Tools 'c': read-only compare of the disk's GPT with the post-install copy (4.4)."""
    flowlib.list_disks(sess, "Compare an installed disk with the state saved at install time (read-only)")
    unread = flowlib.unreadable_problem(sess.disks)
    if unread:
        log.say("  note: %s" % unread)
    installs = _installs(sess)
    if not installs:
        raise Refused("R_NOT_FOUND", "no install of this ISO's partition set was found")
    if len(installs) == 1:
        d, matched = installs[0]
    else:
        for i, (di, ents) in enumerate(installs):
            log.say("  %d  %s (%d/18 partitions)" % (i + 1, di.kname, len(ents)))
        d, matched = flowlib.pick("cmp.disk", installs, "Disk")
    want = ["gpt_post_install_primary.bin", "gpt_post_install_backup.bin", "layout_pre_install.json", "SHA256SUMS"]
    files = read_gbinstall(d, matched, want)
    result, diffs, info = "unknown", [], []
    ok_files = False
    if files and all(n in files for n in want):
        try:
            sums = parse_sha256sums(files["SHA256SUMS"].decode("ascii"))
            ok_files = all(sums.get(n) == hashlib.sha256(files[n]).hexdigest() for n in want[:3])
        except (ValueError, UnicodeDecodeError):
            ok_files = False
    if ok_files:
        S = d.sector
        N = d.n_lbas
        try:
            with sysfs.DirectReader(d.node) as rd:
                cur_p, cur_b = rd.read(0, 34), rd.read(N - 33, 33)
        except OSError as e:
            raise Failed("F_GPT", "reading the partition table: %s" % e)
        pp, pb = files["gpt_post_install_primary.bin"], files["gpt_post_install_backup.bin"]
        vb_guids = [p["guid"] for p in sess.m["db_parts"] if p["name"] in ("vbmeta_a", "vbmeta_b")]
        vb_slots = [e["slot"] for e in matched if e["guid"] in vb_guids]
        for which, a, b in (("primary", pp[S:2 * S], cur_p[S:2 * S]), ("backup", pb[32 * S:], cur_b[32 * S:])):
            ha, hb = gpt.parse_header(a), gpt.parse_header(b)
            if ha is None or hb is None:
                diffs.append("%s header missing" % which)
            else:
                diffs += plan.header_diffs(ha, hb, which)
        for which, a, b in (("primary", pp[2 * S:34 * S], cur_p[2 * S:34 * S]), ("backup", pb[:32 * S],
                                                                                 cur_b[:32 * S])):
            for slot, kind in plan.slot_diffs(a, b, ignore_vbmeta_slots=vb_slots):
                if kind == "vbmeta-bits":
                    info.append("%s slot %d: vbmeta slot bits changed (Android A/B state)" % (which, slot))
                else:
                    diffs.append("%s slot %d differs" % (which, slot))
        if pp[:S] != cur_p[:S]:
            diffs.append("sector 0 differs")
        import json
        try:
            lay = json.loads(files["layout_pre_install.json"].decode("utf-8"))
            cur = {e["guid"]: e for e in d.g["primary"]["entries"]}
            for p in lay["partitions"]:
                e = cur.get(p["id"])
                if e is None:
                    diffs.append("partition #%s %s missing" % (p["number"], p["id"]))
                    continue
                same = (e["first"] * S == p["offset"] and (e["last"] - e["first"] + 1) * S == p["length"] and
                        e["type"] == p["type"] and e["name"] == p["name"] and e["attrs"] == parse_attr(p["attributes"]))
                if not same:
                    diffs.append("partition #%s (%s) differs from before the install" % (p["number"], p["id"]))
        except (ValueError, KeyError, UnicodeDecodeError) as e:
            diffs.append("layout_pre_install.json unreadable: %s" % e)
        result = "same" if not diffs else "differs"
    else:
        log.say("  the \\gbinstall\\ copies on the Googlebook ESP are missing or do not match their SHA256SUMS")
    for x in info:
        log.say("  info: %s" % x)
    for x in diffs:
        log.say("  DIFFERENCE: %s" % x)
    log.marker("COMPARE", ("disk", d.kname), ("result", result), ("diffs", len(diffs)))
    if result == "same":
        log.say("  Windows' partitions and the partition table are as the installer left them.")


def savelog(sess):
    """Tools 'w': copy logs, RAM backups, state and journals (and the \\gbinstall\\ folder of a found install)."""
    disks = flowlib.list_disks(sess, "Save the installer logs to a USB stick")
    items = stick.eligible(disks, sess.protected, None, 64 << 20)
    if not items:
        log.say("  no eligible partition: a FAT32/exFAT partition with >= 64 MiB free on a USB stick that is not an "
                "installer, Windows, Ventoy or GB-backup medium")
        raise Failed("F_BACKUP", "no eligible partition for the logs")
    stick.show_eligible(items)
    it = flowlib.pick("savelog.part", items, "Partition")
    files = {}
    files.update(stick.collect_tree(const.LOG_DIR, "log"))
    files.update(stick.collect_tree(const.BACKUP_DIR, "backup"))
    files.update(stick.collect_tree(const.STATE_DIR, "state"))
    for p in journal.all_files():
        try:
            with open(p, "rb") as f:
                files[os.path.basename(p)] = f.read()
        except OSError:
            pass
    inst = _installs(sess)
    if len(inst) == 1:
        gi = read_gbinstall(inst[0][0], inst[0][1], None)
        if gi:
            for n, b in gi.items():
                files["gbinstall/" + n] = b
    rel = "gbsb3-logs/%s-%s" % (sess.build_id, utc_stamp())
    path = stick.write_dir(it, rel, files, code="F_BACKUP")
    log.say("  written to %s on %s" % (path, it["kname"]))
