#!/usr/bin/env python3
"""End-to-end tests of gb_gpu_compute (host glibc build of the same sources) against lavapipe.

Run as root (sudo) on the builder:  python3 -I -B test_service.py <host build dir> <tiny q4_0 gguf> <tiny q8_0 gguf> <run dir>

Root is needed only to act as several uids: the service runs as uid 9999 (Android's nobody), the listening socket
is created by this script (like init) and handed over in ANDROID_SOCKET_gb_gpu_compute, a mock gb_power (thread)
serves the fake /dev/socket/gb_power, and clients run as uid 0 (this process) or as child processes with other
uids, so SO_PEERCRED is real. Everything lives under <run dir>/root (GB_COMPUTE_ROOT prefix of the test build).
Covers GC-03..GC-08, GC-11 (launcher refusals) plus lifecycle cases; each check prints PASS/FAIL with evidence.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gc_http  # noqa: E402
from mock_gb_power import MockGbPower  # noqa: E402

LVP = "/usr/lib/x86_64-linux-gnu/libvulkan_lvp.so"
SVC_UID = 9999
RESULTS = []


def rec(tid, name, ok, evidence):
    RESULTS.append((tid, name, "PASS" if ok else "FAIL", evidence))
    print("%s %s %s: %s" % ("PASS" if ok else "FAIL", tid, name, evidence), flush=True)


def wfile(path, text, mode=0o644, uid=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)
    os.chown(path, uid, uid)


class Env:
    def __init__(self, build, q4, q8, run):
        self.build, self.run = build, run
        self.root = os.path.join(run, "root")
        if os.path.exists(run):
            shutil.rmtree(run)
        os.makedirs(self.root)
        os.chmod(run, 0o755)
        R = self.root
        wfile(R + "/odm/etc/gb_hw", "native\n")
        wfile(R + "/odm/app/GbGpu/GbGpu.apk", "")
        lib = R + "/odm/dgpu/lib64/gpu-compute"
        os.makedirs(lib)
        for n in ("libgb_gpu_compute.so", "libgb_nvk_loader.so"):
            shutil.copy(os.path.join(build, n), lib + "/" + n)
            os.chmod(lib + "/" + n, 0o644)
        os.makedirs(R + "/vendor/bin")
        shutil.copy(os.path.join(build, "gb_gpu_compute"), R + "/vendor/bin/gb_gpu_compute")
        os.chmod(R + "/vendor/bin/gb_gpu_compute", 0o755)
        # GbGpu's data dir owned by its uid (appId 10123); packages.list unreadable for the service, with a decoy
        os.makedirs(R + "/data/data/gb.sb3.gpu")
        os.chown(R + "/data/data/gb.sb3.gpu", 10123, 10123)
        os.chmod(R + "/data/data", 0o771)
        wfile(R + "/data/system/packages.list", "gb.sb3.gpu 10555 0 /data/user/0/gb.sb3.gpu default 3003\n", 0o640)
        os.makedirs(R + "/data/vendor/gb/models")
        os.chmod(R + "/data/vendor/gb", 0o771)
        os.chmod(R + "/data/vendor/gb/models", 0o775)
        wfile(R + "/data/vendor/gb/compute_allow", "# test\n10200 com.example.allowed\n9999 bogus.system\n")
        os.makedirs(R + "/data/vendor/gb/compute")
        os.chown(R + "/data/vendor/gb/compute", SVC_UID, SVC_UID)
        os.chmod(R + "/data/vendor/gb/compute", 0o700)
        m = R + "/data/vendor/gb/models/"
        shutil.copy(q4, m + "tiny.gguf")
        shutil.copy(q8, m + "tiny8.gguf")
        for n in ("tiny.gguf", "tiny8.gguf"):
            os.chmod(m + n, 0o644)
        os.symlink("tiny.gguf", m + "link.gguf")
        wfile(m + "bad id.gguf", "x")
        wfile(m + ("x" * 65) + ".gguf", "x")
        wfile(m + "notes.txt", "x")
        with open(m + "huge.gguf", "w") as f:
            f.truncate(3585 << 20)  # sparse, > 3584 MiB
        # fake sysfs: i915 card0/renderD128, nouveau renderD129, the dGPU function, the eDP connector
        for node, drv in (("card0", "i915"), ("renderD128", "i915"), ("card1", "nouveau"), ("renderD129", "nouveau")):
            os.makedirs(R + "/sys/class/drm/%s/device" % node)
            os.symlink("/sys/bus/pci/drivers/" + drv, R + "/sys/class/drm/%s/device/driver" % node)
        wfile(R + "/sys/class/drm/card0-eDP-1/dpms", "On\n")
        d = R + "/sys/bus/pci/devices/0000:02:00.0"
        wfile(d + "/vendor", "0x10de\n")
        wfile(d + "/class", "0x030200\n")
        wfile(d + "/power/control", "auto\n")
        wfile(R + "/sys/bus/pci/devices/0000:00:02.0/vendor", "0x8086\n")
        wfile(R + "/sys/bus/pci/devices/0000:00:02.0/class", "0x030000\n")
        wfile(R + "/proc/meminfo", "MemTotal: 16000000 kB\nMemAvailable: 9000000 kB\n")
        os.makedirs(R + "/dev/socket")
        os.chmod(R + "/dev/socket", 0o755)
        self.sock = R + "/dev/socket/gb_gpu_compute"
        self.power_control = d + "/power/control"
        self.mock = MockGbPower(R + "/dev/socket/gb_power", self.power_control)

    def power(self):
        with open(self.power_control) as f:
            return f.read().strip()


class Server:
    def __init__(self, env, name, extra=None, init_socket=True, strace=None):
        self.env, self.name, self.extra, self.init_socket, self.strace = env, name, extra or {}, init_socket, strace
        self.p = None
        self.log = os.path.join(env.run, "server-%s.log" % name)

    def start(self, wait=True):
        e = {"PATH": "/usr/bin:/bin", "GB_COMPUTE_ROOT": self.env.root, "GB_NVK_ICD": LVP,
             "GGML_VK_VISIBLE_DEVICES": "0", "GB_POWER_SOCKET": self.env.root + "/dev/socket/gb_power",
             "HOME": self.env.root + "/data/vendor/gb/compute", "LC_ALL": "C"}
        e.update(self.extra)
        fds = ()
        ls = None
        if os.path.exists(self.env.sock):
            os.unlink(self.env.sock)
        if self.init_socket:  # like init: socket gb_gpu_compute stream 0666 nobody graphics
            ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            ls.bind(self.env.sock)
            os.chmod(self.env.sock, 0o666)
            os.chown(self.env.sock, SVC_UID, SVC_UID)
            ls.listen(16)
            os.set_inheritable(ls.fileno(), True)
            e["ANDROID_SOCKET_gb_gpu_compute"] = str(ls.fileno())
            fds = (ls.fileno(),)
        else:
            e["GB_COMPUTE_SOCKET"] = self.env.sock
            os.chown(self.env.root + "/dev/socket", SVC_UID, SVC_UID)
        cmd = [self.env.root + "/vendor/bin/gb_gpu_compute"]
        if self.strace:
            cmd = ["strace", "-f", "-qq", "--seccomp-bpf", "-e", "trace=socket,connect,bind,listen,accept4", "-o", self.strace] + cmd
        self.logf = open(self.log, "ab")
        self.p = subprocess.Popen(cmd, env=e, user=SVC_UID, group=SVC_UID, extra_groups=[], pass_fds=fds,
                                  stdout=self.logf, stderr=subprocess.STDOUT, cwd="/")
        if ls:
            ls.close()
        if wait:
            dl = time.monotonic() + 30
            while time.monotonic() < dl:
                if self.p.poll() is not None:
                    raise RuntimeError("server %s exited %s" % (self.name, self.p.returncode))
                try:
                    r = gc_http.request(self.env.sock, "GET", "/health", timeout=5)
                    if r.status in (200, 503):
                        return
                except OSError:
                    pass
                time.sleep(0.2)
            raise RuntimeError("server %s did not answer" % self.name)

    def stop(self, sig=signal.SIGTERM, timeout=15):
        if not self.p or self.p.poll() is not None:
            return (self.p.returncode if self.p else None), 0.0
        t0 = time.monotonic()
        self.p.send_signal(sig)
        try:
            rc = self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()
            rc = self.p.wait()
            return "killed", time.monotonic() - t0
        return rc, time.monotonic() - t0

    def logtext(self):
        with open(self.log, "rb") as f:
            return f.read().decode("utf-8", "replace")


def collapsed(states):
    """compute-state sequence with consecutive duplicates folded"""
    out = []
    for x in states:
        if not out or out[-1] != x:
            out.append(x)
    return out


def as_uid(env, uid, method, url, body=None):
    cmd = [sys.executable, "-I", "-B", os.path.join(HERE, "gc_client.py"), env.sock, method, url]
    if body is not None:
        cmd.append(json.dumps(body))
    out = subprocess.run(cmd, user=uid, group=uid, extra_groups=[], capture_output=True, text=True, timeout=300,
                         env={"PATH": "/usr/bin:/bin"})
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"status": None, "body": out.stdout, "error": out.stderr[-300:]}


CHAT = {"model": "tiny", "messages": [{"role": "user", "content": "Hello there"}], "max_tokens": 16,
        "temperature": 0}


def chat(env, body=None, **kw):
    return gc_http.request(env.sock, "POST", "/v1/chat/completions", body or CHAT, **kw)


def big_body(env):
    """2 MiB body: the server answers 413 after the header and closes, so our send may hit EPIPE; read anyway."""
    s = gc_http.connect(env.sock)
    try:
        try:
            gc_http.send(s, "POST", "/v1/completions", raw=json.dumps({"model": "tiny", "prompt": "a" * (2 << 20)})
                         .encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        return gc_http.read_response(s)
    finally:
        s.close()


def launcher_refusals(env):
    """GC-11 precursor (host): the launcher exits 3 NO_DEVICE without a native image, nouveau node or payload."""
    R = env.root
    exe = R + "/vendor/bin/gb_gpu_compute"
    base = {"PATH": "/usr/bin:/bin", "GB_COMPUTE_ROOT": R}

    def run(args=()):
        p = subprocess.run([exe] + list(args), env=base, user=SVC_UID, group=SVC_UID, extra_groups=[],
                           capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout + p.stderr).strip().splitlines()[-1:]

    wfile(R + "/odm/etc/gb_hw", "vm\n")
    rc, out = run()
    rec("GC-11a", "launcher on a VM image (gb_hw=vm)", rc == 3, "exit %s %s" % (rc, out))
    wfile(R + "/odm/etc/gb_hw", "native\n")
    os.rename(R + "/sys/class/drm/renderD129", R + "/sys/class/drm/off-renderD129")
    rc, out = run()
    rec("GC-11b", "launcher without a nouveau render node", rc == 3, "exit %s %s" % (rc, out))
    os.rename(R + "/sys/class/drm/off-renderD129", R + "/sys/class/drm/renderD129")
    lib = R + "/odm/dgpu/lib64/gpu-compute/libgb_gpu_compute.so"
    os.rename(lib, lib + ".off")
    rc, out = run()
    rec("GC-11c", "launcher without the payload library", rc == 3, "exit %s %s" % (rc, out))
    os.rename(lib + ".off", lib)
    rc, out = run(["--version"])
    rec("GC-11d", "launcher -> dlopen payload ($ORIGIN RUNPATH) -> gb_gpu_compute_main --version", rc == 0,
        "exit %s %s" % (rc, out))
    rc, out = run()
    rec("GC-11e", "service without an init socket (manual start)", rc == 4, "exit %s %s" % (rc, out))


def main():
    build, q4, q8, run = sys.argv[1:5]
    env = Env(build, q4, q8, run)
    launcher_refusals(env)
    env.mock.start()

    # ------------------------------------------------------------------ server A: defaults, init socket
    A = Server(env, "A", {"GB_COMPUTE_KEEPALIVE_MS": "600000"})  # exact state sequences below: no keep-alive lines
    A.start()
    r = gc_http.request(env.sock, "GET", "/health")
    rec("GC-03a", "GET /health", r.status == 200 and r.json() == {"status": "ok", "gb_state": "idle"},
        "%d %s" % (r.status, r.body.decode()))
    r = gc_http.request(env.sock, "GET", "/v1/models")
    ids = [m["id"] for m in r.json()["data"]] if r.status == 200 else []
    sizes = {m["id"]: m["size"] for m in r.json()["data"]} if r.status == 200 else {}
    rec("GC-03b", "GET /v1/models (regular <id>.gguf files only)",
        r.status == 200 and ids == ["huge", "tiny", "tiny8"] and sizes.get("tiny") == os.path.getsize(q4)
        and not any(m["gb_loaded"] for m in r.json()["data"]), "%d ids=%s" % (r.status, ids))
    t0 = time.monotonic()
    r = chat(env, timeout=600)
    j = r.json() if r.status == 200 else {}
    c1 = j.get("choices", [{}])[0].get("message", {}).get("content")
    rec("GC-03c", "POST /v1/chat/completions (non-stream, greedy, max_tokens 16, first load)",
        r.status == 200 and j.get("object") == "chat.completion" and isinstance(c1, str)
        and j["usage"]["completion_tokens"] == 16 and j["choices"][0]["finish_reason"] == "length",
        "%d in %.1f s usage=%s finish=%s" % (r.status, time.monotonic() - t0, j.get("usage"),
                                             j.get("choices", [{}])[0].get("finish_reason")))
    st = env.mock.states()
    rec("GC-08a", "gb_power sequence on the first request",
        env.mock.lines(SVC_UID)[:1] == ["hello"] and st[:5] == ["idle", "loading", "loaded", "serving", "loaded"],
        "lines=%s" % env.mock.lines(SVC_UID)[:7])
    rec("GC-08b", "power hold applied while loaded (mock wrote power/control)", env.power() == "on",
        "power/control=%s" % env.power())
    r = gc_http.request(env.sock, "GET", "/v1/models")
    lo = {m["id"]: m["gb_loaded"] for m in r.json()["data"]}
    h = gc_http.request(env.sock, "GET", "/health").json()
    rec("GC-03d", "loaded model reported", lo.get("tiny") is True and lo.get("tiny8") is False
        and h["gb_state"] == "loaded", "gb_loaded=%s health=%s" % (lo, h))
    body = dict(CHAT, stream=True)
    r = chat(env, body, timeout=300)
    ev = r.events() if r.status == 200 else []
    deltas = "".join(e["choices"][0]["delta"].get("content", "") for e in ev if isinstance(e, dict))
    rec("GC-03e", "chat completion stream=true (SSE)",
        r.status == 200 and r.headers.get("content-type") == "text/event-stream" and ev and ev[-1] == "[DONE]"
        and ev[0]["choices"][0]["delta"].get("role") == "assistant"
        and ev[-2]["choices"][0]["finish_reason"] == "length" and deltas == c1,
        "%d events=%d streamed text == non-stream text: %s" % (r.status, len(ev), deltas == c1))
    r = gc_http.request(env.sock, "POST", "/v1/completions",
                        {"model": "tiny", "prompt": "Once upon a time", "max_tokens": 8, "temperature": 0})
    j = r.json() if r.status == 200 else {}
    rec("GC-03f", "POST /v1/completions", r.status == 200 and j.get("object") == "text_completion"
        and j["usage"]["completion_tokens"] == 8, "%d usage=%s" % (r.status, j.get("usage")))
    r = gc_http.request(env.sock, "POST", "/v1/completions",
                        {"model": "tiny", "prompt": "Once upon a time", "max_tokens": 8, "temperature": 0,
                         "stream": True})
    ev = r.events() if r.status == 200 else []
    rec("GC-03g", "POST /v1/completions stream", r.status == 200 and ev[-1] == "[DONE]"
        and ev[-2]["choices"][0]["finish_reason"] == "length", "%d events=%d" % (r.status, len(ev)))
    r = chat(env, dict(CHAT, max_tokens=200, temperature=0.9, seed=7, stop=["e"]))
    j = r.json() if r.status == 200 else {}
    txt = j.get("choices", [{}])[0].get("message", {}).get("content", "")
    rec("GC-03h", "stop string honoured (stop=['e'])", r.status == 200 and "e" not in txt
        and j["choices"][0]["finish_reason"] in ("stop", "length"),
        "%d finish=%s len=%d" % (r.status, j.get("choices", [{}])[0].get("finish_reason"), len(txt)))
    r1 = chat(env, dict(CHAT, temperature=0.8, seed=42)).json()["choices"][0]["message"]["content"]
    r2 = chat(env, dict(CHAT, temperature=0.8, seed=42)).json()["choices"][0]["message"]["content"]
    rec("GC-03i", "seeded sampling is reproducible", r1 == r2, "same text: %s" % (r1 == r2))

    # ---- request validation and limits (GC-05 body / model size)
    cases = [
        ("bad JSON", gc_http.request(env.sock, "POST", "/v1/chat/completions", raw=b"{nope"), 400, "invalid_request"),
        ("no model", chat(env, {"messages": [{"role": "user", "content": "x"}]}), 400, "invalid_request"),
        ("bad model id", chat(env, dict(CHAT, model="../etc/x")), 400, "invalid_request"),
        ("unknown model", chat(env, dict(CHAT, model="nosuch")), 404, "not_found"),
        ("symlinked model", chat(env, dict(CHAT, model="link")), 404, "not_found"),
        ("max_tokens 5000", chat(env, dict(CHAT, max_tokens=5000)), 400, "invalid_request"),
        ("GET on a POST path", gc_http.request(env.sock, "GET", "/v1/chat/completions"), 400, "invalid_request"),
        ("unknown path", gc_http.request(env.sock, "GET", "/v2/x"), 404, "not_found"),
        ("chunked body", gc_http.request(env.sock, "POST", "/v1/completions", raw=b"",
                                         headers={"Transfer-Encoding": "chunked"}), 400, "invalid_request"),
        ("2 MiB body", big_body(env), 413, "too_large"),
        ("model > 3584 MiB", chat(env, dict(CHAT, model="huge")), 413, "too_large"),
    ]
    for name, r, code, typ in cases:
        try:
            e = r.json()["error"]
            ok = r.status == code and e["type"] == typ and e["code"] == code
        except (ValueError, KeyError):
            ok = False
        rec("GC-05", "error: " + name, ok, "%d %s" % (r.status, r.body[:120].decode("utf-8", "replace")))

    # ---- peer credentials (GC-04)
    for uid, want, why in ((2000, 200, "shell"), (10123, 200, "GbGpu appId (data dir owner)"),
                           (1010123, 200, "GbGpu appId, user 10"), (10200, 200, "compute_allow"),
                           (1010200, 200, "compute_allow, user 10"), (10999, 403, "not listed"),
                           (10555, 403, "packages.list decoy (unreadable for the service)"),
                           (1000, 403, "system uid"), (9999, 403, "allowlist line for a system uid ignored")):
        o = as_uid(env, uid, "GET", "/health")
        ok = o["status"] == want
        if want == 403:
            try:
                ok = ok and json.loads(o["body"])["error"]["type"] == "forbidden"
            except (ValueError, KeyError, TypeError):
                ok = False
        rec("GC-04", "uid %d (%s) -> %d" % (uid, why, want), ok, "status=%s %s" % (o["status"], o["error"]))
    o = as_uid(env, 10999, "POST", "/v1/chat/completions", CHAT)
    rec("GC-04", "uid 10999 POST chat -> 403 (no generation)", o["status"] == 403, "status=%s" % o["status"])
    wfile(env.root + "/data/vendor/gb/compute_allow", "10200 com.example.allowed\n10999 com.example.added\n")
    o = as_uid(env, 10999, "GET", "/health")
    rec("GC-04", "compute_allow re-read on mtime change (uid 10999 added)", o["status"] == 200,
        "status=%s" % o["status"])
    os.rename(env.root + "/odm/app/GbGpu/GbGpu.apk", env.root + "/odm/app/GbGpu/GbGpu.apk.off")
    time.sleep(5.5)
    o = as_uid(env, 10123, "GET", "/health")
    rec("GC-04", "GbGpu uid refused when the system APK is absent", o["status"] == 403, "status=%s" % o["status"])
    os.rename(env.root + "/odm/app/GbGpu/GbGpu.apk.off", env.root + "/odm/app/GbGpu/GbGpu.apk")

    # ---- connection limit and queue (GC-05)
    long_body = dict(CHAT, max_tokens=4096, stream=True)
    s1 = gc_http.connect(env.sock)
    gc_http.send(s1, "POST", "/v1/chat/completions", long_body)
    st1, _, _ = gc_http.read_head(s1)
    s2 = gc_http.connect(env.sock)
    gc_http.send(s2, "POST", "/v1/chat/completions", dict(CHAT, model="tiny8"))
    r = gc_http.read_response(s2)
    rec("GC-05", "other model while a request runs -> 409", r.status == 409 and r.json()["error"]["type"] == "conflict",
        "%d %s" % (r.status, r.body[:100]))
    s2.close()
    r = gc_http.request(env.sock, "POST", "/gb/unload")
    rec("GC-05", "/gb/unload while a request runs -> 409", r.status == 409, "%d" % r.status)
    time.sleep(0.5)  # let the server reap the two closed connections before counting
    qs = []
    for _ in range(3):
        s = gc_http.connect(env.sock)
        gc_http.send(s, "POST", "/v1/chat/completions", dict(CHAT, max_tokens=4))
        qs.append(s)
    time.sleep(0.5)
    s5 = gc_http.connect(env.sock)
    gc_http.send(s5, "GET", "/health")
    r = gc_http.read_response(s5)
    closed = s5.recv(1) == b""
    rec("GC-05", "5th connection refused (4 open: 1 generating + 3 queued)",
        r.status == 429 and r.json()["error"]["type"] == "busy" and closed, "%d closed=%s" % (r.status, closed))
    s5.close()
    s1.close()  # client goes away mid-stream: the generation must be aborted
    t0 = time.monotonic()
    rs = [gc_http.read_response(s, 120) for s in qs]
    for s in qs:
        s.close()
    rec("GC-05", "queued requests served after the running one is aborted by its client",
        all(x.status == 200 for x in rs) and time.monotonic() - t0 < 60 and st1 == 200,
        "statuses=%s in %.1f s" % ([x.status for x in rs], time.monotonic() - t0))
    lg = A.logtext()
    rec("GC-05", "disconnect aborts the generation", "(aborted)" in lg, "log has '(aborted)': %s" % ("(aborted)" in lg))
    r = gc_http.request(env.sock, "POST", "/gb/unload")
    st = env.mock.states()
    rec("GC-08c", "/gb/unload -> unloading, idle; hold released", r.status == 200 and r.json() == {"unloaded": True}
        and st[-2:] == ["unloading", "idle"] and env.power() == "auto",
        "%d %s states=%s power=%s" % (r.status, r.body.decode(), st[-3:], env.power()))

    # ---- no TCP (GC-07): every socket of the running service is AF_UNIX
    chat(env)
    pid = int(subprocess.run(["pgrep", "-f", "-n", env.root + "/vendor/bin/gb_gpu_compute"], capture_output=True,
                             text=True).stdout.split()[0])
    inodes = set()
    for fd in os.listdir("/proc/%d/fd" % pid):
        try:
            t = os.readlink("/proc/%d/fd/%s" % (pid, fd))
        except OSError:
            continue
        if t.startswith("socket:["):
            inodes.add(t[8:-1])
    unix = set(l.split()[6] for l in open("/proc/net/unix").read().splitlines()[1:] if len(l.split()) > 6)
    inet = set()
    for f in ("tcp", "tcp6", "udp", "udp6", "raw", "raw6"):
        for l in open("/proc/net/" + f).read().splitlines()[1:]:
            p = l.split()
            if len(p) > 9:
                inet.add(p[9])
    rec("GC-07a", "service sockets are AF_UNIX only (/proc/<pid>/fd vs /proc/net/*)",
        inodes and inodes <= unix and not (inodes & inet), "%d sockets, all unix: %s, inet: %d"
        % (len(inodes), inodes <= unix, len(inodes & inet)))

    # ---- SIGTERM during a generation (GC-06b)
    s1 = gc_http.connect(env.sock)
    gc_http.send(s1, "POST", "/v1/chat/completions", long_body)
    gc_http.read_head(s1)
    time.sleep(1.0)
    rc, el = A.stop(signal.SIGTERM, 15)
    rest = b""
    try:
        while True:
            d = s1.recv(65536)
            if not d:
                break
            rest += d
    except OSError:
        pass
    s1.close()
    st = env.mock.states()
    rec("GC-06b", "SIGTERM during a generation: exit 0 within 5 s", rc == 0 and el < 5.0,
        "rc=%s in %.2f s; last states=%s; power=%s" % (rc, el, st[-3:], env.power()))
    rec("GC-08d", "SIGTERM path releases the hold", env.power() == "auto" and st[-1] == "idle",
        "states=%s" % st[-4:])

    # ------------------------------------------------------------------ server B: short timers, path socket, strace
    os.makedirs(os.path.join(run, "strace"), exist_ok=True)
    os.chmod(os.path.join(run, "strace"), 0o777)
    st_log = os.path.join(run, "strace", "strace-B.txt")
    B = Server(env, "B", {"GB_COMPUTE_IDLE_S": "8", "GB_COMPUTE_SCREEN_OFF_S": "2", "GB_COMPUTE_SCREEN_POLL_MS": "500",
                          "GB_COMPUTE_TEST_MAX_CONN": "6", "GB_COMPUTE_HOLD_LOST_MS": "1500",
                          "GB_COMPUTE_KEEPALIVE_MS": "600000"},
               init_socket=False, strace=st_log)
    B.start()
    long_s = gc_http.connect(env.sock)
    gc_http.send(long_s, "POST", "/v1/chat/completions", long_body)
    gc_http.read_head(long_s, 600)
    qs = []
    for _ in range(3):
        s = gc_http.connect(env.sock)
        gc_http.send(s, "POST", "/v1/chat/completions", dict(CHAT, max_tokens=2))
        qs.append(s)
    time.sleep(0.5)
    r = chat(env, dict(CHAT, max_tokens=2))
    rec("GC-05", "queue full: 1 in flight + 3 queued -> 429 (max_conn raised to 6)",
        r.status == 429 and "queue" in r.json()["error"]["message"], "%d %s" % (r.status, r.body[:100]))
    long_s.close()
    for s in qs:
        gc_http.read_response(s)
        s.close()
    # screen off >= 2 s -> unload, keep running
    wfile(env.root + "/sys/class/drm/card0-eDP-1/dpms", "Off\n")
    t0 = time.monotonic()
    unloaded = False
    while time.monotonic() - t0 < 10:
        if env.mock.states()[-1:] == ["idle"] and env.power() == "auto":
            unloaded = True
            break
        time.sleep(0.2)
    h = gc_http.request(env.sock, "GET", "/health")
    rec("GC-06c", "screen off (eDP dpms Off) -> model unloaded, service keeps running",
        unloaded and h.status == 200 and h.json()["gb_state"] == "idle" and "screen off" in B.logtext(),
        "after %.1f s, health=%s" % (time.monotonic() - t0, h.body.decode()))
    wfile(env.root + "/sys/class/drm/card0-eDP-1/dpms", "On\n")
    # gb_power lost while loaded -> unload after the grace time; back -> loads again
    chat(env)
    env.mock.stop()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 10 and "power hold lost" not in B.logtext():
        time.sleep(0.2)
    lost = "power hold lost" in B.logtext()
    r = chat(env)
    rec("GC-08e", "gb_power gone: model unloaded after the grace time; new loads refused (503)",
        lost and r.status == 503 and r.json()["error"]["type"] == "gb_unavailable",
        "unloaded=%s, request -> %d %s" % (lost, r.status, r.body[:90]))
    n0 = len(env.mock.lines(SVC_UID))
    env.mock.start()
    r = chat(env)
    again = env.mock.lines(SVC_UID)[n0:]
    rec("GC-08f", "gb_power back: reconnect, hello, loads again", r.status == 200
        and again[:1] == ["hello"] and "compute-state loading" in again[1:3], "%d lines=%s" % (r.status, again[:5]))
    env.mock.deny = True
    gc_http.request(env.sock, "POST", "/gb/unload")
    r = chat(env)
    rec("GC-08g", "gb_power refuses compute-state (EPERM) -> 503, no model", r.status == 503, "%d" % r.status)
    env.mock.deny = False
    wfile(env.root + "/proc/meminfo", "MemTotal: 16000000 kB\nMemAvailable: 1000 kB\n")
    r = chat(env)
    rec("GC-05", "not enough free memory -> 503", r.status == 503 and "memory" in r.json()["error"]["message"],
        "%d %s" % (r.status, r.body[:90]))
    wfile(env.root + "/proc/meminfo", "MemTotal: 16000000 kB\nMemAvailable: 9000000 kB\n")
    chat(env)
    # idle exit (GC-06a): no connection, nothing in flight for 8 s -> unload, idle, exit 0
    t0 = time.monotonic()
    try:
        rc = B.p.wait(30)
    except subprocess.TimeoutExpired:
        rc = None
    el = time.monotonic() - t0
    st = collapsed(env.mock.states())
    rec("GC-06a", "idle exit after the idle time (8 s in this run, 120 s default): unload, idle, exit 0",
        rc == 0 and 7.0 <= el <= 15.0 and st[-2:] == ["unloading", "idle"],
        "rc=%s after %.1f s states=%s" % (rc, el, st[-3:]))
    stxt = open(st_log).read()
    bad = [l for l in stxt.splitlines() if "AF_INET" in l or "AF_INET6" in l]
    nsock = sum(1 for l in stxt.splitlines() if "socket(" in l)
    rec("GC-07b", "strace: no AF_INET/AF_INET6 socket during a full run", nsock > 0 and not bad,
        "%d socket() calls, %d inet" % (nsock, len(bad)))

    # ------------------------------------------------------------------ server D: long quiet hold (review R1-H1)
    # The compute-svc connection must keep its hold while a loaded model sits idle. The mock first behaves like
    # gb_power (30 s idle close, compute-svc exempt), then like a strict server that idle-closes every connection
    # after 2.5 s: the service's keep-alive (1 s here, 15 s default) must then keep the connection and the hold.
    env.mock.stop()
    env.mock = MockGbPower(env.root + "/dev/socket/gb_power", env.power_control, idle_close_s=2.5, exempt_compute=True)
    env.mock.start()
    D = Server(env, "D", {"GB_COMPUTE_IDLE_S": "120", "GB_COMPUTE_KEEPALIVE_MS": "600000"})
    D.start()
    r = chat(env)
    time.sleep(8)
    rec("GC-08h", "exempt compute-svc connection: hold kept through 8 s without a request (no keep-alive)",
        r.status == 200 and env.power() == "on" and env.mock.idle_closed == 0
        and "connection lost" not in D.logtext(), "%d power=%s idle_closed=%d" % (r.status, env.power(),
                                                                                 env.mock.idle_closed))
    D.stop()
    env.mock.stop()
    env.mock = MockGbPower(env.root + "/dev/socket/gb_power", env.power_control, idle_close_s=2.5, exempt_compute=False)
    env.mock.start()
    D = Server(env, "D2", {"GB_COMPUTE_IDLE_S": "120", "GB_COMPUTE_KEEPALIVE_MS": "1000"})
    D.start()
    r = chat(env)
    n0 = len(env.mock.states())
    time.sleep(8)
    resent = env.mock.states()[n0:]
    rec("GC-08i", "strict server (idle close 2.5 s for every connection): keep-alive keeps the hold for 8 s",
        r.status == 200 and env.power() == "on" and env.mock.idle_closed == 0 and len(resent) >= 3
        and set(resent) == {"loaded"} and "connection lost" not in D.logtext(),
        "%d power=%s idle_closed=%d re-sent=%d %s" % (r.status, env.power(), env.mock.idle_closed, len(resent),
                                                      sorted(set(resent))))
    D.stop()
    env.mock.stop()
    env.mock = MockGbPower(env.root + "/dev/socket/gb_power", env.power_control)
    env.mock.start()

    # ------------------------------------------------------------------ server C: no GPU device (GC-11 style)
    C = Server(env, "C", {"GB_NVK_ICD": "/nonexistent/vulkan.nouveau.so", "GB_COMPUTE_IDLE_S": "20"})
    C.start()
    r = chat(env)
    h = gc_http.request(env.sock, "GET", "/health")
    rec("GC-11f", "no Vulkan device (ICD missing): 503 gb_unavailable, no CPU fallback; /health 503",
        r.status == 503 and r.json()["error"]["type"] == "gb_unavailable" and h.status == 503,
        "%d %s; health %d" % (r.status, r.body[:100], h.status))
    rc, el = C.stop()
    rec("GC-11g", "service without a device stops cleanly", rc == 0, "rc=%s in %.2f s" % (rc, el))
    env.mock.stop()

    fails = [x for x in RESULTS if x[2] != "PASS"]
    print("\nSUMMARY %d checks, %d failures" % (len(RESULTS), len(fails)))
    with open(os.path.join(run, "results.json"), "w") as f:
        json.dump(RESULTS, f, indent=1)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
