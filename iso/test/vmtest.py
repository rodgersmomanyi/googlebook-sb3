r"""VM lifecycle, pre-start guard and serial driver of the TEST harness (DESIGN.md 7.1).

* make_vmx(): the single throwaway VM D:\GB-iso\test\vm\gbiso-test.vmx (UEFI, Secure Boot off, 2 GB, 2 vCPU, no USB,
  no network, no floppy, no sound, serial0 = \\.\pipe\gbiso_con), disks swapped per session.
* guard_vmx() / guard_vmdk(): the pre-start guard. MUST pass before every vmrun start.
* Vmrun: every call asserts the vmx path is below D:\GB-iso\test\ (never any other VM; `list` is read-only).
* PipeTransport: the serial console over the named pipe, polled with PeekNamedPipe (own code, same technique as
  C:\GB-x86\gbsh.py, which is never used or imported).
* Console: expect-style driver on any transport: marker parsing, prompts (5.2/5.3), the runtime kill-switch,
  the busybox shell (tools s) and file pulls.
"""
import base64
import gzip
import io
import os
import re
import stat
import subprocess
import tarfile
import time
import uuid

import common as C

# ------------------------------------------------------------------------------------------------ vmx
SLOT_MODES = {"nvme0:0": "independent-persistent", "sata0:1": "independent-nonpersistent",
              "sata0:2": "independent-nonpersistent", "sata0:3": "independent-persistent",
              "sata0:4": "independent-persistent"}
# extra vmx keys a session may add (high-resolution GOP for the console font test, RTC in UTC); nothing else
EXTRA_KEYS = ("svga.autodetect", "svga.maxWidth", "svga.maxHeight", "svga.vramSize", "rtc.diffFromUTC")
FIXED_KEYS = {  # key -> exact value the guard requires (7.1)
    "firmware": "efi", "uefi.secureBoot.enabled": "FALSE",
    "usb.present": "FALSE", "usb_xhci.present": "FALSE", "ehci.present": "FALSE",
    "usb.generic.autoConnect": "FALSE", "ethernet0.present": "FALSE", "floppy0.present": "FALSE",
    "sound.present": "FALSE", "serial0.fileType": "pipe", "serial0.fileName": C.PIPE,
}
FORBIDDEN_SUBSTRINGS = ("\\\\.\\", "\\\\?\\", "physicaldrive", "globalroot", "fulldevice", "partitioneddevice",
                        "rawdevice")


def make_vmx(nvme=None, cd=None, sata1=None, sata2=None, display="gbiso-test", sata3=None, extra=(), sata4=None):
    """vmx text. nvme/sata1/sata2/sata3/sata4: absolute .vmdk paths (or None; sata3/sata4 are persistent: test
    'sticks' the installer may write a GPT copy or logs to); cd: absolute ISO path (or None); extra: (key, value)
    pairs from EXTRA_KEYS."""
    v = [
        ('.encoding', 'UTF-8'), ('config.version', '8'), ('virtualHW.version', '21'),
        ('displayName', display), ('guestOS', 'other6xlinux-64'),
        ('firmware', 'efi'), ('uefi.secureBoot.enabled', 'FALSE'),
        ('memSize', '2048'), ('numvcpus', '2'), ('cpuid.coresPerSocket', '2'),
        ('nvram', C.NVRAM_NAME), ('msg.autoAnswer', 'TRUE'), ('uuid.action', 'keep'),
        ('uuid.bios', '56 4d 47 42 49 53 4f 54-65 73 74 56 4d 30 30 31'),
        ('tools.syncTime', 'FALSE'), ('svga.present', 'TRUE'),
        ('pciBridge0.present', 'TRUE'), ('pciBridge4.present', 'TRUE'),
        ('pciBridge4.virtualDev', 'pcieRootPort'), ('pciBridge4.functions', '8'),
        ('usb.present', 'FALSE'), ('usb_xhci.present', 'FALSE'), ('ehci.present', 'FALSE'),
        ('usb.generic.autoConnect', 'FALSE'),
        ('ethernet0.present', 'FALSE'), ('floppy0.present', 'FALSE'), ('sound.present', 'FALSE'),
        ('serial0.present', 'TRUE'), ('serial0.fileType', 'pipe'), ('serial0.fileName', C.PIPE),
        ('serial0.pipe.endPoint', 'server'), ('serial0.tryNoRxLoss', 'TRUE'), ('serial0.yieldOnMsrRead', 'TRUE'),
        ('serial0.startConnected', 'TRUE'), ('rtc.diffFromUTC', '0'),
    ]
    for k, val in extra:
        if k not in EXTRA_KEYS or k == 'rtc.diffFromUTC':
            raise C.HarnessError("vmx key %s is not an allowed extra key" % k)
        if not re.match(r'^[A-Z0-9]+$', val):
            raise C.HarnessError("vmx value %r for %s" % (val, k))
        v.append((k, val))
    if nvme:
        v += [('nvme0.present', 'TRUE'), ('nvme0:0.present', 'TRUE'), ('nvme0:0.fileName', nvme),
              ('nvme0:0.deviceType', 'disk'), ('nvme0:0.mode', SLOT_MODES['nvme0:0'])]
    if cd or sata1 or sata2 or sata3 or sata4:
        v += [('sata0.present', 'TRUE')]
    if cd:
        v += [('sata0:0.present', 'TRUE'), ('sata0:0.deviceType', 'cdrom-image'), ('sata0:0.fileName', cd),
              ('sata0:0.startConnected', 'TRUE')]
    for slot, path in (('sata0:1', sata1), ('sata0:2', sata2), ('sata0:3', sata3), ('sata0:4', sata4)):
        if path:
            v += [(slot + '.present', 'TRUE'), (slot + '.fileName', path), (slot + '.deviceType', 'disk'),
                  (slot + '.mode', SLOT_MODES[slot])]
    for k, val in v:
        if '"' in val:
            raise C.HarnessError("quote in vmx value: %r" % val)
    return "".join('%s = "%s"\n' % (k, val) for k, val in v)


def parse_vmx(text):
    """{key: value} (keys as written; later duplicates are reported by the guard)."""
    out, dups = {}, []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = re.match(r'^([^=\s]+)\s*=\s*"(.*)"\s*$', ln)
        if not m:
            dups.append("unparsable vmx line: " + ln)
            continue
        if m.group(1).lower() in (k.lower() for k in out):
            dups.append("duplicate vmx key " + m.group(1))
        out[m.group(1)] = m.group(2)
    return out, dups


_EXTENT = re.compile(r'^(RW|RDONLY|NOACCESS)\s+(\d+)\s+(\S+)\s+"([^"]*)"(?:\s+(\d+))?\s*$')


def parse_vmdk(text):
    """(fields dict, extents [(access, sectors, kind, path, offset|None)], problems)."""
    fields, ext, probs = {}, [], []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = _EXTENT.match(s)
        if m:
            ext.append((m.group(1), int(m.group(2)), m.group(3), m.group(4),
                        int(m.group(5)) if m.group(5) is not None else None))
            continue
        m = re.match(r'^([A-Za-z0-9_.]+)\s*=\s*"?([^"]*)"?\s*$', s)
        if m:
            fields[m.group(1)] = m.group(2)
        elif re.match(r'^(RW|RDONLY|NOACCESS)\b', s):
            probs.append("unparsable extent line: " + s)
        else:
            probs.append("unparsable descriptor line: " + s)
    return fields, ext, probs


def _regular_below(path, root):
    """Problems for 'path must resolve to an existing regular file below root' (no symlink/junction/reparse)."""
    p = []
    real = os.path.realpath(path)
    if not C.is_below(real, root):
        p.append("%s resolves outside %s (%s)" % (path, root, real))
    try:
        st = os.lstat(path)
    except OSError as e:
        return p + ["%s: %s" % (path, e)]
    if not stat.S_ISREG(st.st_mode):
        p.append("%s is not a regular file" % path)
    if getattr(st, "st_file_attributes", 0) & 0x400:
        p.append("%s is a reparse point" % path)
    if os.path.islink(path):
        p.append("%s is a symlink" % path)
    return p


def _value_problems(key, val):
    lv = val.lower()
    if key.lower() == "serial0.filename":
        return [] if val == C.PIPE else ["serial0.fileName must be %s, is %s" % (C.PIPE, val)]
    return ["%s contains a forbidden device string (%s)" % (key, val) for s in FORBIDDEN_SUBSTRINGS if s in lv][:1]


def guard_vmdk(vmdk, root=C.TEST_ROOT):
    """Pre-start guard for one descriptor: monolithicFlat, extents `RW|RDONLY <n> FLAT "<path>" 0` only, each a regular
    file below root, n sectors == the file's size (never beyond it)."""
    probs = _regular_below(vmdk, root)
    if probs:
        return probs
    with open(vmdk, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if len(text) > 64 * 1024:
        return ["%s: descriptor larger than 64 KiB (not a flat descriptor?)" % vmdk]
    fields, ext, probs = parse_vmdk(text)
    for k, val in fields.items():
        probs += _value_problems(k, val)
    if fields.get("createType") != "monolithicFlat":
        probs.append('%s: createType is %r, must be "monolithicFlat"' % (vmdk, fields.get("createType")))
    if "parentFileNameHint" in fields or fields.get("parentCID", "ffffffff").lower() != "ffffffff":
        probs.append("%s: has a parent (delta disk)" % vmdk)
    if not ext:
        probs.append("%s: no extent" % vmdk)
    for acc, n, kind, path, off in ext:
        if acc not in ("RW", "RDONLY") or kind != "FLAT" or off != 0:
            probs.append('%s: extent "%s %d %s %s %s" not of the form RW|RDONLY <n> FLAT "<path>" 0'
                         % (vmdk, acc, n, kind, path, off))
            continue
        for bad in FORBIDDEN_SUBSTRINGS:
            if bad in path.lower():
                probs.append("%s: extent path %s is a device" % (vmdk, path))
        full = path if os.path.isabs(path) else os.path.join(os.path.dirname(os.path.abspath(vmdk)), path)
        pp = _regular_below(full, root)
        probs += pp
        if not pp and n * C.SECTOR != os.path.getsize(full):
            probs.append("%s: extent %d sectors != file size %d of %s" % (vmdk, n, os.path.getsize(full), full))
    return probs


def guard_vmx(vmx, root=C.TEST_ROOT):
    """The full pre-start guard (7.1). Returns a list of problems; empty = may start."""
    probs = []
    if not C.is_below(vmx, root):
        return ["vmx %s is not below %s" % (vmx, root)]
    probs += _regular_below(vmx, root)
    if probs:
        return probs
    with open(vmx, "r", encoding="utf-8", errors="replace") as f:
        kv, probs = parse_vmx(f.read())
    low = {k.lower(): v for k, v in kv.items()}
    for k, want in FIXED_KEYS.items():
        if low.get(k.lower()) != want:
            probs.append('%s must be "%s", is %r' % (k, want, low.get(k.lower())))
    for k in low:
        if k.startswith("usb.autoconnect.device"):
            probs.append("forbidden key " + k)
        if re.match(r"^usb(_xhci)?:\d+\.", k) or k.startswith("ehci:"):
            probs.append("USB device key present: " + k)
        if re.match(r"^ethernet\d+\.present$", k) and low[k] != "FALSE":
            probs.append(k + " must be FALSE")
    try:
        if int(low.get("memsize", "0")) > 2048 or int(low.get("memsize", "0")) <= 0:
            probs.append("memSize must be 1..2048, is %s" % low.get("memsize"))
        if int(low.get("numvcpus", "0")) > 2 or int(low.get("numvcpus", "0")) <= 0:
            probs.append("numvcpus must be 1..2, is %s" % low.get("numvcpus"))
    except ValueError:
        probs.append("memSize/numvcpus not integers")
    nv = low.get("nvram", "")
    if not nv or "\\" in nv or "/" in nv or ":" in nv:
        probs.append("nvram must be a plain file name in the VM folder, is %r" % nv)
    for k, val in kv.items():
        probs += _value_problems(k, val)
        lk = k.lower()
        if lk.endswith(".devicetype") and val not in ("cdrom-image", "disk"):
            probs.append("%s = %s (only cdrom-image or disk allowed)" % (k, val))
        if lk.endswith(".filename") and lk != "serial0.filename":
            full = val if os.path.isabs(val) else os.path.join(os.path.dirname(os.path.abspath(vmx)), val)
            dev = low.get(lk[:-len(".filename")] + ".devicetype")
            if dev is None:
                probs.append("%s has no deviceType" % k)
            pp = _regular_below(full, root)
            probs += pp
            if not pp and dev == "disk":
                if not full.lower().endswith(".vmdk"):
                    probs.append("%s: a disk must be a .vmdk descriptor: %s" % (k, full))
                else:
                    probs += guard_vmdk(full, root)
            if not pp and dev == "cdrom-image" and not full.lower().endswith(".iso"):
                probs.append("%s: a CD must be an .iso file: %s" % (k, full))
    for k in low:
        if re.match(r"^(ide|scsi|nvme|sata)\d+:\d+\.present$", k) and low[k] == "TRUE":
            base = k[:-len(".present")]
            if base + ".filename" not in low:
                probs.append("%s present without a fileName" % base)
    return probs


def write_vmx(text, path=C.VMX):
    C.assert_test_path(path)
    C.mkdir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


# ------------------------------------------------------------------------------------------------ vmrun
class Vmrun:
    def __init__(self, exe=C.VMRUN):
        self.exe = exe

    def _call(self, args, timeout=180):
        p = subprocess.run([self.exe, "-T", "ws"] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
        return p.returncode, p.stdout.decode(errors="replace")

    @staticmethod
    def _assert(vmx):
        if not C.norm(vmx).startswith(os.path.normcase(C.TEST_ROOT + "\\")):
            raise C.HarnessError("vmrun refused: %s is not below %s" % (vmx, C.TEST_ROOT))

    def running(self):
        rc, out = self._call(["list"], timeout=60)
        if rc:
            raise C.HarnessError("vmrun list failed: " + out)
        return [ln.strip() for ln in out.splitlines()[1:] if ln.strip()]

    def is_running(self, vmx):
        return any(C.norm(p) == C.norm(vmx) for p in self.running())

    def start(self, vmx):
        self._assert(vmx)
        probs = guard_vmx(vmx)
        if probs:
            raise C.HarnessError("guard: " + "; ".join(probs))
        others = [p for p in self.running() if C.is_below(p, C.TEST_ROOT)]
        if others:
            raise C.HarnessError("another test VM is running: %s" % others)
        rc, out = self._call(["start", vmx, "nogui"], timeout=300)
        if rc:
            raise C.HarnessError("vmrun start failed: " + out)

    def stop_hard(self, vmx):
        self._assert(vmx)
        if not self.is_running(vmx):
            return "not running"
        rc, out = self._call(["stop", vmx, "hard"], timeout=180)
        return out.strip() or ("ok" if rc == 0 else "rc %d" % rc)

    def wait_off(self, vmx, timeout):
        self._assert(vmx)
        end = time.time() + timeout
        while time.time() < end:
            if not self.is_running(vmx):
                return True
            time.sleep(2)
        return False


# ------------------------------------------------------------------------------------------------ transports
class PipeTransport:
    """Client end of the VM's serial pipe (VMware is the server). Non-blocking reads via PeekNamedPipe."""

    def __init__(self, path=C.PIPE, open_timeout=60):
        import ctypes
        import msvcrt
        self._ct, self._ms = ctypes, msvcrt
        if path != C.PIPE:
            raise C.HarnessError("refusing pipe %s (only %s)" % (path, C.PIPE))
        end = time.time() + open_timeout
        while True:
            try:
                self.f = open(path, "r+b", buffering=0)
                break
            except OSError:
                if time.time() > end:
                    raise C.HarnessError("cannot open %s (VM not started?)" % path)
                time.sleep(0.25)
        self.closed = False

    def read_available(self):
        if self.closed:
            return None
        ct = self._ct
        n = ct.c_ulong(0)
        h = ct.c_void_p(self._ms.get_osfhandle(self.f.fileno()))
        if not ct.windll.kernel32.PeekNamedPipe(h, None, 0, None, ct.byref(n), None):
            self.closed = True
            return None
        return self.f.read(n.value) if n.value else b""

    def write(self, data):
        for i in range(0, len(data), 1024):
            self.f.write(data[i:i + 1024])

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.f.close()
            except OSError:
                pass


class ScriptTransport:
    """Offline transport for unit tests: `script` is a list of (expect_written_regex|None, bytes_to_deliver)."""

    def __init__(self, script):
        self.script = list(script)
        self.written = b""
        self.closed = False

    def read_available(self):
        if not self.script:
            return None if self.closed else b""
        want, data = self.script[0]
        if want is None or re.search(want, self.written.decode(errors="replace")):
            self.script.pop(0)
            return data
        return b""

    def write(self, data):
        self.written += data

    def close(self):
        self.closed = True


# ------------------------------------------------------------------------------------------------ console driver
class KillSwitch(Exception):
    pass


class ConsoleClosed(Exception):
    pass


class Timeout(Exception):
    pass


class UnexpectedPrompt(Exception):
    def __init__(self, pid, wanted):
        super().__init__("unexpected prompt %s (waiting for %s)" % (pid, wanted))
        self.pid = pid


_ANSI = re.compile(r"\x1b(\[[0-9;?]*[ -/]*[@-~]|[()][0-9A-Za-z]|[=>78DEHM]|\][^\x07]*\x07)")


def strip_ansi(s):
    return _ANSI.sub("", s)


class Console:
    """Expect-style driver. `allowed_sizes`: the sizes (bytes) of the disks this session attached; any @@GB DISK with
    tran=usb or another size triggers `on_kill` (vmrun stop hard) and raises KillSwitch."""

    def __init__(self, transport, transcript_path=None, allowed_sizes=(), on_kill=None, clock=time.time,
                 sleep=time.sleep):
        self.t = transport
        self.raw = bytearray()
        self.lines = []                 # complete lines (str, \r stripped)
        self.partial = ""
        self.markers = []               # (line index, tag, kv)
        self.allowed_sizes = set(int(s) for s in allowed_sizes)
        self.on_kill = on_kill
        self.failures = []              # harness-detected protocol failures (journal.ack, gptbackup.part, ...)
        self.clock, self.sleep = clock, sleep
        self.tr = open(transcript_path, "ab") if transcript_path else None
        self.killed = None
        self.answered = set()           # PROMPT marker indices already answered

    # -- input side
    def _ingest(self, data):
        self.raw += data
        if self.tr:
            self.tr.write(data)
            self.tr.flush()
        text = self.partial + data.decode("utf-8", errors="replace")
        parts = text.split("\n")
        self.partial = parts.pop()
        for ln in parts:
            ln = ln.rstrip("\r")
            self.lines.append(ln)
            m = C.parse_marker(ln)
            if m is None and "@@GB " in ln:
                # GRUB leaves its menu screen (escape sequences, no final newline) on the serial line, so the first
                # line the installer prints can follow that residue. Accept a marker after terminal residue only
                # (the prefix must contain an ESC); any other mid-line "@@GB " is not a marker (5.2: column 0).
                pre = ln[:ln.index("@@GB ")]
                if "\x1b" in pre:
                    m = C.parse_marker(ln[len(pre):])
            if m:
                self.markers.append((len(self.lines) - 1, m[0], m[1]))
                self._watch(m[0], m[1])

    def _watch(self, tag, kv):
        if tag != "DISK" or self.killed:
            return
        why = None
        if kv.get("tran") == "usb":
            why = "unexpected disk: tran=usb (%s)" % kv.get("kname")
        else:
            try:
                size = int(kv.get("size", "-1"))
            except ValueError:
                size = -1
            if size not in self.allowed_sizes:
                why = "unexpected disk: %s size=%s not attached by this test" % (kv.get("kname"), kv.get("size"))
        if why:
            self.killed = why
            if self.on_kill:
                self.on_kill(why)
            raise KillSwitch(why)

    def pump(self):
        d = self.t.read_available()
        if d is None:
            raise ConsoleClosed("serial console closed")
        if d:
            self._ingest(d)
        return len(d)

    def wait(self, pred, timeout, what):
        """Poll until pred() is truthy; returns its value."""
        end = self.clock() + timeout
        while True:
            r = pred()
            if r:
                return r
            if self.clock() > end:
                raise Timeout("timeout after %ss waiting for %s" % (timeout, what))
            if not self.pump():
                self.sleep(0.05)

    # -- output side
    def send(self, text):
        self.t.write(text.encode("utf-8"))

    def reply(self, text):
        """A prompt reply (5.2): only after the whole @@GB PROMPT line was received; CR-terminated."""
        self.send(text + "\r")

    # -- marker helpers
    def mark(self):
        return len(self.markers)

    def since(self, mark, tag=None, **match):
        return [(t, kv) for _, t, kv in self.markers[mark:]
                if (tag is None or t == tag) and all(kv.get(k) == v for k, v in match.items())]

    def wait_marker(self, tag, timeout, mark=None, **match):
        start = self.mark() if mark is None else mark

        def p():
            r = self.since(start, tag, **match)
            return r[0] if r else None
        return self.wait(p, timeout, "@@GB %s %s" % (tag, match))

    def text_since_line(self, line_idx):
        return "\n".join(self.lines[line_idx:]) + "\n" + self.partial

    def wait_text(self, needle, timeout, from_byte=0, ansi=False):
        def p():
            s = bytes(self.raw[from_byte:]).decode("utf-8", errors="replace")
            if ansi:
                s = strip_ansi(s)
            return needle in s
        return self.wait(p, timeout, repr(needle))

    def _unanswered(self):
        """Index of the last @@GB PROMPT marker if it was not answered yet, else None."""
        for i in range(len(self.markers) - 1, -1, -1):
            if self.markers[i][1] == "PROMPT":
                return None if i in self.answered else i
        return None

    def at_prompt(self, pid):
        i = self._unanswered()
        return i is not None and self.markers[i][2].get("id") == pid

    def _auto_answer(self, idx, tolerate):
        """journal.ack -> 'b' + failure; gptbackup.part -> 's' + failure (7.1); tolerated prompts get their reply.
        Returns False if the prompt is none of these."""
        pid = self.markers[idx][2].get("id")
        tol = dict(tolerate)
        if pid == "journal.ack":
            self.failures.append("unexpected journal.ack prompt (answered b)")
            reply = "b"
        elif pid == "gptbackup.part":
            self.failures.append("unexpected gptbackup.part prompt (answered s)")
            reply = "s"
        elif pid in tol:
            reply = tol[pid]
        else:
            return False
        self.answered.add(idx)
        self.reply(reply)
        return True

    def expect_prompt(self, pid, timeout, mark=None, tolerate=()):
        """Wait for @@GB PROMPT id=<pid> (a pending unanswered prompt counts). Auto-answers journal.ack,
        gptbackup.part and `tolerate` ((id, reply) pairs); any other prompt -> UnexpectedPrompt.
        Returns the marker index of the prompt (the caller answers it)."""
        if mark is None:
            u = self._unanswered()
            mark = u if u is not None else self.mark()
        end = self.clock() + timeout
        start = mark
        while True:
            idx = self._prompt_index(start, max(1, end - self.clock()), pid)
            start = idx + 1
            if idx in self.answered:
                continue
            if self.markers[idx][2].get("id") == pid:
                return idx
            if not self._auto_answer(idx, tolerate):
                raise UnexpectedPrompt(self.markers[idx][2].get("id"), pid)

    def _prompt_index(self, start, timeout, pid):
        def p():
            for i in range(start, len(self.markers)):
                if self.markers[i][1] == "PROMPT":
                    return i + 1
            return None
        return self.wait(p, timeout, "@@GB PROMPT id=%s" % pid) - 1

    def ask(self, pid, reply, timeout=120, mark=None, tolerate=()):
        """Wait for prompt pid, then send reply. Returns the marker count at the time of the reply."""
        idx = self.expect_prompt(pid, timeout, mark=mark, tolerate=tolerate)
        self.answered.add(idx)
        m = self.mark()
        self.reply(reply)
        return m

    def disks_since(self, mark):
        return [kv for t, kv in self.since(mark, "DISK")]

    def pick_disk(self, pid, mark, kname=None, size=None, timeout=300):
        """Answer a disk prompt with the n of the disk listed after `mark` that matches kname/size."""
        idx = self.expect_prompt(pid, timeout)
        self.answered.add(idx)
        cands = [kv for kv in self.disks_since(mark)
                 if (kname is None or kv.get("kname") == kname) and (size is None or kv.get("size") == str(size))]
        if len(cands) != 1:
            self.reply("b")
            raise C.HarnessError("prompt %s: %d disks match kname=%s size=%s (listed: %s)" % (
                pid, len(cands), kname, size, [(d.get("kname"), d.get("size")) for d in self.disks_since(mark)]))
        m = self.mark()
        self.reply(cands[0]["n"])
        return cands[0], m

    def wait_result(self, flow, timeout, mark, tolerate=()):
        """The flow's @@GB RESULT kv, then answers its 'continue' prompt. A prompt before the RESULT is auto-answered
        (journal.ack, gptbackup.part, `tolerate`) or raises UnexpectedPrompt."""
        end = self.clock() + timeout

        def p():
            r = self.since(mark, "RESULT", flow=flow)
            if r:
                return ("RESULT", r[0][1])
            u = self._unanswered()
            if u is not None and u >= mark and self.markers[u][2].get("id") != "continue":
                return ("PROMPT", u)
            return None
        while True:
            kind, val = self.wait(p, max(1, end - self.clock()), "@@GB RESULT flow=%s" % flow)
            if kind == "RESULT":
                break
            if not self._auto_answer(val, tolerate):
                raise UnexpectedPrompt(self.markers[val][2].get("id"), "RESULT flow=%s" % flow)
        self.ask("continue", "", timeout=120)
        return val

    # -- shell (tools s)
    def shell_enter(self, timeout=60):
        """From the main menu prompt: tools -> s; returns when the busybox prompt is shown."""
        self.ask("main", "4", timeout=timeout)
        m = self.ask("tools", "s", timeout=timeout)
        start = len(self.raw)
        self.wait_marker("SHELL", timeout, mark=m)
        self.wait_text("gbsh# ", timeout, from_byte=start)

    def sh(self, cmd, timeout=120):
        """Run one busybox sh command line; returns (exit status, output text). Begin/end markers are split by ""
        in the typed line so the tty/line-editor echo can never match the printed markers."""
        if "\n" in cmd or "\r" in cmd:
            raise C.HarnessError("shell command must be one line")
        tag = uuid.uuid4().hex[:10]
        beg, end = "__GBB%s__" % tag, "__GBE%s__" % tag
        line = 'echo __G""BB%s__; { %s ; } 2>&1; echo __G""BE%s__ $?' % (tag, cmd, tag)
        start = len(self.raw)
        self.send(line + "\n")
        rx = re.compile(re.escape(beg) + r"\r?\n(.*?)" + re.escape(end) + r" (\d+)\r?\n", re.S)
        endb = end.encode()

        def p():
            if endb not in self.raw[start:]:
                return None
            return rx.search(bytes(self.raw[start:]).decode("utf-8", errors="replace"))
        m = self.wait(p, timeout, "shell command " + cmd[:60])
        return int(m.group(2)), m.group(1).replace("\r", "")

    def sh_ok(self, cmd, timeout=120):
        st, out = self.sh(cmd, timeout)
        if st:
            raise C.HarnessError("guest command failed (%d): %s\n%s" % (st, cmd, out[-1500:]))
        return out

    def shell_exit(self, timeout=60):
        """exit the shell, then tools -> b: back at the main menu prompt."""
        m = self.mark()
        self.send("exit\n")
        self.ask("tools", "b", timeout=timeout, mark=m)
        self.expect_prompt("main", timeout)

    def pull_tree(self, guest_dir, local_dir, timeout=900):
        """tar + gzip + base64 of a guest directory below /run/gb -> extracted into local_dir (tarfile 'data'
        filter: nothing outside local_dir, no devices)."""
        if not re.match(r"^/run/gb/[A-Za-z0-9_./-]*$", guest_dir) or ".." in guest_dir:
            raise C.HarnessError("pull only below /run/gb: " + guest_dir)
        out = self.sh_ok("tar -cf - -C %s . | gzip -c | base64 -w 0; echo" % guest_dir, timeout)
        raw = gzip.decompress(base64.b64decode("".join(out.split())))
        C.mkdir(local_dir)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            tf.extractall(local_dir, filter="data")
        return sorted(os.listdir(local_dir))

    def efivars(self, timeout=300):
        """({name: raw bytes incl. the 4 attribute bytes} of every Boot* variable of the EFI global GUID, the
        guest's own `sha256sum` text)."""
        d = "/sys/firmware/efi/efivars"
        out = self.sh_ok('for f in %s/Boot*-%s; do [ -e "$f" ] && echo "${f##*/} $(base64 -w 0 "$f")"; done; true'
                         % (d, C.EFI_GLOBAL), timeout)
        vars_ = {}
        for ln in out.splitlines():
            ln = ln.strip()
            if ln:
                name, _, b64 = ln.partition(" ")
                vars_[name.split("-")[0]] = base64.b64decode(b64)
        sums = self.sh_ok("sha256sum %s/Boot*-%s" % (d, C.EFI_GLOBAL), timeout)
        return vars_, sums

    def power_off(self, timeout=60):
        """tools poweroff: waits (30 s) for the next unanswered main or tools prompt, then main -> 4 -> poweroff."""
        self.wait(lambda: self._unanswered() is not None, 30, "a menu prompt")
        if self.at_prompt("main"):
            self.ask("main", "4")
        m = self.ask("tools", "poweroff", timeout=30)
        try:
            self.wait_marker("POWEROFF", timeout, mark=m)
        except (ConsoleClosed, Timeout):
            pass

    def close(self):
        self.t.close()
        if self.tr:
            self.tr.close()
            self.tr = None
