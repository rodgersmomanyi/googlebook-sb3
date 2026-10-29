r"""W5 VNNI measurement, one throwaway VM instance per process (run 'off' and 'on' at the same time).

  python -I -B run_w5.py --inst off|on OUT_ROOT [--barrier-timeout SECS]

Prerequisites (done by the caller with w5vm.py): the instance's disk.img == the integrate step's pristine image except
its EFI-SYSTEM (off: esp\vnni-off.img, on: esp\vnni-on.img), mkvm, guard OK.
Steps (each writes OUT_ROOT\<inst>\NN-name.txt; key results in OUT_ROOT\<inst>\RESULT.json):
  start; wait for init's 'started service gb_console' in serial0.log (no console client before the guest has opened
  ttyS1); probe the root console; push + start the detached sampler (gbw5_sampler.sh: gb_vnni summary at uptime
  60/200/420/... s and right after sys.boot_completed); wait for sys.boot_completed + gb_adb (serial0.log only, no
  adb polling), for uptime >= 425 s and for gb_dexopt's 'GB_DEXOPT done'; barrier with the other instance; on-device
  checks (fingerprints, cmdline, /odm/vnni, mountinfo, sha256, maps, kmsg/SELinux); adler32 KAT (system + vendor
  libz, dex checksum, host zlib stream, throughput); adb install of our own GbGpu.apk; 'cmd package compile -m speed -f'
  on 4 packages (wall time per package, counter before/after); logcat/tombstone checks; final snapshot; power off.
The user's VM, its pipe and its adb address are never touched (w5vm.py refuses them)."""
import importlib.util, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
assert args[0] == "--inst"
INST = args[1]
OUT_ROOT = os.path.abspath(args[2])
BARRIER_TIMEOUT = float(args[args.index("--barrier-timeout") + 1]) if "--barrier-timeout" in args else 1200
BOOT2 = "--boot2" in args          # second boot of the same disk (no reset): samples, checks, crash/ANR records only
os.environ["GBW5_INST"] = INST
spec = importlib.util.spec_from_file_location("w5vm", os.path.join(HERE, "w5vm.py"))
vm = importlib.util.module_from_spec(spec); spec.loader.exec_module(vm)
vm.select(INST)
OUT = os.path.join(OUT_ROOT, INST)
os.makedirs(OUT, exist_ok=True)
SERIAL = vm.VMDIR + "\\serial0.log"
APK = r"C:\GB-build\native\gpu-ui\out\GbGpu.apk"
KAT = os.path.join(HERE, "adler_kat", "out") if os.path.isdir(os.path.join(HERE, "adler_kat", "out")) else os.path.join(HERE, "adler_kat")
COMPILE_PKGS = ["com.android.settings", "com.android.systemui", "com.google.android.apps.nexuslauncher", "gb.sb3.gpu"]
R = {"inst": INST, "host_start": None, "checks": {}}
N = [0]


def log(msg):
    line = time.strftime("%H:%M:%S") + f" [{INST}] " + msg
    print(line, flush=True)
    with open(os.path.join(OUT, "run.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def save(name, text):
    N[0] += 1
    p = os.path.join(OUT, f"{N[0]:02d}-{name}.txt")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p


def dump():
    with open(os.path.join(OUT, "RESULT.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1)


def sh(cmd, t=120, name=None):
    st, out = vm.run(cmd, t, echo=False)
    txt = out.decode("utf-8", "replace")
    if name:
        save(name, f"$ {cmd if len(cmd) < 2000 else cmd[:2000] + ' ...'}\n(exit {st})\n{txt}")
    return st, txt


def adb(*a, t=120, name=None):
    p = vm.adb(R["ip"], *a, timeout=t)
    if name:
        save(name, f"$ adb {' '.join(a)}\n(exit {p.returncode})\n{p.stdout}{p.stderr}")
    return p


def serial_lines():
    if not os.path.exists(SERIAL):
        return []
    with open(SERIAL, "rb") as f:
        d = f.read().decode("utf-8", "replace").replace("\r", "")
    out, seen = [], set()
    for l in d.split("\n"):
        l2 = re.sub(r"^<\d+>", "", l)
        if l2 not in seen:
            seen.add(l2); out.append(l2)
    return out


def ktime(l):
    m = re.match(r"\[\s*([\d.]+)\]", l)
    return float(m.group(1)) if m else None


def wait_serial(pattern, limit, every=2):
    t0 = time.time()
    while time.time() - t0 < limit:
        for l in serial_lines():
            m = re.search(pattern, l)
            if m:
                return l, m
        if not vm.is_running():
            return None, None
        time.sleep(every)
    return None, None


def summary_first(txt):
    m = re.search(r"emulated (\d+) vex128 (\d+) vex256 (\d+) word (\d+) byte (\d+) irqon (\d+) efault (\d+)", txt)
    return {k: int(v) for k, v in zip(("emulated", "vex128", "vex256", "word", "byte", "irqon", "efault"), m.groups())} if m else None


def snap(label):
    st, txt = sh("read u r < /proc/uptime; echo uptime=$u; cat /sys/kernel/gb_vnni/summary", 30, f"summary-{label}")
    up = re.search(r"uptime=([\d.]+)", txt)
    s = summary_first(txt)
    R.setdefault("snaps", {})[label] = {"uptime": float(up.group(1)) if up else None, **(s or {})}
    dump()
    return R["snaps"][label]


def barrier(name):
    d = os.path.join(OUT_ROOT, "barrier")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, f"{name}-{INST}"), "w").write(time.strftime("%Y-%m-%dT%H:%M:%S"))
    other = "on" if INST == "off" else "off"
    t0 = time.time()
    while time.time() - t0 < BARRIER_TIMEOUT:
        if os.path.exists(os.path.join(d, f"{name}-{other}")) or os.path.exists(os.path.join(d, f"{name}-{other}.gone")):
            log(f"barrier {name}: both ready after {time.time() - t0:.0f} s")
            R.setdefault("barriers", {})[name] = round(time.time() - t0)
            return True
        time.sleep(2)
    log(f"barrier {name}: TIMEOUT, continuing alone")
    R.setdefault("barriers", {})[name] = "timeout"
    return False


def main():
    # ---------------------------------------------------------------- start
    g = subprocess.run([sys.executable, "-I", "-B", os.path.join(HERE, "w5vm.py"), "--inst", INST, "guard"],
                       capture_output=True, text=True)
    save("guard", g.stdout + g.stderr)
    if g.returncode:
        log("GUARD FAILED"); sys.exit(1)
    e = subprocess.run([sys.executable, "-I", "-B", os.path.join(HERE, "w5vm.py"), "--inst", INST, "espsha"],
                       capture_output=True, text=True)
    save("espsha", e.stdout + e.stderr)
    R["esp_sha256"] = (re.search(r"sha256 ([0-9a-f]{64})", e.stdout) or [None, None])[1]
    R["host_start"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    t_start = time.time()
    rc = vm.start()
    log(f"vm start rc={rc}")
    if rc:
        sys.exit(1)
    # ---------------------------------------------------------------- console + sampler
    l, m = wait_serial(r"init: \.\.\. started service 'gb_console' has pid", 600)
    if not l:
        log("gb_console never started"); R["error"] = "no gb_console"; dump(); sys.exit(1)
    R["gb_console_started_k"] = ktime(l)
    log(f"gb_console started at k={ktime(l)} (host +{time.time() - t_start:.0f} s)")
    time.sleep(4)
    ok = False
    for i in range(4):
        st, txt = sh("echo GBW5_PROBE_$((40+2)); cat /proc/uptime", 20, f"console-probe{i}")
        if "GBW5_PROBE_42" in txt:
            ok = True; break
        time.sleep(5)
    R["console_ok"] = ok
    if not ok:
        log("CONSOLE DEAD"); dump()
    else:
        assert vm.push(os.path.join(HERE, "gbw5_sampler.sh"), "/dev/gbw5_sampler.sh") == 0
        st, txt = sh("/system/bin/toybox setsid /system/bin/sh /dev/gbw5_sampler.sh </dev/null >/dev/null 2>&1 & "
                     "sleep 2; cat /dev/gbw5/series.txt; cat /proc/uptime", 30, "sampler-started")
        R["sampler_started"] = "start t=" in txt
        log(f"sampler started: {R['sampler_started']} :: {txt.strip()[:160]}")
    dump()
    # ---------------------------------------------------------------- boot_completed (serial only)
    l, m = wait_serial(r"init: processing action \(sys\.boot_completed=1\)", 1500, 3)
    R["boot_completed_k"] = ktime(l) if l else None
    log(f"sys.boot_completed=1 at k={R['boot_completed_k']} (host +{time.time() - t_start:.0f} s)")
    l, m = wait_serial(r"gb_adb: adbd on tcp:5555 \(inet ([0-9.]+)\)", 600, 3)
    if not m:
        log("no gb_adb line"); R["error"] = "no gb_adb"; dump(); sys.exit(1)
    R["ip"] = m.group(1)
    assert R["ip"] != vm.USER_VM_ADB, "the throwaway got the user VM's address"
    log(f"gb_adb ip {R['ip']}")
    dump()
    # ---------------------------------------------------------------- wait for 420 s sample + gb_dexopt
    t0 = time.time()
    while time.time() - t0 < 1800:
        lines = serial_lines()
        s420 = [l for l in lines if "GBW5S t420 " in l]
        done = [l for l in lines if re.search(r"GB_DEXOPT (done|already done)", l)]
        if (s420 or not R.get("sampler_started")) and done:
            break
        time.sleep(5)
    lines = serial_lines()
    dex = [(ktime(l), l.split("] ", 2)[-1]) for l in lines if "GB_DEXOPT" in l]
    R["gb_dexopt"] = dex
    st_ = [k for k, t in dex if "GB_DEXOPT start" in t]
    dn = [k for k, t in dex if "GB_DEXOPT done" in t]
    R["gb_dexopt_wall_k"] = round(dn[0] - st_[0], 1) if st_ and dn else None
    log(f"gb_dexopt: {len(st_)} packages, wall {R['gb_dexopt_wall_k']} s (kernel time)")
    dump()
    barrier("after-boot")
    # ---------------------------------------------------------------- on-device checks
    snap("checks-begin")
    st, txt = sh(
        "echo '=== fingerprints'; getprop ro.system.build.fingerprint; getprop ro.vendor.build.fingerprint; "
        "echo '=== cmdline gb.vnni'; tr ' ' '\\n' < /proc/cmdline | grep -a gb.vnni; echo '(end)'; "
        "echo '=== odm/vnni'; ls -lRZ /odm/vnni 2>&1 | head -30; "
        "echo '=== mountinfo init (pid 1)'; grep -a -E 'libz|vnni' /proc/1/mountinfo; "
        "echo '=== mountinfo console shell'; grep -a -E 'libz|vnni' /proc/self/mountinfo; "
        "SS=$(pidof system_server); echo \"=== mountinfo system_server ($SS)\"; grep -a -E 'libz|vnni' /proc/$SS/mountinfo; "
        "echo '=== sha256'; sha256sum /system/lib64/libz.so /vendor/lib64/libz.so /odm/vnni/*/system/lib64/libz.so "
        "/odm/vnni/*/vendor/lib64/libz.so 2>&1; "
        "echo '=== stat (dev inode)'; stat -L -c '%d %i %s %n' /system/lib64/libz.so /vendor/lib64/libz.so "
        "/odm/vnni/*/*/lib64/libz.so 2>&1; "
        "echo '=== ls -lZ'; ls -lZ /system/lib64/libz.so /vendor/lib64/libz.so; "
        "for p in system_server surfaceflinger zygote64 logd; do P=$(pidof $p | cut -d' ' -f1); "
        "echo \"=== maps $p ($P)\"; grep -a 'libz.so' /proc/$P/maps | head -3; done; "
        "echo '=== libz bind ro check'; grep -a 'libz.so' /proc/self/mountinfo | grep -c ' ro,'; "
        "echo '=== uname'; uname -r; echo '=== slot'; getprop ro.boot.slot_suffix; echo '=== selinux'; getenforce",
        120, "checks-device")
    R["checks"]["device_txt"] = os.path.basename(save("checks-device-copy", txt))
    R["fp_system"] = (re.search(r"=== fingerprints\n(.*)\n(.*)\n", txt) or [None, None, None])[1]
    R["mountinfo_libz_lines"] = len(re.findall(r"libz\.so", txt.split("=== mountinfo console shell")[1].split("===")[0])) \
        if "=== mountinfo console shell" in txt else None
    shas = dict((p, h) for h, p in re.findall(r"^([0-9a-f]{64})  (\S+)$", txt, re.M))
    R["sha256"] = shas
    dump()
    # kmsg: init's lines for the vnni actions, parse errors, SELinux
    lines = serial_lines()
    keep = [l for l in lines if re.search(r"unexported|vnni|libz|gb\.rc.*(fail|error|invalid)|Unable to parse|"
                                          r"property trigger|restorecon.*(fail|error)", l, re.I)
            and "gb_vnni: emulating" not in l and "GBW5S" not in l]
    save("kmsg-vnni-actions", "\n".join(keep) + "\n")
    den = [l for l in lines if "avc:" in l and "denied" in l]
    den_vnni = [l for l in den if re.search(r"libz|vnni", l)]
    enforced = [l for l in den if "permissive=0" in l]
    save("kmsg-avc", f"avc denied lines: {len(den)} (permissive=0: {len(enforced)}; naming libz/vnni: {len(den_vnni)})\n"
         + "\n".join(den_vnni + enforced) + "\n")
    R["avc_denied"] = len(den); R["avc_enforced"] = len(enforced); R["avc_vnni"] = len(den_vnni)
    R["kmsg_vnni_action_lines"] = keep[:40]
    dump()
    if BOOT2:
        st, txt = sh("for t in data_app_crash system_app_crash system_server_crash data_app_anr system_app_anr "
                     "SYSTEM_TOMBSTONE data_app_native_crash; do echo \"=== dropbox $t\"; "
                     "dumpsys dropbox --print $t 2>&1 | grep -a -E '^(Process|Package|Subject|[A-Za-z.]+(Exception|Error)|"
                     "Caused by|[[:space:]]+at )|========|Drop' | head -n 400; done", 600, "boot2-dropbox")
        z = re.findall(r"(?i)(java\.util\.zip\S*|Adler32|Inflater|Deflater|DataFormatException|ZipException|"
                       r"checksum|libz\.so)", txt)
        R["dropbox_zlib_mentions"] = sorted(set(z))
        st, txt = sh("ls -l /data/anr /data/tombstones; for f in /data/anr/anr_*; do echo \"=== $f\"; "
                     "grep -a -m 3 -E '^(Subject|Cmd line)' $f; done", 120, "boot2-anr")
        st, txt = sh("logcat -d -b crash | grep -a -E 'Process:|FATAL|Caused by|zip|Adler|Inflater' | head -40",
                     60, "boot2-crash-buffer")
        snap("final")
        sh("touch /dev/gbw5/stop; sleep 2", 20)
        sh("cat /dev/gbw5/series.txt", 60, "sampler-series")
        sh("cat /dev/gbw5/full.txt", 120, "sampler-full")
        poweroff_and_save()
        return
    # ---------------------------------------------------------------- adler32 KAT
    for i in range(10):
        c = subprocess.run([vm.ADB, "connect", f"{R['ip']}:5555"], capture_output=True, text=True, timeout=30)
        p = adb("shell", "getprop sys.boot_completed", t=30)
        if p.stdout.strip() == "1":
            break
        time.sleep(5)
    log(f"adb connected: {c.stdout.strip()} boot_completed={p.stdout.strip()}")
    sh("mkdir -p /data/local/tmp/w5; chmod 777 /data/local/tmp/w5", 30)
    for f in ("adler_kat", "classes.dex", "host_stream.zlib"):
        p = adb("push", os.path.join(KAT, f), "/data/local/tmp/w5/" + f, t=300)
        log(f"adb push {f}: rc={p.returncode} {p.stdout.strip()[-80:]}")
    snap("kat-before")
    st, txt = sh("cd /data/local/tmp/w5 && chmod 755 adler_kat && sha256sum adler_kat classes.dex host_stream.zlib && "
                 "read a r < /proc/uptime; ./adler_kat --bench 16 --dex classes.dex --zlib host_stream.zlib 3000000 "
                 "/system/lib64/libz.so /vendor/lib64/libz.so > kat.out 2>&1; echo kat_exit=$?; read b r < /proc/uptime; "
                 "echo kat_uptime $a $b; grep -v '^ADLER_KAT FAIL' kat.out; echo fail_lines=$(grep -c '^ADLER_KAT FAIL' kat.out); "
                 "grep '^ADLER_KAT FAIL' kat.out | head -20", 900, "adler-kat")
    R["kat_exit"] = (re.search(r"kat_exit=(\d+)", txt) or [None, None])[1]
    R["kat"] = {"result": (re.search(r"ADLER_KAT RESULT (\S+ cases=\d+ fail=\d+)", txt) or [None, None])[1],
                "bench": re.findall(r"ADLER_KAT bench lib=(\S+) MiB=(\d+) seconds=([\d.]+) MiB_per_s=([\d.]+)", txt),
                "dex": re.findall(r"dex=\S+ size=\d+ header=(\w+) adler32=(\w+) (\w+)", txt)}
    snap("kat-after")
    st, txt2 = sh("cat /sys/kernel/gb_vnni/summary | grep -a adler_kat", 30, "kat-lru")
    R["kat"]["lru_lines"] = [l for l in txt2.splitlines() if "adler_kat" in l]
    log(f"adler KAT: {R['kat']['result']}; bench {R['kat']['bench']}")
    dump()
    # ---------------------------------------------------------------- app install
    snap("install-before")
    t0 = time.time()
    p = adb("install", "-r", APK, t=600, name="adb-install")
    R["install"] = {"rc": p.returncode, "out": (p.stdout + p.stderr).strip()[-200:], "host_s": round(time.time() - t0, 1)}
    st, txt = sh("pm path gb.sb3.gpu; dumpsys package gb.sb3.gpu | grep -a -E 'versionCode|versionName|dataDir' | head -4",
                 60, "install-check")
    R["install"]["pm_path"] = "package:" in txt
    snap("install-after")
    log(f"install: {R['install']}")
    dump()
    # ---------------------------------------------------------------- dex2oat-heavy action
    barrier("before-compile")
    snap("compile-before")
    R["compile"] = {}
    for pkg in COMPILE_PKGS:
        st, txt = sh(f"read a r < /proc/uptime; cmd package compile -m speed -f {pkg} 2>&1 | tail -n 3; "
                     f"read b r < /proc/uptime; echo GBW5C {pkg} begin=$a end=$b", 1800, f"compile-{pkg}")
        m = re.search(r"GBW5C \S+ begin=([\d.]+) end=([\d.]+)", txt)
        R["compile"][pkg] = {"wall": round(float(m.group(2)) - float(m.group(1)), 2) if m else None,
                             "success": "Success" in txt, "tail": txt.strip().splitlines()[-3:]}
        log(f"compile {pkg}: {R['compile'][pkg]}")
        dump()
    snap("compile-after")
    st, txt = sh("for p in " + " ".join(COMPILE_PKGS) + "; do echo \"== $p\"; dumpsys package $p | grep -a -E "
                 "'\\[status=|compilation_filter|arm64|x86_64: \\[' | head -4; done", 120, "compile-status")
    # ---------------------------------------------------------------- logcat / crashes
    st, txt = sh("echo '=== crash buffer lines'; logcat -d -b crash | wc -l; logcat -d -b crash | grep -a -E "
                 "'Process:|Cmdline:|signal|Abort message' | head -20; echo '=== tombstones'; ls -l /data/tombstones; "
                 "echo '=== checksum/zlib/dex2oat errors (all buffers)'; logcat -d -b all | grep -a -i -E "
                 "'checksum|adler|zlib|inflate|deflate|dex2oat.*(error|fail|abort)|Failed to (open|verify) dex|"
                 "FATAL EXCEPTION|ANR in|INSTALL_FAILED' | grep -a -v -i -E 'GBW5|crc32' | tail -40; "
                 "echo '=== anr'; ls -l /data/anr 2>&1 | head", 180, "logcat-checks")
    R["logcat"] = {"crash_lines": int((re.search(r"=== crash buffer lines\n(\d+)", txt) or [0, "-1"])[1]),
                   "tombstones": len(re.findall(r"tombstone_\d+", txt))}
    snap("final")
    sh("touch /dev/gbw5/stop; sleep 2", 20)
    st, txt = sh("cat /dev/gbw5/series.txt", 60, "sampler-series")
    st, txt = sh("cat /dev/gbw5/full.txt", 120, "sampler-full")
    dump()
    poweroff_and_save()


def poweroff_and_save():
    sh("sync; (sleep 2; reboot -p) & echo poweroff", 20)
    t0 = time.time()
    while vm.is_running() and time.time() - t0 < 240:
        time.sleep(3)
    if vm.is_running():
        log("soft power-off timed out: vmrun stop soft"); vm.stop("soft")
    R["powered_off"] = not vm.is_running()
    R["host_end"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    import gzip, shutil
    with open(SERIAL, "rb") as fi, gzip.open(os.path.join(OUT, "serial0.log.gz"), "wb") as fo:
        shutil.copyfileobj(fi, fo)
    dump()
    open(os.path.join(OUT_ROOT, "barrier", f"done-{INST}"), "w").write(R["host_end"])
    log("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        R["exception"] = traceback.format_exc()
        dump()
        log("EXCEPTION " + repr(e))
        d = os.path.join(OUT_ROOT, "barrier"); os.makedirs(d, exist_ok=True)
        for b in ("after-boot", "before-compile"):
            open(os.path.join(d, f"{b}-{INST}.gone"), "w").write("exception")
        raise
