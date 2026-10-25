"""Mock of gb_power's control socket for the compute-service tests (INTERFACES 9; GC-08).

Implements the part the compute service uses, with gb_power's exact reply format:
  hello                -> {"v":1,"ok":true,"cmd":"hello","api":1,"role":<role>,"uid":<uid>,"server":"gb_power 1"}
  compute-state <s>    -> {"v":1,"ok":true,"cmd":"compute-state","hold":0|1}   (role compute-svc only, else EPERM)
  anything else        -> ENOSYS
Role: compute-svc for peer uid 9999 (the real daemon also checks /proc/<pid>/exe), other otherwise.
While a compute-svc connection's last state is loading/loaded/serving the mock writes "on" to the fake dGPU
power/control (the real daemon does that in its worker) and "auto" again on idle/unloading/EOF.
Idle close (INTERFACES 9.1): a connection without a request for idle_close_s seconds is closed (EOF; a held power
hold is released), except the compute-svc connection once it sent compute-state (exempt_compute, as the real
gb_power). exempt_compute=False models a stricter server (the compute service's keep-alive must then keep it open).
Every request line is recorded with the peer uid.
"""
import json
import os
import selectors
import socket
import struct
import threading
import time

HOLD_STATES = ("loading", "loaded", "serving")
STATES = ("idle", "loading", "loaded", "serving", "unloading")


class MockGbPower:
    def __init__(self, path, power_control=None, compute_uid=9999, idle_close_s=30.0, exempt_compute=True):
        self.path = path
        self.idle_close_s = idle_close_s
        self.exempt_compute = exempt_compute
        self.idle_closed = 0  # connections closed by the idle rule
        self.power_control = power_control
        self.compute_uid = compute_uid
        self.log = []  # (t, uid, line)
        self.deny = False  # reply EPERM to compute-state
        self.lock = threading.Lock()
        self.th = None
        self.running = False

    def start(self):
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.ls.bind(self.path)
        os.chmod(self.path, 0o666)
        self.ls.listen(16)
        self.ls.setblocking(False)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.ls, selectors.EVENT_READ, None)
        self.conns = {}
        self.running = True
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def stop(self):
        self.running = False
        if self.th:
            self.th.join(5)
        for s in list(self.conns):
            self._close(s)
        try:
            self.ls.close()
        except OSError:
            pass
        if os.path.exists(self.path):
            os.unlink(self.path)

    def lines(self, uid=None):
        with self.lock:
            return [l for (_, u, l) in self.log if uid is None or u == uid]

    def states(self):
        return [l.split(" ", 1)[1] for l in self.lines() if l.startswith("compute-state ")]

    def _set_power(self, v):
        if self.power_control:
            with open(self.power_control, "w") as f:
                f.write(v + "\n")

    def _close(self, s):
        c = self.conns.pop(s, None)
        try:
            self.sel.unregister(s)
        except (KeyError, ValueError):
            pass
        s.close()
        if c and c.get("hold"):
            self._set_power("auto")

    def _reply(self, s, obj):
        s.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def _handle(self, s, c, line):
        c["last"] = time.monotonic()
        with self.lock:
            self.log.append((time.monotonic(), c["uid"], line))
        cmd, _, arg = line.partition(" ")
        role = "compute-svc" if c["uid"] == self.compute_uid else "other"
        if cmd == "hello":
            self._reply(s, {"v": 1, "ok": True, "cmd": "hello", "api": 1, "role": role, "uid": c["uid"],
                            "server": "gb_power 1"})
        elif cmd == "compute-state":
            if role != "compute-svc" or self.deny:
                self._reply(s, {"v": 1, "ok": False, "cmd": "compute-state", "err": "EPERM", "msg": "not allowed"})
            elif arg not in STATES:
                self._reply(s, {"v": 1, "ok": False, "cmd": "compute-state", "err": "EINVAL", "msg": "bad state"})
            else:
                hold = arg in HOLD_STATES
                if hold != c.get("hold", False):
                    self._set_power("on" if hold else "auto")
                c["hold"] = hold
                c["compute"] = True
                self._reply(s, {"v": 1, "ok": True, "cmd": "compute-state", "hold": 1 if hold else 0})
        else:
            self._reply(s, {"v": 1, "ok": False, "cmd": cmd, "err": "ENOSYS", "msg": "unknown command"})

    def _idle(self):
        now = time.monotonic()
        for s, c in list(self.conns.items()):
            if self.exempt_compute and c.get("compute"):
                continue
            if self.idle_close_s and now - c["last"] > self.idle_close_s:
                self.idle_closed += 1
                self._close(s)

    def _run(self):
        while self.running:
            self._idle()
            for key, _ in self.sel.select(0.2):
                s = key.fileobj
                if s is self.ls:
                    try:
                        cs, _ = self.ls.accept()
                    except BlockingIOError:
                        continue
                    pid, uid, gid = struct.unpack("3i", cs.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                                      struct.calcsize("3i")))
                    cs.setblocking(True)
                    self.conns[cs] = {"uid": uid, "pid": pid, "buf": b"", "hold": False, "last": time.monotonic()}
                    self.sel.register(cs, selectors.EVENT_READ, None)
                    continue
                c = self.conns.get(s)
                if c is None:
                    continue
                try:
                    d = s.recv(4096)
                except OSError:
                    d = b""
                if not d:
                    self._close(s)
                    continue
                c["buf"] += d
                while b"\n" in c["buf"]:
                    line, c["buf"] = c["buf"].split(b"\n", 1)
                    self._handle(s, c, line.decode("ascii", "replace"))
