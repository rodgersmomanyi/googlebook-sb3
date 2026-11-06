#!/usr/bin/env python3
"""analyze_gblog.py: map a Googlebook OS (lapis) native boot log folder to the Surface Book 3 hardware checklist.

Every native boot writes timed snapshots to the stick's EFI-SYSTEM partition (FAT32) under \\gblog\\
(boot<N>-t<T>.txt, T = 40, 90, 180, 420, 900 s; audio-b<N>-t<T>.txt from gb_audiodiag; the format is GB_BOOTLOG_SH in
C:\\GB-x86\\build_esp.py). This script reads one boot's snapshots and writes TRIAGE.md: every item of the
DEVICE-STATUS hardware checklist (plus the software items around it) as PASS, FAIL or UNKNOWN, each with the exact
log lines (file:line) it was decided on.

    python -I analyze_gblog.py <gblog folder | folder containing gblog\\ | one boot<N>-t<T>.txt> [options]

Read-only: it never writes into the gblog folder (the default output is TRIAGE.md in the current directory). No
third-party modules. Serial numbers, MAC addresses, e-mail addresses, SSIDs, account names and the boot partition
UUID are redacted from every line it prints. See README.md next to this file.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict

VERSION = "1.0"
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
K15_RELEASE = "6.18.21-android17-5-g3c2c2f588d3f-ab16302715"
K16_RELEASE = "6.18.21-android17-5-gace3ce479bb5-ab16302716"     # wave 8: k15 + LOW-3 (escaped comm) + SD-reader modules
EXPECT_KERNEL = K16_RELEASE          # the kernel the current images ship
KNOWN_KERNELS = {
    K16_RELEASE: "kernel build #16 (k16)",
    K15_RELEASE: "kernel build #15 (k15)",
    "6.18.21-android17-5-g9629ced3e50e-ab16302714": "kernel build #14 (k14)",
}
MAX_EV = 8          # evidence lines printed per item (the count of the rest is given)
MAX_LINE = 360      # evidence lines are cut to this many characters

SNAP_RE = re.compile(r"^boot(\d+)-t(\d+)\.txt$", re.I)
AUDIO_RE = re.compile(r"^audio-b(\d+)-t(\d+)\.txt$", re.I)
KTS_RE = re.compile(r"^(?:<\d+>)?\[\s*(\d+\.\d+)\]")                       # a kernel log line ([  12.345678] ...)
LOGCAT_RE = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d+\s+\d+\s+\d+\s+[VDIWEFS]\s")   # logcat -v threadtime
PROP_RE = re.compile(r"^\[([^\]]+)\]: \[(.*)\]$")
UPTIME_RE = re.compile(r"^== uptime (\S+)(?: \S+)? boot_completed=(\S*) hw=(\S*)")

# Top-level headers of a snapshot, in the order gb_bootlog writes them. "report" repeats (one per
# /data/vendor/gb/report/<kind>-<N>.txt); inside a report, "== pci" / "== asound" are report sub-sections.
TOP = [
    ("summary", lambda s: s.startswith("== summary")),
    ("uptime", lambda s: s.startswith("== uptime")),
    ("cmdline", lambda s: s == "== cmdline"),
    ("gbinit", lambda s: s == "== gbinit module loading"),
    ("dmi", lambda s: s == "== dmi"),
    ("block", lambda s: s == "== block"),
    ("input", lambda s: s == "== input devices"),
    ("iio", lambda s: s == "== iio"),
    ("asound", lambda s: s == "== asound"),
    ("gpu", lambda s: s == "== gpu"),
    ("modules", lambda s: s == "== modules"),
    ("drm_power", lambda s: s == "== drm/input/power"),
    ("pci", lambda s: s == "== pci"),
    ("getprop", lambda s: s == "== getprop"),
    ("dgpu", lambda s: s == "== dgpu"),
    ("report", lambda s: s.startswith("== /data/vendor/gb/report/")),
    ("dmesg_live", lambda s: s == "== dmesg-live (tail)"),
    ("dmesg_live_prev", lambda s: s == "== dmesg-live.prev (tail)"),
    ("nouveau_log", lambda s: s == "== gb_nouveau.log (tail)"),
    ("dmesg", lambda s: s == "== dmesg"),
    ("logcat", lambda s: s == "== logcat"),
    ("sf", lambda s: s == "== dumpsys SurfaceFlinger"),
    ("dumpsys_input", lambda s: s == "== dumpsys input"),
]
TOP_INDEX = {name: i for i, (name, _) in enumerate(TOP)}
# Sections whose kernel/logcat lines belong to an EARLIER boot: never evidence for this one.
PREV_BOOT_SECTIONS = {"dmesg_live_prev"}
# (wave 9 hw-native1) gb_nouveau's live kernel log (/data/vendor/gb/report/dmesg-live.txt) is rotated into .prev only
# some time after boot_completed: an early snapshot's "== dmesg-live (tail)" can still be the PREVIOUS boot's log (first
# native boot, boot 3 at t40: lines up to 486 s, the shutdown of boot 2, in a snapshot taken at 48 s, so boot 2's
# GB_SENSORS line looked like boot 3's). Such a section is skipped when it has a kernel timestamp past the newest one of
# the same snapshot's "== dmesg" dump, which is taken after it (+ LIVE_SLACK_S; without that dump: the snapshot's uptime
# + LIVE_NODMESG_S, as the snapshot takes up to ~5 s between its uptime line and the live log): a log of this boot cannot
# be ahead of the kernel that wrote the snapshot.
LIVE_SECTIONS = {"dmesg_live"}
LIVE_SLACK_S = 2.0
LIVE_NODMESG_S = 60.0


def live_limit(sn):
    """(wave 9 hw-native1) The newest kernel timestamp a dmesg-live section of THIS boot can have in snapshot sn."""
    ts = [float(m.group(1)) for _ln, s in sn.lines("dmesg") for m in [KTS_RE.match(s)] if m]
    if ts:
        return max(ts) + LIVE_SLACK_S
    return sn.uptime + LIVE_NODMESG_S if sn.uptime is not None else None

SUMMARY_KEYS = ("serial 8250:", "serial errors:", "serial surface_serial_hub bound:", "inputs (Surface Aggregator HID",
                "audio cards:", "audio DRAS:", "firmware i915 DMC:", "swaps:", "pci functions without a driver:",
                "i915 white veil:", "i915 modules.load:", "HWUI Vulkan queueCount abort lines:", "gb_slotsync HOLD:",
                "apgetty:", "fs-verity:",
                # (wave 5) the lines GB_BOOTLOG_SH added (logs of older images lack them)
                "kernel log:", "deferred probes", "buttons:", "posture:", "posture override:", "audio dsp_driver=",
                "i915 PSR/underrun lines:", "wifi:", "bluetooth:", "camera:", "linker:",
                # (wave 6, OTA gate F2) gbinit's verdict from /odm/etc/gb/ota_state
                "ota gate:",
                # (wave 9 hw-native1) the Surface Aggregator after gb_hw's re-bind, the Integrated Sensor Hub
                "sam: MSHW0084", "ish: ")
# (wave 5, native-readiness A-L1) -517 = -EPROBE_DEFER: a probe deferred and retried later, never an error by itself
# (on the SB3 surface_hotplug's first probe, before lapis's pinctrl-icelake, logs "failed to set up IRQ 0: -517")
DEFERRED_RE = re.compile(r"-517(?!\d)|EPROBE_DEFER")


def not_deferred(hits):
    """The (Ev, match) pairs of hits without the deferred-probe (-517) lines."""
    return [h for h in hits if not DEFERRED_RE.search(h[0].text)]

# ---------------------------------------------------------------------------------------------------- privacy
_REDACT = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])"), "<mac>"),
    (re.compile(r"(?i)(?<![a-z0-9])(serial_?no|serial_?number|serial-number|serialnumber)(\]?\s*[=:]\s*\[?)"
                r"[^\s\],;]+"),
     r"\1\2<redacted>"),
    (re.compile(r"(?i)(?<![a-z0-9])(b?ssid)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"), r"\1\2<redacted>"),
    (re.compile(r"(androidboot\.boot_part_uuid=)\S+"), r"\1<redacted>"),
    (re.compile(r"(?i)(account\s*\{\s*name=)[^,}\s]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(?<![a-z0-9])(imei|meid|iccid|imsi)(\s*[=:]\s*)\S+"), r"\1\2<redacted>"),
    (re.compile(r"\b(Uniq=)\S+"), r"\1<redacted>"),
]


def redact(text):
    """The line with personal data replaced (serials, MACs, e-mails, SSIDs, accounts, the boot partition UUID)."""
    for rx, rep in _REDACT:
        text = rx.sub(rep, text)
    return text


# ---------------------------------------------------------------------------------------------------- parsing
class Ev(tuple):
    """One evidence line: (file name, 1-based line number, text)."""
    __slots__ = ()

    def __new__(cls, f, ln, text):
        return tuple.__new__(cls, (f, ln, text))

    f = property(lambda self: self[0])
    ln = property(lambda self: self[1])
    text = property(lambda self: self[2])


class Snap:
    """One boot<N>-t<T>.txt snapshot split into its top-level sections."""

    def __init__(self, path, boot, t):
        self.path, self.name, self.boot, self.t = path, os.path.basename(path), boot, t
        self.sec = {}           # section name -> [(lineno, text)]
        self.hdr = {}           # section name -> (lineno, header text)
        self.reports = []       # [((lineno, header), [(lineno, text)])]
        self.uptime = None
        self.boot_completed = ""
        self.hw = ""
        self._parse()

    def _parse(self):
        with open(self.path, "rb") as fh:
            data = fh.read()
        lines = data.decode("utf-8", "replace").split("\n")
        cur, name, body = -1, "preamble", []
        self.sec["preamble"] = body
        for i, raw in enumerate(lines, 1):
            s = raw.rstrip("\r")
            if s.startswith("== "):
                hit = None
                for j, (nm, test) in enumerate(TOP):
                    if cur >= 0 and TOP[cur][0] == "report":
                        if nm not in ("report", "dmesg_live"):
                            continue     # inside a report only the next report or the end of the reports counts
                    elif j <= cur:
                        continue
                    if test(s):
                        hit = j
                        break
                if hit is not None:
                    cur, name, body = hit, TOP[hit][0], []
                    if name == "report":
                        self.reports.append(((i, s), body))
                    else:
                        self.sec[name] = body
                        self.hdr[name] = (i, s)
                    if name == "uptime":
                        m = UPTIME_RE.match(s)
                        if m:
                            try:
                                self.uptime = float(m.group(1))
                            except ValueError:
                                self.uptime = None
                            self.boot_completed, self.hw = m.group(2), m.group(3)
                    continue
            body.append((i, s))

    def lines(self, name):
        return self.sec.get(name, [])

    def whole(self, name):
        """(review 2026-10-29) True if section name is in this file and another top-level section or a report follows
        it, i.e. the file was not cut short inside it (a power cut during the copy)."""
        if name not in self.hdr:
            return False
        h = self.hdr[name][0]
        return any(ln > h for ln, _s in self.hdr.values()) or any(ln > h for (ln, _s), _b in self.reports)

    def ev(self, ln, text):
        return Ev(self.name, ln, text)

    # -- summary helpers (wave 3 "== summary" block)
    def sline(self, prefix):
        for ln, s in self.lines("summary"):
            if s.startswith(prefix):
                return Ev(self.name, ln, s)
        return None

    def sblock(self, header):
        """The lines after the summary sub-header '-- <header>...' up to the next sub-header or key line."""
        out, on = [], False
        for ln, s in self.lines("summary"):
            if on:
                if s.startswith("-- ") or s.startswith(SUMMARY_KEYS):
                    break
                out.append(Ev(self.name, ln, s))
            elif s.startswith("-- " + header):
                on = True
        return out if on else None


class AudioDiag:
    """One audio-b<N>-t<T>.txt (gb_audiodiag) file: kept as lines."""

    def __init__(self, path, boot, t):
        self.path, self.name, self.boot, self.t = path, os.path.basename(path), boot, t
        with open(path, "rb") as fh:
            self.lines = [s.rstrip("\r") for s in fh.read().decode("utf-8", "replace").split("\n")]


def find_files(src):
    """(snapshots, audio files, folder) for a gblog folder, a folder holding gblog\\, or one snapshot file."""
    if os.path.isfile(src):
        d = os.path.dirname(os.path.abspath(src))
        names = [os.path.basename(src)]
    else:
        d = src
        if not any(SNAP_RE.match(n) for n in os.listdir(d)) and os.path.isdir(os.path.join(d, "gblog")):
            d = os.path.join(d, "gblog")
        names = sorted(os.listdir(d))
    snaps, audio = [], []
    for n in names:
        m = SNAP_RE.match(n)
        if m:
            snaps.append((int(m.group(1)), int(m.group(2)), os.path.join(d, n)))
            continue
        m = AUDIO_RE.match(n)
        if m:
            audio.append((int(m.group(1)), int(m.group(2)), os.path.join(d, n)))
    return snaps, audio, d


class Boot:
    """Every snapshot of one boot, with merged kernel-log and logcat corpora (each line once, first file wins)."""

    def __init__(self, n, snaps, audio):
        self.n = n
        self.snaps = sorted(snaps, key=lambda s: s.t)
        self.audio = sorted(audio, key=lambda a: a.t)
        # a power cut during the copy can leave the newest file empty or cut short: "latest" is the newest
        # snapshot that has its uptime header (falls back to the newest file)
        full = [s for s in self.snaps if "uptime" in s.hdr]
        self.latest = full[-1] if full else self.snaps[-1]
        self.earliest = self.snaps[0]
        self.dmesg, self.logcat = [], []
        seen_d, seen_l = set(), set()

        def take(f, ln, s):
            if KTS_RE.match(s):
                if s not in seen_d:
                    seen_d.add(s)
                    self.dmesg.append(Ev(f, ln, s))
            elif LOGCAT_RE.match(s):
                if s not in seen_l:
                    seen_l.add(s)
                    self.logcat.append(Ev(f, ln, s))

        self.stale_live = []        # (wave 9 hw-native1) [(file, newest timestamp, uptime)]: an earlier boot's dmesg-live
        for sn in self.snaps:
            for name, body in sn.sec.items():
                if name in PREV_BOOT_SECTIONS:
                    continue
                if name in LIVE_SECTIONS:
                    ts = [float(m.group(1)) for _ln, s in body for m in [KTS_RE.match(s)] if m]
                    lim = live_limit(sn)
                    if ts and lim is not None and max(ts) > lim:
                        self.stale_live.append((sn.name, max(ts), lim))
                        continue
                for ln, s in body:
                    take(sn.name, ln, s)
            for _hdr, body in sn.reports:
                for ln, s in body:
                    take(sn.name, ln, s)
        for a in self.audio:
            for i, s in enumerate(a.lines, 1):
                take(a.name, i, s)
        # the earliest kernel timestamp of any "== dmesg" section: < 2 s means the boot's start is in the log
        starts = []
        for sn in self.snaps:
            for _ln, s in sn.lines("dmesg"):
                m = KTS_RE.match(s)
                if m:
                    starts.append(float(m.group(1)))
                    break
        self.klog_start = min(starts) if starts else None
        self._rx = {}

    @property
    def klog_from_zero(self):
        return self.klog_start is not None and self.klog_start < 2.0

    def _c(self, pat, flags):
        k = (pat, flags)
        if k not in self._rx:
            self._rx[k] = re.compile(pat, flags)
        return self._rx[k]

    def dm(self, pat, flags=0):
        """[(Ev, match)] of kernel log lines matching pat (search)."""
        rx = self._c(pat, flags)
        return [(e, m) for e in self.dmesg for m in [rx.search(e.text)] if m]

    def lc(self, pat, flags=0):
        rx = self._c(pat, flags)
        return [(e, m) for e in self.logcat for m in [rx.search(e.text)] if m]

    # -- properties: getprop of the newest snapshot first, then the older ones; also the dgpu/report subsets
    def prop(self, key):
        for sn in reversed(self.snaps):
            for name in ("getprop", "dgpu"):
                for ln, s in sn.lines(name):
                    m = PROP_RE.match(s)
                    if m and m.group(1) == key:
                        return m.group(2), Ev(sn.name, ln, s)
            for ln, s in sn.lines("dgpu"):       # gb_dgpu_check "== policy properties": '    key=value'
                st = s.strip()
                if st.startswith(key + "="):
                    return st[len(key) + 1:], Ev(sn.name, ln, s)
        return None, None

    def prop_in(self, sn, key):
        for name in ("getprop", "dgpu"):
            for ln, s in sn.lines(name):
                m = PROP_RE.match(s)
                if m and m.group(1) == key:
                    return m.group(2), Ev(sn.name, ln, s)
        return None, None

    def newest_with(self, section):
        for sn in reversed(self.snaps):
            if sn.lines(section):
                return sn
        return None

    def newest_whole(self, section):
        """(review 2026-10-29) The newest snapshot whose section is whole (not cut short), else the newest that has
        the section at all: a newest file cut inside '== pci' must not hide an older snapshot's whole list."""
        for sn in reversed(self.snaps):
            if sn.lines(section) and sn.whole(section):
                return sn
        return self.newest_with(section)

    def summary_snaps(self):
        return [sn for sn in self.snaps if "summary" in sn.sec]

    def newest_sline(self, prefix):
        """(wave 5) The newest snapshot's summary line starting with prefix; None in logs without it."""
        for sn in reversed(self.summary_snaps()):
            e = sn.sline(prefix)
            if e:
                return e
        return None


# ------------------------------------------------------------------------------------------- structured views
def parse_inputs(sn):
    """[{name, bus, vendor, product, ev}] from '== input devices' (/proc/bus/input/devices); no Phys/Uniq kept."""
    out, cur = [], None
    for ln, s in sn.lines("input"):
        if s.startswith("I:"):
            cur = {"name": "", "bus": "", "vendor": "", "product": "", "ev": None}
            out.append(cur)
            m = re.search(r"Bus=(\w+) Vendor=(\w+) Product=(\w+)", s)
            if m:
                cur["bus"], cur["vendor"], cur["product"] = m.group(1).lower(), m.group(2).lower(), m.group(3).lower()
        elif s.startswith("N:") and cur is not None:
            m = re.match(r'N: Name="(.*)"\s*$', s)
            cur["name"] = m.group(1) if m else s[3:]
            cur["ev"] = Ev(sn.name, ln, s)
    return out


def parse_supplies(sn):
    """{name: {KEY: value, '_ev': [Ev]}} from the power_supply uevents in '== drm/input/power'."""
    out, cur = OrderedDict(), None
    for ln, s in sn.lines("drm_power"):
        m = re.match(r"^/sys/class/power_supply/([^/]+)/uevent$", s)
        if m:
            cur = {"_ev": []}
            out[m.group(1)] = cur
            continue
        if cur is not None:
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", s)
            if m:
                cur[m.group(1)] = m.group(2)
                cur["_ev"].append(Ev(sn.name, ln, s))
            else:
                cur = None
    return out


def parse_pci(sn):
    """{bdf: (vendor, device, driver, Ev)} from '== pci'."""
    out = OrderedDict()
    for ln, s in sn.lines("pci"):
        m = re.match(r"^/sys/bus/pci/devices/(\S+) (0x[0-9a-fA-F]+):(0x[0-9a-fA-F]+) driver=(\S*)\s*$", s)
        if m:
            drv = m.group(4)
            out[m.group(1)] = (m.group(2).lower(), m.group(3).lower(), "" if drv in (".", "") else drv,
                               Ev(sn.name, ln, s))
    return out


DGPU_LINE_RE = re.compile(r"^(\S+) (0x[0-9a-fA-F]+):(0x[0-9a-fA-F]+) class=(\S+) driver=(\S*) control=(\S*) "
                          r"runtime=(\S*) state=(\S*)")


def parse_dgpu(sn):
    """[{bdf, vendor, device, class, driver, control, runtime, state, ev}] of the '== dgpu' per-function lines."""
    out = []
    for ln, s in sn.lines("dgpu"):
        m = DGPU_LINE_RE.match(s)
        if m:
            drv = m.group(5)
            out.append({"bdf": m.group(1), "vendor": m.group(2).lower(), "device": m.group(3).lower(),
                        "class": m.group(4).lower(), "driver": "" if drv in (".", "") else drv,
                        "control": m.group(6), "runtime": m.group(7), "state": m.group(8), "ev": Ev(sn.name, ln, s)})
    return out


def parse_power_json(sn):
    """{cmd: (dict, Ev)} of the gb_power socket replies (hello, status, get-mode) in '== dgpu'."""
    out = {}
    for ln, s in sn.lines("dgpu"):
        st = s.strip()
        if st.startswith('{"v":'):
            try:
                d = json.loads(st)
            except ValueError:
                continue
            if isinstance(d, dict) and "cmd" in d:
                out[d["cmd"]] = (d, Ev(sn.name, ln, s))
    return out


def parse_gbinit(sn):
    """(ok list, {module: (reason, [Ev])}) from '== gbinit module loading'."""
    ok, failed, last_insmod = [], OrderedDict(), None
    for ln, s in sn.lines("gbinit"):
        e = Ev(sn.name, ln, s)
        if s.startswith("ok "):
            ok.append(s[3:].strip())
        elif s.startswith("insmod:"):
            last_insmod = e
        elif s.startswith("FAILED "):
            mod = s[7:].strip()
            reason, evs = "", [e]
            if last_insmod is not None and mod in last_insmod.text:
                reason = last_insmod.text.rsplit(":", 1)[-1].strip()
                evs = [last_insmod, e]
            failed[mod] = (reason, evs)
            last_insmod = None
    return ok, failed


def parse_modules(sn):
    return {s.split()[0]: Ev(sn.name, ln, s) for ln, s in sn.lines("modules") if s.strip()}


# ---------------------------------------------------------------------------------------------------- items
class Item:
    def __init__(self, iid, title, ref, kind):
        self.id, self.title, self.ref, self.kind = iid, title, ref, kind
        self.status, self.reason = UNKNOWN, ""
        self.ev, self.notes = [], []
        self.maxline = MAX_LINE

    def set(self, status, reason):
        self.status, self.reason = status, reason
        return self

    def add(self, evs):
        for e in evs:
            if isinstance(e, tuple) and len(e) == 2 and not isinstance(e, Ev):
                e = e[0]                    # (Ev, match) pairs from Boot.dm/lc
            if e is not None and e not in self.ev:
                self.ev.append(e)
        return self

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)
        return self


# (wave 10 misc-suspend) signals that end a process by a crash (SIGILL, SIGTRAP, SIGABRT, SIGBUS, SIGFPE, SIGSEGV,
# SIGSYS); init's own stop of a service is SIGTERM/SIGKILL (15/9) and is not one
CRASH_SIGNALS = {"4", "5", "6", "7", "8", "11", "31"}


class Analysis:
    def __init__(self, boot, env_override=None, expect_kernel=EXPECT_KERNEL):
        self.b = boot
        self.expect_kernel = expect_kernel
        sn = boot.newest_with("dmi")
        self.dmi = " / ".join(s.strip() for _ln, s in (sn.lines("dmi") if sn else []) if s.strip())
        self.dmi_ev = [sn.ev(ln, s) for ln, s in sn.lines("dmi") if s.strip()] if sn else []
        low = self.dmi.lower()
        if env_override:
            self.env = env_override
        elif "vmware" in low:
            self.env = "vmware"
        elif "surface book 3" in low:
            self.env = "surface"
        elif self.dmi:
            self.env = "other"
        else:
            self.env = "unknown"
        self.env_label = {"vmware": "VMware", "surface": "Surface Book 3", "other": "not a Surface Book 3",
                          "unknown": "platform unknown (no == dmi)"}[self.env]
        self.judge_hw = self.env in ("surface", "unknown")
        sc = boot.newest_with("cmdline")
        self.cmdline_ev = None
        if sc:
            self.cmdline_ev = next((Ev(sc.name, ln, s) for ln, s in sc.lines("cmdline") if s.strip()), None)
        if self.cmdline_ev is None:
            hits = boot.dm(r"Command line: (.*)$")
            self.cmdline_ev = hits[0][0] if hits else None
        txt = self.cmdline_ev.text if self.cmdline_ev else ""
        if "Command line: " in txt:
            txt = txt.split("Command line: ", 1)[1]
        self.cmdline = txt.split()
        # (review 2026-10-29) each list from the newest snapshot in which it is whole (another section follows it), so
        # a newest file cut short inside a list does not replace an older snapshot's whole one
        si = boot.newest_whole("input")
        self.inputs = parse_inputs(si) if si else []
        # True only when the input list is whole: a file cut short before or inside '== input devices' is no evidence
        # that an input device (keyboard, touchpad, gpio-keys) is missing
        self.inputs_whole = bool(si) and si.whole("input")
        ss = boot.newest_whole("drm_power")
        self.supplies = parse_supplies(ss) if ss else {}
        self.supplies_whole = bool(ss) and ss.whole("drm_power")
        sp = boot.newest_whole("pci")
        self.pci = parse_pci(sp) if sp else {}
        # (wave-5 integration 2026-10-29) True only when the '== pci' list is whole: a section follows it in the same
        # snapshot. A file cut short before or inside '== pci' is no evidence that a PCI function is missing
        self.pci_whole = bool(sp) and sp.whole("pci")
        sg = boot.newest_with("gbinit")
        self.gb_ok, self.gb_failed = parse_gbinit(sg) if sg else ([], {})
        sm = boot.newest_with("modules")
        self.modules = parse_modules(sm) if sm else {}
        self.power_json = parse_power_json(boot.newest_with("dgpu") or boot.latest)
        self.items = []
        self.base = self._base_state()
        self.crash_procs = {}                 # filled by the crash collector (also used by the display item)
        self._collect_crashes()

    # -- helpers
    def token(self, key):
        for t in self.cmdline:
            if t == key or t.startswith(key + "="):
                return t
        return None

    def mod_loaded(self, mod):
        """(True/False/None, [Ev]) for a module name like 'surface_aggregator' (gbinit 'ok x.ko' or /proc/modules)."""
        key = mod.replace("-", "_")
        evs = []
        if key in self.modules:
            evs.append(self.modules[key])
            return True, evs
        ko = mod + ".ko"
        if ko in self.gb_failed:
            return False, self.gb_failed[ko][1]
        if ko in self.gb_ok or mod.replace("_", "-") + ".ko" in self.gb_ok:
            sg = self.b.newest_with("gbinit")
            for ln, s in sg.lines("gbinit"):
                if s.strip() in ("ok " + ko, "ok " + mod.replace("_", "-") + ".ko"):
                    evs.append(sg.ev(ln, s))
            return True, evs
        return (None if not self.modules else False), evs

    def module_precondition(self, it, mods):
        """In a non-Surface environment a module that failed to insmod for another reason than ENODEV fails on the
        laptop too: FAIL. True if it set FAIL."""
        for m in mods:
            f = self.gb_failed.get(m if m.endswith(".ko") else m + ".ko")
            if f and "No such device" not in f[0]:
                it.add(f[1]).set(FAIL, f"gbinit could not load {m} ({f[0] or 'see the log'}): this fails on the "
                                       "laptop too")
                return True
        return False

    def not_here(self, it, extra=""):
        it.set(UNKNOWN, f"{self.env_label}: the Surface Book 3 hardware is not present, so this cannot be judged"
               + (f"; {extra}" if extra else ""))

    def _base_state(self):
        v, e = self.b.prop("vendor.gb.dtx.base")
        if v:
            return v, e
        gm = self.power_json.get("get-mode")
        if gm and gm[0].get("base") not in (None, "", "unknown"):
            return gm[0]["base"], gm[1]
        hits = self.b.dm(r"gb_power: DTX ready: base (\S+?),")
        if hits:
            return hits[-1][1].group(1), hits[-1][0]
        return "", None

    @property
    def base_detached(self):
        return self.base[0] == "detached"

    def input_named(self, rx):
        r = re.compile(rx, re.I)
        return [d for d in self.inputs if r.search(d["name"])]

    def _collect_crashes(self):
        b = self.b
        procs = OrderedDict()

        def add(p, e):
            p = os.path.basename(p.strip().split()[0]) if p.strip() else "?"
            procs.setdefault(p, []).append(e)

        for e, m in b.lc(r"F DEBUG\s*: Cmdline: (.+)$"):
            add(m.group(1), e)
        for e, m in b.lc(r"Fatal signal \d+ \((\w+)\).*pid \d+ \(([^)]+)\)"):
            add(m.group(2), e)
        for e, m in b.lc(r"FATAL EXCEPTION IN SYSTEM PROCESS"):
            add("system_server", e)
        for e, m in b.lc(r"AndroidRuntime: Process: ([\w.:]+), PID"):
            add(m.group(1), e)
        for e, m in b.lc(r"ANR in (\S+)"):
            add(m.group(1), e)
        # (wave 10 misc-suspend) the event-log records of the same events, which stay in the 6000-line logcat window
        # when the AndroidRuntime / ActivityManager text lines have scrolled out of it (am_crash: [user,pid,process,..];
        # am_anr: [user,pid,package,...]); a zygote child ended by a crash signal; the kernel's ANOM_ABEND audit record of a
        # core-dumping signal (the kernel log covers more time than the logcat window)
        for e, m in b.lc(r"\bam_(?:crash|anr)\s*: \[-?\d+,-?\d+,([^,\]]+),"):
            add(m.group(1), e)
        for e, m in b.lc(r"Zygote\s*: Process (\d+) exited due to signal (\d+)"):
            if m.group(2) in CRASH_SIGNALS:     # (review) 9 = killed by the ActivityManager or lmkd: a stop, not a crash
                add(f"app-pid-{m.group(1)}", e)
        for e, m in b.dm(r"type=1701 audit\([^)]*\):.*?comm=\"([^\"]*)\".*?\bsig=(\d+)"):
            add(m.group(1), e)
        loops = Counter()
        loop_ev = {}
        for e, m in b.dm(r"init: Service '([^']+)' \(pid \d+\) (?:exited with status (\d+)|received signal (\d+)|"
                         r"killed by signal (\d+))"):
            st = m.group(2)
            if st == "0":
                continue
            loops[m.group(1)] += 1
            loop_ev.setdefault(m.group(1), []).append(e)
            sig = m.group(3) or m.group(4)
            if sig in CRASH_SIGNALS:      # (wave 10 misc-suspend) one crash by a signal is a crash, not only a loop
                procs.setdefault(f"{m.group(1)} (init service, signal {sig})", []).append(e)
        for svc, c in loops.items():
            if c >= 3:
                procs.setdefault(f"{svc} (init service, {c} abnormal exits)", []).extend(loop_ev[svc])
        self.crash_procs = procs
        self.tombstones = b.lc(r"Tombstone written to: (\S+)")
        self.kernel_issues = b.dm(r"(Kernel panic - not syncing|kernel BUG at|BUG: (?:unable|kernel|scheduling|"
                                  r"sleeping|soft lockup|KASAN|KFENCE|Bad page|workqueue|spinlock)|Oops: |"
                                  r"WARNING: CPU: \d+ PID: \d+|general protection fault|Unable to handle kernel|"
                                  r"INFO: task \S+ blocked for more than|rcu: INFO: rcu_\w+ detected stall)")

    # ------------------------------------------------------------------------------------------- the checks
    def run(self):
        for fn in CHECKS:
            it = Item(*fn.meta)
            try:
                fn(self, it)
            except Exception as exc:          # a parser surprise must not hide the other items
                it.set(UNKNOWN, f"analyzer error: {type(exc).__name__}: {exc}")
            it.reason = redact(it.reason)
            it.notes = [redact(n) for n in it.notes]
            self.items.append(it)
        return self.items


CHECKS = []


def check(iid, title, ref, kind):
    def deco(fn):
        fn.meta = (iid, title, ref, kind)
        CHECKS.append(fn)
        return fn
    return deco


# ------------------------------------------------------------------------------------- platform and software
@check("kernel", "Kernel build #15 (k15)", "header; kernel/K15.md", "SW")
def c_kernel(a, it):
    b = a.b
    hits = b.dm(r"Linux version (\S+)")
    rel, e = (hits[0][1].group(1), hits[0][0]) if hits else (None, None)
    if not rel:
        for sn in reversed(b.snaps):
            for (_h, body) in sn.reports:
                for ln, s in body:
                    if s.startswith("uname_r="):
                        rel, e = s[len("uname_r="):].strip(), sn.ev(ln, s)
                        break
                if rel:
                    break
            if rel:
                break
    if not rel:
        return it.set(UNKNOWN, "no 'Linux version' line (kernel log wrapped or missing) and no gb_dgpu_report "
                               "uname_r")
    it.add([e])
    name = KNOWN_KERNELS.get(rel, "an unknown kernel build")
    if rel == a.expect_kernel:
        it.set(PASS, f"{rel} is {name}")
    else:
        it.set(FAIL, f"{rel} is {name}, expected {KNOWN_KERNELS.get(a.expect_kernel, a.expect_kernel)}")
    for sn in reversed(b.summary_snaps()):
        blk = sn.sblock("gb_vnni")
        if blk:
            if any("No such file" in x.text for x in blk):
                it.note("/sys/kernel/gb_vnni/summary is missing (a node of k15 and later)")
            else:
                it.note("/sys/kernel/gb_vnni/summary is present (a node of k15 and later)")
            break


CMDLINE_REQUIRED = [
    ("8250.nr_uarts", lambda v: v.isdigit() and int(v) >= 1,
     "the Surface Aggregator UART never registers without it (row 12; images built before wave 3 dropped it)"),
    ("gb.hw", lambda v: v == "native", "native mode (/gbnative tree)"),
    ("snd_intel_dspcfg.dsp_driver", lambda v: v == "3", "SOF audio (row 39)"),
    ("reboot", lambda v: v == "pci", "reboot/shutdown on the Surface (row 47)"),
    ("module_blacklist", lambda v: "tpm" in v.split(",") and "spi_intel_pci" in v.split(","),
     "keeps the TPM and the SPI flash drivers away (rows 9-10)"),
    ("efi_pstore.pstore_disable", lambda v: v == "1", "no crash logs in the UEFI variable store"),
]


@check("cmdline", "Kernel command line (8250.nr_uarts, native tokens)", "rows 9-12, 39, 47; checklist 19", "SW")
def c_cmdline(a, it):
    if not a.cmdline:
        return it.set(UNKNOWN, "no == cmdline section and no 'Command line:' kernel line")
    it.add([a.cmdline_ev])
    it.maxline = 4096                       # the whole command line is the evidence
    bad = []
    for key, ok, why in CMDLINE_REQUIRED:
        t = a.token(key)
        v = t.split("=", 1)[1] if t and "=" in t else None
        if v is None or not ok(v):
            bad.append(f"{key}{'=' + v if v is not None else ' missing'} ({why})")
    t = a.token("8250.nr_uarts")
    if t and t != "8250.nr_uarts=4":
        it.note(f"{t}: the image ships 8250.nr_uarts=4")
    for key, what in (("gb.nouveau=0", "NVIDIA driver kill switch set for this boot"),
                      ("gb.vnni=0", "VNNI libz patch kill switch set for this boot"),
                      ("gb.overlay=1", "ESP bring-up config (gb\\gb.cfg) requested for this boot"),
                      ("gb.boot=usb", "booted from the USB stick")):
        if key in a.cmdline:
            it.note(f"{key}: {what}")
    if any(t.startswith("earlycon=efifb") for t in a.cmdline):    # B-6: FLASHING.md section 7, "Debug boot"
        lv = [t[len("loglevel="):] for t in a.cmdline if t.startswith("loglevel=")]
        eff = lv[-1] if lv else "?"
        full = "ignore_loglevel" in a.cmdline and not any(
            t.replace("-", "_") == "printk.ignore_loglevel=0" for t in a.cmdline)
        it.note("earlycon=efifb: debug boot (kernel messages drawn on the screen, so this boot is slower); "
                f"effective loglevel={eff}" + ("" if eff == "5" and not full else
                " - expected loglevel=5 and printk.ignore_loglevel=0 typed at the END of the line (press End "
                "in the boot editor first)"))
    if bad:
        return it.set(FAIL, "; ".join(bad))
    it.set(PASS, "8250.nr_uarts, gb.hw=native, the SOF, reboot, blacklist and pstore tokens are all present")


@check("gbinit", "gbinit module loading", "rows 12-41 (modules.load)", "SW")
def c_gbinit(a, it):
    sg = a.b.newest_with("gbinit")
    if not sg:
        return it.set(UNKNOWN, "no == gbinit module loading section")
    if not a.gb_failed:
        return it.set(PASS, f"{len(a.gb_ok)} modules loaded, none failed")
    for _m, (_r, evs) in a.gb_failed.items():
        it.add(evs)
    enodev = [m for m, (r, _e) in a.gb_failed.items() if "No such device" in r]
    other = [m for m in a.gb_failed if m not in enodev]
    if not a.judge_hw and not other:
        it.note("ENODEV = the module refused hardware that is not here (surface_gpe checks the Surface DMI)")
        return it.set(PASS, f"{len(a.gb_ok)} modules loaded; failed only with 'No such device' "
                            f"({', '.join(enodev)}), expected in {a.env_label}")
    it.set(FAIL, f"{len(a.gb_ok)} loaded, {len(a.gb_failed)} failed: " +
           ", ".join(f"{m} ({a.gb_failed[m][0] or '?'})" for m in a.gb_failed))


# ----------------------------------------------------------------------------- serial and Surface Aggregator
@check("serial8250", "8250 serial driver: ports registered, no dw-apb-uart -22", "row 12; checklist 19", "SW")
def c_serial(a, it):
    b = a.b
    banner = b.dm(r"Serial: 8250/16550 driver, (\d+) ports")
    sum_banner = [sn.sline("serial 8250:") for sn in b.summary_snaps()]
    sum_banner = [e for e in sum_banner if e and "Serial: 8250/16550 driver" in e.text]
    err22 = b.dm(r"dw-apb-uart.*-22\b")
    sum_err = []
    for sn in b.summary_snaps():
        e = sn.sline("serial errors:")
        if e:
            m = re.search(r"dw-apb-uart -22: (\d+)", e.text)
            if m and int(m.group(1)) > 0:
                sum_err.append(e)
    # (wave 9 hw-native1) not the Surface Aggregator's own "surface_serial_hub serialN-M: ... failed to setup IRQ" (its
    # wakeup GPIO, judged by the ssam item): the 8250 port itself is fine then
    irq = [x for x in not_deferred(b.dm(r"failed to setup IRQ")) if re.search(r"serial|uart|8250|ttyS", x[0].text,
                                                                         re.I) and "surface_serial_hub" not in x[0].text]
    it.add(banner[:1] or sum_banner[:1]).add(err22).add(sum_err).add(irq)
    ports = []
    for sn in reversed(b.summary_snaps()):
        blk = sn.sblock("/proc/tty/driver/serial")
        if blk:
            ports = [e for e in blk if re.match(r"^\d+: uart:", e.text)]
            break
    if err22 or sum_err:
        return it.set(FAIL, "dw-apb-uart failed with -22: the 8250 core has no port for the Surface Aggregator UART "
                            "(8250.nr_uarts missing?)")
    if irq:
        return it.set(FAIL, "a serial port failed to set up its IRQ")
    n = None
    if banner:
        n = int(banner[0][1].group(1))
    elif sum_banner:
        m = re.search(r"driver, (\d+) ports", sum_banner[0].text)
        n = int(m.group(1)) if m else None
    if n is None:
        if b.klog_from_zero:
            return it.set(FAIL, "no 'Serial: 8250/16550 driver' line although the kernel log starts at boot: the 8250 "
                                "driver never registered (8250.nr_uarts missing from the command line?)")
        return it.set(UNKNOWN, "no 8250 banner, and the kernel log of every snapshot starts after boot (wrapped)")
    if n < 1:
        return it.set(FAIL, f"the 8250 driver registered {n} ports")
    if ports:
        it.add(ports[:4])
    if a.env == "surface":
        mmio = [e for e in ports if "mmio:" in e.text.lower() and "uart:unknown" not in e.text]
        if ports and not mmio:
            return it.set(FAIL, f"{n} ports, but /proc/tty/driver/serial lists no MMIO UART (the Surface "
                                "Aggregator's dw-apb-uart did not register)")
    it.set(PASS, f"Serial: 8250/16550 driver, {n} ports; dw-apb-uart -22: 0")


@check("ssam", "Surface Aggregator (surface_serial_hub bound, SAM firmware)", "rows 12, 18, 42; checklist 19", "HW")
def c_ssam(a, it):
    b = a.b
    bound = None
    for sn in reversed(b.summary_snaps()):
        e = sn.sline("serial surface_serial_hub bound:")
        if e:
            bound = e
            break
    fw = b.dm(r"(surface_serial_hub|surface_aggregator|ssam)\S*.*SAM firmware version")
    errs = [x for x in not_deferred(b.dm(r"(surface_serial_hub|surface_aggregator|ssam)\S*\s.*\b(fail(ed)?|error|"
                                         r"timed? ?out)\b", re.I)) if "avc:" not in x[0].text]
    # (wave 9 hw-native1) k16's first-stage probe fails before lapis's pinctrl-icelake registers INT3455 (the SAM wakeup
    # GPIO): "error -EINVAL: failed to setup IRQ" + the driver core's "-22"; gb_hw 'sam' binds it again at boot
    # ('gb_hw: sam:' line, vendor.gb.sam, the summary's 'sam: MSHW0084' line). Those two lines are that known first
    # failure, not an error of the running Surface Aggregator.
    first = [x for x in errs if re.search(r"surface_serial_hub \S+: (error -EINVAL: failed to setup IRQ|probe with driver "
                                          r"surface_serial_hub failed with error -22)", x[0].text)]
    other = [x for x in errs if x not in first]
    gbs = b.dm(r"gb_hw: sam: (.*)$")
    vsam, vev = b.prop("vendor.gb.sam")
    ssum = b.newest_sline("sam: MSHW0084")
    loaded, mev = a.mod_loaded("surface_aggregator")
    it.add([bound] if bound else []).add(fw[:2]).add(first[:2]).add(other[:4]).add(mev[:1]).add(gbs[-1:]) \
        .add([vev] if vev else []).add([ssum] if ssum else [])
    val = bound.text.split("bound:", 1)[1].strip() if bound else None
    if not a.judge_hw:
        if a.module_precondition(it, ["surface_aggregator"]):
            return
        return a.not_here(it, f"surface_aggregator {'loaded' if loaded else 'not loaded'}, "
                              f"surface_serial_hub bound: {val if val is not None else '?'}"
                              + (f", gb_hw sam: {vsam}" if vsam else ""))
    gbsam = f"gb_hw sam: {vsam}" if vsam else (f"gb_hw: sam: {gbs[-1][1].group(1)[:160]}" if gbs else "")
    if first:
        it.note("the first-stage probe failed with -EINVAL ('failed to setup IRQ'): k16's ssam_irq_setup() does not "
                "defer while lapis's pinctrl-icelake has not registered INT3455 yet (kernel patch 0114, backlog); "
                + ("gb_hw re-binds it at boot" if (vsam or gbs) else
                   "no gb_hw sam result: an image before wave 9 has no re-bind"))
    # (wave 9 hw-native1) "serial surface_serial_hub bound: none" is not bound, even with a SAM firmware line (the first
    # native boot: the firmware version is read before the IRQ setup fails)
    if val != "none" and (val or fw):
        if other:
            return it.set(FAIL, "bound, but the Surface Aggregator logged errors")
        if first and val is None and not gbs and not vsam:
            return it.set(FAIL, "the first-stage probe failed with -EINVAL ('failed to setup IRQ') and no summary line "
                                "says it was bound later")
        return it.set(PASS, f"surface_serial_hub bound: {val or '(SAM firmware version logged)'}"
                      + (f"; {gbsam}" if gbsam else ""))
    hint = []
    if first:
        hint.append("its first-stage probe failed with -EINVAL, 'failed to setup IRQ'; "
                    + (gbsam if gbsam else "no gb_hw sam re-bind: an image before wave 9"))
    elif gbsam:
        hint.append(gbsam)
    if not a.token("8250.nr_uarts"):
        hint.append("8250.nr_uarts is missing from the command line")
        it.add([a.cmdline_ev] if a.cmdline_ev else [])
    e22 = b.dm(r"dw-apb-uart.*-22\b")
    if e22:
        hint.append("dw-apb-uart failed with -22")
        it.add(e22[:1])
    if loaded is False:
        hint.append("surface_aggregator.ko is not loaded")
    if val == "none" or (val is None and b.klog_from_zero):
        return it.set(FAIL, "surface_serial_hub is not bound: no base keyboard, touchpad, batteries, charger or "
                            "detach" + (f" ({'; '.join(hint)})" if hint else ""))
    it.set(UNKNOWN, "no summary line and no SAM firmware line (older log format or a wrapped kernel log)")


SAM_SUPPLIES = ("BAT1", "BAT2", "ADP1")


@check("ssam_registry", "Surface Aggregator registry and hub (client devices)", "rows 18, 19, 25, 42", "HW")
def c_registry(a, it):
    mods = {}
    for m in ("surface_aggregator_registry", "surface_aggregator_hub"):
        loaded, ev = a.mod_loaded(m)
        mods[m] = loaded
        it.add(ev[:1])
    clients = [d for d in a.inputs if re.match(r"Microsoft Surface 045E:", d["name"], re.I)]
    it.add([d["ev"] for d in clients][:4])
    sups = [n for n in a.supplies if n in SAM_SUPPLIES]
    for n in sups:
        it.add(a.supplies[n]["_ev"][:1])
    dtx = a.b.dm(r"gb_power: DTX ready:")
    it.add(dtx[:1])
    n_clients = len(clients) + len(sups) + (1 if dtx else 0)
    if any(v is False for v in mods.values()):
        missing = [m for m, v in mods.items() if v is False]
        return it.set(FAIL, f"not loaded: {', '.join(missing)}")
    if not a.judge_hw:
        return a.not_here(it, "registry and hub modules loaded, no SAM client device (expected without the "
                              "Surface)")
    if n_clients:
        return it.set(PASS, f"{n_clients} SAM client device(s): " +
                      ", ".join([d["name"] for d in clients][:4] + sups + (["DTX"] if dtx else [])))
    it.set(FAIL, "the registry and hub are loaded but no SAM client device exists (HID, batteries, charger, DTX)")


def _input_item(a, it, rx, label, gamepad_check=False):
    devs = a.input_named(rx)
    it.add([d["ev"] for d in devs])
    for sn in reversed(a.b.summary_snaps()):
        e = sn.sline("inputs (Surface Aggregator HID")
        if e:
            it.add([e])
            break
    if not a.judge_hw:
        return a.not_here(it, f"no {label} input device (expected without the Surface)")
    if not devs:
        if a.base_detached:
            return it.set(UNKNOWN, f"the base is detached (vendor.gb.dtx.base=detached): no {label} expected")
        if not a.inputs_whole:                            # (review 2026-10-29) a file cut before/inside the list
            return it.set(UNKNOWN, "no whole '== input devices' list (snapshot cut short?): cannot tell whether "
                                   f"'{label}' is missing")
        return it.set(FAIL, f"no '{label}' input device (base attached or base state unknown): the SAM HID path "
                            "(surface_hid) did not create it")
    if gamepad_check:
        sn = a.b.newest_with("dumpsys_input")
        if sn:
            body = sn.lines("dumpsys_input")
            for i, (ln, s) in enumerate(body):
                if re.search(rx, s, re.I):
                    for ln2, s2 in body[i + 1:i + 16]:
                        if re.search(r"Classes:|Sources:", s2) and "GAMEPAD" in s2:
                            it.add([sn.ev(ln2, s2)])
                            return it.set(FAIL, f"{label} is classified as a GAMEPAD (the SAM Fn BTN_0 bug, row 18)")
    it.set(PASS, f"present: {', '.join(d['name'] for d in devs)}")


@check("keyboard", "Base keyboard HID 045E:09AE", "row 18; checklist 1", "HW")
def c_keyboard(a, it):
    loaded, ev = a.mod_loaded("hid-surface")
    if loaded is None:
        loaded, ev = a.mod_loaded("hid_surface")
    it.add(ev[:1])
    _input_item(a, it, r"045E:09AE Keyboard", "Microsoft Surface 045E:09AE Keyboard", gamepad_check=True)


@check("touchpad", "Base touchpad HID 045E:09AF", "row 19; checklist 2, 20", "HW")
def c_touchpad(a, it):
    _input_item(a, it, r"045E:09AF Touchpad", "Microsoft Surface 045E:09AF Touchpad")


# ------------------------------------------------------------------------------------------------- power
@check("batteries", "Batteries BAT1/BAT2 and charger ADP1 (gb_power combined battery)", "row 42", "HW")
def c_batteries(a, it):
    sups = a.supplies
    for n in SAM_SUPPLIES:
        if n in sups:
            it.add([e for e in sups[n]["_ev"] if re.match(r"POWER_SUPPLY_(NAME|STATUS|PRESENT|CAPACITY|ONLINE)=",
                                                          e.text)])
    gp = a.b.dm(r"gb_power: (battery \d+%|no battery present)")
    if gp:
        it.add([gp[-1][0]])
    if not a.judge_hw:
        if a.module_precondition(it, ["surface_battery", "surface_charger"]):
            return
        return a.not_here(it, "only test_power's supplies are present" if "test_battery" in sups else "")

    def present(n):
        return n in sups and sups[n].get("POWER_SUPPLY_PRESENT", "1") != "0"
    bad = []
    if not present("BAT1"):
        bad.append("BAT1 (clipboard) missing")
    if "ADP1" not in sups:
        bad.append("ADP1 (charger) missing")
    if not present("BAT2"):
        if a.base_detached:
            it.note("BAT2 (base battery) absent: the base is detached")
        else:
            bad.append("BAT2 (base) missing with the base attached or base state unknown")
    # (review 2026-10-29) a file cut before/inside '== drm/input/power' is no evidence that a supply is missing
    cut = bad and not a.supplies_whole
    if cut:
        bad = []
    if gp and "no battery present" in gp[-1][0].text:
        bad.append("gb_power sees no battery")
    if bad:
        return it.set(FAIL, "; ".join(bad) + " (surface_battery / surface_charger need the SAM link)")
    if cut:
        return it.set(UNKNOWN, "no whole '== drm/input/power' list (snapshot cut short?): cannot tell whether BAT1, "
                               "BAT2 and ADP1 are missing")
    it.set(PASS, "BAT1" + (", BAT2" if present("BAT2") else "") + " and ADP1 present")


@check("dtx", "Clipboard detach (surface_dtx + gb_power DTX)", "row 25; checklist 16", "HW")
def c_dtx(a, it):
    ready = a.b.dm(r"gb_power: DTX ready: base (\S+?), latch (\S+?), mode (\S+?);")
    errs = a.b.dm(r"gb_power: (DTX device gone|DTX device hung up|DTX EVENTS_ENABLE failed)")
    loaded, mev = a.mod_loaded("surface_dtx")
    props = [a.b.prop(k) for k in ("vendor.gb.dtx.base", "vendor.gb.dtx.mode", "vendor.gb.dtx.latch")]
    it.add(ready[:1]).add(errs[:3]).add([e for _v, e in props if e]).add(mev[:1])
    base = props[0][0]
    if not a.judge_hw:
        if a.module_precondition(it, ["surface_dtx"]):
            return
        return a.not_here(it, f"vendor.gb.dtx.base={base or '(empty)'}")
    if ready or base in ("attached", "detached", "not-feasible"):
        if errs and not ready:
            return it.set(FAIL, "the DTX device failed")
        return it.set(PASS, f"DTX ready; base {base or ready[0][1].group(1)}")
    if loaded is False:
        return it.set(FAIL, "surface_dtx is not loaded")
    # (review 2026-10-29) a snapshot cut short before getprop and dmesg: missing lines are no evidence
    if base is None and not a.b.newest_with("getprop") and not a.b.klog_from_zero:
        return it.set(UNKNOWN, "no getprop section and no kernel log from the boot (snapshot cut short?)")
    it.set(FAIL, "no 'gb_power: DTX ready' line and no vendor.gb.dtx.base value: /dev/surface/dtx not working")


# (readiness A-H1, fixprep platform) gb_hw.sh 'buttons' binds MSHW0040 to soc_button_array once pinctrl-icelake drives
# the INT3455 GPIO controller; result: 'gb_hw: buttons: ...', vendor.gb.buttons, and two "gpio-keys" input devices
@check("buttons", "Power button and volume keys (MSHW0040 -> soc_button_array -> gpio-keys)", "row 23; checklist 11",
       "HW")
def c_buttons(a, it):
    res = a.b.dm(r"gb_hw: buttons: (.*)$")
    diag = a.b.dm(r"gb_hw: diag: buttons MSHW0040:00 driver=(\S+) gpio-keys inputs=(\d+)")
    v, pe = a.b.prop("vendor.gb.buttons")
    keys = a.input_named(r"^gpio-keys$")
    # (review 2026-10-29) GB_BOOTLOG_SH's summary line (cd94022f), written at every snapshot from sysfs and
    # /proc/bus/input/devices: 'buttons: MSHW0040 <absent|bound to X|NOT bound>; INT3455 pinctrl <bound|not bound>;
    # gpio-keys inputs: N; <gb_hw's last 'gb_hw: buttons:' line, timestamp removed>'
    sb = a.b.newest_sline("buttons:")
    sm = re.match(r"buttons: MSHW0040 (.*?); INT3455 pinctrl (.*?); gpio-keys inputs: (\d+); ?(.*)$", sb.text) \
        if sb else None
    sres = re.sub(r"^gb_hw: buttons: ", "", sm.group(4)) if sm and sm.group(4).startswith("gb_hw: buttons: ") else ""
    it.add(res[-2:]).add(diag[-1:]).add([pe] if pe else []).add([d["ev"] for d in keys]).add([sb] if sb else [])
    if not a.judge_hw:
        if a.module_precondition(it, ["soc_button_array"]):
            return
        return a.not_here(it, f"vendor.gb.buttons={v}" if v else "")
    failed = [x[1].group(1) for x in res if "FAILED" in x[1].group(1)] + ([sres] if "FAILED" in sres else [])
    if keys:
        if len(keys) < 2:
            it.note(f"{len(keys)} gpio-keys input device: 2 are expected (power button; volume rocker)")
        return it.set(PASS, f"{len(keys)} gpio-keys input device(s)" + (f"; vendor.gb.buttons={v}" if v else ""))
    if sm and int(sm.group(3)) > 0 and not a.inputs_whole:    # no whole input list: the summary's own count
        if int(sm.group(3)) < 2:
            it.note(f"{sm.group(3)} gpio-keys input device: 2 are expected (power button; volume rocker)")
        return it.set(PASS, f"{sm.group(3)} gpio-keys input device(s) (summary, {sb.f}); MSHW0040 {sm.group(1)}")
    if (v or "").startswith("FAILED") or failed:
        return it.set(FAIL, "soc_button_array could not bind MSHW0040 (no power button, no volume keys): "
                            + (v if (v or "").startswith("FAILED") else failed[-1]))
    if v == "absent" or (sm and sm.group(1) == "absent"):
        return it.set(FAIL, "no MSHW0040:00 platform device (ACPI \\_SB.MSBT missing or disabled)")
    if not a.inputs_whole:                                # (review 2026-10-29) a file cut before/inside the list
        return it.set(UNKNOWN, "no whole '== input devices' list (snapshot cut short?): cannot tell whether the "
                               "gpio-keys inputs exist" + (f"; vendor.gb.buttons={v}" if v else ""))
    if not res and not v and not sres:
        return it.set(UNKNOWN, "no 'gb_hw: buttons:' line and no gpio-keys input device (an image without the "
                               "wave-5 buttons fix, or the log ends before 'on boot')")
    it.set(FAIL, "no gpio-keys input device" + (f" (vendor.gb.buttons={v})" if v else ""))


# (review 2026-10-29) GB_BOOTLOG_SH's (cd94022f) summary line, written at every snapshot from 'dumpsys device_state',
# 'dumpsys input' (EventHub 'Enabled: false') and /sys/class/input/*/inhibited, internal device names only:
#   posture: mCommittedState=OPEN mBaseState=OPEN mOverrideState=none | disabled by Android: <name>;+N other; |
#   inhibited by the kernel: <name>;(another device);
# and before Android is up 'posture: device_state n/a | disabled by Android: n/a (no input service) | ...'
POSTURE_RE = re.compile(r"^posture: (.*?)\s*\| disabled by Android: (.*?)\s*\| inhibited by the kernel: (.*?)\s*$")
BUILTIN_KT_RE = re.compile(r"045E:09A[EF] (Keyboard|Touchpad)")
AKA_RE = re.compile(r"\s*\(aka device -?\d+ - built-in keyboard\)\s*$")     # EventHub's suffix of the built-in keyboard


def _builtin_kt(names):
    """The built-in keyboard / touchpad names in a ';' separated list (EventHub's '(aka ...)' suffix removed)."""
    out = []
    for n in names.split(";"):
        n = AKA_RE.sub("", n.strip())
        if BUILTIN_KT_RE.search(n) and n not in out:
            out.append(n)
    return out


def _posture_summary(a):
    """[(Snap, Ev, committed state, [built-in kb/tp disabled by Android], [built-in kb/tp inhibited by the kernel])] of
    the snapshots whose summary 'posture:' line has a device state and an input report (Android up), oldest first."""
    out = []
    for sn in a.b.summary_snaps():
        e = sn.sline("posture: ")
        m = POSTURE_RE.match(e.text) if e else None
        if not m:
            continue
        st = re.search(r"\bmCommittedState=([A-Za-z_]+)", m.group(1))
        if not st or st.group(1) == "none" or m.group(2).startswith("n/a"):
            continue
        out.append((sn, e, st.group(1), _builtin_kt(m.group(2)), _builtin_kt(m.group(3))))
    return out


# (readiness A-M1) lapis's DeviceStateManager posture (DesktopDeviceStateProvider): CLOSED disables the built-in
# keyboard and touchpad. gb_hw diag logs 'device_state' and the built-in input devices' 'Enabled:' lines once, at
# boot_completed + 45 s; (review 2026-10-29) the summary's 'posture:' line (every snapshot) is the fallback when those
# kmsg lines are missing (a kernel ring flooded by avc lines loses them), and its later snapshots are noted.
@check("posture", "Posture (lapis device state) leaves the built-in keyboard enabled", "rows 18, 24, 25; checklist 1",
       "HW")
def c_posture(a, it):
    com = a.b.dm(r"gb_hw: (?:diag: )?posture: device_state mCommittedState=.*name='([A-Z_]+)'")
    ovr = a.b.dm(r"gb_hw: posture: (\S+=\S+) -> cmd device_state state ([^\s:]+)")
    lines = a.b.dm(r"gb_hw: (?:diag: )?posture: input (.*)$")
    kb_off, cur = {}, ""
    for e, m in lines:
        s = m.group(1)
        h = re.match(r"(?:Device )?-?\d+: (.*)$", s)
        if h:
            cur = AKA_RE.sub("", h.group(1))
        elif BUILTIN_KT_RE.search(cur):
            if s.strip() == "Enabled: false":
                kb_off[cur] = e
            elif s.strip() == "Enabled: true":
                kb_off.pop(cur, None)
    early = _posture_summary(a)
    # like gb_hw's diag (boot_completed + 45 s), only snapshots taken after boot_completed judge: before it the
    # lid/tablet reading and the input policy may not be settled yet
    summ = [x for x in early if x[0].boot_completed == "1"]
    pick = summ[0] if summ else None                  # the first one after boot_completed: closest to gb_hw's diag
    diag = bool(com or lines)
    it.add(com[-1:]).add(ovr[-1:]).add(list(kb_off.values())[:2]).add([pick[1]] if pick else [x[1] for x in early[:1]])
    if not a.judge_hw:
        st = com[-1][1].group(1) if com else (pick[2] if pick else "")
        return a.not_here(it, f"device state {st}" if st else "")
    if ovr and ovr[-1][1].group(2).lower() == "reset":
        it.note(f"posture override cancelled ({ovr[-1][1].group(1)})")
    elif ovr:
        it.note(f"posture override applied ({ovr[-1][1].group(1)} -> state {ovr[-1][1].group(2)}): closing the lid "
                "does not put the device in CLOSED while it is active")
    if diag:
        state = com[-1][1].group(1) if com else None
        off, src = sorted(kb_off), "at diag time"
    elif pick:
        state, off, src = pick[2], pick[3], f"in the {pick[0].name} summary"
    else:
        state = off = src = None
    # snapshots that saw something else later (the lid closed, the base flipped or detached): noted, not judged
    later = [x for x in summ if (diag or x is not pick) and (x[2] != state or x[3] != off)]
    for sn, e, st, dis, _inh in later[:2]:
        it.add([e]).note(f"{sn.name}: device state {st}" + (f", Android disabled the built-in {', '.join(dis)}"
                                                             if dis else "") + " (a later change?)")
    inh = sorted({n for x in summ for n in x[4]})
    if inh:
        it.note(f"the kernel inhibited the built-in {', '.join(inh)} in a snapshot (lapis's touchpad inhibitor while "
                "typing, or gb_power's studio mode): not judged")
    if a.base_detached:
        return it.set(UNKNOWN, "the base is detached (vendor.gb.dtx.base=detached): no built-in keyboard or touchpad "
                               "to judge" + (f"; device state {state}" if state else ""))
    if state is None and not off and not diag:
        return it.set(UNKNOWN, "no 'gb_hw: diag: posture:' lines and no summary 'posture:' line with a device state "
                               "after boot_completed (an image without the wave-5 diagnostics, Android not up, or the "
                               "log ends before boot_completed + 45 s)"
                      + (f"; before boot_completed {early[0][0].name} saw device state {early[0][2]}" if early else ""))
    if off:
        mode, me = a.b.prop("vendor.gb.dtx.mode")
        if mode in ("tablet", "studio"):
            it.add([me])
            return it.set(UNKNOWN, f"the base is in {mode} mode (vendor.gb.dtx.mode={mode}): the built-in keyboard is "
                                   f"off there by design (device state {state or 'unknown'}); judge it in laptop mode")
        return it.set(FAIL, f"Android disabled the built-in {', '.join(off)} (device state {state or 'unknown'}, "
                            f"{src}): one-boot recovery GB_PROP_1=vendor.gb.posture=open (FLASHING.md)")
    if state == "CLOSED":
        return it.set(FAIL, f"device state CLOSED {src} (lid closed, or a wrong lid/tablet reading): the built-in "
                            "keyboard and touchpad are off in CLOSED; if the lid was open, use "
                            "GB_PROP_1=vendor.gb.posture=open (FLASHING.md)")
    it.set(PASS, f"device state {state or 'not logged'} {src}; built-in keyboard/touchpad enabled")


@check("rapl", "CPU power limits (gb_hw RAPL snapshot/restore)", "row 1; checklist 3", "HW")
def c_rapl(a, it):
    snap = a.b.dm(r"gb_hw: rapl-snapshot: firmware MSR_PKG_POWER_LIMIT (.*)$")
    mode = a.b.dm(r"gb_hw: rapl: (mode=\S+ PL1/PL2.*|mode=lapis.*|firmware locked.*)$")
    bad = a.b.dm(r"gb_hw: (rapl-snapshot: (no MSR device|cannot read MSR 0x610|implausible)|rapl: (no intel-rapl|"
                 r"no firmware snapshot|bad values|values out of range))")
    v, pe = a.b.prop("vendor.gb.rapl.fw")
    it.add(snap[:1]).add(mode[:1]).add(bad[:3]).add([pe] if pe else [])
    if not a.judge_hw:
        return a.not_here(it, f"vendor.gb.rapl.fw={v}" if v else "")
    if bad:
        return it.set(FAIL, "gb_hw could not snapshot or restore the firmware limits")
    if snap and mode:
        return it.set(PASS, f"firmware limits {snap[0][1].group(1)}; {mode[0][1].group(1)}")
    if snap or (v and not v.startswith("pl1_uw=0 ")):
        return it.set(PASS, "firmware limits read" + (f" ({v})" if v else ""))
    it.set(UNKNOWN, "no gb_hw rapl lines (gb_hw logs to the kernel log; wrapped?)")


# (wave 10 misc-suspend) What a suspend leaves in this image's logs, besides the kernel's own PM: lines:
#   gb_power  "gb_power: system resumed (suspend_stats/success A -> B)" (resume_check(), after every resume)
#   gb_iptsd  "gb_iptsd: iptsd stopped: resume, rc=..." (it restarts iptsd after every resume) and, with the wave-10
#             gb_iptsd.sh, "gb_iptsd: resume diag: success=.. fail=.. last_hw_sleep=.. total_hw_sleep=.. max_hw_sleep=..
#             lpi_system_us=.. lpi_cpu_us=.." (the kernel's suspend counters and the SoC's hardware-sleep (S0ix)
#             microseconds of the last suspend; '-' = the kernel has no such file)
#   logcat    the PowerManager's sleep line (AOSP texts, not yet seen on lapis: "Powering off display group due to
#             <reason>" (PowerGroup, Android 12+), "Sleeping power group (... reason=<reason>)", the older "Going to
#             sleep due to <reason>"), "Waking up from ..." and the event-log power_sleep_requested / screen_toggled
#             records (only while they are in the 6000-line logcat window)
#   posture   device state CLOSED in a snapshot summary (a snapshot ran while the lid was closed)
#   next boot this boot's live kernel log (gb_dgpu_dmesg's dmesg-live.txt) as the NEXT boot's snapshots copy it:
#             lines written after this boot's last snapshot, e.g. a suspend that never resumed (a forced power-off)
# gb_bootlog's snapshots go by /proc/uptime, which counts the time spent suspended.
# the kernel time of a PM: line itself, also inside gb_hw's 'diag: k: [t] ...' replays (the same t: counted once)
SUSP_TS = r"\[\s*(\d+\.\d+)\]\s+"
# (review) a failed or aborted suspend ATTEMPT. k16's pm_suspend() prints 'PM: suspend entry' and 'PM: suspend exit' and
# counts suspend_stats/fail for every attempt, also one that a wakeup event or a busy driver stopped (alarmtimer returns
# -EBUSY when an alarm is due within 2 s: the usual abort on Android), and Android's autosuspend simply tries again: such
# an attempt is a FAIL only while no suspend completed. k16 prints a device's failure through dev_err with 'PM: ' as
# '<driver> <device>: PM: failed to suspend[ async| late| noirq]: error N' (older kernels 'PM: Device <device> failed to
# suspend ...'), the freezer 'Freezing <what> failed|aborted after ...'
SUSP_BAD_RE = (r"PM: (Some devices failed to suspend|suspend of devices aborted|Device \S+ failed to (?:suspend|resume)|"
               r"noirq suspend of devices failed|late suspend of devices failed|Wakeup pending, aborting suspend|"
               r"dpm_run_callback\(\): \S+ returns -?\d+|failed to (?:suspend|freeze|quiesce))|"
               r"Freezing of tasks failed|Freezing (?:user space processes|remaining freezable tasks) (?:failed|aborted)")
# (review) a device that failed to RESUME ('<driver> <device>: PM: failed to resume[ async| early| noirq]: error N', its
# 'PM: dpm_run_callback(): <..resume..> returns N', or the older 'PM: Device <device> failed to resume'): a FAIL even
# after a completed cycle (that device may be dead until the next boot)
SUSP_RESUME_BAD_RE = r"PM: (?:Device \S+ )?failed to resume|PM: dpm_run_callback\(\): \S*resume\S* returns -?\d+"
# (review) the device named by a failure line, in either format
SUSP_DEV_RE = r"PM: Device (\S+) failed to|(\S+): PM: (?:failed to|dpm_run_callback\(\))"
SUSP_DIAG_RE = (r"gb_iptsd: resume diag: success=(\S+) fail=(\S+) last_hw_sleep=(\S+) total_hw_sleep=(\S+) "
                r"max_hw_sleep=(\S+) lpi_system_us=(\S+) lpi_cpu_us=(\S+)")
SLEEP_REQ_RE = (r"(?:Going to sleep due to |Powering off display group due to |Sleeping power group \(.*?reason=)"
                r"(\w+)|\bpower_sleep_requested\b|\bscreen_toggled\s*: 0\b")
# (wave 10) a device driver error in the minute after a resume: noted (the resume itself completed)
RESUME_ERR_RE = re.compile(r"(?i)(i915|iwlwifi|surface_|ssam|ipts|nouveau|xhci|usb \d|snd_|sof-|intel_ish|cros_ec|"
                           r"mei|ipu6|btintel|Bluetooth).*(error|fail|timed? ?out|-110|-5\b|-19\b)")


def _num(v):
    return int(v) if v and v.isdigit() else None


def _susp_t(e, m):
    """(review) The kernel time of a matched PM: line: the last '[t]' before the match (inside a gb_hw 'diag: k: [t]'
    replay the replayed line's own time), else None."""
    ts = re.findall(r"\[\s*(\d+\.\d+)\]", e.text[:m.start()])
    return float(ts[-1]) if ts else None


def _susp_dev(lines):
    """(review) The first device named by the failure lines [(Ev, match)], or None."""
    for e, _m in lines:
        md = re.search(SUSP_DEV_RE, e.text)
        if md:
            return md.group(1) or md.group(2)
    return None


def _dgpu_before(a, t):
    """(Snap, function dict) of the NVIDIA VGA function in the newest snapshot taken before kernel time t (before a
    suspend /proc/uptime and the kernel's clock agree), or None."""
    best = None
    for sn in a.b.snaps:
        if sn.uptime is not None and sn.uptime < t:
            vga = [f for f in parse_dgpu(sn) if f["vendor"] == "0x10de" and f["class"].startswith("0x03")]
            if vga:
                best = (sn, vga[0])
    return best


def next_boot_tail(src, n):
    """(wave 10 misc-suspend) This boot's own live kernel log as the NEXT boot (n+1) of the same folder copies it:
    gb_dgpu_dmesg's dmesg-live.txt is the previous boot's until it rotates it into dmesg-live.prev.txt at its start
    (gb_nouveau.sh do_dmesg_stream), so boot n+1's snapshots show boot n's file either as a stale '== dmesg-live
    (tail)' (ahead of that snapshot's own kernel; live_limit) or, once rotated, as '== dmesg-live.prev (tail)' next to
    a fresh '== dmesg-live (tail)'. The copy counts as boot n's only if it shares at least 20 timestamped kernel lines
    with boot n's own snapshots (checked by the caller). [Ev] (each line once), [] when there is no such copy."""
    try:
        snaps, _audio, _d = find_files(src)
    except OSError:
        return []
    out, seen = [], set()
    for bn, t, p in sorted(snaps):
        if bn != n + 1:
            continue
        try:
            sn = Snap(p, bn, t)
        except OSError:
            continue
        live = sn.lines("dmesg_live")
        ts = [float(m.group(1)) for _ln, s in live for m in [KTS_RE.match(s)] if m]
        lim = live_limit(sn)
        stale = bool(ts) and lim is not None and max(ts) > lim
        body = live if stale else (sn.lines("dmesg_live_prev") if ts else [])
        for ln, s in body:
            if KTS_RE.match(s) and s not in seen:
                seen.add(s)
                out.append(sn.ev(ln, s))
    return out


@check("suspend", "Suspend (s2idle) and resume", "row 46; checklist 11", "HW")
def c_suspend(a, it):
    d = a.b.dm(r"gb_hw: diag: mem_sleep '([^']*)' suspend_stats success=(\d*) fail=(\d*)")
    entry = a.b.dm(SUSP_TS + r"PM: suspend entry \((\w+)\)")
    exit_ = a.b.dm(SUSP_TS + r"PM: suspend exit")
    bad = a.b.dm(SUSP_BAD_RE)
    gbp = a.b.dm(r"gb_power: system resumed \(suspend_stats/success (\d+) -> (\d+)\)")
    ipt = a.b.dm(r"gb_iptsd: iptsd stopped: resume\b")
    rdg = a.b.dm(SUSP_DIAG_RE)
    sleep = a.b.lc(SLEEP_REQ_RE)
    wake = a.b.lc(r"PowerManagerService: Waking up from (\S+)")
    closed = [x for x in _posture_summary(a) if x[2] == "CLOSED"]
    dg_test = []                    # gb_dgpu_report's [NVIDIA test] suspend phase (report test-<N>.txt)
    for sn in a.b.snaps:
        for (_hl, _hs), body in sn.reports:
            dg_test += [(sn.ev(ln, s), m) for ln, s in body for m in [re.search(r"\bsuspends_seen=(\d+)", s)] if m]
    # this boot's live kernel log as the next boot copied it: only the part after this boot's own newest kernel line
    tail = list(getattr(a.b, "next_tail", None) or [])
    own = {e.text for e in a.b.dmesg}
    newest = max((float(m.group(1)) for e in a.b.dmesg for m in [KTS_RE.match(e.text)] if m), default=None)
    late = []
    if tail and len([e for e in tail if e.text in own]) >= 20 and newest is not None:
        late = [e for e in tail if float(KTS_RE.match(e.text).group(1)) > newest]
    late_entry = [(e, m) for e in late for m in [re.search(SUSP_TS + r"PM: suspend entry", e.text)] if m]
    late_exit = [(e, m) for e in late for m in [re.search(SUSP_TS + r"PM: suspend exit", e.text)] if m]
    late_bad = [(e, m) for e in late for m in [re.search(SUSP_BAD_RE, e.text)] if m]
    rbad = a.b.dm(SUSP_RESUME_BAD_RE) + [(e, m) for e in late for m in [re.search(SUSP_RESUME_BAD_RE, e.text)] if m]
    it.add(rbad[:2])
    it.add(d[-1:]).add(entry[:1]).add(exit_[:1]).add(bad[:3]).add(gbp[-1:]).add(ipt[:1]).add(rdg[-1:])
    it.add(sleep[:2]).add(wake[:1]).add([x[1] for x in closed[:1]]).add([x[0] for x in dg_test[-1:]])
    it.add(late_entry[-1:]).add(late_exit[-1:]).add(late_bad[:2])
    if not a.judge_hw:
        return a.not_here(it)
    if d:
        ms = d[-1][1].group(1)
        if "[s2idle]" not in ms:
            it.note(f"mem_sleep '{ms}': s2idle is expected (FADT low-power S0 idle)")
    if entry and entry[0][1].group(2) != "s2idle":
        it.note(f"suspend entry ({entry[0][1].group(2)}): s2idle is expected")
    if late:
        it.note(f"the next boot's copy of this boot's live kernel log has {len(late)} line(s) after this boot's last "
                f"snapshot (up to {KTS_RE.match(late[-1].text).group(1)} s)")
    # the newest gb_iptsd resume diag (wave-10 images): counters and the SoC hardware sleep of the last suspend
    rd = rdg[-1][1] if rdg else None
    fl = d[-1][1].group(3) if d else ""
    rfl = _num(rd.group(2)) if rd else None
    t_in = max((float(m.group(1)) for _e, m in late_entry), default=None)
    t_out = max((float(m.group(1)) for _e, m in late_exit), default=None)
    if t_in is not None and (t_out is None or t_out < t_in):
        return it.set(FAIL, f"the system suspended and never resumed: this boot's live kernel log (copied by the next "
                            f"boot) ends inside a suspend (PM: suspend entry at {t_in:.1f} s, no PM: suspend exit "
                            "after it); it was powered off by hand")
    if rbad:
        dev = _susp_dev(rbad)
        return it.set(FAIL, "a device failed to resume" + (f" ({dev})" if dev else "") + ": "
                      + redact(KTS_RE.sub("", rbad[0][0].text).strip())[:160])
    # (review) each attempt = the newest 'PM: suspend entry' at or before its 'PM: suspend exit'; it failed (or was
    # aborted) when a failure line lies between the two. Only a clean attempt or a success counter is a completed cycle
    exits = {m.group(1) for _e, m in exit_} | {m.group(1) for _e, m in late_exit}
    ents = sorted({float(m.group(1)) for _e, m in entry} | {float(m.group(1)) for _e, m in late_entry})
    bad_t = [t for t in (_susp_t(e, m) for e, m in bad + late_bad) if t is not None]
    clean, failed_att = [], []
    for te in sorted(float(x) for x in exits):
        tn = max((t for t in ents if t <= te), default=te)
        (failed_att if any(tn <= tb <= te for tb in bad_t) else clean).append(te)
    why = []
    if fl and int(fl) > 0:
        why.append(f"suspend_stats fail={fl}")
    if rfl:
        why.append(f"resume diag fail={rfl}")
    dev = _susp_dev(bad + late_bad)
    if dev:
        why.append(f"device {dev}")
    failed = bool(bad or late_bad or (fl and int(fl) > 0) or (rfl and rfl > 0))
    succ = [_num(m.group(2)) for _e, m in gbp] + ([_num(rd.group(1))] if rd else [])
    if d and _num(d[-1][1].group(2)):
        succ.append(_num(d[-1][1].group(2)))
    cycles = max([len(clean), len(ipt), len({m.group(2) for _e, m in gbp})]
                 + [s for s in succ if s] + [_num(m.group(1)) or 0 for _e, m in dg_test])
    if failed and not cycles:
        return it.set(FAIL, "a suspend failed" + (f" ({', '.join(why)})" if why else "") + ": no suspend completed")
    if cycles:
        if failed:
            n = max([len(failed_att), _num(fl) or 0, rfl or 0, 1])
            first = (bad + late_bad)[0][0] if bad or late_bad else None
            it.add([first] if first else []).note(
                f"{n} suspend attempt(s) failed or were aborted" + (f" ({', '.join(why)})" if why else "")
                + (f", first: {redact(KTS_RE.sub('', first.text).strip())[:120]}" if first else "")
                + ": Android retries a suspend that a wakeup event or a busy driver stopped (alarmtimer -16 = an alarm "
                  "due within 2 s); not a FAIL while suspends complete")
        src = []
        if clean:
            src.append(f"{len(clean)} 'PM: suspend exit'")
        if gbp:
            src.append(f"gb_power 'system resumed' x{len(gbp)}")
        if ipt:
            src.append(f"gb_iptsd restarted iptsd after a resume x{len(ipt)}")
        if dg_test:
            src.append(f"[NVIDIA test] suspends_seen={dg_test[-1][1].group(1)}")
        if rd:
            hw, tot, lpi = _num(rd.group(3)), _num(rd.group(4)), _num(rd.group(6))
            if hw == 0 and lpi:
                # two independent counters: intel_pmc_core's per-suspend report and ACPI LPIT's SLP_S0 residency since
                # boot (acpi_lpit.c); when they disagree both are said and nothing is judged
                it.note(f"last_hw_sleep=0 (intel_pmc_core) but the LPIT system residency is {lpi / 1e6:.1f} s since "
                        "boot: S0ix was reached at some time; whether in this suspend is not certain")
            elif hw == 0:
                it.note("the SoC did not reach its hardware sleep state (S0ix) in the last suspend (gb_iptsd resume diag "
                        "last_hw_sleep=0, LPIT system residency " + ("0" if lpi == 0 else "not reported") + "): suspend "
                        "works but drains the battery like idle; find the blocker with the Intel PMC substate/LTR lines "
                        "(BACKLOG HW-S0IX)")
                dg = _dgpu_before(a, float(entry[-1][1].group(1))) if entry else None
                if dg and dg[1]["state"] != "D3cold":
                    it.add([dg[1]["ev"]]).note(f"the dGPU was in {dg[1]['state']} in {dg[0].name}, the last snapshot "
                                               "before the suspend: there is no S0ix while it is powered (row 46)")
            elif hw is not None:
                it.note(f"S0ix reached: {hw / 1e6:.1f} s of hardware sleep in the last suspend"
                        + (f" ({tot / 1e6:.1f} s over all suspends of this boot)" if tot is not None else ""))
            else:
                it.note("this kernel reports no hardware-sleep time (last_hw_sleep '-'): S0ix not judged")
        else:
            it.note("S0ix (the SoC hardware sleep during suspend) is not in this image's logs: the wave-10 gb_iptsd "
                    "resume diag line adds it")
        # a resume completed: driver errors in the next 60 s are noted (keyboard base, Wi-Fi, touch, GPU after resume)
        t_ex = sorted(float(t) for t in exits)
        errs = []
        for t0 in t_ex:
            errs += [e for e in a.b.dmesg for m in [KTS_RE.match(e.text)] if m and t0 < float(m.group(1)) <= t0 + 60
                     and "avc:" not in e.text and RESUME_ERR_RE.search(e.text) and not DEFERRED_RE.search(e.text)]
        if errs:
            it.add(errs[:3]).note(f"{len(errs)} driver error line(s) within 60 s after a resume (first: "
                                  f"{redact(KTS_RE.sub('', errs[0].text).strip())[:120]})")
        return it.set(PASS, f"{cycles} suspend/resume cycle(s) completed ({'; '.join(src) or 'suspend_stats'})")
    last = a.b.latest
    up = f"{last.uptime:.0f} s" if last.uptime is not None else "?"
    if entry:           # cannot be seen in this boot's own snapshots after a resume, which logs 'PM: suspend exit'
        return it.set(UNKNOWN, f"'PM: suspend entry' at {entry[-1][1].group(1)} s without a 'PM: suspend exit' in this "
                               "boot's snapshots: read the next boot's snapshots too (its copy of this boot's live "
                               "kernel log shows whether it resumed)")
    if sleep or closed:
        what = []
        if sleep:
            r = next((m.group(1) for _e, m in sleep if m.group(1)), None)
            what.append("the display went to sleep" + (f" ({r})" if r else ""))
        if closed:
            what.append(f"device state CLOSED in {closed[0][0].name}")
        return it.set(UNKNOWN, f"{' and '.join(what)}, but the kernel never suspended in the logged time (no PM: "
                               f"suspend entry; last snapshot {last.name} at {up}): a wake lock kept it awake, or the "
                               "lid was opened again too soon; repeat with the lid closed for 60 s (DEVICE-STATUS row 46)")
    it.set(UNKNOWN, f"no suspend in the logged time (last snapshot {last.name} at {up} uptime; no lid close, sleep "
                    "request or PM: suspend line): to test it, on a start that is not the first one after writing the "
                    "stick, close the lid for 60 s about 5 min after power-on, then keep the system on until 16 min "
                    "(the t420/t900 snapshots record the resume; DEVICE-STATUS row 46)")


# ------------------------------------------------------------------------------------------------ display
@check("i915", "Intel GPU: i915 bound to 8086:8A52, DMC firmware loaded", "row 14; checklist 21, 23", "HW")
def c_i915(a, it):
    gpu = a.pci.get("0000:00:02.0")
    dmc_ok = a.b.dm(r"i915 \S+: .*Finished loading DMC firmware (\S+)")
    dmc_bad = a.b.dm(r"i915 \S+: .*(Failed to load DMC|Direct firmware load for i915/\S*dmc\S* failed)", re.I)
    init = a.b.dm(r"\[drm\] Initialized i915 ")
    sdmc = None
    for sn in a.b.summary_snaps():
        e = sn.sline("firmware i915 DMC:")
        if e and "no DMC line" not in e.text:
            sdmc = e
            break
    it.add([gpu[3]] if gpu else []).add(dmc_ok[:1]).add(dmc_bad[:3]).add(init[:1]).add([sdmc] if sdmc else [])
    if not a.judge_hw:
        if a.module_precondition(it, ["i915"]):
            return
        return a.not_here(it, "no 8086:8A52 at 00:02.0" if not gpu else "")
    bad = []
    cut = not gpu and not a.pci_whole       # (wave-5 integration) a file cut before/inside == pci: 00:02.0 unseen
    if cut:
        pass
    elif not gpu or gpu[1] != "0x8a52":
        bad.append("no 8086:8A52 at 0000:00:02.0")
    elif gpu[2] != "i915":
        bad.append(f"00:02.0 driver is '{gpu[2] or 'none'}', not i915")
    if dmc_bad:
        bad.append("DMC firmware failed to load (no runtime PM / deep package C-states)")
    elif not dmc_ok and not (sdmc and "Finished loading" in sdmc.text):
        bad.append("no 'Finished loading DMC firmware' line")
    if bad:
        return it.set(FAIL, "; ".join(bad))
    if cut:
        return it.set(UNKNOWN, "00:02.0 is not in a whole == pci list (snapshot cut short?); "
                               + (dmc_ok[0][1].group(1) if dmc_ok else "DMC") + " loaded")
    it.set(PASS, "i915 bound to 00:02.0; " + (dmc_ok[0][1].group(1) if dmc_ok else "DMC") + " loaded")


@check("display", "Panel, DRM node and backlight (hwc3-drm / SurfaceFlinger)", "row 15; checklist 4, 21", "HW")
def c_display(a, it):
    bl = a.b.dm(r"gb_hw: backlight: (\S+) max=(\d*)")
    card = []
    sn = a.b.newest_with("dgpu")
    if sn:
        card = [sn.ev(ln, s) for ln, s in sn.lines("dgpu") if re.match(r"^card\d+ -> 0x8086 i915", s)]
    sf_ok = a.b.lc(r"SurfaceFlinger.*Boot is finished")
    sf_crash = [p for p in a.crash_procs if p.startswith("surfaceflinger")]
    veil = None
    for s2 in reversed(a.b.summary_snaps()):
        veil = s2.sline("i915 white veil:")
        if veil:
            break
    it.add(bl[:2]).add(card[:1]).add(sf_ok[:1]).add([veil] if veil else [])
    for p in sf_crash:
        it.add(a.crash_procs[p][:2])
    # (wave 5, native-readiness B-1) before wave 5 the summary counted 'drmhwc: Missing', which the composer's real
    # threadtime line ('V drmhwc  : Missing optional ...') never contains: count the raw logcat lines too
    raw = a.b.lc(r"drmhwc[^:]*: Missing optional LOG_GAMMA_LUT property")
    n = 0
    if veil:
        m = re.search(r"LOG_GAMMA_LUT lines: (\d+)", veil.text)
        n = int(m.group(1)) if m else 0
        h = re.search(r"hwc: use_log_gamma_lut=(\S*)(?: persistent_hdr=(\S*))?(?: force_color_mode=(\S*))?",
                      veil.text)
        if h:
            # (wave-5 integration) display's B-2 analysis of the composer binary: force_color_mode=13 and
            # persistent_hdr=true (lapis's xe-panel values) give a BT.2020 output + gamut matrix on this sRGB panel
            it.note(f"composer vendor.hwc.use_log_gamma_lut={h.group(1) or '(unset)'}, vendor.hwc.drm.persistent_hdr="
                    f"{h.group(2) or '(unset)'}, vendor.hwc.drm.force_color_mode={h.group(3) or '(unset)'} (wave-5 "
                    "images set the composer defaults false/false/-1; lapis's true/true/13 make the composer output "
                    "BT.2020 with a gamut matrix on this sRGB panel, the expected white-veil cause: native-readiness B-2)")
    if len(raw) > n:
        it.add(raw[:1])
        n = len(raw)
    if n > 0:
        # (wave-5 integration) the recovery of display's B-2 docs: gb_ignore_user_gamma=3 changes nothing natively (the
        # composer clears the gamma LUT on every commit), =4 drops i915's colour matrix
        it.note(f"drmhwc logged LOG_GAMMA_LUT {n} times (lapis's composer colour settings are active): if the panel "
                "looks pale or washed out, boot once with GB_PROP_1=vendor.hwc.drm.force_color_mode=-1, "
                "GB_PROP_2=vendor.hwc.drm.persistent_hdr=false, GB_PROP_3=vendor.hwc.use_log_gamma_lut=false and "
                "gb.overlay=1 (keeps Night Light; native-readiness B-2); if it is still pale, "
                "GB_MODOPTS_i915=gb_ignore_user_gamma=4 (drops i915's colour matrix, so Night Light stops working; "
                "=3 changes nothing on the native image; checklist 21)")
    # (wave 5, B-3) panel self refresh / pipe underruns: the summary count, or the raw kernel lines of older logs
    spsr = a.b.newest_sline("i915 PSR/underrun lines:")
    psr = a.b.dm(r"i915 .*(FIFO underrun|Selective fetch area calculation failed|PSR.*(error|fail))")
    m = re.search(r"lines: (\d+)", spsr.text) if spsr else None
    npsr = max(int(m.group(1)) if m else 0, len(psr))
    if npsr > 0:
        it.add([spsr] if spsr else []).add(psr[:2])
        it.note(f"i915 logged {npsr} PSR / pipe underrun line(s): if the panel flickers or shows ghost images, try "
                "GB_MODOPTS_i915=enable_psr=0 enable_fbc=0 with gb.overlay=1 (native-readiness B-3)")
    if not a.judge_hw:
        return a.not_here(it, "SurfaceFlinger cannot start without the Intel GPU" if sf_crash else "")
    bad, unk = [], []
    # (review 2026-10-29) a missing line counts only when its section is there (a cut-short snapshot has neither)
    if not [x for x in bl if x[1].group(1) == "intel_backlight"]:
        (bad if a.b.klog_from_zero else unk).append("no 'gb_hw: backlight: intel_backlight' line")
    if not card:
        (bad if sn else unk).append("no DRM card owned by i915")
    if sf_crash:
        bad.append("SurfaceFlinger crashed")
    if bad:
        return it.set(FAIL, "; ".join(bad))
    if unk:
        return it.set(UNKNOWN, "; ".join(unk) + " (no kernel log from the boot or no == dgpu section: snapshot cut short?)")
    it.set(PASS, "intel_backlight present, i915 owns the DRM card, SurfaceFlinger running")


# -------------------------------------------------------------------------------------------- touch and pen
@check("ipts", "Touchscreen and pen: IPTS (ipts.ko) + iptsd", "row 21", "HW")
def c_ipts(a, it):
    hid = a.input_named(r"^IPTS [0-9A-F]{4}:[0-9A-F]{4}")
    vts = a.input_named(r"IPTSD Virtual Touchscreen")
    vst = a.input_named(r"IPTSD Virtual Stylus")
    start = a.b.dm(r"gb_iptsd: starting \S+ \S+ \(IPTS")
    bad = a.b.dm(r"gb_iptsd: (still no IPTS device|iptsd stopped|.*reloading the ipts driver|insmod \S+ failed|"
                 r"warning: \S+ missing)|Failed to set memory window")
    svc, se = a.b.prop("init.svc.gb_iptsd")
    it.add([d["ev"] for d in hid + vts + vst]).add(start[:2]).add(bad[:4]).add([se] if se else [])
    if not a.judge_hw:
        if a.module_precondition(it, ["ipts"]):
            return
        return a.not_here(it, f"init.svc.gb_iptsd={svc}" if svc else "")
    if not hid:
        return it.set(FAIL, "no 'IPTS VVVV:PPPP' HID device: ipts.ko did not bind the MEI client (8086:34E4)")
    if not vts:
        return it.set(FAIL, "the IPTS HID device exists but iptsd created no 'IPTSD Virtual Touchscreen'")
    stops = [x for x in bad if "iptsd stopped" in x[0].text or "reloading" in x[0].text]
    if len(stops) >= 3:
        return it.set(FAIL, f"iptsd stopped or the driver was reloaded {len(stops)} times")
    it.set(PASS, f"{hid[0]['name']}; iptsd touchscreen{' and stylus' if vst else ''} devices present")


# ------------------------------------------------------------------------------------------------ wireless
@check("wifi", "Wi-Fi AX201 (iwlwifi firmware loaded)", "row 37; checklist 23", "HW")
def c_wifi(a, it):
    loaded = a.b.dm(r"iwlwifi \S+: loaded firmware version (\S+) (\S+) op_mode (\S+)")
    det = a.b.dm(r"iwlwifi \S+: Detected (.*)")
    bad = a.b.dm(r"iwlwifi .*(no suitable firmware found|failed to load firmware chunk|Failed to start RT ucode|"
                 r"Microcode SW error|minimum version required)")
    dev = a.pci.get("0000:00:14.3")
    it.add(det[:1]).add(loaded[:1]).add(bad[:4]).add([dev[3]] if dev else [])
    # (wave 5, native-readiness C-2/C-3) the summary's start probes: wpa_supplicant and hostapd must load under bionic,
    # or Wi-Fi cannot scan or join and the hotspot cannot start. That is software, the same on any machine
    sw = a.b.newest_sline("wifi:")
    probe = re.findall(r"\b(wpa_supplicant|hostapd) (FAILED[^;]*)", sw.text) if sw else []
    if sw:
        it.add([sw])
    # (review 2026-10-29) a probe that timed out (exit 124: no answer in 5 s) or that the boot logger was not allowed
    # to start (exit 126, Permission denied: SELinux, noexec) says nothing about the binary: noted, not a FAIL. A
    # CANNOT LINK line, a crash or any other unexpected answer stays a FAIL
    vague = [(n, d) for n, d in probe if "CANNOT LINK" not in d and (
        d.startswith("FAILED (exit 124)") or (d.startswith("FAILED (exit 126)") and
                                              re.search(r"Permission denied|Operation not permitted", d)))]
    for n, d in vague:
        it.note(f"the {n} start probe gave no answer ({d.strip()[:150]}): not judged; check it by hand "
                f"(adb shell /vendor/bin/hw/{n} -v)")
    probe = [x for x in probe if x not in vague]
    for n in re.findall(r"\b(wpa_supplicant|hostapd) absent\b", sw.text if sw else ""):
        it.note(f"/vendor/bin/hw/{n} is not on this image (probe 'absent'): Wi-Fi " +
                ("cannot scan or connect" if n == "wpa_supplicant" else "hotspot cannot start") + " without it")
    if probe:
        what = ("Wi-Fi cannot scan or connect" if any(n == "wpa_supplicant" for n, _d in probe)
                else "the Wi-Fi hotspot cannot start")
        return it.set(FAIL, "; ".join(f"{n} does not run ({d.strip()[:150]})" for n, d in probe) + f": {what} on any "
                            "machine (native-readiness C-2)")
    if not a.judge_hw:
        return a.not_here(it)
    if bad and not loaded:
        return it.set(FAIL, "iwlwifi found no usable firmware: " + bad[0][1].group(1))
    if loaded:
        if bad:
            it.note("iwlwifi also logged firmware errors after loading")
            return it.set(FAIL, f"{loaded[0][1].group(2)} loaded, then a firmware error")
        return it.set(PASS, f"{loaded[0][1].group(2)} (version {loaded[0][1].group(1)}) loaded, op_mode "
                            f"{loaded[0][1].group(3)}")
    if dev and not dev[2]:
        return it.set(FAIL, "8086:34F0 at 00:14.3 has no driver")
    if a.b.klog_from_zero:
        return it.set(FAIL, "no 'iwlwifi ... loaded firmware version' line")
    it.set(UNKNOWN, "no iwlwifi lines (kernel log wrapped)")


@check("bluetooth", "Bluetooth 8087:0026 (btintel firmware loaded)", "row 38; checklist 23", "HW")
def c_bt(a, it):
    found = a.b.dm(r"Bluetooth: hci\d+: Found device firmware: (\S+)")
    ok = a.b.dm(r"Bluetooth: hci\d+: (Firmware loaded in|Device booted in|Firmware revision|Firmware already loaded|"
                r"Applying Intel DDC parameters completed)")
    # the .sfi is the firmware itself (legacy ibt-19-* or TLV ibt-<cnvi>-<cnvr>); a missing .ddc is optional
    bad = a.b.dm(r"(Bluetooth: hci\d+: Failed to load Intel firmware file (\S+)|Direct firmware load for "
                 r"(intel/ibt-\S+\.sfi) failed|Bluetooth: hci\d+: (?=.*[Ff]irmware)(?=.*(?:[Ff]ail|[Ee]rror|"
                 r"[Tt]imed? ?out)).*)")
    hci = a.b.dm(r"Bluetooth: hci\d+:")
    sbt = a.b.newest_sline("bluetooth:")          # (wave 5) controller names and the HAL state
    it.add(found[:1]).add(ok[:2]).add(bad[:4]).add([sbt] if sbt else [])
    if not a.judge_hw:
        return a.not_here(it)
    if bad:
        names = sorted({n for _e, m in bad for n in (m.group(2), m.group(3)) if n})
        return it.set(FAIL, "Bluetooth firmware failed" + (f": {', '.join(names)} (stage it, INPUTS.md)" if names
                                                           else ""))
    if ok:
        return it.set(PASS, (found[0][1].group(1) + " " if found else "") + "loaded, controller booted")
    if not hci and a.b.klog_from_zero:
        return it.set(FAIL, "no 'Bluetooth: hci0' line: btusb/btintel did not register the controller")
    it.set(UNKNOWN, "no Bluetooth firmware lines")


# --------------------------------------------------------------------------------------------------- audio
def legacy_hda(a):
    """(wave 5, native-readiness C-4) (True if this boot used snd_intel_dspcfg.dsp_driver=1, the legacy HDA driver whose
    card id is PCH, else False; the evidence line or None): the summary's dsp_driver line, else the command line."""
    e = a.b.newest_sline("audio dsp_driver=")
    if e and re.match(r"audio dsp_driver=\d", e.text):     # (review 2026-10-29) '-' (parameter unreadable): cmdline
        return e.text.startswith("audio dsp_driver=1 "), e
    t = a.token("snd_intel_dspcfg.dsp_driver")
    return t == "snd_intel_dspcfg.dsp_driver=1", (a.cmdline_ev if t else None)


@check("audio_card", "Sound card: SOF, ALSA card id sofhdadsp", "rows 39-41; checklist 22", "HW")
def c_audio_card(a, it):
    cards = []
    sn = a.b.newest_with("asound")
    if sn:
        cards = [sn.ev(ln, s) for ln, s in sn.lines("asound") if re.match(r"^\s*\d+ \[\S+\s*\]: ", s)
                 or "no soundcards" in s]
    scard = None
    for s2 in reversed(a.b.summary_snaps()):
        scard = s2.sline("audio cards:")
        if scard:
            break
    adiag = []
    for ad in a.b.audio:
        for i, s in enumerate(ad.lines, 1):
            if re.match(r"^--- /proc/asound/card\d+ id=", s):
                adiag.append(Ev(ad.name, i, s))
    sof_ok = a.b.dm(r"sof-audio-pci-intel-\w+ \S+: (Firmware info|Firmware: ABI|Topology file|Firmware file)")
    sof_bad = a.b.dm(r"(sof-audio-pci-intel-\w+ \S+: .*(error|failed|fail to)|Direct firmware load for "
                     r"intel/sof\S* failed)", re.I)
    legacy, lev = legacy_hda(a)
    cid = "PCH" if legacy else "sofhdadsp"
    it.add(cards[:3]).add([scard] if scard else []).add([lev] if lev else []).add(adiag[:2]).add(sof_ok[:2])
    it.add(sof_bad[:3])
    text = " ".join(e.text for e in cards + ([scard] if scard else []) + adiag)
    has = bool(re.search(rf"\[{cid}\s*\]|id={cid}\b", text))
    if not a.judge_hw:
        return a.not_here(it, "no sound card" if not has else "")
    if has and legacy:
        it.note("snd_intel_dspcfg.dsp_driver=1: the legacy HDA driver (speakers, headphone jack, HDMI/DP; the DMIC "
                "array needs SOF)")
        return it.set(PASS, "legacy HDA card id PCH present (dsp_driver=1, native-readiness C-4)")
    if has:
        return it.set(PASS, "ALSA card id sofhdadsp present")
    if not legacy:
        it.note("if SOF fails on the Surface, boot once with snd_intel_dspcfg.dsp_driver=1 (boot menu 'e'; gb.cfg cannot "
                "set it): the legacy HDA driver, FLASHING.md 'No sound' (native-readiness C-4)")
    hint = ""
    if not a.pci.get("0000:00:02.0") or a.pci["0000:00:02.0"][2] != "i915":
        hint = " (SOF waits for i915: without i915 no audio works at all, row 41)"
    it.set(FAIL, f"no {cid} card" + hint)


@check("audio_dras", "DRAS audio HAL configured for card id sofhdadsp", "row 39; checklist 22", "SW")
def c_dras(a, it):
    hits = a.b.lc(r'primary_soundcard_name: Some\("([^"]+)"\)')
    cnt = None
    for s2 in reversed(a.b.summary_snaps()):
        cnt = s2.sline("audio DRAS:")
        if cnt:
            break
    it.add([cnt] if cnt else []).add(hits[:2])
    if not hits:
        return it.set(UNKNOWN, "no DRAS 'Startup options' line in logcat")
    names = sorted({m.group(1) for _e, m in hits})
    pids = {e.text.split()[2] for e, _m in hits if len(e.text.split()) > 2}
    cid = "PCH" if legacy_hda(a)[0] else "sofhdadsp"      # (wave 5, C-4) gb_audio's legacy config uses card id PCH
    if names != [cid]:
        return it.set(FAIL, f"DRAS uses soundcard {', '.join(names)}; DRAS matches the ALSA card id: {cid}")
    if len(pids) > 3:
        msg = f"DRAS started {len(pids)} times (it exits while its card is missing)"
        if a.env == "surface" and any(s.status == PASS for s in a.items if s.id == "audio_card"):
            return it.set(FAIL, msg + " although the card exists")
        it.note(msg)
    it.set(PASS, f'DRAS starts with primary_soundcard_name Some("{cid}")')


# --------------------------------------------------------------------------------------- sensors and cameras
@check("sensors", "Sensors: ISH accelerometer/gyro (cros-ec-accel) + APDS-9960 light", "rows 27-28; checklist 10",
       "HW")
def c_sensors(a, it):
    names = []
    sn = a.b.newest_with("iio")
    if sn:
        for ln, s in sn.lines("iio"):
            m = re.match(r"^/sys/bus/iio/devices/(\S+)/name (.*)$", s)
            if m:
                names.append((m.group(2).strip(), sn.ev(ln, s)))
    wait = a.b.dm(r"GB_SENSORS: waited (\d+) ms for:(.*?); iio:(.*?)\s*missing:\s*(.*)$")
    noish = a.b.dm(r"GB_SENSORS: no ISH")
    # (wave 9 hw-native1) the Integrated Sensor Hub's coldboot probe can time out on its firmware (first native boot,
    # boot 3): then no HID sensor hub and no accelerometer exist; gb_hw 'ish' resets and re-binds it at early-boot
    ishf = a.b.dm(r"intel_ish_ipc \S+: (waiting for ishtp start failed|HBM haven't started|ISHTP: Protocol init failed|"
                  r"ISH: hw start failed)")
    gbi = a.b.dm(r"gb_hw: ish: (.*)$")
    vish, iev = a.b.prop("vendor.gb.ish")
    it.add([e for _n, e in names][:6]).add(wait[-1:]).add(noish[:1]).add(ishf[-2:]).add(gbi[-1:]) \
        .add([iev] if iev else [])
    for f, newest, lim in getattr(a.b, "stale_live", []):
        it.note(f"{f}: its dmesg-live section is an earlier boot's (kernel time {newest:.0f} s, past this snapshot's "
                f"{lim:.0f} s): not used")
    if not a.judge_hw:
        return a.not_here(it, "gb_sensors found no ISH (8086:34fc) / MSHW0184" if noish else "")
    ish_why = ""
    if ishf:
        ish_why = ("; the Integrated Sensor Hub did not start at coldboot (intel_ish_ipc: the ISH firmware did not answer"
                   ", ISHTP protocol init failed)"
                   + (f"; gb_hw ish: {vish}" if vish else "; no gb_hw ish re-probe: an image before wave 9"))
    elif vish:
        it.note(f"gb_hw ish: {vish}")
    if wait:
        miss = wait[-1][1].group(4).strip()
        if miss and miss != "none":
            return it.set(FAIL, f"missing IIO devices after the wait: {miss}" + (ish_why if "accel" in miss else ""))
        if ishf:
            it.note("the ISH's coldboot probe failed, but the accelerometer was there before the sensors HAL started"
                    + (f" (gb_hw ish: {vish})" if vish else ""))
        return it.set(PASS, f"IIO devices registered: {wait[-1][1].group(3).strip()}")
    if noish:
        return it.set(FAIL, "gb_sensors found no ISH (8086:34fc) and no MSHW0184")
    have = {n for n, _e in names}
    need = {"cros-ec-accel", "cros-ec-light"}
    if need <= have:
        return it.set(PASS, f"IIO devices: {', '.join(sorted(have))}")
    if names:
        return it.set(FAIL, f"missing {', '.join(sorted(need - have))} (have {', '.join(sorted(have))})"
                      + (ish_why if "cros-ec-accel" not in have else ""))
    it.set(FAIL, "no IIO sensor device" + ish_why)


# (wave 9, BACKLOG 13.3 "rotation analyser", relabel B.4) gb_sensors.sh rot_report writes one read-only kmsg line per
# posture change (at most 3 per start; the 'diag' one at boot_completed): 'GB_SENSORS: rotation: <trigger>:
# device_state=.. dtx=.. ... => <verdict>', the verdict computed ON THE DEVICE by rot_verdict (C:\GB-build\native\
# sensors\gb_sensors.sh): 'ok: ...', 'MISMATCH: ...', 'WARN: config_supportAutoRotation is off ...', 'info: ...', plus
# '; note: DTX mode X disagrees with the device state Y'. The expected verdicts per posture are DEVICE-STATUS item 10.
ROT_RE = r"GB_SENSORS: rotation: ([^:]+): (.*?) => (.*)$"


@check("rotation", "Rotation per posture (GB_SENSORS: rotation: verdicts)", "row 27; checklist 10", "HW")
def c_rotation(a, it):
    rows = a.b.dm(ROT_RE)
    it.add(rows[:6])
    if not a.judge_hw:
        return a.not_here(it, f"{len(rows)} 'GB_SENSORS: rotation:' line(s)" if rows else "")
    if not rows:
        return it.set(UNKNOWN, "no 'GB_SENSORS: rotation:' line (gb_sensors writes one at boot_completed and one per "
                               "posture change: Android was not up within the log window, or an image before wave 6)")
    bad, ok, info = [], [], []
    for i, (e, m) in enumerate(rows):            # kernel-log order (Boot.dmesg: snapshots in time order, each line once)
        verdict, _, note = m.group(3).strip().partition("; note: ")
        if note:
            it.note(f"{m.group(1)}: {note} (the lid switch or SW_TABLET_MODE: checklist 25, A-M1)")
        (bad if verdict.startswith(("MISMATCH:", "WARN:")) else ok if verdict.startswith("ok:") else info).append(
            (i, m.group(1), verdict))
    for _i, trig, verdict in info[:2]:
        it.note(f"{trig}: {verdict}")
    if bad:
        i0, trig, verdict = bad[0]
        rem = ""
        if verdict.startswith("MISMATCH:"):
            rem = ("; Android's posture policy did not follow the hardware: one detach/attach of the clipboard or the "
                   "Auto-rotate tile corrects a missed boot-time state (rotation-al3 R1)")
            if any(i2 > i0 for i2, _t2, _v2 in ok):
                rem += "; a later line is ok"
        elif verdict.startswith("WARN:"):
            rem = "; the sensors overlay did not apply (cmd overlay list | grep gb_sb3)"
        return it.set(FAIL, f"{len(bad)} of {len(rows)} rotation line(s) not ok; {trig}: {verdict}{rem}")
    if ok:
        return it.set(PASS, "; ".join(sorted({v for _i, _t, v in ok})) + f" ({len(ok)} line(s))")
    it.set(UNKNOWN, f"only info verdicts ({info[0][1]}: {info[0][2]})")


@check("cameras", "Cameras: IPU4P firmware loaded, sensors probed", "row 29; checklist 9", "HW")
def c_cameras(a, it):
    dev = a.pci.get("0000:00:05.0")
    hw = a.b.dm(r"intel-ipu6 \S+: .*hardware version")
    fwbad = a.b.dm(r"(Requesting signed firmware (\S+) failed|Direct firmware load for (intel/ipu\S*) failed|"
                   r"intel-ipu6 \S+: .*(fw boot timeout|Firmware mapping failed|failed to create pkg dir|"
                   r"Failed to init ipu7 firmware region))")
    found = a.b.dm(r"Found supported sensor (\S+)")
    conn = a.b.dm(r"Connected (\d+) cameras?")
    serr = a.b.dm(r"\b(ov5693|ov8865|ov7251|dw9719)\b.*\b(fail(ed)?|error|mismatch|timeout)\b|"
                  r"\b(ov5693|ov8865|ov7251|dw9719)\b.*: probe .* failed with error", re.I)
    # (wave 9 hw-native1) the IR camera OV7251 (INT347E) is not exposed by the camera HAL (camera_hal.yaml) and its probe
    # always failed on the Surface Book 3 (-121); images from wave 9 on no longer load it: a note, never a FAIL
    serr_ir = [x for x in serr if re.search(r"\bov7251\b", x[0].text, re.I)]
    serr = [x for x in serr if x not in serr_ir]
    # (wave 10 misc-cameras) gb_hw.sh vcm loads the rear focus motor's dw9719 once ipu-bridge has created its i2c device
    # and its runtime-PM link to the sensor. Its own lines ('gb_hw: vcm:', 'gb_hw: diag: vcm') are judged here, never
    # as a sensor driver's error; a FAILED one (or runtime=error) is the FAIL reason.
    vcm = a.b.dm(r"gb_hw: (diag: )?vcm[: ]")
    serr = [x for x in serr if not re.search(r"gb_hw: (diag: )?vcm[: ]", x[0].text)]
    vbad = [x for x in vcm if re.search(r"\bFAILED\b|\bruntime=error\b", x[0].text)]
    park = [x for x in serr if re.search(r"\bdw9719\b.*Error writing reg 0x0003: -121", x[0].text)]
    it.add([dev[3]] if dev else []).add(hw[:1]).add(fwbad[:3]).add(found[:3]).add(conn[:1]).add(serr[:4]) \
        .add(serr_ir[:1]).add(vbad[:1] or vcm[-1:])
    if serr_ir:
        it.note("the IR camera ov7251 (INT347E) logged errors: not used by Android (the camera HAL exposes only the "
                "front and rear cameras); images from wave 9 on do not load it")
    # (misc-cameras review) the old-order cause only when gb_hw vcm did not load the motor itself (no vcm line, or
    # its re-bind of a VCM that an older load list had bound before the link)
    old_order = not vcm or any("before its runtime-PM link existed" in x[0].text for x in vcm)
    if park and old_order:
        it.note("dw9719 'Error writing reg 0x0003: -121' = the rear focus motor's first runtime suspend (the lens park) "
                "without power: dw9719 probed before ipu-bridge's VCM -> sensor runtime-PM link existed (gbinit loaded "
                "it, images before wave 10). The rear sensor is not affected (the lens stays at rest); the VCM stays "
                "in the runtime-PM error state (w10prep misc-cameras)")
    elif park:
        it.note("dw9719 'Error writing reg 0x0003: -121' although gb_hw vcm loaded dw9719 after ipu-bridge's VCM -> "
                "sensor runtime-PM link existed: the lens park failed with the link in place, so the load-order cause "
                "of the earlier boots does not explain it (see the gb_hw vcm line; w10prep misc-cameras RESULT "
                "section 5)")
    if vcm and not vbad:
        it.note("rear focus motor: " + vcm[-1][0].text.split("gb_hw: ", 1)[-1][:200])
    # (W5, C-1) the HAL must load on any machine: a dlopen failure is software, so it FAILs in VMware too (it hid
    # behind "no hardware" in every native VM smoke before wave 5)
    hal = a.b.lc(r"Could not load (/odm/lib64/hw/camera\.sb3\.so|camera HAL module)")
    # (review 2026-10-29) GB_BOOTLOG_SH's 'camera: provider=.., exits seen: N, HAL load failures: N | lcam-probe link:
    # ..' counts the HAL load failures in the whole logcat (the snapshot keeps only its last 6000 lines): the highest
    # count of any snapshot (logcat's ring can drop early lines later)
    scam = []
    for sn in a.b.summary_snaps():
        e = sn.sline("camera:")
        m = re.search(r"HAL load failures: (\d+)", e.text) if e else None
        if m:
            scam.append((int(m.group(1)), e))
    nhal, sev = max(scam, key=lambda x: x[0]) if scam else (0, None)
    lp = re.search(r"lcam-probe link: (FAILED.*)$", scam[-1][1].text) if scam else None
    if lp and "CANNOT LINK" in lp.group(1):
        it.add([scam[-1][1]]).note("lcam-probe cannot link (" + lp.group(1)[:160] + "): the libcamera libraries the "
                                   "camera HAL loads may not link either (C-1)")
    if hal or nhal:
        it.add(hal[:2]).add([sev] if nhal else [])
        return it.set(FAIL, "the camera HAL /odm/lib64/hw/camera.sb3.so does not load (dlopen failed, see the "
                            + ("logcat line" if hal else f"summary: {nhal} HAL load failure(s)")
                            + "): no camera works on any machine (C-1)")
    if not a.judge_hw:
        if a.module_precondition(it, ["intel-ipu6", "intel-ipu6-isys"]):
            return
        return a.not_here(it)
    it.note("the IPU4P firmware (intel/ipu/ipu4p_fw.bin) comes from your own Windows driver package (INPUTS.md)")
    bad = []
    if not dev:
        if a.pci_whole:                                   # (wave-5 integration) not in a file cut before/inside == pci
            bad.append("no IPU4P 8086:8A19 at 00:05.0")
    elif dev[2] not in ("intel-ipu6", "intel_ipu6"):
        bad.append(f"00:05.0 driver is '{dev[2] or 'none'}', not intel-ipu6")
    if fwbad:
        bad.append("IPU4P firmware failed to load")
    if not found and not conn and a.b.klog_from_zero:     # (review 2026-10-29) only with the boot's kernel log
        bad.append("ipu-bridge found no camera sensor")
    if serr:
        bad.append("a sensor driver logged errors (" + ", ".join(sorted({re.search(
            r"\b(ov5693|ov8865|dw9719)\b", x[0].text, re.I).group(1).lower() for x in serr})) + ")")
    if vbad:
        bad.append("the rear focus motor's loader (gb_hw vcm) reported: " + vbad[0][0].text.split("gb_hw: ", 1)[-1][:160])
    if bad:
        return it.set(FAIL, "; ".join(bad))
    if not found and not conn:
        return it.set(UNKNOWN, "no ipu-bridge line: the kernel log does not reach back to the boot (snapshot cut short?)")
    n = conn[0][1].group(1) if conn else str(len(found))
    it.set(PASS, f"IPU bound, firmware loaded, {n} camera(s) connected")


# -------------------------------------------------------------------------------------------------- NVIDIA
def _dgpu_funcs(a):
    """[(snap, [functions])] for every snapshot (oldest first)."""
    return [(sn, parse_dgpu(sn)) for sn in a.b.snaps if sn.lines("dgpu")]


@check("dgpu_power", "NVIDIA dGPU power: D3cold when idle", "rows 17, 46; checklist 11, 14, 17", "HW")
def c_dgpu_power(a, it):
    per = _dgpu_funcs(a)
    if not per:
        return it.set(UNKNOWN, "no == dgpu section in any snapshot")
    sn_last, last = per[-1]
    vga = [f for f in last if f["class"].startswith("0x03")]
    absent = [sn_last.ev(ln, s) for ln, s in sn_last.lines("dgpu") if s.startswith("no NVIDIA function present")]
    st, se = a.b.prop("vendor.gb.dgpu.state")
    it.add([f["ev"] for f in last][:3]).add(absent[:1]).add([se] if se else [])
    if not a.judge_hw:
        return a.not_here(it, "no NVIDIA function present" if not last else "")
    if not vga:
        if a.base_detached:
            return it.set(UNKNOWN, "the base is detached: no dGPU")
        if a.base[0] == "attached":
            return it.set(FAIL, "the base is attached but no NVIDIA VGA function is enumerated")
        return it.set(UNKNOWN, "no NVIDIA function and the base state is unknown")
    d3 = [(sn, f) for sn, fs in per for f in fs if f["class"].startswith("0x03") and f["state"] == "D3cold"]
    if any(f["runtime"] == "error" for f in vga):
        return it.set(FAIL, "runtime_status=error on the dGPU")
    if vga[0]["state"] == "D3cold":
        return it.set(PASS, f"{vga[0]['bdf']} state=D3cold runtime={vga[0]['runtime']} driver="
                            f"{vga[0]['driver'] or 'none'}")
    if d3:
        it.add([d3[-1][1]["ev"]])
        return it.set(PASS, f"D3cold seen at {d3[-1][0].name}; {vga[0]['state']} at the last snapshot "
                            f"(vendor.gb.dgpu.state={st})")
    if st and st.startswith("busy"):
        return it.set(UNKNOWN, f"the dGPU was in use ({st}) at every snapshot: D3cold not observable")
    it.set(FAIL, f"{vga[0]['bdf']} is {vga[0]['state'] or '?'} (runtime={vga[0]['runtime']}) while idle "
                 f"(vendor.gb.dgpu.state={st}): no S0ix, battery drain")


@check("nouveau", "nouveau on the dGPU per graphics mode (vendor.gb.nouveau.result)", "row 17; checklist 14, 17",
       "HW")
def c_nouveau(a, it):
    keys = ("persist.vendor.gb.gfx_mode", "vendor.gb.nouveau.mode", "vendor.gb.nouveau.result", "vendor.gb.dgpu.state")
    vals = {}
    for k in keys:
        v, e = a.b.prop(k)
        vals[k] = v or ""
        if e:
            it.add([e])
    sn = a.b.newest_with("dgpu") or a.b.latest
    vga = [f for f in parse_dgpu(sn) if f["class"].startswith("0x03")]
    rc = a.b.dm(r"gb_nouveau: .*\b(result|clean|unhealthy|insmod|bind)\b")
    it.add([f["ev"] for f in vga][:1]).add(rc[-3:])
    res = vals["vendor.gb.nouveau.result"]
    mode = vals["persist.vendor.gb.gfx_mode"] or "auto"
    if not a.judge_hw:
        return a.not_here(it, f"vendor.gb.nouveau.result={res or '(empty)'}, vendor.gb.dgpu.state="
                              f"{vals['vendor.gb.dgpu.state'] or '(empty)'}")
    if "gb.nouveau=0" in a.cmdline:
        return it.set(UNKNOWN, "gb.nouveau=0 on the command line: nouveau disabled for this boot")
    if not vga:
        return it.set(UNKNOWN, "no NVIDIA function (base detached or not enumerated): see dgpu_power")
    drv = vga[0]["driver"]
    if res == "refused:model" and not drv and not re.fullmatch(r"0x1f[89a-f][0-9a-f]", vga[0]["device"]):
        # (wave 5, B-4) a 15-inch SB3 (TU116/TU106): the image has no firmware for it. On a TU117 it is a gate bug: FAIL
        return it.set(UNKNOWN, f"{vga[0]['bdf']} is NVIDIA {vga[0]['device']}, not the 13.5-inch model's TU117: "
                               "nouveau stays off by design (refused:model); dgpu_power must still show D3cold")
    if mode != "intel" and not _completed(a)[0] and not drv:
        return it.set(UNKNOWN, "the boot never completed, and nouveau loads only after sys.boot_completed")
    if mode == "intel":
        if drv:
            return it.set(FAIL, f"Intel-only mode but {drv} is bound")
        return it.set(PASS, "Intel-only mode: no driver on the dGPU, as designed")
    if res in ("clean", "healthy") and drv == "nouveau":
        return it.set(PASS, f"mode {mode}: nouveau bound, result={res}")
    if res.startswith(("unhealthy", "insmod-failed", "bind-failed", "crashloop", "refused:")) or res in ("crashloop",):
        return it.set(FAIL, f"mode {mode}: vendor.gb.nouveau.result={res}")
    up = sn.uptime or 0
    if res in ("", "idle", "loading") and up < 420:
        return it.set(UNKNOWN, f"result '{res or 'unset'}' at uptime {up:.0f} s: nouveau loads after boot completes;"
                               " read the t420/t900 snapshot")
    it.set(FAIL, f"mode {mode}: result '{res or 'unset'}', driver '{drv or 'none'}' at uptime {up:.0f} s")


# (wave 10 misc-dgpu-report) When a dgpu-<N>.txt can exist. gb_bootlog requests the report at t40 (setprop
# vendor.gb.dgpu_report 1); gb.rc ('on property:vendor.gb.dgpu_report=1' in nouveau-kernel\native\gb_nouveau.rc) sets
# the property back to 0 and starts the oneshot service gb_dgpu_report, so 0 is the trigger at rest, never a result
# (dgpu\INTERFACES.md section 6: "rc resets to 0"). 'gb_dgpu_report.sh report' writes report/dgpu-<N>.txt.tmp and
# renames it only at its end, and on the normal entry its '== power' part takes 30 'gb_power -o' samples 10 s apart
# (INTERFACES 7.10): the first native boot's boot 2 ran 295.75 s (started 40.3 s, "report written" 336.1 s), so the t420
# snapshot is the first that can hold it. A gb.nouveau=test boot skips that part (boot 3: 3.15 s).
DGPU_REPORT_TYPICAL_S = 300    # normal entry: 29 x 10 s sleeps + 30 short samples
DGPU_REPORT_MAX_S = 840        # normal entry at worst: 29 x 10 s + 30 x 15 s (each sample's timeout) + 2 x 30 s (the
#                                vkjson and SurfaceFlinger timeouts) + 40 s; a run from t40 still running at t900 hangs
DGPU_REPORT_TEST_MAX_S = 120   # gb.nouveau=test boot (no '== power' part): the two 30 s timeouts + slack
DGPU_START_RE = re.compile(r"init: starting service 'gb_dgpu_report'")
DGPU_EXIT_RE = re.compile(r"init: Service 'gb_dgpu_report' \(pid (\d+)\) (exited with status (\d+)|received "
                          r"(SIG[A-Z0-9]+|signal \d+)|killed by signal (\d+))")
DGPU_WRITTEN_RE = re.compile(r"gb_dgpu_report: report written: dgpu-(\d+)\.txt")
DGPU_REFUSED_RE = re.compile(r"gb_dgpu_report: report: (.+)$")
DGPU_HDR_RE = re.compile(r"^== gb_dgpu_report report n=(\d+)")


def _dgpu_klines(sns):
    """(wave 10 misc-dgpu-report) gb_dgpu_report's service and report lines of THIS boot: only the snapshots' own
    '== dmesg' dumps (the kernel ring buffer), never dmesg-live or a report file, which can hold an earlier boot's lines.
    {kind: [(timestamp, Ev, match)]} for kind start, exit, written, refused (each line once)."""
    out = {"start": [], "exit": [], "written": [], "refused": []}
    seen = set()
    for sn in sns:
        for ln, s in sn.lines("dmesg"):
            if "gb_dgpu_report" not in s or s in seen:
                continue
            seen.add(s)
            mt = KTS_RE.match(s)
            if not mt:
                continue
            for kind, rx in (("start", DGPU_START_RE), ("exit", DGPU_EXIT_RE), ("written", DGPU_WRITTEN_RE),
                             ("refused", DGPU_REFUSED_RE)):
                m = rx.search(s)
                if m:
                    out[kind].append((float(mt.group(1)), sn.ev(ln, s), m))
                    break
    return out


def _dgpu_report_stale(a, sn, hdr, kl):
    """(wave 10 misc-dgpu-report) A reason text when the dgpu report hdr found in snapshot sn cannot be this boot's,
    else None. gb_bootlog adds the NEWEST report/dgpu-*.txt, which is an earlier boot's while this boot's run has not
    renamed its .tmp yet. Proven only from complete evidence: (1) this boot's kernel log names its own file (every run of
    a boot writes dgpu-<bootseq>.txt) and n differs; (2) sn's getprop shows gb_dgpu_report running, and sn's own dmesg
    dump (taken after the report was read) is whole (the file is not cut inside it), starts at boot and holds exactly
    one start of it and no exit or write. (On the SB3 the ring buffer starts at boot only in the early snapshots: the
    second native boot's t180 dump starts at 17.9 s, so (2) proves nothing there and the report counts as before.)"""
    m = DGPU_HDR_RE.match(hdr)
    n = m.group(1) if m else None
    own = sorted({w[2].group(1) for w in kl["written"]})
    if n is not None and own and n not in own:
        return f"n={n}, but this boot writes dgpu-{own[-1]}.txt"
    st, _e = a.b.prop_in(sn, "init.svc.gb_dgpu_report")
    dm = sn.lines("dmesg")
    first = next((KTS_RE.match(s) for _ln, s in dm if KTS_RE.match(s)), None)
    # (review 2026-11-05) a file cut inside its dmesg dump can lack this run's 'report written' and exit lines
    if st != "running" or not sn.whole("dmesg") or not first or float(first.group(1)) >= 2.0:
        return None
    k = _dgpu_klines([sn])
    if len(k["start"]) == 1 and not k["exit"] and not k["written"]:
        return (f"n={n or '?'}, while this boot's only gb_dgpu_report run (started at {k['start'][0][0]:.1f} s) was "
                "still running")
    return None


def _dgpu_report_missing(a, it, kl):
    """(wave 10 misc-dgpu-report) No dgpu-<N>.txt of this boot in any snapshot: UNKNOWN while gb_dgpu_report is still
    inside its run time (or not requested yet), FAIL when it was refused, exited or was killed without a report, wrote
    one that no snapshot holds, runs longer than it can (hung) or never started."""
    v, ve = a.b.prop("vendor.gb.dgpu_report")
    if v is not None:
        it.add([ve])
        if v in ("1", "2"):
            # (review 2026-11-05) seconds after the setprop the trigger still set: gb.rc's property trigger never acted
            it.note(f"vendor.gb.dgpu_report={v}: requested, but gb.rc has not set it back to 0 (it does that as it "
                    f"starts {'gb_dgpu_report' if v == '1' else 'gb_dgpu_test'}): the rc trigger did not act")
        else:
            it.note(f"vendor.gb.dgpu_report={v}: the request trigger at rest (gb_bootlog sets 1 at t40, gb.rc sets it "
                    "back to 0 as it starts gb_dgpu_report; 2 = the test sequence): never a result")
    nv = [t.split("=", 1)[1] for t in a.cmdline if t.startswith("gb.nouveau=")]
    test = bool(nv) and nv[-1] == "test"
    bound = DGPU_REPORT_TEST_MAX_S if test else DGPU_REPORT_MAX_S
    kind = "a gb.nouveau=test boot (no '== power' part)" if test else "the normal entry"
    for k in ("start", "exit", "written", "refused"):
        it.add([x[1] for x in kl[k]])
    if kl["refused"]:
        return it.set(FAIL, f"gb_dgpu_report refused: {kl['refused'][-1][2].group(1)}")
    bad = [x for x in kl["exit"] if x[2].group(3) != "0"]
    if bad:
        m = bad[-1][2]
        return it.set(FAIL, f"gb_dgpu_report (pid {m.group(1)}) {m.group(2)} without a report")
    # the newest snapshot whose getprop holds the service's state (a file cut before getprop judges nothing)
    sn = next((x for x in reversed(a.b.snaps) if a.b.prop_in(x, "init.svc.gb_dgpu_report")[0] is not None), None)
    if sn is None:
        sn = next((x for x in reversed(a.b.snaps) if x.whole("getprop")), None)
    if sn is None:
        return it.set(UNKNOWN, "no dGPU report yet and no whole getprop in any snapshot (files cut short?)")
    up = sn.uptime or 0
    st, se = a.b.prop_in(sn, "init.svc.gb_dgpu_report")
    bt, be = a.b.prop_in(sn, "ro.boottime.gb_dgpu_report")
    it.add([se, be])
    # the newest start line first: ro.boottime.<service> is write-once (the FIRST start of the boot)
    starts = [x[0] for x in kl["start"] if x[0] <= up + 5]
    start = starts[-1] if starts else (int(bt) / 1e9 if bt and bt.isdigit() else None)
    # gb_bootlog reads report/*.txt after getprop and before '== dmesg-live (tail)': a file cut before that point
    # never listed the reports, so it is no evidence that one is missing
    listed = any(k in sn.hdr for k in ("dmesg_live", "dmesg_live_prev", "nouveau_log", "dmesg", "logcat"))
    if (kl["written"] or (st and st != "running")) and not listed:
        return it.set(UNKNOWN, f"no dGPU report yet: {sn.name} is cut short before gb_bootlog's report listing")
    if kl["written"]:
        w = kl["written"][-1]
        if w[0] <= up:
            return it.set(FAIL, f"gb_dgpu_report wrote dgpu-{w[2].group(1)}.txt at {w[0]:.1f} s, but no snapshot "
                                f"holds it ({sn.name} at uptime {up:.0f} s reads the newest report/dgpu-*.txt)")
        return it.set(UNKNOWN, f"dgpu-{w[2].group(1)}.txt was written at {w[0]:.1f} s, after the newest snapshot "
                               f"({sn.name}) had started")
    if st == "running":
        if start is None:
            return it.set(UNKNOWN, f"gb_dgpu_report is running in {sn.name} (uptime {up:.0f} s), start time unknown")
        age = up - start
        if age > bound:
            return it.set(FAIL, f"gb_dgpu_report is still running {age:.0f} s after its start at {start:.1f} s ("
                                f"{sn.name}, uptime {up:.0f} s): it takes at most {bound} s on {kind}, so it hangs")
        if not test and age > DGPU_REPORT_TYPICAL_S + 60:
            it.note(f"slower than usual: about {DGPU_REPORT_TYPICAL_S} s is typical")
        due = "within seconds" if test else (f"about {DGPU_REPORT_TYPICAL_S // 60} min after its start (30 power "
                                             "samples 10 s apart), so the t420 snapshot is the first to hold it: "
                                             "keep the machine on for at least 7 min (15 min for t900)")
        return it.set(UNKNOWN, f"gb_dgpu_report is still running in the newest snapshot ({sn.name}, uptime {up:.0f} "
                               f"s; started at {start:.1f} s, {age:.0f} s ago): on {kind} it writes dgpu-<N>.txt {due}")
    if st:
        return it.set(FAIL, f"gb_dgpu_report ended (init.svc.gb_dgpu_report={st} in {sn.name}) without a "
                            "dgpu-<N>.txt in that snapshot")
    if start is not None:
        if up - start > bound:
            return it.set(FAIL, f"no dGPU report {up - start:.0f} s after gb_dgpu_report started at {start:.1f} s "
                                f"(at most {bound} s on {kind})")
        return it.set(UNKNOWN, f"gb_dgpu_report started at {start:.1f} s; no dGPU report yet ({sn.name}, uptime "
                               f"{up:.0f} s)")
    if up > DGPU_REPORT_TEST_MAX_S:
        return it.set(FAIL, f"gb_dgpu_report never started ({sn.name}, uptime {up:.0f} s): gb_bootlog requests it at "
                            "t40 (vendor.gb.dgpu_report=1)")
    return it.set(UNKNOWN, f"no dGPU report yet ({sn.name}, uptime {up:.0f} s): gb_bootlog requests it at t40")


@check("dgpu_report", "gb_dgpu_report written (dGPU report, NVIDIA payload check)", "row 17; checklist 14, 18", "SW")
def c_dgpu_report(a, it):
    kl = _dgpu_klines(a.b.snaps)
    reps, stale = [], []
    for sn in a.b.snaps:
        for (ln, hdr), body in sn.reports:
            for ln2, s in body:
                if s.startswith("== gb_dgpu_report report"):
                    why = _dgpu_report_stale(a, sn, s, kl)
                    (stale if why else reps).append((sn, ln2, s, body, why))
                    break
    for sn, ln2, s, _b, why in stale[-1:]:
        it.add([sn.ev(ln2, s)])
        it.note(f"{sn.name} holds an earlier boot's dGPU report ({why}): not counted for this boot")
    if not reps:
        return _dgpu_report_missing(a, it, kl)
    sn, ln, s, body, _w = reps[-1]
    it.add([sn.ev(ln, s)])
    kinds = sorted({re.sub(r"-\d+\.txt$", "", h.rsplit("/", 1)[-1]) for x in a.b.snaps for (_l, h), _b in x.reports})
    other = [k for k in kinds if k != "dgpu"]
    if other:
        it.note("other gb reports in the snapshots: " + ", ".join(other) + " (load/bind: the nouveau start; test: "
                "the [NVIDIA test] entry, checklist 18; selftest: the NVK self-test)")
    fw =[(l2, x) for l2, x in body if x.startswith("firmware ")]
    for l2, x in fw:
        it.add([sn.ev(l2, x)])
    for l2, x in fw:
        m = re.match(r"^firmware (\S+) payload=(\S*) ok=(\d+) mismatch=(\d+) missing=(\d+)", x)
        if m:
            payload, okn, mis, miss = m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5))
            if mis:
                return it.set(FAIL, f"{mis} NVIDIA firmware file(s) differ from tu117.sha256")
            if payload and miss:
                return it.set(FAIL, f"NVIDIA payload {payload} mounted but {miss} firmware file(s) missing")
            if not payload:
                it.note("the NVIDIA payload (gb\\dgpu-<krel>.erofs) was not mounted when the report ran "
                        "(normal before a nouveau load, and always without a dGPU)")
            else:
                it.note(f"NVIDIA payload {payload}: {okn} firmware files match")
    it.set(PASS, f"report present ({s.replace('== ', '')})")


@check("gfx_switching", "Graphics switching state (gb_power, GbGfxDriver, ANV/ANGLE props)", "row 17; checklist 15",
       "SW")
def c_gfx(a, it):
    want = {"ro.gfx.driver.0": "gb.sb3.gfxdriver", "ro.hardware.vulkan": "intel", "ro.hardware.egl": "angle",
            "vendor.gb.power.api": "1", "init.svc.gb_power": "running"}
    bad = []
    seen = 0
    for k, w in want.items():
        v, e = a.b.prop(k)
        if e:
            it.add([e])
            seen += 1
        if v != w:
            bad.append(f"{k}={v if v is not None else '(unset)'} (want {w})")
    act, e1 = a.b.prop("vendor.gb.gfx_active")
    rsn, e2 = a.b.prop("vendor.gb.gfx_reason")
    it.add([x for x in (e1, e2) if x])
    if act not in ("intel", "nvidia"):
        bad.append(f"vendor.gb.gfx_active={act or '(unset)'}")
    gm = a.power_json.get("get-mode")
    if gm:
        it.add([gm[1]])
        if not gm[0].get("ok"):
            bad.append("gb_power get-mode failed")
    elif seen:
        bad.append("no gb_power get-mode reply on /dev/socket/gb_power")
    hwui = None
    for sn in reversed(a.b.summary_snaps()):
        hwui = sn.sline("HWUI Vulkan queueCount abort lines:")
        if hwui:
            break
    if hwui:
        it.add([hwui])
        m = re.search(r"lines: (\d+)", hwui.text)
        if m and int(m.group(1)) > 0:
            bad.append(f"HWUI aborted {m.group(1)} times on a one-queue Vulkan device")
    nvk = a.b.lc(r"NVK: \d+ NVIDIA physical device")
    if nvk:
        it.add(nvk[:1]).note("gb_gfxdriver put NVK first for an app (checklist 15)")
    if not seen:
        return it.set(UNKNOWN, "no getprop section")
    if bad:
        return it.set(FAIL, "; ".join(bad))
    it.set(PASS, f"gfx_active={act} (reason {rsn or '-'}); GbGfxDriver registered; gb_power answers")


# ----------------------------------------------------------------------------------------- system services
@check("gatekeeper", "Gatekeeper HAL (gb_gatekeeper registered)", "wave 4 (lock screen)", "SW")
def c_gatekeeper(a, it):
    reg = a.b.dm(r"gb_gatekeeper: registered android\.hardware\.gatekeeper\.IGatekeeper/default")
    start = a.b.dm(r"gb_gatekeeper: gb_gatekeeper \S+ starting")
    exits = a.b.dm(r"init: Service 'gb_gatekeeper' \(pid \d+\) (exited|received signal|killed)")
    errs = a.b.dm(r"gb_gatekeeper: E ")
    nodev = a.b.lc(r"gatekeeperd: Could not find Gatekeeper device")
    svc, se = a.b.prop("init.svc.gb_gatekeeper")
    it.add(start[:1]).add(reg[:1]).add(errs[:2]).add(exits[:2]).add(nodev[:1]).add([se] if se else [])
    if not (reg or start or exits or errs or svc):
        return it.set(UNKNOWN, "no gb_gatekeeper trace: this image predates the Gatekeeper HAL (wave 4)")
    if reg and len(exits) < 3:
        return it.set(PASS, "IGatekeeper/default registered" + (f"; init.svc.gb_gatekeeper={svc}" if svc else ""))
    if reg:
        return it.set(FAIL, f"registered, but the service exited {len(exits)} times")
    if exits or errs or nodev:
        return it.set(FAIL, "IGatekeeper/default never registered" + (f" ({len(exits)} exits)" if exits else ""))
    if svc == "running":
        return it.set(UNKNOWN, "running, but its registration line is not in the kernel log (wrapped?)")
    it.set(FAIL, f"init.svc.gb_gatekeeper={svc}")


@check("zram", "zram swap active", "native fstab / mmd", "SW")
def c_zram(a, it):
    swaps = None
    for sn in reversed(a.b.summary_snaps()):
        swaps = sn.sline("swaps:")
        if swaps:
            break
    add = a.b.dm(r"Adding (\d+)k swap on (/dev/block/zram\d+)")
    bad = a.b.dm(r"(zram|swapon).*\b(fail(ed)?|error)\b", re.I) + a.b.lc(r"(zram|swapon).*\b(fail(ed)?|error)\b",
                                                                         re.I)
    bad = [x for x in bad if "avc:" not in x[0].text]
    v, pe = a.b.prop("mmd.zram.enabled")
    it.add([swaps] if swaps else []).add(add[:1]).add(bad[:3]).add([pe] if pe else [])
    active = bool(add) or bool(swaps and "zram" in swaps.text)
    if active:
        return it.set(PASS, "swap on zram" + (f" ({int(add[0][1].group(1)) // 1024} MiB)" if add else ""))
    if bad:
        return it.set(FAIL, "zram/swapon errors")
    if not _completed(a)[0]:
        return it.set(UNKNOWN, "no swap yet: mmd sets zram up only after the boot completes, and it did not")
    it.set(FAIL, "the boot completed but no zram swap is active" + (f" (mmd.zram.enabled={v})" if v else ""))


VNNI_RE = re.compile(r"^emulated (\d+) vex128 (\d+) vex256 (\d+) word (\d+) byte (\d+) irqon (\d+) efault (\d+)")


def _vnni_blocks(a):
    out = []
    for sn in a.b.summary_snaps():
        blk = sn.sblock("gb_vnni")
        if blk is not None:
            out.append((sn, blk))
    return out


@check("vnni", "AVX-VNNI #UD emulator counter (irqon == emulated)", "checklist 24; kernel/K15.md", "SW")
def c_vnni(a, it):
    blks = _vnni_blocks(a)
    if not blks:
        return it.set(UNKNOWN, "no '-- gb_vnni' summary block (log written before wave 3)")
    series = []
    for sn, blk in blks:
        for e in blk:
            m = VNNI_RE.match(e.text)
            if m:
                series.append((sn, e, m))
                break
    sn, blk = blks[-1]
    if not series:
        it.add(blk[:2])
        if any("No such file" in e.text for e in blk):
            if any(a.b.dm(r"Linux version " + re.escape(k)) for k in (K15_RELEASE, K16_RELEASE)):
                return it.set(FAIL, "k15/k16 kernel but /sys/kernel/gb_vnni/summary is missing")
            return it.set(UNKNOWN, "/sys/kernel/gb_vnni/summary is missing (kernel before k15)")
        return it.set(UNKNOWN, "the gb_vnni block has no counter line")
    sn, e, m = series[-1]
    it.add([e])
    em, irqon, efault = int(m.group(1)), int(m.group(6)), int(m.group(7))
    it.note("emulated by snapshot: " + ", ".join(f"t{x.t}={int(mm.group(1)):,}" for x, _e, mm in series))
    comms = Counter()
    adler = 0
    for b2 in blk:
        mm = re.match(r"^comm=(.*) rip=([0-9a-fA-F]+) count=(\d+)", b2.text)
        if mm:
            comms[mm.group(1)] += int(mm.group(3))
            off = int(mm.group(2), 16) & 0xFFF
            if off == 0x067 or 0x145 <= off <= 0x1EA:
                adler += 1
    if comms:
        it.note("recent emulation sites by comm: " + ", ".join(f"{c} ({n})" for c, n in comms.most_common(5)))
    if adler:
        it.note(f"{adler} recent RIP(s) at adler32_simd_ page offsets (0x067, 0x145-0x1ea): stock libz still "
                "emulating? (see libz_bind)")
    if efault:
        it.note(f"efault={efault}: emulations that faulted on user memory")
    if irqon != em:
        return it.set(FAIL, f"irqon {irqon} != emulated {em} (k15 contract: every emulation runs IRQs-on)")
    it.set(PASS, f"emulated {em:,}, irqon {irqon:,} (equal)")


@check("libz_bind", "VNNI libz patch: fingerprint-gated libz.so binds fired", "wave 5; vnni-perf INTEGRATION.md",
       "SW")
def c_libz(a, it):
    proc = a.b.dm(r"processing action \(([^()]*\bro\.(system|vendor)\.build\.fingerprint=[^()]*)\) from "
                  r"\((/odm/[^)]*)\)")
    # (wave 6, w6prep vnni-hardening) the vnni block's one ungated action, at `on init`: the gbvnni tmpfs goes read-only
    # for every mount of it. "No such file or directory" = no /odm/vnni this boot (the OTA gate after a Google OTA, or
    # gbinit's fallback; the gated actions report their own failures): not a bind failure. Any other error: the binds
    # work, but the patched libz stays writable (the wave-5 state). (Wave 7) lapis 276's init logs no ENOENT of a
    # builtin below its DEBUG log level, so without /odm/vnni there is no tail line at all (wave-6 forced OTA-gate
    # boots); the ENOENT case only matters for a build that does log it
    tail = a.b.dm(r"init: Command 'mount tmpfs gbvnni /odm/vnni remount ro'.*failed: (.*)")
    tail_ev = {e for e, _m in tail}
    tail_bad = [(e, m) for e, m in tail if "No such file or directory" not in m.group(1)]
    fail = [(e, m) for e, m in a.b.dm(r"init: Command '[^']*(/odm/vnni|libz\.so)[^']*'.*failed") if e not in tail_ev]
    parse = a.b.dm(r"(unexported property trigger.*fingerprint|fingerprint.*unexported property trigger|"
                   r"/odm/\S*gb\.rc: \d+: .*(vnni|libz|fingerprint))")
    it.add(proc[:2]).add(fail[:3]).add(parse[:2]).add(tail[:1])
    if "gb.vnni=0" in a.cmdline:
        it.add([a.cmdline_ev])
        return it.set(UNKNOWN, "gb.vnni=0 on the command line: the binds are switched off for this boot")
    if fail or parse:
        return it.set(FAIL, "a libz bind action failed or was rejected (the stock libz stays under emulation)")
    if tail and not tail_bad:
        it.note("the gbvnni read-only remount (wave 6) found no /odm/vnni: removed this boot (OTA gate, gbinit "
                "fallback)")
    if proc:
        parts = sorted({m.group(2) for _e, m in proc})
        if len(parts) < 2:
            it.note(f"only the {parts[0]} bind fired (the other partition's fingerprint did not match)")
        if tail_bad:
            return it.set(FAIL, f"the binds ran, but the gbvnni read-only remount (wave 6) failed "
                                f"({tail_bad[0][1].group(1)[:80]}): the patched libz stays writable")
        return it.set(PASS, f"{len(proc)} fingerprint-gated bind action(s) ran ({', '.join(parts)}), no failure")
    if tail_bad:
        it.note(f"the gbvnni read-only remount (wave 6) failed: {tail_bad[0][1].group(1)[:80]}")
    if not a.b.klog_from_zero:
        return it.set(UNKNOWN, "the early-init part of the kernel log is not in any snapshot")
    it.set(UNKNOWN, "no fingerprint-gated /odm action ran: an image without the wave-5 libz patch, or a gate that "
                    "does not match this build (another slot after an OTA)")


@check("fsverity", "fs-verity on /data (ext4 feature)", "checklist 24", "SW")
def c_fsverity(a, it):
    line = None
    for sn in reversed(a.b.summary_snaps()):
        line = sn.sline("fs-verity:")
        if line:
            break
    en = a.b.dm(r"Enabling ext4 verity(?: on (\S+))?")
    # (review 2026-10-29) the userdata line is the evidence; fs_mgr also logs it for metadata, often first
    ud = [x for x in en if "userdata" in (x[1].group(1) or "")]
    bad = a.b.dm(r"FS_STAT_ENABLE_VERITY_FAILED|Failed to run tune2fs")
    it.add([line] if line else []).add((ud or en)[:1]).add(bad[:2])
    if bad:
        return it.set(FAIL, "enabling verity on userdata failed")
    if line:
        if "ext4 feature present" in line.text:
            return it.set(PASS, "ext4 verity feature present")
        return it.set(FAIL, "ext4 verity feature absent")
    if ud:
        return it.set(PASS, "fs_mgr enabled ext4 verity on userdata")
    if en:
        return it.set(PASS, "fs_mgr enabled ext4 verity on " + (en[0][1].group(1) or "a partition") + " (no userdata line)")
    it.set(UNKNOWN, "no fs-verity summary line (log written before wave 3)")


@check("apgetty", "Serial login console (apgetty) off natively", "checklist 24", "SW")
def c_apgetty(a, it):
    line = None
    for sn in reversed(a.b.summary_snaps()):
        line = sn.sline("apgetty:")
        if line:
            break
    if line:
        it.add([line])
        m = re.search(r"desktop\.apgetty\.enable=(\S*) init\.svc\.apgetty=(\S*)", line.text)
        en, svc = (m.group(1), m.group(2)) if m else ("", "")
    else:
        en, e1 = a.b.prop("desktop.apgetty.enable")
        svc, e2 = a.b.prop("init.svc.apgetty")
        it.add([x for x in (e1, e2) if x])
        if en is None and svc is None:
            return it.set(UNKNOWN, "no apgetty line and no apgetty properties")
    if svc == "running" or en == "1":
        return it.set(FAIL, f"desktop.apgetty.enable={en} init.svc.apgetty={svc}")
    it.set(PASS, f"desktop.apgetty.enable={en or '(unset)'} init.svc.apgetty={svc or '(unset)'}")


@check("slotsync", "No held system update (gb_slotsync HOLD)", "checklist 24; OTA.md", "SW")
def c_slotsync(a, it):
    hold = a.b.dm(r"gb_slotsync: HOLD slot ")
    line = None
    for sn in reversed(a.b.summary_snaps()):
        line = sn.sline("gb_slotsync HOLD:")
        if line:
            break
    it.add(hold[:2]).add([line] if line else [])
    if hold or (line and line.text.split("HOLD:", 1)[1].strip()):
        return it.set(FAIL, "gb_slotsync holds a system update (kernel interface mismatch, OTA.md)")
    if line or a.b.klog_from_zero:
        return it.set(PASS, "no HOLD line")
    it.set(UNKNOWN, "no summary line and the kernel log is wrapped")


# (wave 6, OTA gate F2; ota-gating DESIGN.md 2.2) GB_BOOTLOG_SH's "ota gate:" line
OTA_LINE_RE = re.compile(r"^ota gate: state=(\S*) token=(\S*) prop=(\S*) vnni=(\S*) notifier=(\S*) booted=(.*?) "
                         r"validated=(.*)$")


def _build_id(fp):
    """The build id field of a fingerprint (brand/product/device:release/ID/incremental:type/tags), else the text."""
    parts = fp.split("/")
    return parts[3] if len(parts) >= 5 and parts[3] else (fp or "unknown")


@check("ota_gate", "Booted build is the one the image was made for (OTA gate)", "checklist 24; OTA.md", "SW")
def c_otagate(a, it):
    line = a.b.newest_sline("ota gate:")
    kl = a.b.dm(r"gb_otagate: UNVALIDATED build: booted (\S+), ESP built for (\S+)")
    pv, pe = a.b.prop("ro.vendor.gb.ota_gate")
    it.add([line] if line else []).add(kl[:1]).add([pe] if pe else [])
    m = OTA_LINE_RE.match(line.text) if line else None
    if line and not m:
        if "no state file" in line.text:
            return it.set(UNKNOWN, "no OTA gate in this image (no /odm/etc/gb/ota_state)")
        return it.set(UNKNOWN, "the 'ota gate:' line has an unknown format")
    if not m:                              # images before wave 6: the kernel line and the property only
        if kl or pv == "unvalidated":
            booted, built = (kl[-1][1].group(1), kl[-1][1].group(2)) if kl else ("?", "?")
            return it.set(FAIL, f"this boot's build {_build_id(booted)} is not the build the image was made for "
                                f"({_build_id(built)}): the VNNI libz patch was skipped; update the ESP to re-validate "
                                "(OTA.md)")
        return it.set(UNKNOWN, "no 'ota gate:' summary line (log written before wave 6) and no unvalidated sign")
    state, token, prop, vnni, notifier, booted, built = m.groups()
    it.note(f"booted {booted or '-'}; image made for {built or '-'}; token {token or '-'}; /odm/vnni {vnni}; "
            f"ro.vendor.gb.ota_gate={prop or '(unset)'}; gb_otagate {notifier or 'not started'}")
    if state == "validated":
        if prop == "unvalidated":
            return it.set(FAIL, "state validated but ro.vendor.gb.ota_gate=unvalidated is set (a packed property?)")
        return it.set(PASS, f"validated: this boot's build {_build_id(booted)} is the one the image was made for")
    if state == "off":
        return it.set(UNKNOWN, "the gate was switched off for this boot (gb.otagate=0): every injection as built")
    if state != "unvalidated":
        return it.set(FAIL, f"unexpected gate state {state!r}")
    if vnni != "absent" or prop != "unvalidated":
        return it.set(FAIL, f"unvalidated, but the gate did not take effect (/odm/vnni {vnni}, "
                            f"ro.vendor.gb.ota_gate={prop or '(unset)'})")
    if token == "force":
        return it.set(UNKNOWN, "forced with the test token gb.otagate=force: the unvalidated path took effect "
                               "(/odm/vnni removed, the property set)")
    it.set(FAIL, f"this boot's build {_build_id(booted)} is not the build the image was made for ({_build_id(built)}): "
                 "the VNNI libz patch was skipped, the rest runs as built (untested on this build); update the ESP from "
                 "the host to re-validate (OTA.md)")


@check("esp_bringup", "ESP bring-up config (gb\\gb.cfg) not used, or applied cleanly", "checklist 24; FLASHING.md 8",
       "SW")
def c_bringup(a, it):
    blk = None
    for sn in reversed(a.b.summary_snaps()):
        blk = sn.sblock("ESP bring-up")
        if blk is not None:
            break
    if blk is None:
        return it.set(UNKNOWN, "no ESP bring-up block (log written before wave 3)")
    key = [e for e in blk if re.search(r"refused|FAILED|done:|rc=|cannot", e.text)]
    it.add(key).add(blk)
    text = "\n".join(e.text for e in blk)
    if "absent (gb.overlay=1 not set)" in text:
        return it.set(PASS, "not used this boot")
    if re.search(r"refused, nothing applied|rewrite FAILED|overlay: FAILED|cannot create", text):
        return it.set(FAIL, "the bring-up config was refused or failed (FLASHING.md section 8)")
    m = re.search(r"bringup rc=(\d+)", text)
    if m and m.group(1) != "0":
        return it.set(FAIL, f"bring-up rc={m.group(1)}")
    refused = [e.text for e in blk if "refused" in e.text]
    if refused:
        it.note(f"{len(refused)} single setting(s) refused (skipped, the rest applied)")
    m2 = re.search(r"done: (\d+) change", text)
    it.set(PASS, f"used this boot (gb.overlay=1): {m2.group(1) if m2 else '?'} change(s) applied, rc=0")


# --------------------------------------------------------------------------------------------------- health
FW_OPTIONAL = [
    (r"^i915/.*_(guc|huc)_", "i915 GuC/HuC are optional (row 14)"),
    (r"^regulatory\.db", "cfg80211 regulatory database (optional)"),
    (r"^iwl-debug-yoyo\.bin$", "iwlwifi debug config (optional)"),
    (r"^iwlwifi-.*\.pnvm$", "iwlwifi PNVM (not used by the AX201)"),
    (r"^rtl_nic/", "r8152 PHY patches (optional, row 35)"),
    (r"^intel/ibt-.*\.ddc$", "Bluetooth DDC parameters (optional)"),
]


@check("firmware", "Missing firmware (Direct firmware load ... failed)", "rows 14, 29, 37-39, 17; checklist 23",
       "SW")
def c_firmware(a, it):
    hits = a.b.dm(r"Direct firmware load for (\S+) failed with error (-?\d+)")
    hits += a.b.dm(r"Failed to load Intel firmware file (\S+)")
    hits += a.b.dm(r"Requesting signed firmware (\S+) failed")
    names = OrderedDict()
    for e, m in hits:
        names.setdefault(m.group(1).rstrip(":,"), []).append(e)
    iwl_loaded = bool(a.b.dm(r"iwlwifi \S+: loaded firmware version"))
    # (review 2026-10-29) iwlwifi always asks for its newest API first; only "no suitable firmware found" means none
    # loaded. Without the "loaded" line (a cut-short log) a failed .ucode request alone is no failure.
    iwl_none = bool(a.b.dm(r"iwlwifi .*no suitable firmware found"))
    required, optional = [], []
    for n in names:
        why = next((w for rx, w in FW_OPTIONAL if re.search(rx, n)), None)
        if why is None and not iwl_none and re.match(r"^iwlwifi-.*\.ucode$", n):
            why = ("iwlwifi API fallback (a later version loaded)" if iwl_loaded else
                   "iwlwifi tries its newest API first; whether one loaded: see the wifi item")
        (optional if why else required).append((n, why))
    for n, _w in required:
        it.add(names[n][:1])
    for n, _w in optional:
        it.add(names[n][:1])
    for n, w in optional:
        it.note(f"optional: {n} ({w})")
    if required:
        return it.set(FAIL, f"{len(required)} firmware file(s) not found: " + ", ".join(n for n, _w in required))
    if not a.b.dmesg:
        return it.set(UNKNOWN, "no kernel log")
    it.set(PASS, "no required firmware file failed to load" + (f" ({len(optional)} optional)" if optional else ""))


HW_DEPENDENT_PROC = re.compile(r"surfaceflinger|allocator|composer|camera|dras|audio|bootanim|minigbm|"
                               r"gpu|nvk|iptsd|sensors", re.I)


@check("crashes", "Crashes: tombstones, fatal signals, crash loops, kernel oops/WARN", "all rows", "SW")
def c_crashes(a, it):
    procs = dict(a.crash_procs)
    # (wave 9 hw-native1) lapis's /vendor/etc/init/powertool.rc runs 'powertool set-soc-slider <value>' (exec_background)
    # on every vendor.powerhal.socslider change (the power HAL's powerhint.json SocSlider node). Ice Lake has no
    # processor_thermal_soc_slider, so powertool logs "Setting SocSlider is not supported" and exits 1 before it writes
    # anything: expected on the Surface Book 3, not a crash (only with that logcat line as proof)
    if a.b.lc(r"powertool\s*:\s+Setting SocSlider is not supported"):
        for p in [p for p in procs if "/vendor/bin/powertool set-soc-slider" in p]:
            it.note(f"{p}: expected on the Surface Book 3 (powertool: 'Setting SocSlider is not supported': Ice Lake "
                    "has no SoC slider; nothing is written), not counted")
            del procs[p]
    for e, _m in a.kernel_issues[:4]:
        it.add([e])
    for p, evs in procs.items():
        it.add(evs[:1])
    it.add(a.tombstones[:2])
    if procs:
        it.note("crashing: " + ", ".join(f"{p} ({len(evs)} lines)" for p, evs in procs.items()))
    if a.tombstones:
        it.note(f"{len(a.tombstones)} tombstone(s) written")
    if a.kernel_issues:
        return it.set(FAIL, f"{len(a.kernel_issues)} kernel oops/BUG/WARNING line(s)")
    if not procs and not a.tombstones:
        if not a.b.logcat:
            return it.set(UNKNOWN, "no logcat in the snapshots")
        return it.set(PASS, "no tombstone, fatal signal, crash loop or kernel warning")
    if not a.judge_hw:
        other = [p for p in procs if not HW_DEPENDENT_PROC.search(p)]
        if not other:
            # (wave-5 integration) camera 04's C-1 rule: a camera provider that exits because its HAL cannot be loaded
            # crashes from software (already FAIL in the cameras item), not from the missing hardware
            cam = next((i for i in a.items if i.id == "cameras"), None)
            if cam and cam.status == FAIL and "does not load" in cam.reason and any("camera" in p for p in procs):
                it.note("the camera provider exits because the camera HAL does not load (software, C-1: see the "
                        "cameras item), not because the hardware is missing")
            return it.set(UNKNOWN, f"{a.env_label}: only services that need the Surface hardware crashed ("
                                   + ", ".join(procs) + "), as expected without it")
        return it.set(FAIL, "crashes not explained by the missing hardware: " + ", ".join(other))
    it.set(FAIL, "crashes: " + ", ".join(list(procs)[:6]) + (f" ({len(a.tombstones)} tombstones)"
                                                              if a.tombstones else ""))


AVC_RE = re.compile(r"avc:\s+denied\s+\{([^}]*)\}.*?scontext=(\S+)\s+tcontext=(\S+)\s+tclass=(\S+)"
                    r"(?:.*?permissive=(\d))?")
OWN_RE = re.compile(r"gb_|gb\.|/odm|iptsd|vnni|gatekeeper|nouveau|dgpu|gfxdriver|sb3|surface|vendor_init|libz")
LOGCAT_TAG_RE = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d+\s+\d+\s+\d+\s+[VDIWEFS]\s+(\S+?)\s*:")   # (wave 10) the tag


@check("selinux", "SELinux denials (enforced ones touching our components)", "all rows", "SW")
def c_selinux(a, it):
    seen, rows = set(), []
    for e in a.b.dmesg + a.b.logcat:
        m = AVC_RE.search(e.text)
        if not m:
            continue
        # (wave 10 misc-suspend) one audit record = one serial: the kernel log writes 'audit(<time>:<serial>)', logd's
        # logcat copies 'audit(0.0:<serial>)' (twice: tag auditd with comm=, and the process's own tag without it), so
        # the time part is not part of the key (it counted each record seen in both logs twice: 3957 for 2549 records
        # on the second native boot)
        au = re.search(r"audit\(\d+\.\d+:(\d+)\)", e.text)
        key = ("audit", au.group(1)) if au else e.text
        if key in seen:
            continue
        seen.add(key)
        comm = re.search(r'comm="([^"]*)"', e.text)
        tag = LOGCAT_TAG_RE.match(e.text) if not comm else None
        obj = re.search(r'(?:path|name)="([^"]*)"', e.text)
        rows.append((e, m.group(1).strip(), m.group(2), m.group(3), m.group(4), m.group(5),
                     comm.group(1) if comm else (tag.group(1) if tag else ""), obj.group(1) if obj else ""))
    mode = None
    sn = a.b.newest_with("dgpu")
    if sn:
        for ln, s in sn.lines("dgpu"):
            if s.strip() in ("Enforcing", "Permissive"):
                mode = sn.ev(ln, s)
    if mode:
        it.add([mode])
    if not rows:
        if not a.b.dmesg:
            return it.set(UNKNOWN, "no kernel log")
        return it.set(PASS, "no avc denial")
    enforced = [r for r in rows if r[5] == "0"]
    permissive = [r for r in rows if r[5] == "1"]
    groups = Counter((r[2], r[3], r[4], r[1], r[6]) for r in rows)
    it.note(f"{len(rows)} distinct denials: {len(enforced)} enforced (permissive=0), {len(permissive)} permissive=1")
    for (sc, tc, cl, perm, comm), n in groups.most_common(6):
        it.note(f"{n} x {{ {perm} }} comm={comm or '?'} {sc} -> {tc} {cl}")
    if mode and mode.text.strip() == "Permissive":
        it.note("getenforce: Permissive")
    ours = [r for r in enforced if OWN_RE.search(" ".join((r[2], r[3], r[6], r[7])))]
    ours_p = [r for r in permissive if OWN_RE.search(" ".join((r[2], r[3], r[6], r[7])))]
    if ours_p:
        it.note(f"{len(ours_p)} permissive=1 denial(s) touch our components: allowed only because that domain is "
                "permissive")
        # (wave 10 misc-suspend) by command: our services run in u:r:shell:s0 and every domain is permissive
        # (inject\odm_sepolicy.cil typepermissive list; gb-power.md 3.5, W2-15), so these are expected; the same access
        # is logged again whenever the kernel's AVC cache drops the entry (e.g. gb_iptsd's 2 s suspend_stats poll)
        byc = Counter(r[6] or "?" for r in ours_p)
        it.note("ours by command: " + ", ".join(f"{c} {n}" for c, n in byc.most_common(6))
                + (f", {len(byc) - 6} more" if len(byc) > 6 else "") + " (accepted noise while the domains stay "
                "permissive; each record counted once)")
    it.add([r[0] for r in ours][:MAX_EV])
    if ours:
        return it.set(FAIL, f"{len(ours)} enforced denial(s) touch our components (gb_*, /odm, iptsd, ...)")
    it.add([r[0] for r in enforced][:3] or [rows[0][0]])
    if enforced:
        return it.set(PASS, f"{len(enforced)} enforced denial(s), none touching our components")
    it.set(PASS, "no enforced (permissive=0) denial")


def _completed(a):
    """(True/False, Ev or None, seconds or None) for sys.boot_completed."""
    act = a.b.dm(r"init: processing action \(sys\.boot_completed=1")
    if act:
        return True, act[0][0], float(KTS_RE.match(act[0][0].text).group(1))
    for sn in a.b.snaps:
        if sn.boot_completed == "1":
            ln, s = sn.hdr["uptime"]
            return True, sn.ev(ln, s), None
    return False, None, None


@check("boot_time", "Boot completed (sys.boot_completed) and when", "all rows", "SW")
def c_boot(a, it):
    done, e, t = _completed(a)
    sf = a.b.lc(r"Boot is finished \((\d+) ms\)")
    it.add([e] if e else []).add(sf[:1])
    last = a.b.latest
    if done:
        # (wave 10 misc-suspend) gb_bootlog counts boots on the stick's ESP from 1: boot 1 is the first start after
        # writing the image, which sets up /data and user 10 and optimises the apps (first-boot dex2oat), so its
        # boot_completed comes about 50 s later than on the next starts (first native boots: boot 1 at 89.8 s and
        # 92.1 s, boots 2/3 at 33.3/42.8 s), while the boot animation ends at about the same time (38.4 / 38.2 s)
        anim = a.b.dm(r"init: Service 'bootanim' \(pid \d+\) exited")
        if anim:
            it.add(anim[:1]).note(f"the boot animation ended at {float(KTS_RE.match(anim[0][0].text).group(1)):.1f} s")
        if a.b.n == 1 and t is not None and t > 60:
            it.note("boot 1 (the first start after writing the image: /data and user 10 are set up and the apps "
                    "optimised): about 90 s is normal here; compare boot times from boot 2 on (33-43 s on the first "
                    "native boots)")
        if t is not None:
            if t > 120:
                it.note(f"slow: {t:.0f} s")
            return it.set(PASS, f"sys.boot_completed=1 at {t:.1f} s (kernel time)" +
                          (f"; SurfaceFlinger boot finished after {int(sf[0][1].group(1)) / 1000:.1f} s" if sf else ""))
        return it.set(PASS, f"boot_completed=1 by {e.f}")
    ln, s = last.hdr.get("uptime", (0, ""))
    if s:
        it.add([last.ev(ln, s)])
    up = last.uptime or 0
    if a.env == "vmware":
        return it.set(UNKNOWN, "VMware: the native tree never completes a boot here (SurfaceFlinger has no Intel GPU,"
                               f" K15.md); last snapshot at {up:.0f} s")
    if up < 180:
        return it.set(UNKNOWN, f"not completed by the last snapshot at {up:.0f} s (too early to judge)")
    it.set(FAIL, f"sys.boot_completed never set (last snapshot at {up:.0f} s)")


# (wave 10, w10prep battery-userspace) The battery as the lapis health HAL shows it. The desktop HAL logs every update
# to logcat ("Battery Debug: Level=N% Status=S CurrentUA=I AC=A USB=U batteryChargeTimeToFullNowSeconds=E
# PluggedIn=P ..." and, unplugged, "Time to shutdown sec: N"); its AOSP BatteryMonitor part logs the raw values to the
# kernel log ("healthd: battery l=.. st=.. chg=a" or "healthd: battery none chg=.."); gb_power logs its combined
# battery on every level/status/AC change ("gb_power: battery N% <status> ac=A | BAT1 ..."). Second native boot (USB
# e0825ee1): Level=0% for a 54 % composite (the HAL's charge_now = test_power's capacity over a uAh charge_full) and
# "battery none" + chg=a for ~20 ms every 10 s (gb_dgpu_report's "gb_power -o" re-ran the old start defaults).
HAL_BD_RE = re.compile(r"Battery Debug: Level=(-?\d+)% Status=(\d+) CurrentUA=(-?\d+) AC=(\d) USB=(\d) "
                       r"batteryChargeTimeToFullNowSeconds=(-?\d+) PluggedIn=(\d)")
HAL_TTS_RE = re.compile(r"android\.hardware\.health-service\.android-desktop: Time to shutdown sec: (-?\d+)")
HAL_HD_RE = re.compile(r"healthd: battery (?:l=(-?\d+) v=-?\d+ t=\S+ h=\d+ st=(\d+)(?: c=(-?\d+))?(?: fc=-?\d+)?"
                       r"(?: cc=-?\d+)?|none) chg=(\S*)$")
HAL_GBP_RE = re.compile(r"gb_power: (?:battery (\d+)% (\S+) ac=(\d)|no battery present \(.*\) ac=(\d))")
HAL_LCTS_RE = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d\.\d+)\s")
HAL_FLAP_S = 2.0          # two AC (or presence) changes closer than this: flapping, not a plug event
HAL_SETTLE_S = 2.0        # a HAL Level may still show the previous composite this long after gb_power's change


def _hal_kts(e):
    m = KTS_RE.match(e.text)
    return float(m.group(1)) if m else None


def _hal_wall(e):
    m = HAL_LCTS_RE.match(e.text)
    if not m:
        return None
    mo, d, hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), float(m.group(5))
    return (((mo * 31 + d) * 24 + hh) * 60 + mm) * 60 + ss


def _hal_offset(bd, hd):
    """(logcat wall - kernel) offset in s: each desktop-HAL update logs its 'Battery Debug' line and, right after it,
    the 'healthd:' kmsg line; the mode (50 ms bins) of the differences of the pairs whose status and AC agree and
    whose raw level equals the Level or whose current agrees (non-zero); None without a pair."""
    cands = []
    for eb, mb in bd:
        tb = _hal_wall(eb)
        if tb is None:
            continue
        lv, st, cur, ac = int(mb.group(1)), int(mb.group(2)), int(mb.group(3)), mb.group(4) == "1"
        for eh, mh in hd:
            if mh.group(1) is None:
                continue
            th = _hal_kts(eh)
            hl, hst, hc, hac = int(mh.group(1)), int(mh.group(2)), int(mh.group(3) or 0), "a" in mh.group(4)
            if th is None or hst != st or hac != ac:
                continue
            if hl == lv or (cur != 0 and (hc == cur or (st == 3 and hc == -abs(cur)))):
                cands.append(tb - th)
    if not cands:
        return None
    bins = Counter(round(c / 0.05) for c in cands)
    best = max(bins.items(), key=lambda kv: (kv[1], -abs(kv[0])))[0]
    near = sorted(c for c in cands if abs(c / 0.05 - best) <= 1)
    return near[len(near) // 2]


def _hal_flaps(seq):
    """[(t1, t2)] where a value changed and changed again within HAL_FLAP_S; seq = [(t, value)] in time order."""
    ch = [seq[i][0] for i in range(1, len(seq)) if seq[i][1] != seq[i - 1][1]]
    return [(ch[i], ch[i + 1]) for i in range(len(ch) - 1) if ch[i + 1] - ch[i] < HAL_FLAP_S]


@check("battery_hal", "Battery level and charger state as the health HAL shows them (wave 10)",
       "row 42; w10prep battery-userspace", "HW")
def c_battery_hal(a, it):
    gbp = [(e, m, _hal_kts(e)) for e, m in a.b.dm(HAL_GBP_RE.pattern)]
    gbp = sorted(((e, m, t) for e, m, t in gbp if t is not None), key=lambda g: g[2])   # dm() is file order
    hd = a.b.dm(HAL_HD_RE.pattern)
    bd = a.b.lc(HAL_BD_RE.pattern)
    tts = a.b.lc(HAL_TTS_RE.pattern)
    packs = [(t, int(m.group(1)), m.group(2), int(m.group(3))) for e, m, t in gbp if m.group(1) is not None]
    if not a.judge_hw:
        return a.not_here(it, "gb_power reports no battery here" if not packs else "")
    if not packs:
        it.add([e for e, _m, _t in gbp[-1:]])
        return it.set(UNKNOWN, "no 'gb_power: battery N%' line: no pack was combined (see item batteries)")
    it.add([e for e, m, _t in gbp if m.group(1) is not None][-1:])
    problems = []
    first_pack = packs[0][0]

    def composite_at(t):
        """(the composite in force at kernel time t, the one before it, the time of the last change) or None."""
        cur = prev = None
        tc = -1e9
        for tg, e_m in ((g[2], g[1]) for g in gbp):
            if tg > t + 0.05:
                break
            v = int(e_m.group(1)) if e_m.group(1) is not None else None
            if v != cur:
                prev, cur, tc = cur, v, tg
        return cur, prev, tc

    # 1. "battery none" (present=false) while gb_power has a pack
    none = []
    for e, m in hd:
        t = _hal_kts(e)
        if m.group(1) is None and t is not None and t > first_pack:
            cur, _p, _tc = composite_at(t)
            if cur is not None:
                none.append((e, t))
    if none:
        it.add([none[0][0]])
        times = sorted(t for _e, t in none)
        step = [round(times[i + 1] - times[i]) for i in range(len(times) - 1) if times[i + 1] - times[i] > 1.0]
        cad = Counter(step).most_common(1)
        problems.append(f"{len(none)} 'healthd: battery none' line(s) while gb_power had a pack (present flapping"
                        + (f", every ~{cad[0][0]} s" if cad and cad[0][1] >= 2 else "") + ")")
        if cad and cad[0][0] == 10 and cad[0][1] >= 2:
            it.note("a 10 s cadence is gb_dgpu_report.sh's 30 'gb_power -o' power samples: before wave 10 each run "
                    "re-wrote the start defaults present=false / ac_online=on / current 0 (w10prep battery-userspace)")
    # 2. AC flapping in the kernel log (healthd chg=) and in logcat (AC=, PluggedIn=)
    kseq = sorted((t, "a" in m.group(4)) for e, m in hd for t in [_hal_kts(e)] if t is not None)
    kfl = _hal_flaps(kseq)
    if kfl:
        problems.append(f"AC flapping in the kernel log: {len(kfl)} chg= change pair(s) < {HAL_FLAP_S:.0f} s apart "
                        f"(first at {kfl[0][0]:.3f} s)")
        it.add([e for e, m in hd if _hal_kts(e) in (kfl[0][0], kfl[0][1])][:2])
    lseq = sorted((t, m.group(4) + m.group(7)) for e, m in bd for t in [_hal_wall(e)] if t is not None)
    lfl = _hal_flaps(lseq)
    if lfl:
        problems.append(f"AC/PluggedIn flapping in the HAL's logcat: {len(lfl)} change pair(s) "
                        f"< {HAL_FLAP_S:.0f} s apart")
    gac = [(t, ac) for t, _p, _s, ac in packs]
    gch = sum(1 for i in range(1, len(gac)) if gac[i][1] != gac[i - 1][1])
    kch = sum(1 for i in range(1, len(kseq)) if kseq[i][1] != kseq[i - 1][1] and kseq[i][0] > first_pack)
    # 3. Level against gb_power's composite
    off = _hal_offset(bd, hd)
    bad_lv, zero_lv, n_lv = [], [], 0
    allv = sorted({p for _t, p, _s, _a in packs})
    for e, m in bd:
        lv = int(m.group(1))
        if off is not None:
            tw = _hal_wall(e)
            tk = tw - off if tw is not None else None
            if tk is None or tk < first_pack + 0.5:
                continue
            cur, prev, tc = composite_at(tk)
            if cur is None:
                continue
            n_lv += 1
            ok = abs(lv - cur) <= 1 or (prev is not None and tk - tc < HAL_SETTLE_S and abs(lv - prev) <= 1)
            ref = cur
        else:
            n_lv += 1
            ok = any(abs(lv - p) <= 1 for p in allv)
            ref = allv[-1] if allv else None
        if not ok:
            bad_lv.append(e)
            if lv == 0 and ref is not None and ref >= 2:
                zero_lv.append(e)
    if bad_lv:
        it.add(bad_lv[:2])
        if zero_lv:
            problems.append(f"the HAL shows 0 % while gb_power's composite is {min(allv)}-{max(allv)} % "
                            f"({len(zero_lv)} of {n_lv} 'Battery Debug' lines; before wave 10 test_power carried uAh "
                            "while the HAL reads charge_now = capacity)")
        else:
            problems.append(f"{len(bad_lv)} of {n_lv} 'Battery Debug' Level values differ from gb_power's composite "
                            f"by more than 1 % (composites {allv})")
    # 4. estimates: time to shutdown < 60 s with a >= 2 % battery; time to full 0..59 s while not Full
    short = [(e, int(m.group(1))) for e, m in tts if 0 <= int(m.group(1)) < 60]
    if short and min(allv) >= 2:
        it.add([short[0][0]])
        problems.append(f"{len(short)} 'Time to shutdown sec' value(s) under 60 s with a {min(allv)}-{max(allv)} % "
                        "battery")
    eta = [e for e, m in bd if 0 <= int(m.group(6)) < 60 and m.group(2) != "5"]
    if eta:
        it.add(eta[:1])
        problems.append(f"{len(eta)} time-to-full value(s) under 60 s while not Full")
    it.note(f"gb_power composite values {allv}; AC changes: gb_power {gch}, healthd chg= {kch} after the first pack"
            + ("; Battery Debug lines paired with the healthd kmsg lines (Level judged against the composite of "
               "that moment)" if off is not None else "; no Battery Debug / healthd pairing (Level compared with every "
               "composite of the boot)"))
    if problems:
        return it.set(FAIL, "; ".join(problems))
    if not bd:
        return it.set(UNKNOWN, "no 'Battery Debug' line in logcat (logcat missing or cut): the HAL's Level is not "
                               "visible; no 'battery none' and no AC flapping in the kernel log")
    it.add(bd[-1:])
    it.set(PASS, f"the HAL's Level matches gb_power's composite in {n_lv} 'Battery Debug' line(s) "
                 f"({sorted({int(m.group(1)) for _e, m in bd})}); no 'battery none' with a pack; no AC flapping")


MANUAL = [
    ("1", "Keyboard keys (Fn row, media, volume, brightness)", "keyboard item: device present, not a gamepad"),
    ("2, 20", "Touchpad tap, scroll, palm rejection, quick flicks", "touchpad item: device present"),
    ("5", "USB-C hub / monitor: data, charging, DP picture", "/sys/class/typec is expected empty"),
    ("6", "SD card reader VID:PID (internal install)", "-"),
    ("7", "SurfaceDock Ethernet, DP, audio", "r8152 lines in the kernel log when docked"),
    ("8", "Surface Pen pairing and top button", "-"),
    ("12", "Clock before/after Wi-Fi (RTC in local time)", "-"),
    ("13", "Reboot and shutdown complete", "the next boot's snapshots exist"),
    ("15", "NVIDIA app on NVK, HWUI on Intel", "gfx_switching notes ('NVK: ... first')"),
    ("16", "Detach/reattach with and without an NVIDIA app", "dtx and dgpu_power items, gb_power lines"),
    ("18", "[NVIDIA test] boot entry", "test-<N>.txt report in the snapshots"),
]


# --------------------------------------------------------------------------------------------------- output
def _fmt_ev(e, maxline=MAX_LINE):
    t = redact(e.text)
    if len(t) > maxline:
        t = t[:maxline] + " [...]"
    return f"{e.f}:{e.ln}  {t}"


def _cell(s):
    return redact(s).replace("|", "\\|").replace("\n", " ")


def render_md(a, src_label, other_boots):
    b, items = a.b, a.items
    c = Counter(i.status for i in items)
    def _snap(s):
        if "uptime" not in s.hdr:
            return f"t{s.t} (empty or cut short before == uptime)"
        cut = "" if "dumpsys_input" in s.hdr else ", cut short"
        if s.uptime is None:
            return f"t{s.t}{cut}"
        return f"t{s.t} (uptime {s.uptime:.0f} s{', boot_completed=1' if s.boot_completed == '1' else ''}{cut})"
    snaps = ", ".join(_snap(s) for s in b.snaps)
    kern = next((i for i in items if i.id == "kernel"), None)
    L = []
    L.append(f"# Native boot triage: boot {b.n}")
    L.append("")
    L.append(f"Generated by `analyze_gblog.py` {VERSION} from `{src_label}`. Read-only; serial numbers, MAC and e-mail "
             "addresses, SSIDs, account names and the boot partition UUID are redacted. Checklist and row numbers "
             "refer to `docs/DEVICE-STATUS.md` (\"Hardware validation checklist\").")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Result | **{c.get(PASS, 0)} PASS, {c.get(FAIL, 0)} FAIL, {c.get(UNKNOWN, 0)} UNKNOWN** |")
    L.append(f"| Environment | {_cell(a.env_label)} (dmi: {_cell(a.dmi or '-')})"
             + ("; HW items cannot be judged here and are UNKNOWN" if not a.judge_hw else "") + " |")
    if kern:
        L.append(f"| Kernel | {_cell(kern.reason or '-')} |")
    L.append(f"| Snapshots | {snaps}"
             + (f"; audio diag: {', '.join(x.name for x in b.audio)}" if b.audio else "") + " |")
    if b.klog_start is not None:
        L.append(f"| Kernel log | earliest snapshot starts at {b.klog_start:.1f} s"
                 + (" (boot messages present)" if b.klog_from_zero else " (wrapped: early boot lines lost)") + " |")
    else:
        L.append("| Kernel log | none |")
    base = a.base[0] or "unknown"
    L.append(f"| Keyboard base (DTX) | {_cell(base)} |")
    if other_boots:
        L.append(f"| Other boots in the folder | {', '.join(str(x) for x in other_boots)} (use --boot N) |")
    L.append("")
    L.append("## Checklist")
    L.append("")
    L.append("| # | Item | Kind | DEVICE-STATUS | Status | Why |")
    L.append("|---|---|---|---|---|---|")
    for n, i in enumerate(items, 1):
        st = f"**{i.status}**" if i.status == FAIL else i.status
        L.append(f"| {n} | [{_cell(i.title)}](#{n}-{i.id}) | {i.kind} | {_cell(i.ref)} | {st} | {_cell(i.reason)} |")
    L.append("")
    L.append("Kind: HW = needs the Surface Book 3 hardware (UNKNOWN in VMware or on another machine); SW = software "
             "that runs the same everywhere.")
    L.append("")
    L.append("## Evidence")
    for n, i in enumerate(items, 1):
        L.append("")
        L.append(f'<a id="{n}-{i.id}"></a>')
        L.append(f"### {n}. {i.title}: {i.status}")
        L.append("")
        L.append(f"{redact(i.reason)}")
        if i.notes:
            L.append("")
            for x in i.notes:
                L.append(f"* {redact(x)}")
        L.append("")
        if i.ev:
            L.append("```text")
            for e in i.ev[:MAX_EV]:
                L.append(_fmt_ev(e, i.maxline))
            if len(i.ev) > MAX_EV:
                L.append(f"... {len(i.ev) - MAX_EV} more line(s)")
            L.append("```")
        else:
            L.append("_No matching log line._")
    L.append("")
    L.append("## Not visible in the log (test by hand)")
    L.append("")
    L.append("| Checklist | What | Related evidence |")
    L.append("|---|---|---|")
    for k, w, h in MANUAL:
        L.append(f"| {k} | {w} | {h} |")
    L.append("")
    return "\n".join(L)


def to_json(a, src_label):
    return {
        "tool": "analyze_gblog.py", "version": VERSION, "source": src_label, "boot": a.b.n, "env": a.env,
        "dmi": redact(a.dmi), "snapshots": [s.name for s in a.b.snaps], "audio": [x.name for x in a.b.audio],
        "base": a.base[0], "counts": dict(Counter(i.status for i in a.items)),
        "items": [{"id": i.id, "title": i.title, "ref": i.ref, "kind": i.kind, "status": i.status,
                   "reason": redact(i.reason), "notes": [redact(x) for x in i.notes],
                   "evidence": [{"file": e.f, "line": e.ln, "text": redact(e.text)[:i.maxline]} for e in i.ev]}
                  for i in a.items],
    }


def load_boot(src, boot_no=None):
    """(Boot, [other boot numbers], folder) or raises ValueError."""
    snaps, audio, folder = find_files(src)
    if not snaps:
        raise ValueError(f"no boot<N>-t<T>.txt snapshot in {src}")
    boots = sorted({n for n, _t, _p in snaps})
    n = boot_no if boot_no is not None else boots[-1]
    if n not in boots:
        raise ValueError(f"boot {n} not found (have {', '.join(map(str, boots))})")
    ss = [Snap(p, bn, t) for bn, t, p in snaps if bn == n]
    aa = [AudioDiag(p, bn, t) for bn, t, p in audio if bn == n]
    return Boot(n, ss, aa), [x for x in boots if x != n], folder


def analyze(src, boot_no=None, env=None, expect_kernel=EXPECT_KERNEL):
    """Analysis object (with .items) for one boot of a gblog folder."""
    boot, others, folder = load_boot(src, boot_no)
    boot.next_tail = next_boot_tail(src, boot.n) if boot.n + 1 in others else []   # (wave 10) the suspend item
    a = Analysis(boot, env_override=env, expect_kernel=expect_kernel)
    a.run()
    a.others, a.folder = others, folder
    return a


def label_for(folder):
    parts = os.path.normpath(os.path.abspath(folder)).split(os.sep)
    return "\\".join(parts[-3:])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Map a Googlebook SB3 native gblog folder to the DEVICE-STATUS "
                                             "hardware checklist (TRIAGE.md).")
    ap.add_argument("src", help="the gblog folder (or a folder containing gblog\\, or one boot<N>-t<T>.txt)")
    ap.add_argument("-o", "--out", default="TRIAGE.md", help="Markdown output (default: TRIAGE.md in the current "
                                                             "directory; never written into the gblog folder unless "
                                                             "you name it)")
    ap.add_argument("--json", help="also write the result as JSON")
    ap.add_argument("--boot", type=int, help="boot number N (default: the newest in the folder)")
    ap.add_argument("--env", choices=("surface", "vmware", "other", "unknown"),
                    help="override the environment detected from == dmi")
    ap.add_argument("--expect-kernel", default=EXPECT_KERNEL, help=f"expected uname -r (default k16: {EXPECT_KERNEL})")
    ap.add_argument("--strict", action="store_true", help="exit 1 when any item is FAIL")
    args = ap.parse_args(argv)
    try:
        a = analyze(args.src, args.boot, args.env, args.expect_kernel)
    except (ValueError, OSError) as exc:
        print(f"analyze_gblog: {exc}", file=sys.stderr)
        return 2
    label = label_for(a.folder)
    md = render_md(a, label, a.others)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(to_json(a, label), fh, indent=1)
            fh.write("\n")
    c = Counter(i.status for i in a.items)
    print(f"boot {a.b.n} ({a.env_label}): {c.get(PASS, 0)} PASS, {c.get(FAIL, 0)} FAIL, {c.get(UNKNOWN, 0)} UNKNOWN"
          f" -> {args.out}")
    for i in a.items:
        if i.status == FAIL:
            print(f"  FAIL {i.id}: {redact(i.reason)}")
    return 1 if args.strict and c.get(FAIL) else 0


if __name__ == "__main__":
    sys.exit(main())
