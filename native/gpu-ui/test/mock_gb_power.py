#!/usr/bin/env python3
"""Mock of gb_power's control socket (INTERFACES.md sections 9 and, v1.2, 23-26) for GbGpu's host tests. Run with -I.

  python3 -I mock_gb_power.py --socket <path> --ctl <path> [--uid N] [--ping-ms 30000] [--idle-ms 30000]
                              [--sim-ms 300]

Implements the 9.1 framing (request <= 255 bytes incl. newline, one JSON reply line <= 4096 bytes, answers in order,
max 8 connections with the 9th getting EBUSY, max 4 watchers, idle close after --idle-ms, 8 KiB output buffer),
the 9.2 roles from SO_PEERCRED (or --uid, like the real host build's GB_POWER_TEST_UID), every 9.3 and 26.1 command
with the 9.4/26.4 error codes and the real daemon's messages, the 9.5 status object (v1.2 fields) in its key order,
the 23.2 derivation of gfx_active/gfx_reason, the 26.2 set-mode effects, the 24.4 opt-in computation, `watch` updates
and pings, and the 9.6 and 26.3 golden transcripts byte for byte.

The state is a plain "world" dict; tests change it through the control socket (--ctl), one JSON object per line:
  {"set": {...}}          merge into the world (any status input, plus booted/stack/cap/kill/boot_nv/prefs/...)
  {"get": true}           reply with the world (and the derived graphics fields)
  {"uid": N}              peer uid used for new connections (null = real SO_PEERCRED)
  {"restart": seconds}    close the listener and every client, re-listen after the delay (a gb_power restart)
  {"stall": true|false}   accept and read requests but never answer (deadline tests)
  {"raw": [lines]}        answer the next non-hello requests with these raw lines (malformed-reply tests)
  {"push": "line"}        send a raw line to every watcher
  {"log": true}           reply with the request lines received so far
  {"quit": true}          exit
Every control request gets one JSON reply line.
"""
import argparse
import json
import os
import re
import selectors
import socket
import struct
import sys
import time

SERVER = "gb_power 1"
MAXCL, MAXWATCH, CL_OUT = 8, 4, 8192
PKG_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+\Z")
SU = {"root", "shell", "ui"}
ALL = SU | {"compute-svc", "compute-client", "other"}
# command -> (roles, takes an argument)  (INTERFACES 9.3 + 26.1; same table as gb_power.c g_cmds)
CMDS = {
    "hello": (ALL, False), "status": (SU, False), "watch": (SU, False), "holders": (SU, False),
    "enable": (SU, False), "disable": (SU, False), "detach-request": (SU, False), "report": (SU, False),
    "stop-compute": (SU, False), "close-holders": (SU, True), "compute-list": (SU, False),
    "compute-allow": (SU, True), "compute-deny": (SU, True), "compute-start": (SU | {"compute-client"}, False),
    "compute-state": ({"compute-svc"}, True),
    # v1.2 (26.1): root, shell, ui only
    "get-mode": (SU, False), "set-mode": (SU, True), "set-app-policy": (SU, True), "set-app-intel": (SU, True),
    "set-app-nvidia": (SU, True), "get-apps": (SU, True), "apps-begin": (SU, True), "apps-add": (SU, True),
    "apps-commit": (SU, True), "run-selftest": (SU, False), "list-holders": (SU, False),
    "close-holders-and-detach": (SU, True), "ui-caps": (SU, True),
}
STATUS_KEYS = ["state", "n", "holders", "mode", "result", "persist", "clean_boots", "probation", "used", "foreign",
               "wedged", "fault_reason", "bdf", "driver", "runtime_status", "power_state", "refcnt", "latch", "base",
               "dtx", "compute", "payload", "gfxdriver",
               "gfx_mode", "gfx_policy", "gfx_active", "gfx_reason", "gfx_ok", "gfx_test", "gfx_test_t", "gfx_cap",
               "gfx_seq", "pending", "detach_ref", "apps_sync"]
BUSY_RE = re.compile(r"busy:([1-9][0-9]{0,2})\Z")
MODES = ("auto", "intel", "nvidia")
PREFMAX, CATMAX, OPTIN_MAX, OPTIN_FILE_MAX = 128, 512, 512, 65536
VALID_NV_MODES = ("default", "auto", "persist", "once", "test", "disabled")


def now_ms():
    return int(time.monotonic() * 1000)


def dumps(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=True)


def default_world():
    return {
        # public status fields (9.5)
        "state": "off", "holders": [], "mode": "default", "result": "idle", "clean_boots": 0,
        "used": 0, "foreign": 0, "wedged": 0, "fault_reason": "", "bdf": "0000:02:00.0", "driver": "",
        "runtime_status": "suspended", "power_state": "D3cold", "refcnt": -1, "latch": "unlocked",
        "base": "attached", "dtx": "ready", "compute": "stopped", "payload": "legacy",
        # hidden inputs (v1.1)
        "booted": 1, "stack": 1, "ui_appid": 10123, "apk": 1,
        "auto_running": 0, "noload": 0, "keep": 0,  # init.svc.gb_nouveau running, /dev/gb_dgpu/noload, .../keep
        "packages": {"gb.sb3.gpu": 10123, "com.example.game": 10500, "com.example.chat": 10501},
        "allow": [], "compute_running": 0, "decision": 0, "reports": 0, "detach_requests": 0,
        # hidden inputs (v1.2, 23.1)
        "gfx_mode": "",       # persist.vendor.gb.gfx_mode ("" = unset = auto)
        "gfx_apps": "",       # persist.vendor.gb.gfx_apps ("" = games)
        "cap": 1,             # native + stack + ro.gfx.driver.0 + GbGfxDriver.apk + gfx_build_id
        "kill": 0,            # gb.nouveau=0 / module_blacklist on the command line
        "boot_nv": "",        # last gb.nouveau= value: "once", "test" or ""
        "recovered": 0,       # keep/noload/forced Intel this boot (crashloop state counts too)
        "gfx_ok": -1,         # the validated record: 1 pass, 0 fail, -2 error, -1 none or stale
        "test_t": 0,          # the record's t
        "test_running": 0,    # gb_gfx_selftest runs
        "selftest_result": "pass",  # what a simulated run ends with (pass/fail/error)
        "selftest_ms": 400,
        "st_finishing": 0,    # the 10 s marker check after a run (run-selftest -> EAGAIN)
        "clean_seen": 1,      # this binding reached its clean mark
        "rebind": 0,          # the rebind guard watches (a load window)
        "pend_detach": 0,     # close-holders-and-detach: pending detach
        "pend_outcome": "confirm",  # simulated end of a pending detach: confirm | timeout
        "detach_ref": 0,
        "prefs": {},          # pkg -> intel|nvidia (gfx_app_prefs)
        "catalog": None,      # [[pkg, "g"|"a"], ...]; None = no gfx_catalog file
        "catalog_gen": -1,    # the packages.list generation the catalog was committed against
        "pkgs_gen": 0,        # bumped on every packages.list change (tests: install/remove)
        "data_ok": 1,         # /data/vendor/gb exists
        "ui_notify": 0,
        "mark_ms": 30,        # time the worker needs for OP_MARK before intel -> nvidia (23.3)
        "gfx_override": {},   # forced values of derived status fields (tests of unknown values)
    }


class Client:
    def __init__(self, sock, uid, pid, role):
        self.sock, self.uid, self.pid, self.role = sock, uid, pid, role
        self.inbuf, self.out = b"", b""
        self.watch = self.closing = self.dead = self.compute = False
        self.last = now_ms()
        self.ping_at = 0
        self.last_st = ""
        self.st_on, self.st_max, self.st = False, 0, []   # apps-begin staging (per connection, 24.3)


class Mock:
    def __init__(self, a):
        self.a = a
        self.w = default_world()
        self.sel = selectors.DefaultSelector()
        self.clients = []
        self.listener = None
        self.ctl_listener = None
        self.ctl_clients = {}
        self.uid_override = a.uid
        self.stall = False
        self.raw = []
        self.log = []
        self.timers = []  # (due_ms, fn)
        self.relisten_at = None
        self.quit = False
        # graphics state (23.2/23.3): what gb_power published, and the pending OP_MARK
        self.active = "intel"
        self.reason = ""
        self.seq = 0
        self.mark_due = None
        self.pend_due = None
        self.gfx_eval()

    # ---- world helpers
    def busy_n(self):
        m = BUSY_RE.match(self.w["state"])
        return int(m.group(1)) if m else 0

    def gfx_mode(self):
        m = self.w["gfx_mode"]
        return m if m in MODES else "auto"

    def policy(self):
        return "all" if self.w["gfx_apps"] == "all" else "games"

    def dtx_ready(self):
        return self.w["dtx"] != "n/a"

    def gfx_attached(self):
        return self.dtx_ready() and self.w["base"] in ("attached", "not-feasible")

    def base_out(self):
        return self.w["base"] if self.dtx_ready() else "unknown"

    def bound(self):
        return self.w["driver"] == "nouveau"

    def gfx_bound(self):
        return self.bound() and (self.w["state"] == "standby" or self.busy_n() > 0)

    def recovered(self):
        w = self.w
        return bool(w["recovered"] or w["keep"] or w["noload"] or w["state"] == "crashloop")

    def pending(self):
        if self.w["pend_detach"]:
            return "detach"
        if self.gfx_mode() == "intel" and self.bound() and self.w["refcnt"] > 0:
            return "intel"
        return ""

    def derive(self):
        """INTERFACES 23.2, first matching row wins."""
        w, st = self.w, self.w["state"]
        if not w["cap"]:
            return "intel", "nopkg"
        if not self.dtx_ready():
            return "intel", "nodtx"
        if w["kill"]:
            return "intel", "kill"
        if w["boot_nv"] == "test":
            return "intel", "test"
        if self.recovered():
            return "intel", "crashloop"
        if st == "wedged" or w["wedged"]:
            return "intel", "wedged"
        if st == "fault":
            return "intel", "fault"
        if self.gfx_mode() == "intel":
            return "intel", "mode"
        if not self.gfx_attached():
            return "intel", "detached"
        if st == "detaching" or w["dtx"] != "ready" or w["pend_detach"]:
            return "intel", "detaching"
        if st == "loading" or w["rebind"]:
            return "intel", "loading"
        if not self.gfx_bound():
            return "intel", "nodriver"
        ok = w["gfx_ok"]
        if ok == -1:
            return "intel", "untested"
        if ok == -2:
            return "intel", "testerror"
        if ok == 0:
            return "intel", "selftest"
        return "nvidia", "ok"

    def gfx_eval(self):
        """23.3: intel -> nvidia only after the (simulated) OP_MARK; nvidia -> intel at once."""
        act, why = self.derive()
        t = now_ms()
        if act == "nvidia" and self.active != "nvidia":
            if self.mark_due is None:
                self.mark_due = t + int(self.w["mark_ms"])
            if t >= self.mark_due:
                self.mark_due = None
                self.active, self.reason = "nvidia", "ok"
                self.seq += 1
                self.w["foreign"] = 1
            else:
                self.reason = "loading"
            return
        self.mark_due = None
        if act == "intel" and self.active == "nvidia":
            self.active = "intel"
            self.seq += 1
        self.reason = why

    def gfx_force_intel(self):
        self.mark_due = None
        if self.active == "nvidia":
            self.active = "intel"
            self.seq += 1

    def gfx_ok(self):
        return self.w["gfx_ok"] if self.w["gfx_ok"] in (1, 0, -2) else -1

    def gfx_test(self):
        if self.w["test_running"]:
            return "running"
        ok = self.gfx_ok()
        if ok != -1:
            return {1: "pass", 0: "fail", -2: "error"}[ok]
        return "pending" if self.w["cap"] and self.gfx_mode() != "intel" and self.gfx_attached() else "none"

    def apps_sync(self):
        return 1 if self.w["catalog"] is not None and self.w["catalog_gen"] == self.w["pkgs_gen"] else 0

    def opt_in(self):
        """INTERFACES 24.4 (gfx_apps_eval): sorted list of opted-in packages."""
        w = self.w
        prefs = w["prefs"]
        pri = {}
        for pkg, cls in (w["catalog"] or []):
            if self.policy() == "all" or cls == "g":
                pri.setdefault(pkg, 1 if cls == "g" else 2)
        for pkg, ch in prefs.items():
            if ch == "nvidia":
                pri[pkg] = 0
        cand = sorted((p for p in pri if prefs.get(p) != "intel" and p in w["packages"]), key=lambda p: (pri[p], p))
        keep, size = [], 0
        for p in cand:
            if len(keep) >= OPTIN_MAX or size + len(p) + 1 > OPTIN_FILE_MAX:
                continue
            size += len(p) + 1
            keep.append(p)
        return sorted(keep)

    def status_obj(self, cmd, with_t):
        w = self.w
        o = {"v": 1, "ok": True, "cmd": cmd}
        derived = {
            "n": self.busy_n(),
            "holders": w["holders"] if w["refcnt"] > 0 and w["driver"] == "nouveau" else [],
            "mode": w["mode"] if w["mode"] in VALID_NV_MODES else "",
            "persist": 1 if self.gfx_mode() != "intel" else 0,
            "probation": 1 if w["mode"] == "auto" and w["clean_boots"] < 3 else 0,
            "base": self.base_out(),
            "gfxdriver": 1 if w["cap"] else 0,
            "gfx_mode": self.gfx_mode(), "gfx_policy": self.policy(), "gfx_active": self.active,
            "gfx_reason": self.reason, "gfx_ok": self.gfx_ok(), "gfx_test": self.gfx_test(),
            "gfx_test_t": w["test_t"] if self.gfx_ok() != -1 else 0, "gfx_cap": 1 if w["cap"] else 0,
            "gfx_seq": self.seq, "pending": self.pending(), "detach_ref": w["detach_ref"],
            "apps_sync": self.apps_sync(),
        }
        derived.update(w["gfx_override"])
        for k in STATUS_KEYS:
            o[k] = derived[k] if k in derived else w[k]
        if with_t:
            o["t_ms"] = now_ms()
        return o

    def status_line(self, cmd="status", with_t=True):
        return dumps(self.status_obj(cmd, with_t))

    def set_busy(self):
        hs = self.w["holders"]
        if self.w["driver"] != "nouveau":
            return
        pids = sorted({h["pid"] for h in hs})
        self.w["refcnt"] = len(hs)
        self.w["state"] = "busy:%d" % min(999, max(1, len(pids))) if hs else "standby"

    # ---- sockets
    def listen(self):
        p = self.a.socket
        if os.path.exists(p):
            os.unlink(p)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(p)
        os.chmod(p, 0o666)
        s.listen(16)
        s.setblocking(False)
        self.listener = s
        self.sel.register(s, selectors.EVENT_READ, ("listen", None))

    def unlisten(self):
        if self.listener is not None:
            self.sel.unregister(self.listener)
            self.listener.close()
            self.listener = None
        for c in list(self.clients):
            c.dead = True
        self.sweep()
        if os.path.exists(self.a.socket):
            os.unlink(self.a.socket)

    def role_of(self, uid, pid):
        w = self.w
        if uid == 0:
            return "root"
        if uid == 2000:
            return "shell"
        if uid < 0:
            return "other"
        app = uid % 100000
        if w["apk"] and 10000 <= w["ui_appid"] <= 19999 and app == w["ui_appid"]:
            return "ui"
        if uid == 9999:
            return "compute-svc"
        if any(e["appId"] == app for e in w["allow"]):
            return "compute-client"
        return "other"

    def accept(self):
        while True:
            try:
                s, _ = self.listener.accept()
            except BlockingIOError:
                return
            if len(self.clients) >= MAXCL:
                try:
                    s.send(b'{"v":1,"ok":false,"cmd":"","err":"EBUSY","msg":"too many connections"}\n')
                except OSError:
                    pass
                s.close()
                continue
            pid, uid, _ = struct.unpack("3i", s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                            struct.calcsize("3i")))
            if self.uid_override is not None:
                uid = self.uid_override
            s.setblocking(False)
            c = Client(s, uid, pid, self.role_of(uid, pid))
            self.clients.append(c)
            self.sel.register(s, selectors.EVENT_READ, ("client", c))

    def send(self, c, line):
        if c.dead:
            return
        data = (line + "\n").encode("ascii")
        if len(c.out) + len(data) > CL_OUT:
            c.dead = True
            return
        c.out += data
        self.flush(c)

    def flush(self, c):
        while c.out and not c.dead:
            try:
                n = c.sock.send(c.out)
            except BlockingIOError:
                return
            except OSError:
                c.dead = True
                return
            c.out = c.out[n:]
        if c.closing and not c.out:
            c.dead = True

    def sweep(self):
        for c in [x for x in self.clients if x.dead]:
            self.clients.remove(c)
            try:
                self.sel.unregister(c.sock)
            except (KeyError, ValueError):
                pass
            c.sock.close()
            if c.compute:
                self.w["compute"] = "starting" if self.w["compute_running"] else "stopped"

    def reply_ok(self, c, cmd, extra=None):
        o = {"v": 1, "ok": True, "cmd": cmd}
        if extra:
            o.update(extra)
        self.send(c, dumps(o))

    def reply_err(self, c, cmd, code, msg):
        self.send(c, dumps({"v": 1, "ok": False, "cmd": cmd, "err": code, "msg": msg}))

    def read_client(self, c):
        try:
            d = c.sock.recv(512)
        except BlockingIOError:
            return
        except OSError:
            c.dead = True
            return
        if not d:
            c.dead = True
            return
        if c.watch or c.closing:
            return
        for b in d:
            if c.dead or c.closing or c.watch:
                break
            if b == 0x0A:
                line, c.inbuf = c.inbuf, b""
                c.last = now_ms()
                self.line(c, line)
                continue
            if len(c.inbuf) >= 254:
                self.reply_err(c, "", "E2BIG", "request too long")
                c.closing = True
                self.flush(c)
                break
            c.inbuf += bytes([b])

    # ---- request handling (same validation order as gb_power.c cl_line)
    def line(self, c, raw):
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        bad = not raw or raw[:1] == b" " or raw[-1:] == b" " or b"  " in raw or any(x < 0x20 or x > 0x7E for x in raw)
        text = raw.decode("latin-1")
        name, _, arg = text.partition(" ")
        if " " in text and " " in arg:
            bad = True
        if not bad:
            self.log.append({"role": c.role, "line": text})
        if self.stall:
            return
        if bad:
            self.reply_err(c, "", "EINVAL", "bad request")
            return
        if self.raw and name != "hello":
            self.send(c, self.raw.pop(0))
            return
        arg = arg if " " in text else None
        if name not in CMDS:
            self.reply_err(c, name[:64], "ENOSYS", "unknown command")
            return
        roles, takes = CMDS[name]
        if c.role not in roles:
            self.reply_err(c, name, "EPERM", "not allowed")
            return
        if not takes and arg is not None:
            self.reply_err(c, name, "EINVAL", "unexpected argument")
            return
        self.gfx_eval()
        getattr(self, "cmd_" + name.replace("-", "_"))(c, arg)
        self.gfx_eval()
        self.watch_update()

    def cmd_hello(self, c, a):
        self.reply_ok(c, "hello", {"api": 1, "role": c.role, "uid": c.uid, "server": SERVER})

    def cmd_status(self, c, a):
        self.send(c, self.status_line())

    def cmd_watch(self, c, a):
        if sum(1 for x in self.clients if x.watch and not x.dead) >= MAXWATCH:
            self.reply_err(c, "watch", "EBUSY", "watch limit")
            return
        c.watch = True
        c.ping_at = now_ms() + self.a.ping_ms
        c.last_st = self.status_line(with_t=False)
        self.send(c, self.status_line())

    def holders_reply(self, c, cmd):
        if self.w["refcnt"] < 0:
            self.reply_err(c, cmd, "ENODEV", "nouveau not loaded")
            return
        self.reply_ok(c, cmd, {"holders": self.w["holders"]})

    def cmd_holders(self, c, a):
        self.holders_reply(c, "holders")

    def cmd_list_holders(self, c, a):
        self.holders_reply(c, "list-holders")

    # ---- graphics mode (26.1, 26.2)
    def set_mode_effect(self, m):
        w = self.w
        if w["booted"] != 1 or w["boot_nv"] in ("once", "test") or w["kill"] or w["wedged"]:
            return "next-boot", 0
        if m != "intel" and (w["noload"] or w["keep"]):
            return "next-boot", 0
        if m == "intel" and w["keep"]:
            return "next-boot", 0
        if m != "intel" and not self.gfx_attached():
            return "next-attach", 0
        if m == "intel" and self.bound() and w["refcnt"] > 0:
            return "pending", max(1, len(w["holders"]))
        return "now", 0

    def set_mode_apply(self, m):
        eff, n = self.set_mode_effect(m)
        if m == "intel":
            self.gfx_force_intel()
            self.w["test_running"] = 0
        self.w["gfx_mode"] = m
        self.gfx_eval()
        return eff, n

    def cmd_set_mode(self, c, a):
        if a not in MODES:
            self.reply_err(c, "set-mode", "EINVAL", "bad mode")
            return
        if not self.w["stack"]:
            self.reply_err(c, "set-mode", "ENODEV", "no NVIDIA driver on this image")
            return
        eff, n = self.set_mode_apply(a)
        self.reply_ok(c, "set-mode", {"mode": self.gfx_mode(), "active": self.active, "effect": eff, "n": n})

    def cmd_enable(self, c, a):
        w = self.w
        if not w["stack"]:
            self.reply_err(c, "enable", "ENODEV", "no NVIDIA driver on this image")
            return
        if w["wedged"]:
            self.reply_err(c, "enable", "EBUSY", "restart required")
            return
        eff, _ = self.set_mode_apply("auto")
        if eff == "now" and w["state"] == "off" and self.gfx_attached():
            self.at(self.a.sim_ms, self.sim_loading)   # mock only: the rc trigger's gb_nouveau loads the driver
        self.reply_ok(c, "enable", {"effect": "next-boot" if eff == "next-boot" else "now"})

    def sim_loading(self):
        self.w.update({"state": "loading", "result": "loading"})
        self.at(self.a.sim_ms, self.sim_loaded)

    def sim_loaded(self):
        self.w.update({"state": "standby", "result": "clean", "driver": "nouveau", "refcnt": 0, "used": 1,
                       "runtime_status": "suspended", "power_state": "D3cold", "latch": "locked", "mode": "auto"})

    def cmd_disable(self, c, a):
        w = self.w
        eff, _ = self.set_mode_apply("intel")
        if eff != "next-boot":
            if w["state"] == "standby":
                self.at(self.a.sim_ms, lambda: self.w.update(
                    {"state": "off", "result": "off-done", "driver": "", "refcnt": -1, "latch": "unlocked"}))
            elif self.busy_n():
                w["result"] = "off-busy:%d" % self.busy_n()
        self.reply_ok(c, "disable", {"effect": "next-boot" if eff == "next-boot" else "now"})

    def cmd_get_mode(self, c, a):
        ok = self.gfx_ok()
        self.reply_ok(c, "get-mode", {
            "mode": self.gfx_mode(), "policy": self.policy(), "active": self.active, "reason": self.reason,
            "gfx_ok": ok, "test": self.gfx_test(), "test_t": self.w["test_t"] if ok != -1 else 0,
            "cap": 1 if self.w["cap"] else 0, "base": self.base_out(), "pending": self.pending(),
            "apps_sync": self.apps_sync(), "prefs_n": len(self.w["prefs"]), "opt_in_n": len(self.opt_in())})

    def cmd_set_app_policy(self, c, a):
        if a not in ("games", "all"):
            self.reply_err(c, "set-app-policy", "EINVAL", "bad argument")
            return
        if not self.w["cap"]:
            self.reply_err(c, "set-app-policy", "ENODEV", "no NVIDIA app graphics on this image")
            return
        self.w["gfx_apps"] = a
        self.reply_ok(c, "set-app-policy", {"policy": a, "opt_in_n": len(self.opt_in()), "effect": "restart-apps"})

    def set_app_choice(self, c, a, cmd, choice):
        pkg, colon, v = (a or "").rpartition(":")
        if not colon or len(pkg) >= 160 or v not in ("0", "1") or not (len(pkg) <= 127 and PKG_RE.match(pkg)):
            self.reply_err(c, cmd, "EINVAL", "bad argument")
            return
        if not self.w["cap"]:
            self.reply_err(c, cmd, "ENODEV", "no NVIDIA app graphics on this image")
            return
        if not self.w["data_ok"]:
            self.reply_err(c, cmd, "EIO", "cannot read gfx_app_prefs")
            return
        prefs = self.w["prefs"]
        if v == "1":
            if pkg not in self.w["packages"]:
                self.reply_err(c, cmd, "ENOENT", "package not installed")
                return
            if pkg not in prefs and len(prefs) >= PREFMAX:
                self.reply_err(c, cmd, "EBUSY", "too many choices")
                return
            prefs[pkg] = choice
        elif prefs.get(pkg) == choice:
            del prefs[pkg]
        self.reply_ok(c, cmd, {"pkg": pkg, "choice": prefs.get(pkg, "default"), "effect": "restart-app"})

    def cmd_set_app_intel(self, c, a):
        self.set_app_choice(c, a, "set-app-intel", "intel")

    def cmd_set_app_nvidia(self, c, a):
        self.set_app_choice(c, a, "set-app-nvidia", "nvidia")

    def cmd_get_apps(self, c, a):
        start = 0
        if a is not None:
            if not a.isdigit() or not a.isascii() or len(a) > 3 or int(a) > PREFMAX:
                self.reply_err(c, "get-apps", "EINVAL", "bad argument")
                return
            start = int(a)
        prefs = sorted(self.w["prefs"].items())
        items, size, i = [], 0, start
        while i < len(prefs):
            e = dumps({"pkg": prefs[i][0], "choice": prefs[i][1]})
            el = len(e) + (1 if i > start else 0)
            if size + el > 3300:
                break
            size += el
            items.append({"pkg": prefs[i][0], "choice": prefs[i][1]})
            i += 1
        self.reply_ok(c, "get-apps", {"policy": self.policy(), "total": len(prefs), "start": start,
                                      "next": i if i < len(prefs) else -1, "prefs": items})

    @staticmethod
    def parse_count(a, mx):
        if a is None or not a.isdigit() or not a.isascii() or len(a) > 4:
            return -1
        n = int(a)
        return n if n <= mx else -1

    def cmd_apps_begin(self, c, a):
        n = self.parse_count(a, CATMAX)
        if n < 0:
            self.reply_err(c, "apps-begin", "EINVAL", "bad argument")
            return
        c.st_on, c.st_max, c.st = True, n, []
        self.reply_ok(c, "apps-begin", {"n": n})

    def cmd_apps_add(self, c, a):
        if not c.st_on:
            self.reply_err(c, "apps-add", "EINVAL", "no apps-begin")
            return
        if not a or a[0] not in "ga" or len(a) < 3 or a[1] != ":":
            self.reply_err(c, "apps-add", "EINVAL", "bad argument")
            return
        els = a[2:].split(",")
        if any(not e or len(e) > 127 or not re.fullmatch(r"[A-Za-z0-9._]+", e) for e in els):
            self.reply_err(c, "apps-add", "EINVAL", "bad argument")
            return
        if len(c.st) + len(els) > c.st_max:
            self.reply_err(c, "apps-add", "EINVAL", "count mismatch")
            return
        c.st += [[e, a[0]] for e in els]
        self.reply_ok(c, "apps-add", {"staged": len(c.st)})

    def cmd_apps_commit(self, c, a):
        if not c.st_on:
            self.reply_err(c, "apps-commit", "EINVAL", "no apps-begin")
            return
        n = self.parse_count(a, CATMAX)
        staged = c.st
        c.st_on, c.st_max, c.st = False, 0, []
        if n < 0:
            self.reply_err(c, "apps-commit", "EINVAL", "bad argument")
            return
        if n != len(staged):
            self.reply_err(c, "apps-commit", "EINVAL", "count mismatch")
            return
        if not self.w["data_ok"]:
            self.reply_err(c, "apps-commit", "EIO", "cannot write gfx_catalog")
            return
        acc, dropped = [], 0
        for pkg, cls in staged:
            if any(x[0] == pkg for x in acc) or not (len(pkg) <= 127 and PKG_RE.match(pkg)) \
                    or pkg not in self.w["packages"]:
                dropped += 1
                continue
            acc.append([pkg, cls])
        self.w["catalog"] = sorted(acc)
        self.w["catalog_gen"] = self.w["pkgs_gen"]
        self.reply_ok(c, "apps-commit", {"n": len(acc), "dropped": dropped, "opt_in_n": len(self.opt_in()),
                                         "apps_sync": self.apps_sync()})

    def st_blocked(self):
        """gb_power.c st_blocked(): the run-selftest refusals in their order (26.1, GP-1B-D11)."""
        w, st = self.w, self.w["state"]
        if w["st_finishing"]:
            return "EAGAIN", "self-test finishing"
        if not w["cap"]:
            return "ENODEV", "no NVIDIA app graphics on this image"
        if not self.dtx_ready() or not self.gfx_attached():
            return "ENODEV", "keyboard base not attached"
        if not self.bound():
            return "ENODEV", "nouveau not bound"
        if w["test_running"]:
            return "EBUSY", "self-test running"
        if w["kill"]:
            return "EBUSY", "NVIDIA disabled for this boot"
        if w["boot_nv"] == "test":
            return "EBUSY", "NVIDIA test boot"
        if self.recovered():
            return "EBUSY", "NVIDIA switched off after a problem"
        if w["wedged"]:
            return "EBUSY", "restart required"
        if w["fault_reason"]:
            return "EBUSY", "NVIDIA GPU fault"
        if self.gfx_mode() == "intel":
            return "EBUSY", "Intel only mode"
        if w["decision"] or st == "detaching":
            return "EBUSY", "detaching"
        if w["pend_detach"]:
            return "EBUSY", "a detach is pending"
        if w["rebind"] or st == "loading" or not self.gfx_bound() or not w["clean_seen"]:
            return "EAGAIN", "NVIDIA driver starting"
        return None

    def cmd_run_selftest(self, c, a):
        b = self.st_blocked()
        if b:
            self.reply_err(c, "run-selftest", b[0], b[1])
            return
        self.w["test_running"] = 1
        self.at(int(self.w["selftest_ms"]), self.sim_selftest_done)
        self.reply_ok(c, "run-selftest", {"test": "running"})

    def sim_selftest_done(self):
        if not self.w["test_running"]:
            return
        r = self.w["selftest_result"]
        self.w.update({"test_running": 0, "gfx_ok": {"pass": 1, "fail": 0}.get(r, -2), "test_t": int(time.time())})

    def cmd_ui_caps(self, c, a):
        if a not in ("notify", "none"):
            self.reply_err(c, "ui-caps", "EINVAL", "bad argument")
            return
        self.w["ui_notify"] = 1 if a == "notify" else 0
        self.reply_ok(c, "ui-caps", {"notify": self.w["ui_notify"]})

    def cmd_detach_request(self, c, a):
        if self.w["dtx"] == "n/a":
            self.reply_err(c, "detach-request", "ENODEV", "no DTX device")
            return
        if self.w["decision"] or self.w["state"] == "detaching":
            self.reply_err(c, "detach-request", "EBUSY", "a detach decision is running")
            return
        self.w["detach_requests"] += 1
        self.reply_ok(c, "detach-request")

    def cmd_report(self, c, a):
        self.w["reports"] += 1
        self.reply_ok(c, "report")

    def cmd_stop_compute(self, c, a):
        w = self.w
        w["compute_running"] = 0
        w["compute"] = "stopped"
        w["holders"] = [h for h in w["holders"] if h.get("comm") != "gb_gpu_compute"]
        self.set_busy()
        self.reply_ok(c, "stop-compute")

    @staticmethod
    def parse_pids(a):
        pids = []
        for part in (a.split(",") if a else []):
            if not re.fullmatch(r"[0-9]+", part) or not 0 < int(part) <= 4194304 or len(pids) == 16:
                return None
            pids.append(int(part))
        return pids or None

    def close_do(self, c, pids):
        closed, refused, gone = [], [], set()
        for pid in pids:
            h = next((x for x in self.w["holders"] if x["pid"] == pid), None)
            if h is None:
                why = "not-holder"
            elif pid == c.pid or h["pkg"] == "gb.sb3.gpu":
                why = "self"
            elif h["uid"] < 0 or h["uid"] % 100000 < 10000 or h["comm"] == "gb_gpu_compute":
                why = "system"
            elif not h["pkg"] or not h["closable"]:
                why = "unknown-pkg"
            else:
                why = None
            if why:
                refused.append({"pid": pid, "reason": why})
            else:
                closed.append({"pid": pid, "pkg": h["pkg"], "user": h["uid"] // 100000})
                gone.add(pid)
        if closed:
            self.w["holders"] = [h for h in self.w["holders"] if h["pid"] not in gone]
            self.set_busy()
        return closed, refused

    def cmd_close_holders(self, c, a):
        pids = self.parse_pids(a)
        if not pids:
            self.reply_err(c, "close-holders", "EINVAL", "bad pid list")
            return
        closed, refused = self.close_do(c, pids)
        if not closed:
            self.reply_err(c, "close-holders", "ESTALE", "none of the pids is a closable holder")
            return
        self.reply_ok(c, "close-holders", {"closed": closed, "refused": refused})

    def cmd_close_holders_and_detach(self, c, a):
        cmd = "close-holders-and-detach"
        pids = self.parse_pids(a)
        if not pids:
            self.reply_err(c, cmd, "EINVAL", "bad pid list")
            return
        if not self.dtx_ready() or self.w["base"] != "attached":
            self.reply_err(c, cmd, "ENODEV", "no DTX device" if not self.dtx_ready() else "keyboard base not attached")
            return
        if self.w["decision"]:
            self.reply_err(c, cmd, "EBUSY", "a detach decision is running")
            return
        if self.w["pend_detach"]:
            self.reply_err(c, cmd, "EBUSY", "a detach is pending")
            return
        closed, refused = self.close_do(c, pids)
        if not closed:
            self.reply_err(c, cmd, "ESTALE", "none of the pids is a closable holder")
            return
        self.w["pend_detach"] = 1
        self.w["compute_running"] = 0
        self.w["compute"] = "stopped"
        self.at(self.a.sim_ms, self.sim_pending_end)
        self.reply_ok(c, cmd, {"closed": closed, "refused": refused, "detach": "pending"})

    def sim_pending_end(self):
        """23.9 pending_detach_step(): LATCH_REQUEST once nouveau is free, else detach_ref++ at the deadline."""
        w = self.w
        if not w["pend_detach"]:
            return
        w["pend_detach"] = 0
        if w["pend_outcome"] == "confirm" and w["refcnt"] <= 0:
            w["detach_requests"] += 1
            w.update({"dtx": "confirmed", "latch": "unlocked"})
        else:
            w["detach_ref"] += 1

    def cmd_compute_list(self, c, a):
        self.reply_ok(c, "compute-list", {"allow": self.w["allow"]})

    def cmd_compute_allow(self, c, a):
        if a is None or len(a) > 127 or not PKG_RE.match(a):
            self.reply_err(c, "compute-allow", "EINVAL", "bad package name")
            return
        app = self.w["packages"].get(a)
        if app is None:
            self.reply_err(c, "compute-allow", "ENOENT", "package not installed")
            return
        self.w["allow"] = [e for e in self.w["allow"] if e["pkg"] != a] + [{"appId": app, "pkg": a}]
        self.reply_ok(c, "compute-allow", {"appId": app})

    def cmd_compute_deny(self, c, a):
        if a is None or len(a) > 127 or not PKG_RE.match(a):
            self.reply_err(c, "compute-deny", "EINVAL", "bad package name")
            return
        if not any(e["pkg"] == a for e in self.w["allow"]):
            self.reply_err(c, "compute-deny", "ENOENT", "not in the allowlist")
            return
        self.w["allow"] = [e for e in self.w["allow"] if e["pkg"] != a]
        self.reply_ok(c, "compute-deny")

    def cmd_compute_start(self, c, a):
        st = self.w["state"]
        if st in ("detaching", "wedged", "fault"):
            self.reply_err(c, "compute-start", "EBUSY", st)
            return
        if st == "loading" or self.w["rebind"]:     # INTERFACES 29 R-6: not inside a load window
            self.reply_err(c, "compute-start", "EAGAIN", "NVIDIA driver starting")
            return
        if st != "standby" and not self.busy_n():
            self.reply_err(c, "compute-start", "ENODEV", "NVIDIA GPU not ready")
            return
        running = self.w["compute_running"]
        self.w["compute_running"] = 1
        if not running:
            self.w["compute"] = "starting"
        self.reply_ok(c, "compute-start", {"socket": "/dev/socket/gb_gpu_compute",
                                           "state": "running" if running else "starting"})

    def cmd_compute_state(self, c, a):
        if a not in ("idle", "loading", "loaded", "serving", "unloading"):
            self.reply_err(c, "compute-state", "EINVAL", "bad state")
            return
        if any(x is not c and x.compute and not x.dead for x in self.clients):
            self.reply_err(c, "compute-state", "EBUSY", "another compute service connection")
            return
        c.compute = True
        self.w["compute"] = a
        hold = 1 if a in ("loading", "loaded", "serving") else 0
        self.reply_ok(c, "compute-state", {"hold": hold})

    # ---- watch, timers
    def watch_update(self):
        cur = self.status_line(with_t=False)
        full = None
        t = now_ms()
        for c in self.clients:
            if not c.watch or c.dead:
                continue
            if cur != c.last_st:
                c.last_st = cur
                full = full or self.status_line()
                self.send(c, full)
            if t >= c.ping_at:
                self.send(c, '{"v":1,"ok":true,"cmd":"ping"}')
                c.ping_at = t + self.a.ping_ms
        if self.stall:
            return  # a hung daemon closes nothing either
        for c in self.clients:
            if not c.watch and not c.compute and not c.dead and t - c.last > self.a.idle_ms:  # 9.1 exemptions
                c.dead = True

    def at(self, ms, fn):
        self.timers.append((now_ms() + ms, fn))

    def run_timers(self):
        t = now_ms()
        due = [x for x in self.timers if x[0] <= t]
        self.timers = [x for x in self.timers if x[0] > t]
        for _, fn in due:
            fn()
        if self.relisten_at is not None and t >= self.relisten_at:
            self.relisten_at = None
            self.listen()

    # ---- control socket
    def ctl(self, s, line):
        try:
            o = json.loads(line)
        except ValueError:
            s.sendall(b'{"ok":false}\n')
            return
        r = {"ok": True}
        if "set" in o:
            self.w.update(o["set"])
        if "uid" in o:
            self.uid_override = o["uid"]
        if "stall" in o:
            self.stall = bool(o["stall"])
        if "raw" in o:
            self.raw = list(o["raw"])
        if "push" in o:
            for c in self.clients:
                if c.watch:
                    self.send(c, o["push"])
        self.gfx_eval()
        if o.get("get"):
            r["world"] = self.w
            r["gfx"] = {"active": self.active, "reason": self.reason, "seq": self.seq, "opt_in": self.opt_in()}
        if o.get("log"):
            r["log"] = self.log
        if "restart" in o:
            self.unlisten()
            self.relisten_at = now_ms() + int(float(o["restart"]) * 1000)
        if o.get("quit"):
            self.quit = True
        try:
            s.sendall((dumps(r) + "\n").encode("ascii"))
        except OSError:
            pass  # the test side went away; the next control connection works normally
        self.watch_update()

    def run(self):
        self.listen()
        p = self.a.ctl
        if os.path.exists(p):
            os.unlink(p)
        self.ctl_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.ctl_listener.bind(p)
        self.ctl_listener.listen(4)
        self.ctl_listener.setblocking(False)
        self.sel.register(self.ctl_listener, selectors.EVENT_READ, ("ctl-listen", None))
        print("mock_gb_power: listening on %s (ctl %s)" % (self.a.socket, p), flush=True)
        while not self.quit:
            timeout = 0.02
            for key, _ in self.sel.select(timeout):
                kind, c = key.data
                if kind == "listen":
                    self.accept()
                elif kind == "client":
                    self.read_client(c)
                elif kind == "ctl-listen":
                    s, _ = self.ctl_listener.accept()
                    self.ctl_clients[s.fileno()] = [s, b""]
                    self.sel.register(s, selectors.EVENT_READ, ("ctl", s))
                elif kind == "ctl":
                    s = c
                    ent = self.ctl_clients.get(s.fileno())
                    try:
                        d = s.recv(65536)
                    except OSError:
                        d = b""
                    if not d:
                        self.sel.unregister(s)
                        del self.ctl_clients[s.fileno()]
                        s.close()
                        continue
                    ent[1] += d
                    while b"\n" in ent[1]:
                        line, ent[1] = ent[1].split(b"\n", 1)
                        self.ctl(s, line.decode("utf-8"))
            for c in self.clients:
                self.flush(c)
            self.run_timers()
            self.gfx_eval()
            self.watch_update()
            self.sweep()
        self.unlisten()
        os.unlink(p)
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--ctl", required=True)
    ap.add_argument("--uid", type=int, default=None)
    ap.add_argument("--ping-ms", type=int, default=30000)
    ap.add_argument("--idle-ms", type=int, default=30000)
    ap.add_argument("--sim-ms", type=int, default=300)
    return Mock(ap.parse_args()).run()


if __name__ == "__main__":
    sys.exit(main())
