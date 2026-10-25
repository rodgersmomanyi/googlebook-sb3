#!/usr/bin/env python3
"""Control-socket protocol test for gb_power (INTERFACES.md sections 9 and 26) against the host build of the daemon.

It starts the real daemon loop on a fake tree, talks to it over a real AF_UNIX socket and checks the golden
transcripts (9.6 and, v1.2, 26.3), the reply format, and the limits of 9.1 (line length, 8 clients, 4 watchers,
slow reader, garbage).

Usage: python3 -I test/test_socket.py <gb_power host binary> <empty work dir>

The host build (not the Android one) honours three test-only environment variables, and only together with -r:
GB_POWER_TEST_UID (peer uid override, to pick a role), GB_POWER_PROPS_DIR (one file per system property) and
GB_POWER_TEST_DTX (answers the DTX ioctls like an EC with the base attached; the tree's /dev/surface/dtx is a FIFO).
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time

FAILS = []
CHECKS = [0]


def check(cond, what):
    CHECKS[0] += 1
    if not cond:
        FAILS.append(what)
        print("FAIL", what)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as f:
        f.write(data)


def read(path):
    try:
        with open(path, encoding="ascii") as f:
            return f.read()
    except OSError:
        return None


def make_tree(root):
    write(root + "/odm/etc/gb_hw", "native\n")
    write(root + "/odm/lib/modules/nouveau.ko", "ko")
    write(root + "/odm/app/GbGpu/GbGpu.apk", "apk")
    write(root + "/data/system/packages.list",
          "gb.sb3.gpu 10123 0 /data/user/0/gb.sb3.gpu platform:targetSdkVersion=36 none 0 1\n"
          "com.example.other 10500 0 /data/user/0/com.example.other default:targetSdkVersion=36 none 0 1\n")
    os.makedirs(root + "/sys/bus/pci/devices", exist_ok=True)
    os.makedirs(root + "/proc", exist_ok=True)
    write(root + "/proc/cmdline", "root=/dev/ram0 gb.hw=native\n")


T_BUILD_ID, T_INCR = "CL3B.260622.276.R4", "16658739"
T_KREL = "6.18.21-android17-5-g3938c8d97142-dirty-ab16302624"
T_GFX = "apk=1111 icd=2222 nvk=3333 probe=4444 pay=legacy"
T_BOOT = "0b5e4c1a-1111-4222-8333-944455556666"
GFX_PROPS = {"vendor.gb.nouveau.booted": "1", "vendor.gb.nouveau.mode": "auto", "vendor.gb.nouveau.result": "clean",
             "ro.gfx.driver.0": "gb.sb3.gfxdriver", "ro.build.id": T_BUILD_ID, "ro.build.version.incremental": T_INCR}


def link(target, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.lexists(path):
        os.unlink(path)
    os.symlink(target, path)


def make_gfx_tree(root):
    """INTERFACES 26.3: native, stack, package, gfx_build_id, DTX attached, nouveau bound, D3cold, refcnt 0,
    result=clean, a valid pass record with t=1761390000, gfx_mode/gfx_apps unset, empty catalog and choices,
    catalog stamp equal to packages.list."""
    make_tree(root)
    write(root + "/odm/app/GbGfxDriver/GbGfxDriver.apk", "apk")
    write(root + "/odm/etc/gb/gfx_build_id", T_GFX + "\n")
    write(root + "/proc/sys/kernel/osrelease", T_KREL + "\n")
    write(root + "/proc/sys/kernel/random/boot_id", T_BOOT + "\n")
    write(root + "/proc/uptime", "95.12 180.00\n")
    fn = root + "/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0"
    for k, v in (("vendor", "0x10de"), ("device", "0x1f91"), ("class", "0x030200"), ("power/control", "auto"),
                 ("power/runtime_status", "suspended"), ("power_state", "D3cold"), ("d3cold_allowed", "1"),
                 ("resource", "0x00000000e0000000 0x00000000e0ffffff 0x0000000000040200")):
        write(fn + "/" + k, v + "\n")
    write(root + "/sys/devices/pci0000:00/0000:00:1d.4/vendor", "0x8086\n")
    link("../../../../bus/pci/drivers/nouveau", fn + "/driver")
    link("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", root + "/sys/bus/pci/devices/0000:02:00.0")
    link("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", root + "/sys/class/drm/renderD129/device")
    link("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", root + "/sys/class/drm/card1/device")
    write(root + "/sys/module/nouveau/refcnt", "0\n")
    write(root + "/dev/dri/renderD129", "")
    write(root + "/dev/dri/card1", "")
    os.makedirs(root + "/dev/surface", exist_ok=True)
    os.mkfifo(root + "/dev/surface/dtx")
    os.makedirs(root + "/data/vendor/gb/notify", exist_ok=True)
    os.makedirs(root + "/data/vendor/gb/selftest", exist_ok=True)
    os.chmod(root + "/data/vendor/gb/selftest", 0o700)
    write(root + "/data/vendor/gb/selftest/result",
          "verdict=pass\nreason=\nbuild=%s/%s\nkrel=%s\ngfx=%s\nt=1761390000\nattempts=1\ncrashes=0\n"
          "report=selftest-1.txt\n" % (T_BUILD_ID, T_INCR, T_KREL, T_GFX))
    st = os.stat(root + "/data/system/packages.list")
    write(root + "/data/vendor/gb/gfx_catalog", "# pkgs %d %d\n" % (st.st_mtime_ns, st.st_size))


def node_mode(path):
    return os.stat(path).st_mode & 0o777


class Server:
    def __init__(self, binary, work, uid, props, root="root", dtx=False, tag=""):
        self.root = os.path.join(work, root)
        self.sock = os.path.join(work, "gb_power%s.sock" % tag)
        self.props = os.path.join(work, "props-%d%s" % (uid, tag))
        os.makedirs(self.props, exist_ok=True)
        for k, v in props.items():
            write(os.path.join(self.props, k), v)
        env = {"PATH": "/usr/bin:/bin", "GB_POWER_TEST_UID": str(uid), "GB_POWER_PROPS_DIR": self.props}
        if dtx:
            env["GB_POWER_TEST_DTX"] = "1"
        if os.path.exists(self.sock):
            os.unlink(self.sock)
        self.logf = open(os.path.join(work, "server-%d%s.log" % (uid, tag)), "w", encoding="ascii")
        args = [binary, "-r", self.root, "-S", self.sock, "-H", "-I"] + ([] if dtx else ["-D"])
        self.p = subprocess.Popen(args, env=env, stdout=self.logf, stderr=subprocess.STDOUT)
        for _ in range(200):
            if os.path.exists(self.sock):
                break
            time.sleep(0.02)
        check(os.path.exists(self.sock), "server %d listening" % uid)

    def prop(self, k):
        v = read(os.path.join(self.props, k))
        return v if v is not None else ""

    def stop(self):
        self.p.send_signal(signal.SIGTERM)
        try:
            rc = self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            rc = "timeout"
        self.logf.close()
        return rc


def conn(path, timeout=2.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    return s


def readline(s):
    buf = b""
    while not buf.endswith(b"\n"):
        try:
            c = s.recv(1)
        except (socket.timeout, ConnectionResetError):
            return None
        if not c:
            return None
        buf += c
    return buf[:-1].decode("ascii")


def req(s, line):
    s.sendall(line if isinstance(line, bytes) else line.encode("ascii"))
    return readline(s)


def closed_by_server(s):
    """Drains what is left; True once the server has closed the connection."""
    try:
        while True:
            d = s.recv(65536)
            if not d:
                return True
    except ConnectionResetError:
        return True
    except socket.timeout:
        return False


def is_json_line(r):
    try:
        o = json.loads(r)
    except (TypeError, ValueError):
        return False
    return isinstance(o, dict) and o.get("v") == 1 and isinstance(o.get("ok"), bool) and "cmd" in o


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    binary, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    os.makedirs(work, exist_ok=True)
    make_tree(os.path.join(work, "root"))

    # ---- golden transcripts (INTERFACES 9.6), role ui
    srv = Server(binary, work, 10123, {"vendor.gb.nouveau.booted": "1", "vendor.gb.nouveau.mode": "default"})
    check(srv.prop("vendor.gb.power.api") == "1", "vendor.gb.power.api=1")
    s = conn(srv.sock)
    golden = [
        ("hello\n", '{"v":1,"ok":true,"cmd":"hello","api":1,"role":"ui","uid":10123,"server":"gb_power 1"}'),
        ("frobnicate\n", '{"v":1,"ok":false,"cmd":"frobnicate","err":"ENOSYS","msg":"unknown command"}'),
        ("enable\n", '{"v":1,"ok":true,"cmd":"enable","effect":"now"}'),
        ("close-holders 1,abc\n", '{"v":1,"ok":false,"cmd":"close-holders","err":"EINVAL","msg":"bad pid list"}'),
    ]
    for line, want in golden:
        got = req(s, line)
        check(got == want, "golden %r: got %r" % (line.strip(), got))
        check(is_json_line(got), "golden %r is one JSON object" % line.strip())
    # v1.2 (26.2): enable = set-mode auto; the retired persist.vendor.gb.nouveau is never written
    check(srv.prop("persist.vendor.gb.gfx_mode") == "auto" and srv.prop("persist.vendor.gb.nouveau") == "",
          "enable set persist.vendor.gb.gfx_mode=auto (not persist.vendor.gb.nouveau)")
    st = req(s, "status\n")
    o = json.loads(st) if is_json_line(st) else {}
    for k in ("state", "n", "holders", "mode", "result", "persist", "clean_boots", "probation", "used", "foreign",
              "wedged", "fault_reason", "bdf", "driver", "runtime_status", "power_state", "refcnt", "latch", "base", "dtx",
              "compute", "payload", "gfxdriver", "gfx_mode", "gfx_policy", "gfx_active", "gfx_reason", "gfx_ok",
              "gfx_test", "gfx_test_t", "gfx_cap", "gfx_seq", "pending", "detach_ref", "apps_sync", "t_ms"):
        check(k in o, "status has %s" % k)
    keys = list(o.keys())
    check(keys[keys.index("gfxdriver") + 1:keys.index("t_ms")] ==
          ["gfx_mode", "gfx_policy", "gfx_active", "gfx_reason", "gfx_ok", "gfx_test", "gfx_test_t", "gfx_cap", "gfx_seq",
           "pending", "detach_ref", "apps_sync"], "v1.2 fields in the 9.5 order after gfxdriver, before t_ms")
    check(o.get("gfx_reason") == "nopkg" and o.get("gfx_cap") == 0 and o.get("gfxdriver") == 0,
          "no driver package in this tree: gfx_reason nopkg, gfx_cap 0")
    check(o.get("state") == "unavailable" and o.get("dtx") == "n/a" and o.get("refcnt") == -1 and o.get("persist") == 1,
          "status values without NVIDIA hardware: %s" % st)
    check(len(st) + 1 <= 4096, "status reply <= 4096 bytes")

    # watch: an update when a field changes (another client's status request ticks the daemon)
    w = conn(srv.sock)
    first = req(w, "watch\n")
    check(first is not None and '"cmd":"status"' in first, "watch: first status")
    write(os.path.join(srv.props, "vendor.gb.nouveau.mode"), "disabled")
    time.sleep(0.3)
    req(s, "status\n")
    upd = readline(w)
    check(upd is not None and '"state":"disabled"' in upd, "watch: update on change: %r" % upd)
    w.close()
    s.close()

    # ---- limits (9.1)
    c = conn(srv.sock)
    got = req(c, b"a" * 300 + b"\n")
    check(got == '{"v":1,"ok":false,"cmd":"","err":"E2BIG","msg":"request too long"}', "300-byte line: E2BIG (%r)" % got)
    check(closed_by_server(c), "E2BIG: connection closed")
    c.close()
    c = conn(srv.sock)
    got = req(c, b"\x00\x01\xfe\xff\n")
    check(got is not None and '"err":"EINVAL"' in got, "garbage bytes: EINVAL (%r)" % got)
    check(req(c, "hello\n") is not None, "connection usable after garbage")
    c.close()
    time.sleep(0.2)
    conns = [conn(srv.sock) for _ in range(8)]
    ok = all(req(x, "hello\n") is not None for x in conns)
    check(ok, "8 clients served")
    ninth = conn(srv.sock)
    got = readline(ninth)
    check(got == '{"v":1,"ok":false,"cmd":"","err":"EBUSY","msg":"too many connections"}', "9th client: EBUSY (%r)" % got)
    check(closed_by_server(ninth), "9th client closed")
    ninth.close()
    for x in conns:
        x.close()
    time.sleep(0.3)
    ws = [conn(srv.sock) for _ in range(5)]
    for i in range(4):
        r = req(ws[i], "watch\n")
        check(r is not None and '"cmd":"status"' in r, "watcher %d accepted" % i)
    r = req(ws[4], "watch\n")
    check(r == '{"v":1,"ok":false,"cmd":"watch","err":"EBUSY","msg":"watch limit"}', "5th watcher: EBUSY (%r)" % r)
    check(req(ws[4], "hello\n") is not None, "5th stays a normal connection")
    for x in ws:
        x.close()
    time.sleep(0.3)

    # slow reader: 400 requests, never reads; the daemon drops it and keeps serving others without delay
    slow = conn(srv.sock, timeout=1.0)
    slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    slow.sendall(b"status\n" * 400)
    probe = conn(srv.sock)
    worst = 0.0
    for _ in range(20):
        t = time.monotonic()
        r = req(probe, "hello\n")
        worst = max(worst, time.monotonic() - t)
        check(r is not None, "probe answered while a client does not read")
    check(worst < 0.5, "probe latency with a slow reader: worst %.3f s" % worst)
    check(closed_by_server(slow), "slow reader dropped by the daemon")
    slow.close()
    probe.close()

    rc = srv.stop()
    check(rc == 0, "SIGTERM: exit 0 (%r)" % rc)
    log = read(os.path.join(work, "server-10123.log")) or ""
    check("does not read its replies: closed" in log and "exiting" in log, "daemon log mentions the drop and the exit")

    # ---- role other (golden EPERM)
    srv = Server(binary, work, 10500, {"vendor.gb.nouveau.booted": "1", "vendor.gb.nouveau.mode": "default"})
    s = conn(srv.sock)
    got = req(s, "enable\n")
    check(got == '{"v":1,"ok":false,"cmd":"enable","err":"EPERM","msg":"not allowed"}', "golden EPERM (%r)" % got)
    check(srv.prop("persist.vendor.gb.nouveau") == "" and srv.prop("persist.vendor.gb.gfx_mode") == "", "EPERM: nothing set")
    got = req(s, "hello\n")
    check(got is not None and '"role":"other"' in got and '"uid":10500' in got, "hello as other (%r)" % got)
    for cmd in ("status", "watch", "holders", "disable", "detach-request", "report", "stop-compute", "compute-list",
                "compute-start", "compute-state idle", "compute-allow a.b", "compute-deny a.b", "close-holders 1",
                "get-mode", "set-mode auto", "set-app-policy all", "set-app-intel a.b:1", "set-app-nvidia a.b:0",
                "get-apps", "apps-begin 1", "apps-add g:a.b", "apps-commit 1", "run-selftest", "list-holders",
                "close-holders-and-detach 1", "ui-caps notify"):
        got = req(s, cmd + "\n")
        check(got is not None and '"err":"EPERM"' in got, "other: %s -> EPERM (%r)" % (cmd, got))
    got = req(s, "set-mode intel\n")
    check(got == '{"v":1,"ok":false,"cmd":"set-mode","err":"EPERM","msg":"not allowed"}',
          "golden 26.3 set-mode as other: EPERM (%r)" % got)
    s.close()
    check(srv.stop() == 0, "second server exit 0")

    # ---- v1.2 golden transcripts (INTERFACES 26.3) over the real socket; the DTX ioctls answered by the host-only EC
    root12 = os.path.join(work, "root12")
    make_gfx_tree(root12)
    srv = Server(binary, work, 10123, GFX_PROPS, root="root12", dtx=True, tag="-v12")
    s = conn(srv.sock)
    want = ('{"v":1,"ok":true,"cmd":"get-mode","mode":"auto","policy":"games","active":"nvidia","reason":"ok",'
            '"gfx_ok":1,"test":"pass","test_t":1761390000,"cap":1,"base":"attached","pending":"","apps_sync":1,'
            '"prefs_n":0,"opt_in_n":0}')
    got, waited = None, 0.0
    while waited < 6.0:                          # the session marker is written by the worker first (23.3)
        got = req(s, "get-mode\n")
        if got == want:
            break
        time.sleep(0.2)
        waited += 0.2
    check(got == want, "golden 26.3 get-mode (after %.1f s): %r" % (waited, got))
    check(srv.prop("vendor.gb.gfx_active") == "nvidia" and srv.prop("vendor.gb.gfx_reason") == "ok",
          "vendor.gb.gfx_active=nvidia, gfx_reason=ok published")
    sess = read(root12 + "/data/vendor/gb/gfx_session") or ""
    check(sess.startswith("boot=" + T_BOOT + " t="), "gfx_session written by the worker: %r" % sess)
    check(node_mode(root12 + "/dev/dri/renderD129") == 0o666, "render node 0666 while nvidia")
    golden12 = [
        ("set-mode turbo\n", '{"v":1,"ok":false,"cmd":"set-mode","err":"EINVAL","msg":"bad mode"}'),
        ("set-app-intel com.example.game\n", '{"v":1,"ok":false,"cmd":"set-app-intel","err":"EINVAL","msg":"bad argument"}'),
        ("apps-commit 3\n", '{"v":1,"ok":false,"cmd":"apps-commit","err":"EINVAL","msg":"no apps-begin"}'),
        ("ui-caps notify\n", '{"v":1,"ok":true,"cmd":"ui-caps","notify":1}'),
    ]
    for line, w in golden12:
        got = req(s, line)
        check(got == w, "golden 26.3 %r: got %r" % (line.strip(), got))
        check(is_json_line(got), "golden 26.3 %r is one JSON object" % line.strip())
    write(root12 + "/odm/etc/gb_hw", "vm\n")
    got = req(s, "set-mode intel\n")
    check(got == '{"v":1,"ok":false,"cmd":"set-mode","err":"ENODEV","msg":"no NVIDIA driver on this image"}',
          "golden 26.3 set-mode intel on a vm tree: %r" % got)
    write(root12 + "/odm/etc/gb_hw", "native\n")
    got = req(s, "set-mode intel\n")
    check(got == '{"v":1,"ok":true,"cmd":"set-mode","mode":"intel","active":"intel","effect":"now","n":0}',
          "golden 26.3 set-mode intel (no holders): %r" % got)
    check(srv.prop("persist.vendor.gb.gfx_mode") == "intel" and srv.prop("persist.vendor.gb.nouveau") == "" and
          srv.prop("vendor.gb.gfx_active") == "intel", "set-mode intel: gfx_mode=intel, gfx_active=intel")
    time.sleep(0.5)
    req(s, "status\n")
    check(not os.path.exists(root12 + "/data/vendor/gb/gfx_session"), "nvidia -> intel: gfx_session removed")
    check(node_mode(root12 + "/dev/dri/renderD129") == 0o660, "render node back to 0660")
    st = json.loads(req(s, "status\n"))
    check(st.get("gfx_mode") == "intel" and st.get("persist") == 0 and st.get("gfx_seq") == 2 and st.get("gfx_cap") == 1,
          "status after set-mode intel: gfx_mode intel, persist 0, gfx_seq 2 (%r)" %
          {k: st.get(k) for k in ("gfx_mode", "persist", "gfx_seq", "gfx_cap")})
    s.close()
    check(srv.stop() == 0, "v1.2 server exit 0")

    # ---- VMware model (gb_power -D on a native tree with the package, GP-1B-23): reason nodtx, get-mode works
    root_vm = os.path.join(work, "rootvm")
    make_gfx_tree(root_vm)
    srv = Server(binary, work, 10123, dict(GFX_PROPS, **{"sys.boot_completed": "1"}), root="rootvm", dtx=False, tag="-vm")
    s = conn(srv.sock)
    got = req(s, "get-mode\n")
    o = json.loads(got) if is_json_line(got) else {}
    check(o.get("ok") is True and o.get("active") == "intel" and o.get("reason") == "nodtx" and o.get("base") == "unknown",
          "VMware model: get-mode intel/nodtx: %r" % got)
    time.sleep(1.0)
    req(s, "status\n")
    notes = os.listdir(root_vm + "/data/vendor/gb/notify")
    check(not any("kind=settings" in (read(os.path.join(root_vm, "data/vendor/gb/notify", n)) or "") for n in notes),
          "VMware model: no settings message queued (%d files)" % len(notes))
    s.close()
    check(srv.stop() == 0, "VMware-model server exit 0")

    print("\n%d checks, %d failures" % (CHECKS[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
