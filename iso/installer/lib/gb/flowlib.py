"""Shared pieces of the destructive flows: check reporting, disk choice, confirmations, power state, in-use and
hibernation checks, read-only guard passes, backups. Device side."""
import contextlib
import json
import os
import signal

from . import const
from . import disks as dk
from . import efi
from . import efivars
from . import journal
from . import log
from . import run
from . import scan
from . import sysfs
from .errors import Back, Cancelled, Refused
from .util import hiber_signature, keyboard_names, power_verdict, utc_stamp


class Checks:
    """Every check is printed (@@GB CHECK); the RESULT code is the first failing one in table order. A check that
    can only pass with the user's typed acknowledgement (report_ack) prints result=warn and is asked after all checks,
    only when none of them refused."""

    def __init__(self):
        self.first = None
        self.results = []
        self.acks = []

    def report_ack(self, name, code, problem, pid, expected, text):
        log.marker("CHECK", ("name", name), ("result", "warn"), ("code", "-"))
        log.say("  [warn] %s: %s (your confirmation is asked below)" % (name, problem))
        self.results.append((name, "ack"))
        self.acks.append((name, code, problem, pid, expected, text))

    def report(self, name, code, problem, warn=False, skipped=False):
        if skipped:
            log.marker("CHECK", ("name", name), ("result", "warn"), ("code", "-"))
            log.say("  [skip] %-20s not evaluated (an earlier check failed)" % name)
            self.results.append((name, "skip"))
            return
        if problem is None:
            log.marker("CHECK", ("name", name), ("result", "ok"), ("code", "-"))
            log.say("  [ok]   %s" % name)
            self.results.append((name, "ok"))
        elif warn:
            log.marker("CHECK", ("name", name), ("result", "warn"), ("code", "-"))
            log.say("  [warn] %s: %s" % (name, problem))
            self.results.append((name, "warn"))
        else:
            log.marker("CHECK", ("name", name), ("result", "refuse"), ("code", code))
            log.say("  [FAIL] %s: %s" % (name, problem))
            self.results.append((name, "refuse"))
            if self.first is None:
                self.first = (code, problem)

    def ok(self):
        return self.first is None

    def raise_first(self):
        if self.first is not None:
            raise Refused(self.first[0], self.first[1])
        for name, code, problem, pid, expected, text in self.acks:
            log.say("")
            log.say(text)
            r = log.prompt(pid, "Type exactly '%s' to continue (anything else stops here):" % expected)
            if r != expected:
                raise Refused(code, "%s (not confirmed)" % problem)
            log.info("check %s: confirmed by the user" % name)


def pick(pid, items, label):
    """A listed number or 'b'. Returns the item."""
    while True:
        r = log.prompt(pid, "%s: number, or b to go back:" % label)
        if r == "b":
            raise Back()
        if r.isdigit() and 1 <= int(r) <= len(items):
            return items[int(r) - 1]
        log.say("  invalid choice")


def pick_disk(pid, disks, label):
    """A listed disk that could be read (a disk with flag 'unreadable' cannot be chosen)."""
    while True:
        d = pick(pid, disks, label)
        if "unreadable" not in d.flags:
            return d
        log.say("  %s could not be read (%s): it cannot be chosen. Unplug it or rescan, then choose again." % (
            d.kname, d.error or "-"))


def unreadable_problem(disks, exclude=None):
    """R_DISK_UNREADABLE text when a listed disk (other than `exclude`) could not be read, else None: the cross-disk
    checks cannot see its partition table."""
    bad = dk.unreadable(disks, exclude)
    if not bad:
        return None
    return ("disk %s could not be read, so this check cannot see its partitions: unplug it or rescan"
            % ", ".join(bad))


def ask_yes_no(pid, text):
    while True:
        r = log.prompt(pid, text + " [y/n]:")
        if r in ("y", "n"):
            return r == "y"
        log.say("  answer y or n")


def confirm(pid, expected, text):
    log.say(text)
    r = log.prompt(pid, "Type exactly '%s' to continue (anything else cancels):" % expected)
    if r != expected:
        raise Cancelled("confirmation %r did not match" % r)


# ------------------------------------------------------------------------------------------------ power (4.0.15)
def power_summary():
    """(state, why, text) of the power supplies; every supply's type/online/status is logged (tools l shows it)."""
    sup = sysfs.power_supplies()
    st, why = power_verdict(sup)
    text = "; ".join("%s type=%s online=%s status=%s" % (n, t or "-", "-" if o is None else o, s or "-")
                     for n, t, o, s in sup) or "no /sys/class/power_supply entries"
    log.info("power: %s (%s); supplies: %s" % (st, why, text))
    return st, why, text


def check_power(checks, applies=True):
    if not applies:
        checks.report("power", "R_ON_BATTERY", None)
        return
    st, why, _ = power_summary()
    if st == "ac":
        checks.report("power", "R_ON_BATTERY", None)
    elif sysfs.is_vmware():
        checks.report("power", "R_ON_BATTERY", "no AC power supply reported (ignored in a VM)", warn=True)
    elif st == "battery":
        checks.report("power", "R_ON_BATTERY", "on battery (%s): plug in the charger, wait 10 s and retry" % why)
    else:
        checks.report_ack("power", "R_ON_BATTERY", "power state unknown (%s)" % why, "power.ack", "AC-OK",
                          "The installer cannot tell whether the charger is connected (the firmware reports no "
                          "charger state; tools l shows what it reports). Plug in the Surface charger and keep it "
                          "plugged in: if the battery runs out during the write, the disk is left half-written.")


# ------------------------------------------------------------------------------------------------ in use
def in_use(d, protected):
    """Mounted (other than installer probe mounts, which are unmounted first) or held. Returns a problem or None.
    On a protected disk nothing is unmounted (the payload lives there; payload_disk refuses it anyway) and the
    installer's own mounts do not count."""
    names = [d.kname] + [p["kname"] for p in sysfs.kernel_partitions(d.kname)]
    nums = set(sysfs.devnum(k) for k in names)
    if d.kname not in protected:
        for m in sysfs.installer_mounts():
            if m["devnum"] in nums and m["mountpoint"].startswith(const.PROBE_DIR + "/"):
                scan.unmount(m["mountpoint"])
    ms = [m for m in sysfs.mounts_on(names) if not m["mountpoint"].startswith(const.RUN + "/")]
    if d.kname not in protected:
        ms = sysfs.mounts_on(names)
    if ms:
        return "mounted (%s)" % ", ".join(sorted(set(m["mountpoint"] for m in ms)))
    held = [k for k in names if sysfs.holders(k)]
    if held:
        return "held by another device (%s)" % ", ".join(held)
    try:
        with open("/proc/swaps") as f:
            for ln in f.readlines()[1:]:
                dev = ln.split()[0] if ln.split() else ""
                if os.path.basename(dev) in names:
                    return "used as swap"
    except OSError:
        pass
    return None


def bitlocker_problem(d):
    hits = []
    if "bitlocker" in d.flags:
        for i, r in enumerate(d.ranges):
            if "bitlocker" in r.get("rflags", ()):
                hits.append(str(i + 1))
        return "BitLocker volume found (partition %s)" % (",".join(hits) or "disk")
    return None


# ------------------------------------------------------------------------------------------------ hibernation (4.2.2)
HIBER_REMEDY = ("start Windows and use Restart (not Shut down), or turn Fast Startup off; if this keeps happening, "
                "run 'powercfg /h off' in an elevated prompt in Windows (it deletes hiberfil.sys), then Restart")


def hibernation(d):
    """(code, problem, encrypted) for the NTFS partitions of d, read with the userspace NTFS tools only.
    encrypted: numbers of BitLocker partitions (their hiberfil.sys cannot be read, so Fast Startup is unchecked
    there; the caller refuses or asks for a typed acknowledgement)."""
    encrypted = []
    for i, r in enumerate(d.ranges):
        if "bitlocker" in r.get("rflags", ()):
            encrypted.append(i + 1)
            continue
        if not r.get("ntfs"):
            continue
        k = r.get("kname")
        if not k:
            return "R_NTFS_UNCHECKED", "NTFS partition %d has no kernel device: cannot check hibernation" % (
                i + 1), encrypted
        try:
            node = sysfs.node(k)
        except OSError as e:
            return "R_NTFS_UNCHECKED", str(e), encrypted
        rc, names = run.ntfs_list_root(node)
        if rc != 0:
            return "R_NTFS_UNCHECKED", "NTFS partition %d (%s) could not be read (exit %s)" % (i + 1, k, rc), \
                encrypted
        hib = [n for n in names if n.lower() == "hiberfil.sys"]
        if not hib:
            log.info("%s: no hiberfil.sys" % k)
            continue
        data, rc2 = run.ntfs_read_head(node, hib[0])
        if rc2 not in (None, 0):
            return "R_NTFS_UNCHECKED", "hiberfil.sys on %s could not be read (exit %s): %s" % (
                k, rc2, HIBER_REMEDY), encrypted
        blocking, sig = hiber_signature(data)
        log.info("%s: hiberfil.sys signature %s (%s)" % (k, sig, "hibernated" if blocking else "not hibernated"))
        if blocking:
            return "R_HIBERNATED", ("Windows is hibernated or was shut down with Fast Startup (%s, hiberfil.sys "
                                    "signature '%s'): %s" % (k, sig, HIBER_REMEDY)), encrypted
    return None, None, encrypted


def encrypted_problem(encrypted):
    return ("Fast Startup / hibernation cannot be checked on the BitLocker-encrypted partition(s) %s (their "
            "hiberfil.sys cannot be read)" % ",".join(str(x) for x in encrypted))


ENCRYPTED_ACK_TEXT = ("Windows partitions on this disk are encrypted (BitLocker), so the installer cannot see whether "
                      "Windows is hibernated or stopped with Fast Startup. Changing the partition table under a "
                      "hibernated Windows can damage it. Continue only if you reached the installer with Windows' "
                      "Restart (not Shut down) or Fast Startup is off ('powercfg /h off').")


# ------------------------------------------------------------------------------------------------ destructive window
@contextlib.contextmanager
def destructive():
    """From the typed confirmation until the flow ends: Ctrl+C, Ctrl+\\ and Ctrl+Z are ignored (they would stop the
    run between two writes), and the Ctrl+Alt+Del handler refuses while the flag file names this process."""
    old = {}
    for s in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP):
        try:
            old[s] = signal.signal(s, signal.SIG_IGN)
        except (ValueError, OSError) as e:
            log.warn("signal %s: %s" % (s, e))
    try:
        journal.flow_flag_set()
        yield
    finally:
        journal.flow_flag_clear()
        for s, h in old.items():
            try:
                signal.signal(s, h)
            except (ValueError, OSError):
                pass


# ------------------------------------------------------------------------------------------------ guard passes (3.10)
def guard_flow_start(target, all_disks, protected):
    """3.10.2: pre-existing partitions of the target, every partition of every non-target disk, every dm/md
    device, and the whole device of every protected disk."""
    names = []
    for d in sysfs.disk_names():
        if d.startswith("dm-") or d.startswith("md"):
            names.append(d)
        elif not sysfs.CAND_EXCLUDE.match(d):
            names += [p["kname"] for p in sysfs.kernel_partitions(d)]
    names += [d for d in protected if sysfs.is_whole(d) and d != target]
    sysfs.guard([n for n in names if n != target], 1, "roguard(flow)")


def guard_after_rescan(target, protected, new_parts):
    """3.10.3: the same set again, except the partitions this flow just created."""
    names = []
    for d in sysfs.disk_names():
        if d.startswith("dm-") or d.startswith("md"):
            names.append(d)
        elif not sysfs.CAND_EXCLUDE.match(d):
            names += [p["kname"] for p in sysfs.kernel_partitions(d)]
    names += [d for d in protected if sysfs.is_whole(d) and d != target]
    sysfs.guard([n for n in names if n != target and n not in new_parts], 1, "roguard(rescan)")


# ------------------------------------------------------------------------------------------------ journal gate
def check_incomplete(checks, d):
    j = journal.load()
    if journal.blocks(j, d.kname, d.diskseq):
        checks.report("incomplete_run", "R_INCOMPLETE_RUN", "an earlier run on %s did not finish (journal: %s); "
                      "acknowledge it first (ACK %s)" % (d.kname, journal.last_phase(j), d.kname))
    else:
        checks.report("incomplete_run", "R_INCOMPLETE_RUN", None)


# ------------------------------------------------------------------------------------------------ backups
def backup_dir():
    p = os.path.join(const.BACKUP_DIR, utc_stamp())
    k = 1
    base = p
    while os.path.exists(p):
        p = "%s-%d" % (base, k)
        k += 1
    os.makedirs(p)
    return p


def write_file(path, data):
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def write_json(path, obj, compact=True):
    write_file(path, json.dumps(obj, separators=(",", ":")) if compact else json.dumps(obj, indent=1))


# ------------------------------------------------------------------------------------------------ EFI helpers
def esp_guids(g):
    return set(e["guid"] for e in g["primary"]["entries"] if e["type"] == const.TYPE_ESP)


def windows_entry(g):
    """(number, option, raw) of a Windows Boot Manager entry of the disk (4.2 definition), or None."""
    order = efivars.boot_order()
    if order is None:
        return None
    guids = esp_guids(g)
    for n in order:
        opt, raw = efivars.load_option(n)
        if opt is not None and efi.is_windows_boot_manager(opt, n, order, guids):
            return n, opt, raw
    return None


def our_entries(esp_guid):
    out = []
    for n in efivars.boot_numbers():
        opt, raw = efivars.load_option(n)
        if opt is not None and efi.is_ours(opt, esp_guid):
            out.append((n, opt, raw))
    return out


def named_entries():
    out = []
    for n in efivars.boot_numbers():
        opt, raw = efivars.load_option(n)
        if opt is not None and opt["description"] == const.BOOT_LABEL:
            out.append(n)
    return out


def keyboards():
    """Names of the input devices that can type (logged; the main screen shows the count)."""
    try:
        return keyboard_names(sysfs.input_devices())
    except OSError:
        return []


def list_disks(sess, title):
    """Protected scan + enumeration + table. Returns the disk list."""
    log.say("")
    log.say(title)
    log.say("  scanning disks (read-only) ...")
    run.lsblk_log()
    prot, insp = scan.protected_scan(sess.payload)
    sess.protected = prot
    disks = dk.enumerate_disks(sess.payload, sess.m, prot, insp)
    sess.disks = disks
    dk.show_disks(disks)
    return disks
