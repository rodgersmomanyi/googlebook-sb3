"""The text UI: start sequence (3.4), menus and prompts (5.3), RESULT handling (5.2). Device side."""
import os
import time
import traceback

from . import const
from . import errors
from . import journal
from . import log
from . import run
from . import scan
from . import sysfs
from .errors import Back, FlowEnd
from .util import utc_stamp


class Session:
    def __init__(self, console):
        self.console = console
        self.payload = None
        self.m = None
        self.build_id = None
        self.protected = []
        self.disks = []
        self.journal = None
        self.target = None
        self.deferred = None
        self.keyboards = None


def read_build_env():
    env = {}
    try:
        with open(const.BUILD_ENV) as f:
            for ln in f:
                ln = ln.strip()
                if "=" in ln and not ln.startswith("#"):
                    k, v = ln.split("=", 1)
                    env[k] = v.strip().strip('"')
    except OSError:
        pass
    return env


def console_env():
    env = {}
    try:
        with open("/run/gb/console.env") as f:
            for ln in f:
                if "=" in ln:
                    k, v = ln.strip().split("=", 1)
                    env[k] = v
    except OSError:
        pass
    return env


def banner(sess):
    from . import flowlib
    log.say("")
    log.say("Googlebook SB3 installer %s  (personal use only)" % sess.build_id)
    if sess.payload:
        p = sess.payload
        log.say("Payload: %s on %s%s" % (p.kind, p.dev, (" file " + p.file) if p.file else ""), screen_only=True)
    kb = flowlib.keyboards()
    if kb != sess.keyboards:
        log.info("keyboard devices: %d (%s)" % (len(kb), "; ".join(kb) or "none"))
        sess.keyboards = kb
    log.say("Keyboard devices: %d%s" % (len(kb), "" if kb else " (plug a USB keyboard into the keyboard base)"))


def start(sess):
    env = read_build_env()
    sess.build_id = env.get("GB_BUILD_ID", "unknown")
    prm = sysfs.params()
    log.configure(markers=os.path.exists(const.MARKERS_FLAG), verbose=prm["debug"])
    cenv = console_env()
    # GRUB leaves its menu screen on the serial line without a final newline: start on a fresh line so that the
    # first marker is at column 0 (5.2)
    log.say("", screen_only=True)
    log.marker("BOOT", ("build", sess.build_id), ("kernel", os.uname().release),
               ("discover", ",".join(prm["methods"])), ("ui", sess.console), ("font", cenv.get("GB_FONT_USED", "-")))
    log.info("cmdline: %s" % sysfs.read_text("/proc/cmdline", ""))
    for w in prm["warnings"]:
        log.warn(w)
        log.say("warning: %s" % w)
    if prm["build"] and prm["build"] != sess.build_id:
        log.warn("gb.build=%s differs from the initramfs build %s" % (prm["build"], sess.build_id))
    # (1) clean up a previous UI instance, (2) read-only guard pass
    scan.cleanup_installer_state()
    scan.guard_pass_listing()
    rescan_payload(sess)


def rescan_payload(sess):
    prm = sysfs.params()
    log.say("Looking for the installer files (up to 30 s) ...")
    p = scan.discover(sess.build_id, prm["methods"])
    sess.payload = p
    sess.m = p.m if p else None
    prot, _ = scan.protected_scan(p)
    sess.protected = prot
    if p is None:
        log.marker("PAYLOAD-NONE")
        log.say("The installer files (gbsb3 folder of build %s) were not found." % sess.build_id)
        return
    p.protected = prot
    scan.write_payload_json(p)
    log.marker("PAYLOAD", ("kind", p.kind), ("dev", p.dev), ("disk", p.disk), ("file", p.file or "-"),
               ("build", p.build_id), ("protected", ",".join(prot) or "-"))
    log.say("Installer files found (%s, %s)." % (p.kind, p.dev))


# ------------------------------------------------------------------------------------------------ journal
def journal_check(sess):
    j = journal.load()
    if j is None:
        return
    if not journal.is_open(j):
        if j.get("result") is not None:
            journal.archive()
        return
    key = (j.get("started"), j.get("result"))
    if sess.deferred == key:
        log.say("Note: an earlier run on %s did not finish (journal): flows on that disk are refused until it is "
                "acknowledged (tools l shows it)." % j.get("disk"))
        return
    res = j.get("result")
    log.marker("JOURNAL", ("flow", j.get("flow")), ("disk", j.get("disk")), ("phase", journal.last_phase(j)),
               ("result", ("FAILED:" + res.split(" ", 1)[1]) if res and " " in res else "none"))
    log.say("")
    log.say("An earlier run did not finish: %s on %s, last phase '%s', result %s." % (
        j.get("flow"), j.get("disk"), journal.last_phase(j), res or "none (the installer stopped)"))
    log.say(journal.recovery_text(j.get("flow"), journal.last_phase(j)))
    while True:
        r = log.prompt("journal.ack", "Type 'ACK %s' to acknowledge, or b to go back:" % j.get("disk"))
        if r == "ACK %s" % j.get("disk"):
            journal.acknowledge()
            sess.deferred = None
            return
        if r == "b":
            sess.deferred = key
            return
        log.say("  invalid reply")


# ------------------------------------------------------------------------------------------------ flows
def run_flow(sess, flow, fn):
    sess.journal = None
    sess.target = None
    status, code, msg = "OK", "-", ""
    try:
        fn(sess)
    except Back:
        return
    except FlowEnd as e:
        status, code, msg = e.status, e.code, e.msg
    except (errors.Internal, run.ToolRefused) as e:
        status, code, msg = "FAILED", "F_INTERNAL", "internal check failed: %s" % e
        log.error(traceback.format_exc())
    except EOFError:
        raise
    except KeyboardInterrupt:
        status, code, msg = "FAILED", "F_INTERRUPTED", ("the run was interrupted; see the journal shown next for "
                                                        "what was written")
        log.error("interrupted: " + traceback.format_exc())
    except Exception as e:
        status, code, msg = "FAILED", "F_INTERNAL", "unexpected error: %s: %s" % (type(e).__name__, e)
        log.error(traceback.format_exc())
    finally:
        run.PERMITS.clear()
        if sess.target is not None:
            sess.target.close()
        scan.efivars_ro()
    if msg:
        log.say("")
        log.say("%s: %s" % (status, msg))
    log.marker("RESULT", ("flow", flow), ("status", status), ("code", code))
    if sess.journal is not None:
        sess.journal.result(status if status in ("OK", "CANCELLED") else "%s %s" % (status, code))
    if status != "OK":
        snapshot_dmesg(flow)
    log.prompt("continue", "Press Enter to continue.")


def snapshot_dmesg(flow):
    try:
        t = run.dmesg_text()
        with open(os.path.join(const.LOG_DIR, "dmesg-%s-%s.txt" % (flow, utc_stamp())), "w") as f:
            f.write(t)
    except (OSError, run.ToolRefused) as e:
        log.warn("dmesg snapshot: %s" % e)


def main_menu(sess):
    from . import dualboot, uninstall, wholedisk
    while True:
        if sess.payload is None:
            if nopayload_menu(sess):
                continue
            return
        journal_check(sess)
        banner(sess)
        log.marker("MENU", ("id", "main"))
        log.say("  1  Write Googlebook to a whole disk (erases that disk)")
        log.say("  2  Install next to Windows (into unallocated space made in Windows)")
        log.say("  3  Uninstall what option 2 added")
        log.say("  4  Tools (shell, log, verify, compare, save logs, rescan, reboot, power off)")
        r = log.prompt("main", "Choice [1-4]:")
        if r == "1":
            run_flow(sess, "wholedisk", wholedisk.run_flow)
        elif r == "2":
            run_flow(sess, "dualboot", dualboot.run_flow)
        elif r == "3":
            run_flow(sess, "uninstall", uninstall.run_flow)
        elif r == "4":
            tools_menu(sess)
        else:
            log.say("  invalid choice")


def nopayload_menu(sess):
    log.marker("MENU", ("id", "nopayload"))
    log.say("")
    log.say("  r        rescan for the installer files")
    log.say("  s        shell")
    log.say("  reboot   reboot")
    log.say("  poweroff power off")
    r = log.prompt("nopayload", "Choice:")
    if r == "r":
        rescan_payload(sess)
    elif r == "s":
        shell()
    elif r in ("reboot", "poweroff"):
        shutdown(r)
    else:
        log.say("  invalid choice")
    return True


def shell():
    log.marker("SHELL")
    log.say("Shell (busybox). Type 'exit' to return to the installer.")
    run.shell()


def shutdown(kind):
    log.marker("REBOOT" if kind == "reboot" else "POWEROFF")
    cleanup()
    log.close()
    if kind == "reboot":
        run.reboot_force()
    else:
        run.poweroff_force()
    time.sleep(30)


def cleanup():
    try:
        run.sync()
        for mp in (const.ESP_MNT, const.SAVELOG_MNT):
            if scan.mounted_at(mp):
                scan.unmount(mp)
        scan.cleanup_installer_state()
        run.sync()
    except Exception as e:
        log.error("cleanup: %s" % e)


def tools_menu(sess):
    from . import tools as tl
    while True:
        log.marker("MENU", ("id", "tools"))
        log.say("")
        log.say("  s  shell            l  show the log      v  verify the installer files")
        log.say("  c  compare an installed disk with its install-time state (read-only)")
        log.say("  w  write the logs to a USB stick          r  rescan for the installer files")
        log.say("  b  back             reboot               poweroff")
        r = log.prompt("tools", "Tool:")
        if r == "s":
            shell()
        elif r == "l":
            tl.show_log()
        elif r == "v":
            run_flow(sess, "verify", tl.verify_payload)
        elif r == "c":
            run_flow(sess, "compare", tl.compare)
        elif r == "w":
            run_flow(sess, "savelog", tl.savelog)
        elif r == "r":
            rescan_payload(sess)
            if sess.payload is None:
                return
        elif r == "b":
            return
        elif r in ("reboot", "poweroff"):
            shutdown(r)
        else:
            log.say("  invalid choice")


def main(console):
    sess = Session(console)
    start(sess)
    main_menu(sess)
