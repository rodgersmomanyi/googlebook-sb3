"""run_tool(): the ONLY code of the installer that starts processes (DESIGN.md 4.0.10).

Every external program is started from here, with shell=False, an absolute path from the table below, a fixed
environment, a timeout, and every argument checked by a per-tool rule. The other modules call only the wrapper
functions at the end of this file and never name a program. Anything outside the table -> ToolRefused (the caller
turns it into F_INTERNAL); nothing is started.

Some forms need a one-shot permit that the calling flow grants right after its own verification (PERMITS):
  rw-mount (node, mountpoint)   4.0.13 node verification passed (ESP state 4.2.11, savelog/gptbackup 4.4)
  efivars-rw                    inside the EFI step (4.2.10 / 4.3.5) only
  target (node)                 the current flow target (kernel partition update, partition table listing/check)
  efi-create (disk, partno)     4.2.10.2
  efi-order (list)              the BootOrder computed and verified in 4.2.10.3 / 4.2.10.5
  efi-delete (XXXX)             the entry was re-verified as ours immediately before
  efi-delete-next (XXXX)        BootNext equals a number this run deleted (or created and rolled back)
  fsck (node)                   the node verified by 4.0.13
  ntfs-read (node, name)        name as listed by the NTFS listing wrapper (hiberfil.sys only)
The validator itself is pure (it takes a context object); host unit tests use it with a fake context. The launcher is
never executed outside the installer environment (the environment guard below refuses).
"""
import os
import re
import select
import stat
import time

from . import log

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"}

BLKID = "/usr/sbin/blkid"
LSBLK = "/usr/bin/lsblk"
LOSETUP = "/usr/sbin/losetup"
MOUNT = "/bin/mount"
UMOUNT = "/bin/umount"
ZSTD = "/usr/bin/zstd"
SGDISK = "/usr/sbin/sgdisk"
EFIBOOTMGR = "/usr/bin/efibootmgr"
PARTX = "/usr/bin/partx"
FSCK_VFAT = "/usr/sbin/fsck.vfat"
NTFSLS = "/usr/bin/ntfsls"
NTFSCAT = "/usr/bin/ntfscat"
DMESG = "/bin/dmesg"
SYNC = "/bin/sync"
SH = "/bin/sh"
REBOOT = "/bin/reboot"
POWEROFF = "/bin/poweroff"

PROBE = "/run/gb/probe/"
EFIVARS_DIR = "/sys/firmware/efi/efivars"
LSBLK_COLS = "NAME,KNAME,MAJ:MIN,SIZE,RO,RM,TYPE,TRAN,FSTYPE,PARTUUID,MOUNTPOINTS"
BOOT_LABEL = "Googlebook OS"
BOOT_LOADER = "\\EFI\\BOOT\\BOOTX64.EFI"

NODE_RE = re.compile(r"^/dev/[a-z][a-z0-9_.-]*$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
LOOP_RE = re.compile(r"^/dev/loop[0-9]+$")
ORDER_RE = re.compile(r"^[0-9A-F]{4}(,[0-9A-F]{4})*$")
NUM_RE = re.compile(r"^[0-9A-F]{4}$")
PARTNO_RE = re.compile(r"^[1-9][0-9]{0,2}$")


class ToolRefused(Exception):
    """A command outside the allow-list (-> F_INTERNAL; nothing was started)."""


# ------------------------------------------------------------------------------------------------ permits
class Permits:
    def __init__(self):
        self._p = {}

    def grant(self, kind, value=True):
        self._p.setdefault(kind, set()).add(value)
        log.debug("permit granted: %s %s" % (kind, value))

    def revoke(self, kind, value=None):
        if value is None:
            self._p.pop(kind, None)
        else:
            self._p.get(kind, set()).discard(value)

    def has(self, kind, value=True):
        return value in self._p.get(kind, set())

    def take(self, kind, value=True):
        if self.has(kind, value):
            self._p[kind].discard(value)
            return True
        return False

    def clear(self):
        self._p = {}


PERMITS = Permits()


# ------------------------------------------------------------------------------------------------ context
class LiveContext:
    """Predicates over the running system (used by run_tool)."""
    permits = PERMITS

    @staticmethod
    def realpath(p):
        return os.path.realpath(p)

    @staticmethod
    def is_block_node(p):
        try:
            return stat.S_ISBLK(os.stat(p).st_mode)
        except OSError:
            return False

    @staticmethod
    def is_partition_node(p):
        if not LiveContext.is_block_node(p):
            return False
        return os.path.exists("/sys/class/block/%s/partition" % os.path.basename(p))

    @staticmethod
    def is_regular_file(p):
        try:
            return stat.S_ISREG(os.stat(p).st_mode)
        except OSError:
            return False

    @staticmethod
    def payload_root():
        try:
            return os.path.realpath("/run/gb/payload") if os.path.islink("/run/gb/payload") else None
        except OSError:
            return None

    @staticmethod
    def loop_backing_below_probe(dev):
        try:
            with open("/sys/block/%s/loop/backing_file" % os.path.basename(dev)) as f:
                bf = f.read().strip()
        except OSError:
            return False
        return bf.startswith(PROBE)

    @staticmethod
    def ntfs_allowed(node):
        from . import sysfs            # local import: sysfs reads only, no process code
        return sysfs.ntfs_allowed_for_node(node)


def _under(ctx, path, prefix):
    rp = ctx.realpath(path)
    return rp.startswith(prefix) and len(rp) > len(prefix)


# ------------------------------------------------------------------------------------------------ the table
def _node(ctx, a):
    return bool(NODE_RE.match(a)) and ctx.is_block_node(a)


def _probe_mp(ctx, a):
    return a.startswith(PROBE) and bool(NAME_RE.match(a[len(PROBE):])) and ctx.realpath(a) == a


def _rule_blkid(ctx, a):
    return len(a) == 4 and a[:3] == ["-p", "-o", "export"] and _node(ctx, a[3])


def _rule_lsblk(ctx, a):
    return a == ["--json", "--bytes", "-o", LSBLK_COLS]


def _rule_losetup(ctx, a):
    if len(a) == 4 and a[:3] == ["-r", "-f", "--show"]:
        return _under(ctx, a[3], PROBE) and ctx.is_regular_file(a[3])
    if len(a) == 2 and a[0] == "-d":
        return bool(LOOP_RE.match(a[1])) and ctx.loop_backing_below_probe(a[1])
    return a == ["-l", "-J"]


RO_MOUNTS = {"iso9660": "ro", "vfat": "ro,shortname=mixed,utf8", "exfat": "ro", "ntfs3": "ro"}
RW_MOUNTS = {("vfat", "rw,flush", "/run/gb/esp"), ("vfat", "rw,flush,shortname=mixed,utf8", "/run/gb/savelog"),
             ("exfat", "rw", "/run/gb/savelog")}


def _rule_mount(ctx, a):
    if a == ["-o", "remount,rw", EFIVARS_DIR]:
        return ctx.permits.has("efivars-rw")
    if a == ["-o", "remount,ro,nosuid,nodev,noexec", EFIVARS_DIR]:
        return True
    if len(a) != 6 or a[0] != "-t" or a[2] != "-o" or not _node(ctx, a[4]):
        return False
    fstype, opts, node, mp = a[1], a[3], a[4], a[5]
    if RO_MOUNTS.get(fstype) == opts and _probe_mp(ctx, mp):
        return fstype != "ntfs3" or ctx.ntfs_allowed(node)
    if (fstype, opts, mp) in RW_MOUNTS:
        return ctx.permits.take("rw-mount", (node, mp))
    return False


def _rule_umount(ctx, a):
    return len(a) == 1 and a[0].startswith("/run/gb/") and _under(ctx, a[0], "/run/gb/")


def _rule_zstd(ctx, a):
    if len(a) != 3 or a[:2] != ["-dcq", "--long=27"]:
        return False
    root = ctx.payload_root()
    return bool(root) and _under(ctx, a[2], root + "/") and ctx.is_regular_file(a[2])


def _rule_sgdisk(ctx, a):
    return len(a) == 2 and a[0] in ("-v", "-p") and _node(ctx, a[1]) and ctx.permits.has("target", a[1])


def _rule_efibootmgr(ctx, a):
    if a == ["--verbose"]:
        return True
    if len(a) == 9 and a[0] == "--create-only" and a[1] == "--disk" and a[3] == "--part" and \
            a[5:] == ["--label", BOOT_LABEL, "--loader", BOOT_LOADER]:
        return _node(ctx, a[2]) and bool(PARTNO_RE.match(a[4])) and ctx.permits.take("efi-create", (a[2], a[4]))
    if len(a) == 2 and a[0] == "--bootorder":
        return bool(ORDER_RE.match(a[1])) and ctx.permits.take("efi-order", a[1])
    if len(a) == 3 and a[0] == "--bootnum" and a[2] == "--delete-bootnum":
        return bool(NUM_RE.match(a[1])) and ctx.permits.take("efi-delete", a[1])
    if a == ["--delete-bootnext"]:
        return ctx.permits.take("efi-delete-next")
    return False


def _rule_partx(ctx, a):
    return len(a) == 2 and a[0] == "-u" and _node(ctx, a[1]) and ctx.permits.has("target", a[1])


def _rule_fsck(ctx, a):
    return len(a) == 2 and a[0] == "-n" and _node(ctx, a[1]) and ctx.permits.take("fsck", a[1])


def _rule_ntfsls(ctx, a):
    return len(a) == 5 and a[:4] == ["-a", "-s", "-p", "/"] and _node(ctx, a[4]) and ctx.is_partition_node(a[4])


def _rule_ntfscat(ctx, a):
    return len(a) == 2 and _node(ctx, a[0]) and a[1].startswith("/") and \
        ctx.permits.take("ntfs-read", (a[0], a[1][1:]))


def _rule_noargs(ctx, a):
    return a == []


def _rule_sh(ctx, a):
    return a == ["-i"]


def _rule_force(ctx, a):
    return a == ["-f"]


TABLE = {
    BLKID: _rule_blkid, LSBLK: _rule_lsblk, LOSETUP: _rule_losetup, MOUNT: _rule_mount, UMOUNT: _rule_umount,
    ZSTD: _rule_zstd, SGDISK: _rule_sgdisk, EFIBOOTMGR: _rule_efibootmgr, PARTX: _rule_partx, FSCK_VFAT: _rule_fsck,
    NTFSLS: _rule_ntfsls, NTFSCAT: _rule_ntfscat, DMESG: _rule_noargs, SYNC: _rule_noargs, SH: _rule_sh,
    REBOOT: _rule_force, POWEROFF: _rule_force,
}


def validate(argv, ctx):
    """Raise ToolRefused unless argv is an allowed form (pure apart from ctx's predicates)."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(x, str) for x in argv):
        raise ToolRefused("argv must be a non-empty list of strings")
    argv = list(argv)
    rule = TABLE.get(argv[0])
    if rule is None:
        raise ToolRefused("program not in the allow-list: %r" % argv[0])
    if any("\0" in x or "\n" in x for x in argv):
        raise ToolRefused("control characters in arguments")
    if not rule(ctx, argv[1:]):
        raise ToolRefused("arguments not allowed for %s: %r" % (os.path.basename(argv[0]), argv[1:]))
    return True


# ------------------------------------------------------------------------------------------------ launcher
_GUARD = None


def _environment_ok():
    global _GUARD
    if _GUARD is None:
        try:
            ok = os.path.exists("/etc/gb-installer-release") and \
                os.path.realpath("/proc/1/exe") == os.path.realpath("/bin/busybox")
            with open("/proc/cmdline") as f:
                ok = ok and "gb.build=" in f.read()
        except OSError:
            ok = False
        _GUARD = ok
    return _GUARD


class Result:
    def __init__(self, rc, out, err):
        self.rc, self.out, self.err = rc, out, err

    @property
    def text(self):
        return self.out.decode("utf-8", "replace") if isinstance(self.out, bytes) else (self.out or "")

    @property
    def errtext(self):
        return self.err.decode("utf-8", "replace") if isinstance(self.err, bytes) else (self.err or "")


def _check(argv):
    validate(argv, LiveContext)
    if not _environment_ok():
        raise ToolRefused("not inside the installer environment")


def _log_cmd(argv):
    log.info("run: %s" % " ".join(argv))


def _log_out(name, data, binary):
    if binary:
        log.info("  %s: %d bytes (binary, not logged)" % (name, len(data)))
        return
    t = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    if not t:
        return
    if len(t) > 16000:
        t = t[:16000] + "\n[... %d more characters]" % (len(t) - 16000)
    for ln in t.rstrip("\n").split("\n"):
        log.info("  %s| %s" % (name, ln))


def run_tool(argv, timeout=60, binary=False, log_output=True):
    """Run an allowed command to completion. Returns Result(rc, out bytes, err bytes)."""
    _check(argv)
    import subprocess as sp      # the only process-creation module, imported only here
    _log_cmd(argv)
    t0 = time.time()
    try:
        p = sp.Popen(list(argv), stdin=sp.DEVNULL, stdout=sp.PIPE, stderr=sp.PIPE, env=dict(ENV), close_fds=True,
                     shell=False)
    except OSError as e:
        log.error("  start failed: %s" % e)
        return Result(127, b"", str(e).encode())
    try:
        out, err = p.communicate(timeout=timeout)
    except sp.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        log.error("  timeout after %d s: killed" % timeout)
        rc = -9
    else:
        rc = p.returncode
    log.info("  exit %d (%.1f s)" % (rc, time.time() - t0))
    if log_output:
        _log_out("out", out, binary)
        _log_out("err", err, False)
    return Result(rc, out, err)


class Stream:
    """Streaming stdout of a long-running allowed command (zstd, ntfscat)."""

    def __init__(self, argv, idle_timeout):
        _check(argv)
        import subprocess as sp
        _log_cmd(argv)
        self.argv = argv
        self.idle = idle_timeout
        self.bytes = 0
        self.err = b""
        self.rc = None
        self._p = sp.Popen(list(argv), stdin=sp.DEVNULL, stdout=sp.PIPE, stderr=sp.PIPE, env=dict(ENV),
                           close_fds=True, shell=False, bufsize=0)
        self._out = self._p.stdout.fileno()
        self._errfd = self._p.stderr.fileno()
        self._eof = False

    def read(self, n):
        """Up to n bytes; b'' at end of stream. Raises OSError on an idle timeout."""
        while not self._eof:
            fds = [self._out] + ([self._errfd] if self._errfd >= 0 else [])
            r, _, _ = select.select(fds, [], [], self.idle)
            if not r:
                self.kill()
                raise OSError("no output for %d s from %s" % (self.idle, os.path.basename(self.argv[0])))
            if self._errfd >= 0 and self._errfd in r:
                chunk = os.read(self._errfd, 65536)
                if not chunk:
                    self._errfd = -1
                elif len(self.err) < 65536:
                    self.err += chunk
            if self._out in r:
                data = os.read(self._out, n)
                if not data:
                    self._eof = True
                    break
                self.bytes += len(data)
                return data
        return b""

    def read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            d = self.read(min(n - len(buf), 1 << 20))
            if not d:
                break
            buf += d
        return bytes(buf)

    def kill(self):
        try:
            self._p.kill()
        except OSError:
            pass

    def close(self, terminate=False):
        """Wait for the process (terminating it first if asked). Returns the exit status."""
        if terminate and self._p.poll() is None:
            try:
                self._p.terminate()
            except OSError:
                pass
        try:
            rest = self._p.stderr.read() if self._errfd != -1 else b""
        except (OSError, ValueError):
            rest = b""
        self.err += rest or b""
        try:
            self.rc = self._p.wait(timeout=30)
        except Exception:
            self.kill()
            self.rc = self._p.wait()
        for f in (self._p.stdout, self._p.stderr):
            try:
                f.close()
            except OSError:
                pass
        log.info("  exit %d, %d bytes of output (binary, not logged)" % (self.rc, self.bytes))
        _log_out("err", self.err, False)
        return self.rc


def run_interactive(argv, extra_env=None):
    """An interactive program on the UI console (tools 's' only): inherits stdin/stdout/stderr, no timeout."""
    _check(argv)
    import subprocess as sp
    _log_cmd(argv)
    env = dict(ENV)
    env.update(extra_env or {})
    p = sp.Popen(list(argv), env=env, close_fds=True, shell=False)
    rc = p.wait()
    log.info("  exit %d" % rc)
    return rc


# ------------------------------------------------------------------------------------------------ wrappers
PRIVATE_BLKID_KEYS = ("LABEL", "LABEL_ENC", "PART_ENTRY_NAME")


def blkid_probe(node):
    """blkid -p -o export <node> -> dict (empty when nothing was found). Volume labels and partition names are
    never logged (5.1): only the other keys are."""
    r = run_tool([BLKID, "-p", "-o", "export", node], timeout=30, log_output=False)
    out = {}
    for ln in r.text.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    log.info("  blkid: " + " ".join("%s=%s" % (k, v) for k, v in sorted(out.items())
                                    if k not in PRIVATE_BLKID_KEYS and k != "DEVNAME"))
    out["_rc"] = r.rc
    return out


def lsblk_log():
    run_tool([LSBLK, "--json", "--bytes", "-o", LSBLK_COLS], timeout=30, log_output=True)


def loop_attach_ro(path):
    r = run_tool([LOSETUP, "-r", "-f", "--show", path], timeout=30)
    dev = r.text.strip()
    return dev if r.rc == 0 and LOOP_RE.match(dev) else None


def loop_detach(dev):
    return run_tool([LOSETUP, "-d", dev], timeout=30).rc == 0


def loop_list_log():
    run_tool([LOSETUP, "-l", "-J"], timeout=30)


def mount_probe(fstype, node, name):
    """Read-only probe mount at /run/gb/probe/<name>. Returns (ok, error text)."""
    mp = PROBE + name
    os.makedirs(mp, exist_ok=True)
    r = run_tool([MOUNT, "-t", fstype, "-o", RO_MOUNTS[fstype], node, mp], timeout=60)
    return r.rc == 0, (r.errtext + r.text).strip()


def mount_esp_rw(node):
    os.makedirs("/run/gb/esp", exist_ok=True)
    r = run_tool([MOUNT, "-t", "vfat", "-o", "rw,flush", node, "/run/gb/esp"], timeout=60)
    return r.rc == 0, (r.errtext + r.text).strip()


def mount_savelog_rw(fstype, node):
    os.makedirs("/run/gb/savelog", exist_ok=True)
    opts = "rw,flush,shortname=mixed,utf8" if fstype == "vfat" else "rw"
    r = run_tool([MOUNT, "-t", fstype, "-o", opts, node, "/run/gb/savelog"], timeout=60)
    return r.rc == 0, (r.errtext + r.text).strip()


def efivars_remount_rw():
    return run_tool([MOUNT, "-o", "remount,rw", EFIVARS_DIR], timeout=30).rc == 0


def efivars_remount_ro():
    return run_tool([MOUNT, "-o", "remount,ro,nosuid,nodev,noexec", EFIVARS_DIR], timeout=30).rc == 0


def umount(path):
    r = run_tool([UMOUNT, path], timeout=60)
    return r.rc == 0, (r.errtext + r.text).strip()


def zstd_stream(path):
    return Stream([ZSTD, "-dcq", "--long=27", path], idle_timeout=180)


def table_verify(node):
    """Partition table consistency check (sgdisk -v). Returns (ok, text)."""
    r = run_tool([SGDISK, "-v", node], timeout=60)
    t = r.text
    ok = r.rc == 0 and "No problems found" in t
    return ok, t


def table_print_log(node):
    run_tool([SGDISK, "-p", node], timeout=60)


def bootmgr_list_log():
    run_tool([EFIBOOTMGR, "--verbose"], timeout=60)


def bootmgr_create(disk_node, partno):
    r = run_tool([EFIBOOTMGR, "--create-only", "--disk", disk_node, "--part", str(partno), "--label", BOOT_LABEL,
                  "--loader", BOOT_LOADER], timeout=60)
    return r.rc == 0


def bootmgr_set_order(order_text):
    return run_tool([EFIBOOTMGR, "--bootorder", order_text], timeout=60).rc == 0


def bootmgr_delete(num_text):
    return run_tool([EFIBOOTMGR, "--bootnum", num_text, "--delete-bootnum"], timeout=60).rc == 0


def bootmgr_delete_next():
    return run_tool([EFIBOOTMGR, "--delete-bootnext"], timeout=60).rc == 0


def kernel_partitions_update(disk_node):
    return run_tool([PARTX, "-u", disk_node], timeout=60).rc == 0


# The dual-boot export's EFI-SYSTEM.img (build_internal.py) carries its volume label only in the boot sector, not as
# a root-directory label entry. dosfstools 4.2 reports that with "-n" and exits 1 although the file system is fine
# (the untouched export image gives the same output). This one note is accepted; any other line fails the check.
FAT_NOTE_LINES = (
    re.compile(r"^Label in boot sector is '[^']*', but there is no volume label in root directory\.$"),
    re.compile(r"^\s*Auto-removing label from boot sector\.$"),
)
FAT_INFO_LINES = (
    re.compile(r"^fsck\.fat \d+\.\d+(\.\d+)? \(.*\)$"),
    re.compile(r"^Leaving filesystem unchanged\.$"),
    re.compile(r"^Starting (check/repair|verification) pass\.$"),
    re.compile(r"^\S+: \d+ files, \d+/\d+ clusters$"),
    # the initramfs has no iconv (gconv) modules: dosfstools says so on stderr and uses its built-in CP850 table
    re.compile(r"^Cannot initialize conversion from (codepage \d+ to \S+|\S+ to codepage \d+): Invalid argument$"),
    re.compile(r"^Using internal CP\d+ conversion table$"),
)


def fat_check_verdict(rc, text):
    """Pure: (ok, note) for the output of the FAT check (-n). Exit 0 -> (True, 'clean'). Exit 1 -> ok only when
    every non-empty line is informational or part of the boot-sector label note (-> (True, 'label')). Else not ok."""
    if rc == 0:
        return True, "clean"
    if rc != 1:
        return False, "exit %s" % rc
    noted = False
    for ln in text.splitlines():
        s = ln.rstrip()
        if not s.strip():
            continue
        if any(p.match(s) for p in FAT_INFO_LINES):
            continue
        if any(p.match(s) for p in FAT_NOTE_LINES):
            noted = True
            continue
        return False, "problem: %s" % s.strip()[:120]
    return (True, "label") if noted else (False, "exit 1 without a known note")


def esp_check(node):
    r = run_tool([FSCK_VFAT, "-n", node], timeout=600)
    ok, note = fat_check_verdict(r.rc, r.text + "\n" + r.errtext)
    if ok and note == "label":
        log.info("FAT check: only the boot-sector label note (as on the untouched export image): accepted")
    elif not ok:
        log.error("FAT check failed: %s" % note)
    return ok, note


def ntfs_list_root(node):
    """Root listing of an NTFS volume (read-only device open). Returns (rc, names)."""
    r = run_tool([NTFSLS, "-a", "-s", "-p", "/", node], timeout=30, log_output=False)
    names = [ln.rstrip("\n") for ln in r.text.splitlines() if ln.strip()]
    log.info("  %d root entries (names not logged); hiberfil.sys %s; stderr: %s" % (
        len(names), "present" if any(n.lower() == "hiberfil.sys" for n in names) else "absent",
        r.errtext.strip()[:300]))
    if r.rc == 0:
        for nm in names:
            if nm.lower() == "hiberfil.sys":
                PERMITS.grant("ntfs-read", (node, nm))
    return r.rc, names


def ntfs_read_head(node, name, n=4096, timeout=30):
    """First n bytes of /<name>. Returns (data, rc) where rc is the exit status (None when terminated after n)."""
    s = Stream([NTFSCAT, node, "/" + name], idle_timeout=timeout)
    try:
        data = s.read_exact(n)
    except OSError as e:
        log.error("  read failed: %s" % e)
        s.close(terminate=True)
        return b"", -1
    if len(data) >= n:
        s.close(terminate=True)
        return data, None
    rc = s.close()
    return data, rc


def dmesg_text():
    """Kernel log without lines that may carry serial numbers (USB enumeration, SCSI VPD)."""
    t = run_tool([DMESG], timeout=10, log_output=False).text
    return "".join(ln + "\n" for ln in t.splitlines() if "serial" not in ln.lower() and "wwn" not in ln.lower())


def sync():
    run_tool([SYNC], timeout=300)


def shell():
    return run_interactive([SH, "-i"], {"PS1": "gbsh# "})


def reboot_force():
    run_tool([REBOOT, "-f"], timeout=30)


def poweroff_force():
    run_tool([POWEROFF, "-f"], timeout=30)
