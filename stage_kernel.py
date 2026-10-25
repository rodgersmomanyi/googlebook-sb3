"""Stage a finished kernel build from the builder (/build/dist) into the workspace (GB_HOME, default: this folder):
  boot/kernel.bin                 <- bzImage
  inject/modules/*.ko             <- the VM module set (MODULE_SIG_PROTECT: every module must come from the same build)
  inject/native/modules/*.ko      <- the native Surface Book 3 set + modules.load (loaded by gbinit before Android init)
                                     + modules.load.late (loaded by gb_modules at early-init, after lapis's first stage)
Every module's 'depends' is checked against the load order (an earlier entry, a built-in, or -- late list only --
a lapis first-stage module), and all vermagics must match. Previous files (incl. both load lists) are copied once to
<archive>\\pre-<TAG>\\<path below C:\\GB-x86>, never next to the live file: build_esp packs inject\\native\\modules.

Usage: python -I stage_kernel.py TAG [--dist DIR] [--archive DIR] [--dry-run] [--force]
  TAG     k<number>[a-z0-9]*, e.g. k14 or k13r (the archive folder and the load-list header)
  DIR     the builder's Kleaf dist directory: /build/dist (k13, default) or /build/dist14 (k14); /build/dist* only
  --archive  backup root (default C:\\GB-x86\\archive\\module-backups)
Run it from PowerShell or cmd: Git Bash rewrites /build/dist14 into a Windows path (use MSYS_NO_PATHCONV=1 there).
"""
import io, os, re, shutil, subprocess, sys, tarfile

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
GB_BUILD = os.environ.get("GB_BUILD") or (HERE if os.path.isdir(os.path.join(HERE, "native")) else r"C:\GB-build")
KEY = os.environ.get("GB_BUILDER_KEY") or r"C:\GB-build\builder_key"   # ssh key of the builder VM (never commit it)
HOST = os.environ.get("GB_BUILDER") or "builder@192.168.115.132"
SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=60", HOST]
DIST = "/build/dist"
ARCHIVE = os.path.join(HERE, "archive", "module-backups")

VM_MODS = ["drm_sysfb_helper", "efidrm", "drm_ttm_helper", "vmwgfx", "psmouse", "vmxnet3", "ac", "battery", "test_power"]

# gbinit, before Android init (order matters: dependencies first)
NATIVE_EARLY = [
    # signed GKI replacements for lapis system_dlkm (Wi-Fi/BT dependency chain, zram, USB network adapters)
    "libarc4", "pps_core", "ptp", "rfkill", "bluetooth", "btbcm", "hidp", "rfcomm", "zsmalloc", "zram",
    "mii", "usbnet", "cdc_ether", "cdc_ncm", "r8152", "ax88179_178a", "cdc-acm",
    # NOT ours: mfd-core, pinctrl-intel, intel-lpss(-pci) (lapis first stage loads them, and its pinctrl-intel-platform
    # is refused - fatally, in first stage - against our copies' protected exports: VM test of k13), nor pinctrl-icelake
    # / i2c-designware (lapis vendor_dlkm). The camera stack needs only the buses they provide, no symbols.
    # Surface Aggregator: base keyboard/touchpad, batteries, charger, profile, detach, lid/GPE, hot-plug, buttons
    "crc-itu-t", "surface_aggregator", "surface_aggregator_registry", "surface_aggregator_hub",
    "surface_aggregator_cdev", "surface_hid_core", "surface_hid", "hid-surface", "surface_battery", "surface_charger",
    "surface_dtx", "surface_gpe", "surface_hotplug",
    "soc_button_array", "test_power",
    # display (Ice Lake Gen11)
    "wmi", "wmi-bmof", "video", "i2c-algo-bit", "drm_buddy", "drm_display_helper", "intel-gtt", "i915",
    # light sensor, thermal, touch
    "kfifo_buf", "industrialio-triggered-buffer", "apds9960", "thermal", "ipts",
    # cameras (k13): IPU4P on the ipu6 driver + sensors, focus motor, INT3472 power control; before lapis's own
    # intel-ipu6/int3472/v4l2-cci (no 8A19 support), which then EEXIST
    "v4l2-cci", "ipu-bridge", "intel_skl_int3472_common", "intel_skl_int3472_discrete",
    "ov5693", "ov8865", "ov7251", "dw9719", "intel-ipu6", "intel-ipu6-isys",
]
# in NO load list. Staged in inject\native\modules as the payload's source: build_esp puts them into
# ESP:/gb/dgpu-<krel>.erofs (INTERFACES 6), never into the native cpio; gb_nouveau.sh loads them from /odm/dgpu.
NATIVE_OPTIN = ["drm_exec", "drm_gpuvm", "gpu-sched", "drm_ttm_helper", "nouveau"]
# gb_modules, after lapis's first stage loaded its mfd-core (hid-sensor-hub must link against that one)
NATIVE_LATE = [
    # after gb_modules modprobes lapis's platform_profile (our copy's export would be protected; lapis's
    # processor_thermal_soc_slider -> Ice Lake thermal/RAPL needs lapis's)
    "surface_platform_profile",
    "hid-sensor-hub", "hid-sensor-iio-common", "hid-sensor-trigger", "hid-sensor-accel-3d", "hid-sensor-gyro-3d",
    "hid-sensor-als", "hid-sensor-rotation", "hid-sensor-incl-3d", "hid-sensor-magn-3d",
]
# exactly what lapis's vendor ramdisk modules.load loads in first-stage init
LAPIS_FIRST_STAGE_LOAD = {"8250_dw", "intel-lpss-pci", "intel-lpss", "intel_iommu_vendor_hooks", "mfd-core",
                          "pinctrl-intel-platform", "pinctrl-intel"}
LAPIS_FIRST_STAGE = {"platform_profile", "mfd_core", "intel_lpss", "intel_lpss_pci", "8250_dw", "pinctrl_intel", "pinctrl_intel_platform"}
# insmod arguments in modules.load (gbinit/gb_modules pass the words after the file name)
MODULE_ARGS = {
    # BAT1/BAT2 report scope "Device": the health HAL skips them and shows gb_power's combined test_power battery
    "surface_battery": "scope_device=1",
}
# lapis vendor_dlkm HDA codec modules (not ours; native audio component): preloaded right after i915 so SOF finds
# its codecs (GKI cannot request_module). Refresh from the booted build's vendor_dlkm with
# native/audio/src/refresh_modules.sh (carve it with carve_lp.py).
AUDIO_DIR = os.path.join(GB_BUILD, "native", "audio", "modules")


def norm(n):
    return n.replace("-", "_")


def ssh(cmd):
    return subprocess.run(SSH + [cmd], capture_output=True, text=True, check=True).stdout


def _opt(name):
    """Value of a `--name VALUE` option (None when absent)."""
    if name not in sys.argv:
        return None
    i = sys.argv.index(name)
    if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("--"):
        sys.exit(f"usage: {name} needs a value")
    return sys.argv[i + 1]


def main():
    global DIST, ARCHIVE
    if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
        sys.exit(__doc__)
    tag = sys.argv[1]
    if not re.fullmatch(r"k[0-9]+[a-z0-9]*", tag):
        sys.exit(f"TAG must look like k14 or k13r, not {tag!r}")
    dry = "--dry-run" in sys.argv
    if _opt("--dist") is not None:
        DIST = _opt("--dist")
        # /build/dist* with no further '/' (and nothing a remote shell would interpret: DIST goes into ssh commands)
        if not re.fullmatch(r"/build/dist[A-Za-z0-9._-]*", DIST):
            sys.exit(f"--dist must be a /build/dist* directory, not {DIST!r}")
    if _opt("--archive") is not None:
        ARCHIVE = _opt("--archive")
    known = {"--dist", "--archive", "--dry-run", "--force"}
    vals = {_opt("--dist"), _opt("--archive")}
    extra = [x for x in sys.argv[2:] if x not in known and x not in vals]
    if extra:
        sys.exit(f"unknown arguments {extra}")
    print(f"dist {DIST}; backups -> {os.path.join(ARCHIVE, 'pre-' + tag)}")
    wanted = sorted(set(VM_MODS + NATIVE_EARLY + NATIVE_LATE + NATIVE_OPTIN))
    info = ssh("cd %s && for m in %s; do f=$m.ko; [ -f $f ] || f=$(echo $m | tr - _).ko; "
               "if [ -f $f ]; then echo \"$m|$f|$(modinfo -F depends $f)|$(modinfo -F vermagic $f)\"; "
               "else echo \"$m|MISSING||\"; fi; done; md5sum bzImage" % (DIST, " ".join(wanted)))
    lines = info.strip().splitlines()
    bz_md5 = lines[-1].split()[0]
    mods = {}
    for l in lines[:-1]:
        name, f, deps, vm = l.split("|", 3)
        mods[name] = (f, [d for d in deps.split(",") if d], vm)
    missing = [n for n, (f, _, _) in mods.items() if f == "MISSING"]
    vermagics = {vm for f, _, vm in mods.values() if f != "MISSING"}
    print(f"bzImage md5 {bz_md5}; {len(mods) - len(missing)} modules found, missing: {missing or 'none'}")
    print(f"vermagic: {vermagics}")
    problems = list(missing)
    if len(vermagics) != 1:
        problems.append("vermagic mismatch")

    # dependency order check
    all_ko = set(norm(n) for n in ssh(f"cd {DIST} && ls *.ko | sed 's/\\.ko$//'").split())

    def check(order, extra_ok=()):
        seen = set()
        for n in order:
            if n in missing:
                continue
            for d in mods[n][1]:
                d = norm(d)
                if d in seen or d in extra_ok:
                    continue
                if d in all_ko:
                    problems.append(f"{n} needs {d}, which is not loaded before it")
                else:
                    problems.append(f"{n} needs {d}, which is neither ours nor known first-stage (vendor module?)")
            seen.add(norm(n))
        return seen

    # lapis's first stage loads these itself; preloading our copies makes its pinctrl-intel-platform fail (protected
    # exports) and a first-stage module failure stops the boot (k13 VM test)
    clash = sorted({norm(n) for n in NATIVE_EARLY + NATIVE_LATE} & {norm(n) for n in LAPIS_FIRST_STAGE_LOAD})
    if clash:
        problems.append(f"native list preloads lapis first-stage modules {clash} (fatal at boot)")
    early = check(NATIVE_EARLY)
    late = check(NATIVE_LATE, extra_ok=early | LAPIS_FIRST_STAGE)
    check(NATIVE_OPTIN, extra_ok=early | late | LAPIS_FIRST_STAGE)
    check(VM_MODS)
    for p in problems:
        print("PROBLEM:", p)
    if problems and "--force" not in sys.argv:
        sys.exit("not staging (use --force to stage anyway)")
    if dry:
        return

    # fetch everything in one tar
    files = sorted({mods[n][0] for n in wanted if n not in missing})
    raw = subprocess.run(SSH + [f"cd {DIST} && tar cf - bzImage {' '.join(files)}"], capture_output=True, check=True).stdout
    tf = tarfile.open(fileobj=io.BytesIO(raw))
    blobs = {m.name: tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()}

    def backup(path):
        """Copy an existing file once to <ARCHIVE>\\pre-<tag>\\<path relative to C:\\GB-x86> (a re-run keeps the first)."""
        if os.path.exists(path):
            bak = os.path.join(ARCHIVE, f"pre-{tag}", os.path.relpath(path, HERE))
            if not os.path.exists(bak):
                os.makedirs(os.path.dirname(bak), exist_ok=True)
                shutil.copy2(path, bak)

    def put(path, data):
        backup(path)
        with open(path, "wb") as f:
            f.write(data)

    put(os.path.join(HERE, "boot", "kernel.bin"), blobs["bzImage"])
    vm_dir = os.path.join(HERE, "inject", "modules")
    for n in VM_MODS:
        if n not in missing:
            put(os.path.join(vm_dir, n + ".ko"), blobs[mods[n][0]])
    nat = os.path.join(HERE, "inject", "native", "modules")
    os.makedirs(nat, exist_ok=True)
    for n in NATIVE_EARLY + NATIVE_LATE + NATIVE_OPTIN:
        if n not in missing:
            put(os.path.join(nat, mods[n][0]), blobs[mods[n][0]])
    hdr = f"# kernel build {tag} (bzImage md5 {bz_md5})\n"
    def line(n):
        a = MODULE_ARGS.get(n)
        return mods[n][0] + (" " + a if a else "") + "\n"

    audio = []
    load_audio = os.path.join(AUDIO_DIR, "modules.load.audio")
    if os.path.exists(load_audio):
        audio = [l.strip() for l in open(load_audio) if l.strip() and not l.startswith("#")]
        for ko in audio:
            put(os.path.join(nat, ko), open(os.path.join(AUDIO_DIR, ko), "rb").read())
    early = []
    for n in NATIVE_EARLY:
        if n in missing:
            continue
        early.append(line(n))
        if n == "i915":
            early += [ko + "\n" for ko in audio]
    backup(os.path.join(nat, "modules.load"))
    backup(os.path.join(nat, "modules.load.late"))
    with open(os.path.join(nat, "modules.load"), "w", newline="\n") as f:
        f.write(hdr + "".join(early))
    with open(os.path.join(nat, "modules.load.late"), "w", newline="\n") as f:
        f.write(hdr + "".join(line(n) for n in NATIVE_LATE if n not in missing))
    if audio:
        print(f"audio: {len(audio)} lapis HDA codec modules after i915")
    print(f"staged bzImage, {len(VM_MODS)} VM modules, {len(NATIVE_EARLY)}+{len(NATIVE_LATE)} native modules")


if __name__ == "__main__":
    main()
