#!/usr/bin/env python3
"""Conformance of mock_gb_power.py (python3 -I): golden transcripts, limits, and equality with the real gb_power.

  python3 -I test_mock.py <mock_gb_power.py> <work dir> [<gb_power host binary>]
  python3 -I test_mock.py --gfx-tree <dir>       (only builds the 26.3 fake tree and <dir>.props, for run_tests.sh)

1. Starts the mock and checks the INTERFACES 9.6 and (v1.2) 26.3 golden transcripts byte for byte, the 9.5 key order
   with the v1.2 fields, plus the 9.1 limits (300-byte line -> E2BIG + close, 9th connection -> EBUSY, 5th watcher ->
   EBUSY, idle close).
2. With the real gb_power host build (power component, built from its unmodified source), UI-1B-12:
   A. the wave-1 34-step session plus a v1.2 session on the wave-1 tree (no NVIDIA device, no DTX, no driver package,
      role ui, booted=1, mode=default): identical reply lines from both servers after removing t_ms;
   B. a v1.2 session on the INTERFACES 26.3 tree (native, package, gfx_build_id, DTX attached through the host-only EC,
      nouveau bound, D3cold, a valid pass record): the 26.3 goldens and every v1.2 command reply identical.
Prints one line per check and "N checks, M failures"; exit 1 on a failure.
"""
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time

FAILS = []
CHECKS = [0]


def check(ok, what):
    CHECKS[0] += 1
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)
    return ok


def conn(path, timeout=3.0):
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


def closed(s):
    try:
        while True:
            if not s.recv(65536):
                return True
    except ConnectionResetError:
        return True
    except socket.timeout:
        return False


def wait_path(p, secs=5.0):
    end = time.time() + secs
    while time.time() < end and not os.path.exists(p):
        time.sleep(0.02)
    return os.path.exists(p)


def ctl(path, obj):
    s = conn(path)
    try:
        return json.loads(req(s, json.dumps(obj) + "\n"))
    finally:
        s.close()


GOLDEN = [
    ("hello\n", '{"v":1,"ok":true,"cmd":"hello","api":1,"role":"ui","uid":10123,"server":"gb_power 1"}'),
    ("frobnicate\n", '{"v":1,"ok":false,"cmd":"frobnicate","err":"ENOSYS","msg":"unknown command"}'),
    ("enable\n", '{"v":1,"ok":true,"cmd":"enable","effect":"now"}'),
    ("close-holders 1,abc\n", '{"v":1,"ok":false,"cmd":"close-holders","err":"EINVAL","msg":"bad pid list"}'),
]
EPERM = '{"v":1,"ok":false,"cmd":"enable","err":"EPERM","msg":"not allowed"}'

# INTERFACES 26.3 (v1.2), in order; "tree" says which fake tree / role the line needs
GET_MODE_26_3 = ('{"v":1,"ok":true,"cmd":"get-mode","mode":"auto","policy":"games","active":"nvidia","reason":"ok",'
                 '"gfx_ok":1,"test":"pass","test_t":1761390000,"cap":1,"base":"attached","pending":"","apps_sync":1,'
                 '"prefs_n":0,"opt_in_n":0}')
GOLDEN_26_3 = [
    ("ui", "get-mode", GET_MODE_26_3),
    ("ui", "set-mode turbo", '{"v":1,"ok":false,"cmd":"set-mode","err":"EINVAL","msg":"bad mode"}'),
    ("other", "set-mode intel", '{"v":1,"ok":false,"cmd":"set-mode","err":"EPERM","msg":"not allowed"}'),
    ("ui", "set-app-intel com.example.game",
     '{"v":1,"ok":false,"cmd":"set-app-intel","err":"EINVAL","msg":"bad argument"}'),
    ("ui", "apps-commit 3", '{"v":1,"ok":false,"cmd":"apps-commit","err":"EINVAL","msg":"no apps-begin"}'),
    ("ui", "ui-caps notify", '{"v":1,"ok":true,"cmd":"ui-caps","notify":1}'),
    ("vm", "set-mode intel", '{"v":1,"ok":false,"cmd":"set-mode","err":"ENODEV","msg":"no NVIDIA driver on this image"}'),
    ("ui", "set-mode intel", '{"v":1,"ok":true,"cmd":"set-mode","mode":"intel","active":"intel","effect":"now","n":0}'),
]

STATUS_KEYS_V12 = ["v", "ok", "cmd", "state", "n", "holders", "mode", "result", "persist", "clean_boots", "probation",
                   "used", "foreign", "wedged", "fault_reason", "bdf", "driver", "runtime_status", "power_state",
                   "refcnt", "latch", "base", "dtx", "compute", "payload", "gfxdriver", "gfx_mode", "gfx_policy",
                   "gfx_active", "gfx_reason", "gfx_ok", "gfx_test", "gfx_test_t", "gfx_cap", "gfx_seq", "pending",
                   "detach_ref", "apps_sync", "t_ms"]

# Session A: the wave-1 script (30 lines on one connection + 4 framing cases = 34 steps), unchanged
SCRIPT = ["hello", "frobnicate", "enable", "close-holders 1,abc", "status", "holders", "disable", "status",
          "report", "stop-compute", "compute-list", "compute-allow com.example.other", "compute-list",
          "compute-deny com.example.other", "compute-deny com.example.other", "compute-allow com.example.missing",
          "compute-allow bad", "compute-start", "detach-request", "compute-state idle", "status x", "close-holders",
          "close-holders 1,", "close-holders 4242", " status", "status ", "hello  x", "enable", "status", "watch"]
# ... plus the v1.2 commands on the same (no-device, no-DTX, no-package) tree, on a second connection
SCRIPT_V12_A = ["hello", "get-mode", "get-mode x", "set-mode turbo", "set-mode", "set-mode nvidia", "get-mode",
                "set-mode intel", "status", "set-mode auto", "set-app-policy bogus", "set-app-policy all",
                "set-app-intel com.example.other", "set-app-intel com.example.other:1", "set-app-nvidia bad:1",
                "set-app-nvidia com.example.other:2", "set-app-intel :1", "get-apps", "get-apps 0", "get-apps 129",
                "get-apps x", "apps-add g:com.example.other", "apps-commit 1", "apps-begin 513", "apps-begin",
                "apps-begin 2", "apps-add g:com.example.other", "apps-add x:com.example.other", "apps-add g:",
                "apps-add a:a,,b", "apps-add a:gb.sb3.gpu,com.example.missing", "apps-add a:gb.sb3.gpu",
                "apps-commit 1", "apps-begin 2", "apps-add g:com.example.other", "apps-add a:gb.sb3.gpu",
                "apps-commit 2", "get-mode", "status", "run-selftest", "run-selftest x", "list-holders",
                "list-holders x", "close-holders-and-detach 1,abc", "close-holders-and-detach 4242",
                "close-holders-and-detach", "ui-caps notify", "ui-caps none", "ui-caps bogus", "ui-caps",
                "get-mode"]
# Session B (26.3 tree, DTX attached): v1.2 command replies (no status lines: the fake dGPU's details differ)
SCRIPT_V12_B = ["set-mode turbo", "set-app-intel com.example.game", "apps-commit 3", "ui-caps notify",
                "set-app-policy bogus", "set-app-policy all", "get-apps", "set-app-nvidia com.example.other:1",
                "get-mode", "set-app-intel com.example.other:1", "get-apps", "get-mode",
                "set-app-intel com.example.other:0", "set-app-nvidia com.example.other:0",
                "set-app-nvidia com.example.missing:1", "set-app-nvidia com.example.missing:0", "set-app-nvidia bad:1",
                "set-app-nvidia com.example.other:1", "set-app-policy games", "get-mode", "apps-begin 3",
                "apps-add g:com.example.other,com.example.missing", "apps-add a:gb.sb3.gpu", "apps-add a:com.x.y",
                "apps-commit 2", "apps-add g:com.example.other", "apps-begin 3",
                "apps-add g:com.example.other,com.example.missing", "apps-add a:gb.sb3.gpu", "apps-commit 3",
                "get-mode", "set-app-policy all", "get-mode", "set-app-intel com.example.other:1", "get-mode",
                "get-apps 1", "get-apps 129", "list-holders", "close-holders-and-detach 4242",
                "close-holders-and-detach 1,abc", "ui-caps none", "ui-caps maybe", "get-mode x",
                "set-mode intel", "get-mode", "run-selftest", "set-mode nvidia"]


def strip_t(line):
    return re.sub(r',"t_ms":\d+', "", line) if line else line


def session_a(path):
    out = []
    s = conn(path)
    for line in SCRIPT:
        out.append(strip_t(req(s, line + "\n")))
    s.close()
    s = conn(path)
    out.append(req(s, b"a" * 300 + b"\n"))
    out.append("closed" if closed(s) else "open")
    s.close()
    s = conn(path)
    out.append(req(s, b"\x00\x01\xfe\xff\n"))
    out.append(req(s, b"hello\r\n"))
    s.close()
    s = conn(path)
    for line in SCRIPT_V12_A:
        out.append(strip_t(req(s, line + "\n")))
    s.close()
    return out


def labels_a():
    return SCRIPT + ["300-byte", "close", "garbage", "hello CRLF"] + ["v1.2 " + x for x in SCRIPT_V12_A]


def wait_get_mode(path, want, secs=6.0):
    """The real daemon publishes nvidia only after its worker wrote gfx_session (23.3); poll like test_socket.py."""
    s = conn(path)
    got, end = None, time.time() + secs
    try:
        while time.time() < end:
            got = req(s, "get-mode\n")
            if got == want:
                break
            time.sleep(0.1)
    finally:
        s.close()
    return got


def session_b(path):
    out = []
    s = conn(path)
    for line in SCRIPT_V12_B:
        out.append(req(s, line + "\n"))
    s.close()
    return out


GFX_TREE_MOCK = {"state": "standby", "driver": "nouveau", "refcnt": 0, "holders": [], "result": "clean",
                 "mode": "auto", "base": "attached", "dtx": "ready", "cap": 1, "stack": 1, "gfx_ok": 1,
                 "test_t": 1761390000, "catalog": [], "catalog_gen": 0, "pkgs_gen": 0, "prefs": {},
                 "gfx_mode": "", "gfx_apps": "", "power_state": "D3cold", "runtime_status": "suspended",
                 "packages": {"gb.sb3.gpu": 10123, "com.example.other": 10500}}


def mock_checks(mock, work):
    sock, ctlp = os.path.join(work, "m.sock"), os.path.join(work, "m.ctl")
    log = open(os.path.join(work, "mock.log"), "w", encoding="ascii")
    # --sim-ms 100000: the golden `enable` must not start the simulated load during these checks
    p = subprocess.Popen([sys.executable, "-I", mock, "--socket", sock, "--ctl", ctlp, "--uid", "10123",
                          "--ping-ms", "400", "--idle-ms", "1000", "--sim-ms", "100000"], stdout=log,
                         stderr=subprocess.STDOUT)
    try:
        check(wait_path(sock) and wait_path(ctlp), "mock listening")
        s = conn(sock)
        for line, want in GOLDEN:
            got = req(s, line)
            check(got == want, "mock golden 9.6 %r -> %s" % (line.strip(), got))
        s.close()
        ctl(ctlp, {"uid": 10500})
        s = conn(sock)
        got = req(s, "enable\n")
        check(got == EPERM, "mock golden 9.6 enable as role other -> %s" % got)
        s.close()
        ctl(ctlp, {"uid": 10123})
        st = json.loads(req(conn(sock), "status\n"))
        check(list(st) == STATUS_KEYS_V12, "mock status keys in the 9.5 order with the v1.2 fields")
        # ---- 26.3 goldens on the first tree
        ctl(ctlp, {"set": GFX_TREE_MOCK})
        time.sleep(0.2)
        s = conn(sock)
        for tree, line, want in GOLDEN_26_3:
            if tree == "other":
                ctl(ctlp, {"uid": 10500})
                o = conn(sock)
                got = req(o, line + "\n")
                o.close()
                ctl(ctlp, {"uid": 10123})
            elif tree == "vm":
                ctl(ctlp, {"set": {"stack": 0}})
                got = req(s, line + "\n")
                ctl(ctlp, {"set": {"stack": 1}})
            else:
                got = req(s, line + "\n")
            check(got == want, "mock golden 26.3 %r (%s) -> %s" % (line, tree, got))
        s.close()
        st = json.loads(req(conn(sock), "status\n"))
        check(st.get("gfx_mode") == "intel" and st.get("persist") == 0 and st.get("gfx_seq") == 2
              and st.get("gfx_active") == "intel" and st.get("gfx_reason") == "mode",
              "mock status after the 26.3 set-mode intel: gfx_mode intel, persist 0, gfx_seq 2 (as the real daemon)")
        ctl(ctlp, {"set": {"gfx_mode": "", "state": "off", "driver": "", "refcnt": -1, "gfx_ok": -1, "test_t": 0,
                           "catalog": None, "mode": "default", "result": "idle"}})
        # limits
        c = conn(sock)
        got = req(c, b"a" * 300 + b"\n")
        check(got == '{"v":1,"ok":false,"cmd":"","err":"E2BIG","msg":"request too long"}' and closed(c),
              "mock 300-byte line -> E2BIG + close")
        c.close()
        time.sleep(0.2)
        cs = [conn(sock) for _ in range(8)]
        check(all(req(x, "hello\n") is not None for x in cs), "mock serves 8 clients")
        ninth = conn(sock)
        got = readline(ninth)
        check(got == '{"v":1,"ok":false,"cmd":"","err":"EBUSY","msg":"too many connections"}' and closed(ninth),
              "mock 9th client -> EBUSY + close")
        for x in cs + [ninth]:
            x.close()
        time.sleep(0.3)
        ws = [conn(sock) for _ in range(5)]
        okw = all('"cmd":"status"' in (req(ws[i], "watch\n") or "") for i in range(4))
        check(okw, "mock 4 watchers accepted")
        check(req(ws[4], "watch\n") == '{"v":1,"ok":false,"cmd":"watch","err":"EBUSY","msg":"watch limit"}',
              "mock 5th watcher -> EBUSY")
        ws[0].settimeout(2.0)
        got = readline(ws[0])
        check(got == '{"v":1,"ok":true,"cmd":"ping"}', "mock watch ping (%s)" % got)
        ctl(ctlp, {"set": {"state": "standby", "driver": "nouveau", "refcnt": 0}})
        got = readline(ws[0])
        check(got is not None and '"state":"standby"' in got, "mock watch update on change")
        ctl(ctlp, {"set": {"detach_ref": 1}})
        got = readline(ws[0])
        check(got is not None and '"detach_ref":1' in got, "mock watch update on a v1.2 field (detach_ref)")
        for x in ws:
            x.close()
        idle = conn(sock)
        req(idle, "hello\n")
        time.sleep(1.4)
        check(closed(idle), "mock closes idle connections (--idle-ms)")
        idle.close()
        ctl(ctlp, {"quit": True})
        p.wait(5)
        check(p.returncode == 0, "mock exits 0")
    finally:
        if p.poll() is None:
            p.kill()
        log.close()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as f:
        f.write(data)


def link(target, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.lexists(path):
        os.unlink(path)
    os.symlink(target, path)


def make_real_tree(root):
    write(root + "/odm/etc/gb_hw", "native\n")
    write(root + "/odm/lib/modules/nouveau.ko", "ko")
    write(root + "/odm/app/GbGpu/GbGpu.apk", "apk")
    write(root + "/data/system/packages.list",
          "gb.sb3.gpu 10123 0 /data/user/0/gb.sb3.gpu platform:targetSdkVersion=36 none 0 1\n"
          "com.example.other 10500 0 /data/user/0/com.example.other default:targetSdkVersion=36 none 0 1\n")
    os.makedirs(root + "/data/vendor/gb", exist_ok=True)
    os.makedirs(root + "/sys/bus/pci/devices", exist_ok=True)
    os.makedirs(root + "/proc", exist_ok=True)
    write(root + "/proc/cmdline", "root=/dev/ram0 gb.hw=native\n")


# The 26.3 tree, as power/test/test_socket.py make_gfx_tree() builds it
T_BUILD_ID, T_INCR = "CL3B.260622.276.R4", "16658739"
T_KREL = "6.18.21-android17-5-g3938c8d97142-dirty-ab16302624"
T_GFX = "apk=1111 icd=2222 nvk=3333 probe=4444 pay=legacy"
T_BOOT = "0b5e4c1a-1111-4222-8333-944455556666"
GFX_PROPS = {"vendor.gb.nouveau.booted": "1", "vendor.gb.nouveau.mode": "auto", "vendor.gb.nouveau.result": "clean",
             "ro.gfx.driver.0": "gb.sb3.gfxdriver", "ro.build.id": T_BUILD_ID, "ro.build.version.incremental": T_INCR}


def make_gfx_tree(root):
    make_real_tree(root)
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


def start_real(real, root, props, sock, log, dtx):
    os.makedirs(props, exist_ok=True)
    for k, v in props_items(dtx):
        write(os.path.join(props, k), v)
    env = {"PATH": "/usr/bin:/bin", "GB_POWER_TEST_UID": "10123", "GB_POWER_PROPS_DIR": props}
    if dtx:
        env["GB_POWER_TEST_DTX"] = "1"
    args = [real, "-r", root, "-S", sock, "-H", "-I"] + ([] if dtx else ["-D"])
    return subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT)


def props_items(dtx):
    if dtx:
        return GFX_PROPS.items()
    return (("vendor.gb.nouveau.booted", "1"), ("vendor.gb.nouveau.mode", "default"))


def compare(a, b, labels, what):
    check(len(a) == len(b), "%s: same number of replies (%d)" % (what, len(a)))
    same = 0
    for i, (x, y) in enumerate(zip(a, b)):
        label = labels[i] if i < len(labels) else str(i)
        if check(x == y, "%s: real == mock for %r" % (what, label)):
            same += 1
        else:
            print("   real: %s\n   mock: %s" % (x, y))
    check(same == len(a), "%s: all %d replies identical (t_ms removed)" % (what, len(a)))


def stop(p):
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


def equality(mock, real, work):
    # ---- A: the wave-1 tree (as power/test/test_socket.py make_tree()), no DTX (-D)
    root, props = os.path.join(work, "root"), os.path.join(work, "props")
    make_real_tree(root)
    rsock = os.path.join(work, "real.sock")
    rlog = open(os.path.join(work, "real.log"), "w", encoding="ascii")
    rp = start_real(real, root, props, rsock, rlog, False)
    msock, mctl = os.path.join(work, "e.sock"), os.path.join(work, "e.ctl")
    mlog = open(os.path.join(work, "mock-eq.log"), "w", encoding="ascii")
    mp = subprocess.Popen([sys.executable, "-I", mock, "--socket", msock, "--ctl", mctl, "--uid", "10123",
                           "--sim-ms", "100000"], stdout=mlog, stderr=subprocess.STDOUT)
    try:
        check(wait_path(rsock), "A: real gb_power listening")
        check(wait_path(msock) and wait_path(mctl), "A: mock listening")
        ctl(mctl, {"set": {"state": "unavailable", "result": "", "bdf": "", "driver": "", "runtime_status": "",
                           "power_state": "", "refcnt": -1, "latch": "unknown", "base": "unknown", "dtx": "n/a",
                           "payload": "", "compute": "stopped", "cap": 0, "catalog": None,
                           "packages": {"gb.sb3.gpu": 10123, "com.example.other": 10500}}})
        a, b = session_a(rsock), session_a(msock)
        compare(a, b, labels_a(), "A")
        for line, want in GOLDEN:
            check(want in a, "A: real server golden 9.6 %r present" % line.strip())
        sts = [json.loads(x) for x in a if x and x.startswith('{"v":1,"ok":true,"cmd":"status"')]
        check(bool(sts) and all(list(o) == STATUS_KEYS_V12[:-1] for o in sts),
              "A: real status objects carry the v1.2 fields in the 9.5 order (%d)" % len(sts))
    finally:
        stop(rp)
        stop(mp)
        rlog.close()
        mlog.close()

    # ---- B: the 26.3 tree with the host-only EC (DTX attached)
    root, props = os.path.join(work, "root12"), os.path.join(work, "props12")
    make_gfx_tree(root)
    rsock = os.path.join(work, "real12.sock")
    rlog = open(os.path.join(work, "real12.log"), "w", encoding="ascii")
    rp = start_real(real, root, props, rsock, rlog, True)
    msock, mctl = os.path.join(work, "e12.sock"), os.path.join(work, "e12.ctl")
    mlog = open(os.path.join(work, "mock-eq12.log"), "w", encoding="ascii")
    mp = subprocess.Popen([sys.executable, "-I", mock, "--socket", msock, "--ctl", mctl, "--uid", "10123",
                           "--sim-ms", "100000"], stdout=mlog, stderr=subprocess.STDOUT)
    try:
        check(wait_path(rsock), "B: real gb_power listening")
        check(wait_path(msock) and wait_path(mctl), "B: mock listening")
        ctl(mctl, {"set": GFX_TREE_MOCK})
        gr, gm = wait_get_mode(rsock, GET_MODE_26_3), wait_get_mode(msock, GET_MODE_26_3)
        check(gr == GET_MODE_26_3, "B: real golden 26.3 get-mode: %s" % gr)
        check(gm == GET_MODE_26_3, "B: mock golden 26.3 get-mode: %s" % gm)
        a, b = session_b(rsock), session_b(msock)
        compare(a, b, SCRIPT_V12_B, "B")
        for _, line, want in GOLDEN_26_3[1:]:
            if line in ("set-mode turbo", "set-app-intel com.example.game", "apps-commit 3", "ui-caps notify",
                        "set-mode intel") and want.startswith('{"v":1,"ok":') and '"EPERM"' not in want \
                    and '"ENODEV"' not in want:
                check(want in a, "B: real server golden 26.3 %r present" % line)
    finally:
        stop(rp)
        stop(mp)
        rlog.close()
        mlog.close()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--gfx-tree":
        make_gfx_tree(os.path.abspath(sys.argv[2]))
        props = os.path.abspath(sys.argv[2]) + ".props"
        os.makedirs(props, exist_ok=True)
        for k, v in GFX_PROPS.items():
            write(os.path.join(props, k), v)
        print("26.3 tree in %s, properties in %s" % (sys.argv[2], props))
        return 0
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        return 2
    mock, work = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    os.makedirs(work, exist_ok=True)
    print("== mock conformance (9.6, 26.3 goldens; 9.1 limits)")
    mock_checks(mock, work)
    if len(sys.argv) == 4:
        print("== UI-1B-12: mock vs real gb_power v1.2 (host build)")
        equality(mock, os.path.abspath(sys.argv[3]), work)
    print("\n%d checks, %d failures" % (CHECKS[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
