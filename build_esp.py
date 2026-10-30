"""Build a 64 MiB EFI System Partition (systemd-boot + GKI bzImage + reconstructed initrd)
and write it into lapis.bin's EFI-SYSTEM partition so the single NVMe disk boots directly."""
import os, re, struct, sys, gzip, zlib

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
BOOT = os.path.join(HERE, "boot")
LAPIS = os.path.join(HERE, "lapis.bin")
ESP_OFFSET = 142606336          # EFI-SYSTEM partition start (bytes)
ESP_BYTES  = 201326592          # 192 MiB (enlarged into unused KERN-A via grow_esp.py, for vulkan.pastel)

def _lapis_write_refused():
    """Why running this file must not write lapis.bin now (None = allowed). build_usb.py imports this module and builds
    its own ESP; only `python build_esp.py` writes lapis.bin (the VM's disk), so that needs GB_WRITE_LAPIS=1 set on
    purpose and is refused while VMware has the VM open (a lapis*.lck folder next to lapis.bin)."""
    if os.environ.get("GB_WRITE_LAPIS") != "1":
        return ("running build_esp.py rewrites the ESP inside lapis.bin (the VM's disk); set GB_WRITE_LAPIS=1 to do "
                "that on purpose, with the VM powered off (build_usb.py builds the USB image without it)")
    try:
        lck = sorted(n for n in os.listdir(HERE) if n.lower().startswith("lapis") and n.lower().endswith(".lck"))
    except OSError as e:
        return f"cannot list {HERE} to check for VMware locks: {e}"
    if lck:
        return f"VMware has the VM open ({', '.join(lck)}); lapis.bin is never written while it runs"
    return None

def _pin_path_refused(new_esp, lapis=None):
    """(W6 prep, SA-7) Why new_esp must not replace the ESP now in lapis.bin (None = allowed): the PIN path of the tree
    it boots (verify_image.pin_path: the Gatekeeper binary, fragment and rc service, the KeyMint service and its
    vup/lib64 graft, the Trusty/Weaver masks, the fstab, the PIN SELinux types) must equal the current one, or a
    lock-screen PIN enrolled on the user's VM may stop verifying (safety audit SA-7). Every difference is printed;
    GB_PIN_MIGRATION=1 allows a reviewed migration."""
    import importlib.util as _u
    try:
        spec = _u.spec_from_file_location("verify_image", os.path.join(HERE, "verify_image.py"))
        vi = _u.module_from_spec(spec); spec.loader.exec_module(vi)
        with open(lapis or LAPIS, "rb") as f:
            f.seek(ESP_OFFSET); old = f.read(ESP_BYTES)
        probs = vi.pin_path_problems(vi.fat_files(old), vi.fat_files(new_esp))
    except (OSError, ValueError, AttributeError, ImportError, SyntaxError, struct.error) as e:
        probs = [f"the PIN path cannot be compared ({e.__class__.__name__}: {e})"]
    for p in probs:
        print(f"PIN path: {p}", flush=True)
    if not probs:
        print("PIN path: the same as the ESP now in lapis.bin", flush=True)
        return None
    if os.environ.get("GB_PIN_MIGRATION") == "1":
        print("PIN path: the differences above are ALLOWED by GB_PIN_MIGRATION=1", flush=True)
        return None
    return (f"the new ESP changes the PIN path of the user's VM ({len(probs)} difference(s) above): an enrolled "
            f"lock-screen PIN may stop verifying. Set GB_PIN_MIGRATION=1 only for a reviewed migration")

if __name__ == "__main__" and _lapis_write_refused():      # refuse before reading anything
    sys.exit(f"build_esp.py: refused: {_lapis_write_refused()}")

SDBOOT  = open(os.path.join(HERE, "debs", "systemd-bootx64.efi"), "rb").read()
KERNEL  = open(os.path.join(BOOT, "kernel.bin"), "rb").read()
# A/B slot to boot. An OTA writes the inactive slot and the boot_control HAL marks it in the GPT; after it has booted
# (gb_slotsync switches the ESP in the guest) it is marked successful, and from then on every host-side rebuild must
# boot that slot too. So the slot and its ramdisks come from lapis.bin itself (extract_slot.py), not from files.
import importlib.util as _ilu
_xs_spec = _ilu.spec_from_file_location("extract_slot", os.path.join(HERE, "extract_slot.py"))
XS = _ilu.module_from_spec(_xs_spec); _xs_spec.loader.exec_module(XS)
SLOT = os.environ.get("GB_SLOT", "").strip().lower() or XS.boot_slot(LAPIS)
assert SLOT in ("a", "b"), f"bad slot {SLOT!r}"
def slot_ramdisks(slot):
    """(generic ramdisk, vendor ramdisk, vendor bootconfig) of a slot, from lapis.bin (fallback for slot a: the
    original boot/*.lz4 extraction)."""
    try:
        return XS.slot_ramdisks(slot, LAPIS)
    except (OSError, AssertionError, KeyError):
        assert slot == "a", f"cannot read slot {slot} ramdisks from {LAPIS}"
        return tuple(open(os.path.join(BOOT, n), "rb").read()
                     for n in ("generic_ramdisk.lz4", "vendor_ramdisk.lz4", "vendor_bootconfig.txt"))
GENERIC, VENDOR, VBC = slot_ramdisks(SLOT)
BUSYBOX = open(os.path.join(HERE, "inject", "busybox"), "rb").read()
FSTAB   = open(os.path.join(HERE, "inject", "fstab.android-desktop"), "rb").read()
def _with_fsverity(fstab):
    """The fstab of BOTH trees (WAVE3_PLAN item 4 [W3R-06] for native; W4 for the VM, user decision 2026-10-26,
    dgpu\\w4\\VM_UPGRADE.md 2.2(b)): `fsverity` appended to the fs_mgr flags of the one userdata line. The flag is
    redundant on lapis but harmless: lapis's fs_mgr already enables ext4 verity on /data without it (every VM-tree and
    native boot log shows "Enabling ext4 verity on /dev/block/by-name/userdata" with the plain fstab; likely AOSP's
    rule for ro.product.first_api_level >= 30, lapis reports 37: INFERRED), and the user's VM /data already has
    RO_COMPAT_VERITY set (ro_compat 0x807b), so fs_mgr's tune_verity() changes nothing there. On a /data without the
    feature it runs `tune2fs -O verity` once (one-way; a failure only logs). Idea: gbOS86 issue #17
    (github.com/linuxkid473/gbos-x86); code ours."""
    out, n = [], 0
    for ln in fstab.split(b"\n"):
        f = ln.split()
        if len(f) == 5 and f[0] == b"/dev/block/by-name/userdata" and f[1] == b"/data" and \
                b"fsverity" not in f[4].split(b","):
            ln = ln.rstrip() + b",fsverity"; n += 1
        out.append(ln)
    assert n == 1, "userdata line not found exactly once"
    return b"\n".join(out)
FSTAB_VERITY = _with_fsverity(FSTAB)
FSTAB_NATIVE = FSTAB_VERITY             # (wave 3 name; since W4 the VM tree ships the same bytes)
ODMCIL  = open(os.path.join(HERE, "inject", "odm_sepolicy.cil"), "rb").read()
ODMPRE  = open(os.path.join(HERE, "inject", "precompiled_sepolicy"), "rb").read()
SEMAN   = open(os.path.join(HERE, "inject", "se_manifest.xml"), "rb").read()
# Display driver modules (built from matching android17-6.18 GKI kernel). Load order = deps first.
_MODNAMES = ["drm_sysfb_helper.ko", "efidrm.ko", "drm_ttm_helper.ko", "vmwgfx.ko"]
MODS = [(n, open(os.path.join(HERE, "inject", "modules", n), "rb").read()) for n in _MODNAMES]
# Seamless (absolute) mouse: VMware's absolute pointer (vmx mouse.vusb.useBasicMouse = "FALSE") matches no Android
# EventHub class, so the gb_absmouse uinput bridge re-emits it as an Android-native absolute mouse; psmouse.ko (built
# with CONFIG_MOUSE_PS2_VMMOUSE in the same GKI build as vmwgfx) adds VMware's vmmouse absolute channel, the trigger
# for tools-less grab/ungrab. Enabled only once that psmouse.ko is staged, i.e. with the matching kernel.
_PSM = os.path.join(HERE, "inject", "modules", "psmouse.ko")
_ABM = os.path.join(HERE, "inject", "mouse", "gb_absmouse")
ABSMOUSE = os.path.exists(_PSM) and os.path.exists(_ABM)
if ABSMOUSE:
    MODS.append(("psmouse.ko", open(_PSM, "rb").read()))
ABSMOUSE_BIN = open(_ABM, "rb").read() if ABSMOUSE else None
# Minimal VMware Tools (GuestRPC/TCLO over the backdoor; source guest/gb_vmtools.c): VMware only runs the
# pointer ungrabbed/absolute and lets the absolute USB tablet stay enabled when Tools report "running"; also makes
# VM > Power > Shut Down / Restart Guest do a clean Android power-off / reboot.
_VMT = os.path.join(HERE, "inject", "vmtools", "gb_vmtools")
VMTOOLS_BIN = open(_VMT, "rb").read() if os.path.exists(_VMT) else None
# Display layout setter (source guest/gb_layout.c): DRM_VMW_UPDATE_LAYOUT, like open-vm-tools' resolutionKMS.
_GBL = os.path.join(HERE, "inject", "vmtools", "gb_layout")
LAYOUT_BIN = open(_GBL, "rb").read() if os.path.exists(_GBL) else None
ABSMOUSE_IDC = (b"# GB absolute mouse (gb_absmouse uinput bridge for the VMware absolute pointer).\n"
                b"# External, so Android's posture logic (no lid/hinge sensor in the VM) never disables it.\n"
                b"device.internal = 0\n"
                b"touch.deviceType = pointer\n"
                b"touch.orientationAware = 1\n")
# Guest display size. VMware shows guest pixels 1:1 on the 3000x2000 (200% scaled) host panel, so 1280x800 was a small,
# soft window; 2560x1600 at 320 dpi (gb_ui.sh) is the same 1280x800 dp workspace drawn at 2 px per dp, the size and
# sharpness of a Windows guest at 200%. gb_layout makes it vmwgfx's preferred mode (= the composer's default config).
DISPLAY = b"2560x1600"
ABSMOUSE_RC = (b"\nservice gb_absmouse /vendor/bin/gb_absmouse --size " + DISPLAY + b"\n"
               b"    class core\n"
               b"    user root\n"
               b"    group root input uhid\n"
               b"    seclabel u:r:shell:s0\n"
               b"    stdio_to_kmsg\n")
# Software KeyMint HAL graft (AOSP reference pure-software keymint from the android16 emulator vendor.img).
# Binary + the 8 android16 keymaster/keymint impl libs lapis lacks; overlaid onto /vendor.
KM_BIN = open(os.path.join(HERE, "inject", "keymint", "bin", "android.hardware.security.keymint-service"), "rb").read()
_KMLIBS = ["lib_android_keymaster_keymint_utils.so", "libcppbor.so", "libcppcose_rkp.so",
           "libkeymaster_messages.so", "libkeymaster_portable.so", "libkeymint.so",
           "libpuresoftkeymasterdevice.so", "libsoft_attestation_cert.so",
           # android16 libcrypto: the android16 keymaster cluster's ComputeHmac smashes its stack
           # against android17 BoringSSL, so give it the matching android16 libcrypto.
           "libcrypto.so"]
KMLIBS = [(n, open(os.path.join(HERE, "inject", "keymint", "lib64", n), "rb").read()) for n in _KMLIBS]
# (W5, readiness C-2) The NATIVE tree ships the same graft with private sonames (lib<x>_gbkm16.so; the service binary
# needs only those), made on the builder by keymint_native_graft.sh (patchelf --no-sort --page-size 16384
# --set-soname/--replace-needed, never stripped; verify_keymint_graft.py proves only the names changed and checks
# SHA256SUMS). So the native /vendor overlay no longer replaces lapis's /vendor/lib64/libcrypto.so (the android16
# copy lacks 58 symbols lapis's android17 libssl.so needs: wpa_supplicant and hostapd could not link = no Wi-Fi, no
# hotspot; rkp_factory_extraction_tool64, cbfstool and elogtool could not link either) nor lapis's
# lib_android_keymaster_keymint_utils.so / libkeymaster_messages.so, while the graft still runs on its android16
# BoringSSL (the ComputeHmac reason above). crossystem stays stubbed natively too (no vboot firmware). The VM tree keeps
# KM_BIN/KMLIBS (the user's VM has no Wi-Fi; change it only with a rehearsal). link_closure.py gates the native overlay
# at build time (native_cpio(); inject\keymint\native\lapis_linkref.json.gz from linkref_prepare.py).
_KMN = os.path.join(HERE, "inject", "keymint", "native")
def _km_native():
    """(service binary, [(lib name, bytes)]) of inject\\keymint\\native, each file checked against its SHA256SUMS."""
    import hashlib
    with open(os.path.join(_KMN, "SHA256SUMS"), encoding="utf-8") as f:
        sums = {l.split()[1]: l.split()[0] for l in f if l.strip() and not l.startswith("#")}
    names = ["bin/android.hardware.security.keymint-service"] + ["lib64/" + n[:-3] + "_gbkm16.so" for n in _KMLIBS]
    assert sorted(sums) == sorted(names), f"inject\\keymint\\native\\SHA256SUMS lists {sorted(sums)}"
    assert sorted(os.listdir(os.path.join(_KMN, "lib64"))) == sorted(n[6:] for n in names[1:]), \
        "inject\\keymint\\native\\lib64 must hold exactly the 9 lib<x>_gbkm16.so files"
    data = {}
    for n in names:
        with open(os.path.join(_KMN, *n.split("/")), "rb") as f:
            data[n] = f.read()
    for n, d in data.items():
        assert hashlib.sha256(d).hexdigest() == sums[n], f"inject\\keymint\\native\\{n}: not the SHA256SUMS bytes"
    return data[names[0]], [(n[6:], data[n]) for n in names[1:]]
KM_BIN_NATIVE, KMLIBS_NATIVE = _km_native()
_lc_spec = _ilu.spec_from_file_location("link_closure", os.path.join(HERE, "link_closure.py"))
LC = _ilu.module_from_spec(_lc_spec); _lc_spec.loader.exec_module(LC)
LINKREF = os.path.join(_KMN, "lapis_linkref.json.gz")       # linkref_prepare.py writes it, pinned to lapis.bin's slot
def _check_links(ents):
    """(C-2) No lapis vendor ELF may resolve worse with the native /vendor overlay; every overlay ELF, wpa_supplicant,
    hostapd and the KeyMint service must link (link_closure.py N0-N5, against the boot slot's vendor/system)."""
    probs, rep = LC.check_native(ents, LAPIS, SLOT, LINKREF, HERE)
    print("native link closure: " + "; ".join(rep), flush=True)
    assert not probs, ("native link-closure gate (link_closure.py):\n  " + "\n  ".join(probs[:60])
                       + (f"\n  ... {len(probs) - 60} more" if len(probs) > 60 else ""))
# Patched minigbm gralloc libs (komeda backend renamed -> vmwgfx, so minigbm uses the dumb-buffer path
# on the vmwgfx DRM device; stock minigbm has no vmwgfx backend -> the allocator aborts "Failed to
# initialize driver"). Overlaid onto /vendor/lib64/.
_VGFX = ["libminigbm_gralloc.so", "libgbm.so"]
VGFX = [(n, open(os.path.join(HERE, "inject", "vendor_gfx", n), "rb").read())
        for n in _VGFX if os.path.exists(os.path.join(HERE, "inject", "vendor_gfx", n))]
# Software Vulkan HAL (SwiftShader "pastel", built for android x86_64) to give SurfaceFlinger a GPU.
# Overlaid OVER lapis's vulkan.intel.so (which needs a real Intel GPU) so the Vulkan loader uses pastel
# (sidesteps write-once ro.hardware.vulkan). ANGLE -> Vulkan(pastel) -> SF SkiaGL gets an EGLConfig.
_VPATH = os.path.join(HERE, "inject", "vulkan.pastel.so")
VPASTEL = open(_VPATH, "rb").read() if os.path.exists(_VPATH) else None
# Diagnostic: tiny Vulkan enumerator (NDK-built). Probes BOTH the Android libvulkan loader path and
# pastel's ICD entry directly, so we can tell whether the loader drops SwiftShader's device or pastel
# itself reports 0 (SF aborts with "Could not find any physical devices, bailing").
_VKE = os.path.join(HERE, "inject", "vkenum")
VKENUM = open(_VKE, "rb").read() if os.path.exists(_VKE) else None
# GPU path: Mesa gallium "svga" EGL/GLES for VMware SVGA3D (NDK-built on builder2 by graphics/mesa/build_mesa.sh),
# staged into /vendor/lib64/egl. Inert until SurfaceFlinger/HWUI are switched to SkiaGL: the system EGL stays ANGLE,
# and only egl_probe (which dlopen()s libEGL_mesa.so directly) uses it, as a go/no-go test of the host 3D path.
_MESA_DIR = os.path.join(HERE, "inject", "mesa")
MESA = sorted((n, open(os.path.join(_MESA_DIR, n), "rb").read()) for n in os.listdir(_MESA_DIR)
              if n.endswith(".so")) if os.path.isdir(_MESA_DIR) else []
_EPR = os.path.join(_MESA_DIR, "egl_probe")
EGLPROBE = open(_EPR, "rb").read() if os.path.exists(_EPR) else None
# /odm/etc/build.prop: init loads it AFTER /vendor/build.prop, so these ro.* win (a gb.rc setprop of an ro.* that vendor
# already set is rejected: "Read-only property was already set" -- that is why the earlier blur setprop never took).
ODM_BUILD_PROP = (b"# GB VM overrides (loaded after /vendor/build.prop)\n"
                  # Background blur (taskbar, shade, dialogs, app launch) renders on the host GPU (Mesa svga); it was
                  # off only for the old CPU (SwiftShader) renderer. sRGB-only composition (no wide-colour/HDR
                  # per-pixel conversion), as Google's own desktop emulator image (sdk_gdesktop_x86_64) configures it.
                  b"ro.surface_flinger.supports_background_blur=1\n"
                  b"ro.surface_flinger.has_wide_color_display=false\n"
                  b"ro.surface_flinger.has_HDR_display=false\n"
                  b"ro.surface_flinger.use_color_management=false\n"
                  # Slow VM: scale input-dispatch/ANR and HAL timeouts so a busy frame isn't 'not responding'.
                  b"ro.hw_timeout_multiplier=4\n")
EGLPROBE_SH = (b"#!/system/bin/sh\n"
               b"exec >/dev/kmsg 2>&1\n"
               b"/system/bin/sleep 25\n"
               b"echo 'GBEGL --- probe start'\n"
               b"/system/bin/toybox ls -l /dev/dri/ /vendor/lib64/egl/ 2>&1 | while IFS= read -r l; do echo \"GBEGL ls $l\"; done\n"
               b"export LD_LIBRARY_PATH=/vendor/lib64/egl:/vendor/lib64\n"
               b"export EGL_LOG_LEVEL=debug MESA_DEBUG=1\n"
               b"/vendor/bin/egl_probe /vendor/lib64/egl/libEGL_mesa.so 2>&1 | while IFS= read -r l; do echo \"$l\"; done\n"
               b"echo \"GBEGL --- probe exit\"\n")
# Extra runtime deps of pastel that lapis lacks (names filled in from readelf once known).
_VPDEPS = []
VPDEPS = [(n, open(os.path.join(HERE, "inject", "vulkan_deps", n), "rb").read()) for n in _VPDEPS]
# Forced EDID for the vmwgfx virtual connector. vmwgfx exposes no EDID, so SurfaceFlinger's
# getDisplayIdentificationData returns -8 and SF drops to "legacy multi-display mode", in which the
# framework (LocalDisplayAdapter) gets activeDisplayModeId=-1 ("Can't find display mode with id -1")
# and never creates logical display 0 -> DisplayManagerService "Default display is null" -> system_server
# blocks at PHASE_WAIT_FOR_DEFAULT_DISPLAY. Injecting a valid 1280x800 EDID (via drm_kms_helper.
# edid_firmware=edid/gb.bin, searched under firmware_class.path=/vendor/firmware) gives SF a real display
# identity so it stays on the modern display path with a valid active mode id.
_EDID = os.path.join(HERE, "inject", "edid_1280x800.bin")
EDID = open(_EDID, "rb").read() if os.path.exists(_EDID) else None
# Stub that replaces the Trusty secretkeeper HAL binary. Its init service is `class hal` (not oneshot),
# defined in /vendor/etc/init/...secretkeeper.trusty.desktop.rc which init PARSES BEFORE our /vendor
# overlay mount (early-init) -> the rc-file whiteout cannot unregister the already-parsed service, so it
# keeps starting and SIGABRTs (no Trusty VM) ~100x -> sys.init.updatable_crashing -> RescueParty reboot.
# Overlaying the BINARY with a script that blocks forever makes init see a healthy running service: no
# crash, no restart storm, no RescueParty. (ISecretkeeper never registered either way; boot tolerates it.)
SK_STUB = b"#!/system/bin/sh\nexec /system/bin/sleep 1000000000\n"
# crossystem stub. There is no ChromeOS vboot firmware in this VM, and the image's real crossystem can't
# even LINK: it pulls in our grafted android16 /vendor/lib64/libcrypto.so (overlaid to fix the keymint
# stack-smash), which lacks CRYPTO_BUFFER_dup_ref that the image's android17 /vendor/lib64/libssl.so
# needs -> "CANNOT LINK EXECUTABLE crossystem". So the android-desktop boot_control HAL's
# mark-boot-successful (crossystem fw_result=success fw_try_count=0) fails with exit 256, the A/B slot is
# never marked successful, vold's cp_needsCheckpoint() stays true, and system_server reboots
# 'Checkpoint commit failed' on EVERY boot (fresh system_server pid each time -> endless reboot loop,
# boot_completed never fires). Overlay /vendor/bin/crossystem with a stub that links nothing and reports a
# healthy, booted-successfully vboot state so isSlotMarkedSuccessful() is TRUE (independent of whether
# anything actually marked the slot) -> cp_needsCheckpoint()==false. Sets (field=value) are no-ops; all
# queries print a sane value and exit 0.
CROSSYSTEM_STUB = (b"#!/system/bin/sh\n"
                   b"first=1\n"
                   b'for a in "$@"; do\n'
                   b'  case "$a" in\n'
                   b"    *=*) continue ;;\n"
                   b"    fw_result|fw_prev_result) v=success ;;\n"
                   b"    mainfw_act|fw_try_next) v=A ;;\n"
                   b"    kernkey_vfy) v=sig ;;\n"
                   b"    *) v=0 ;;\n"
                   b"  esac\n"
                   b'  if [ $first -eq 1 ]; then printf "%s" "$v"; first=0; else printf " %s" "$v"; fi\n'
                   b"done\n"
                   b"exit 0\n")
# Empty VINTF device manifest, bind-mounted over the strongbox keymint apex's VINTF fragment to undeclare
# IKeyMintDevice/strongbox (whose service never starts -> keystore2 earlyBootEnded hangs on waitForService).
EMPTY_VINTF = b'<manifest version="1.0" type="device"/>\n'
# This kernel lacks CONFIG_DRM_LOAD_EDID_FIRMWARE (no /sys/module/drm_kms_helper/parameters/edid_firmware),
# so inject the forced EDID the other supported way: the DRM debugfs `edid_override` of the vmwgfx
# Virtual-1 connector. Run synchronously at early-init (right after vmwgfx insmod, before the composer
# starts) so the connector carries the EDID by the time hwc3-drm probes it -> getDisplayIdentificationData
# succeeds -> SF leaves legacy multi-display mode -> valid active mode id -> framework default display.
GB_EDID_SH = (b"#!/system/bin/sh\n"
              b"exec >/dev/kmsg 2>&1\n"
              b"echo GB_EDID_SH_START\n"
              b"echo \"GB_EDID mount: $(/system/bin/toybox mount -t debugfs none /sys/kernel/debug 2>&1)\"\n"
              b"echo \"GB_EDID dri=$(/system/bin/toybox ls /sys/kernel/debug/dri 2>&1)\"\n"
              # dump each dri minor's entries (to see the connector dir names + whether edid_override exists)
              b"for d in /sys/kernel/debug/dri/*/; do echo \"GB_EDID dir $d : $(/system/bin/toybox ls $d 2>&1 | /system/bin/toybox tr '\\n' ' ')\"; done\n"
              b"F=$(/system/bin/toybox find /sys/kernel/debug/dri -name edid_override 2>/dev/null)\n"
              b"echo \"GB_EDID overrides=[$F]\"\n"
              b"for f in $F; do /system/bin/toybox dd if=/odm/gb_edid.bin of=\"$f\" bs=128 count=1 2>&1; echo \"GB_EDID wrote $f rc=$?\"; done\n"
              b"S=$(/system/bin/toybox find /sys/kernel/debug/dri -name trigger_hotplug 2>/dev/null)\n"
              b"for s in $S; do echo 1 > \"$s\" 2>/dev/null; done\n"
              # also try the sysfs status re-detect to force a reprobe of the override
              b"for st in /sys/class/drm/*Virtual-1*/status; do echo detect > \"$st\" 2>/dev/null; done\n"
              b"echo GB_EDID_SH_DONE\n")

# ---- /odm override injection (ARM-style): rdinit stages /odm tmpfs, then exec /init ----
GBINIT = (b"#!/busybox sh\n"
          # Hardware mode: gb.hw=vm|native from the boot entry's kernel command line, else DMI (VMware -> vm).
          # vm = /gbstage (VMware drivers and tuning), native = /gbnative (Surface Book 3 drivers + firmware).
          b"/busybox mkdir -p /proc /sys /odm\n"
          b"/busybox mount -t proc proc /proc\n"
          b"/busybox mount -t sysfs sysfs /sys\n"
          # HWTOK: the explicit token gb.hw=native (never the DMI fallback); OV: gb.overlay=1 (the ESP bring-up, below);
          # (W5) VN=0: the token gb.vnni=0, the kill switch of the VNNI libz patch (below, after the /odm copy)
          b"HW=; USB=0; HWTOK=0; OV=0; VN=1\n"
          b"for a in $(/busybox cat /proc/cmdline); do case \"$a\" in gb.hw=*) HW=${a#gb.hw=}; HWTOK=0; [ \"$HW\" = native ] && HWTOK=1;; gb.boot=usb) USB=1;; gb.overlay=1) OV=1;; gb.vnni=0) VN=0;; esac; done\n"
          # (W5b) OG: the OTA gate's tokens (below, after the /odm copy): gb.otagate=0 = off (every injection as built,
          # whatever the build), gb.otagate=force = the unvalidated path on the validated build (tests); exact tokens only
          b"OG=; for a in $(/busybox cat /proc/cmdline); do case \"$a\" in gb.otagate=0) OG=0;; gb.otagate=force) OG=force;; esac; done\n"
          b"if [ -z \"$HW\" ]; then\n"
          b"  case \"$(/busybox cat /sys/class/dmi/id/sys_vendor 2>/dev/null)\" in *VMware*) HW=vm;; *) HW=native;; esac\n"
          b"fi\n"
          b"SRC=/gbstage\n"
          b"[ \"$HW\" = native ] && [ -d /gbnative ] && SRC=/gbnative\n"
          b"/busybox umount /sys\n"
          b"/busybox umount /proc\n"
          b"/busybox mount -t tmpfs tmpfs /odm\n"
          b"/busybox cp -a $SRC/. /odm/\n"
          # (W5) VNNI libz patch kill switch: without /odm/vnni every fingerprint-gated bind in gb.rc fails harmlessly
          # (init logs it and goes on) and the stock libz runs under k15's VEX AVX-VNNI emulation, which is correct
          b"[ \"$VN\" = 0 ] && /busybox rm -rf /odm/vnni\n"
          # (W5b) OTA gate (C:\GB-build\native\ota-gating\DESIGN.md; OTA_CLASSES below). build_esp writes the init_boot
          # ramdisk fingerprint of the build this ESP was built for into /odm/etc/gb/ota_validated. This boot's own init_boot
          # ramdisk (the slot systemd-boot started: after an A/B OTA gb_slotsync reuses this cpio with the new slot's
          # ramdisks) carries its fingerprint in /system/etc/ramdisk/build.prop. The same build: nothing changes. Another or
          # an unreadable one ("unvalidated"): the build-specific injections that are not needed to boot (OTA_CLASSES
          # "skip": /odm/vnni, the patched libz) are removed from this /odm copy, ro.vendor.gb.ota_gate=unvalidated goes
          # into /odm/etc/build.prop (/odm/etc/init/gb_otagate.rc then starts gb_otagate at boot_completed: a kmsg line
          # and the gb_update notification) and everything needed to boot stays as built (OTA_CLASSES "keep"/"always").
          # A tree without ota_validated (built without the gate): nothing happens. No step can stop this script before
          # exec /init.
          b"if [ -f /odm/etc/gb/ota_validated ]; then\n"
          b"  FV=$(/busybox sed -n 's/^ro\\.bootimage\\.build\\.fingerprint=//p' /odm/etc/gb/ota_validated | /busybox tail -n 1)\n"
          b"  FB=$(/busybox sed -n 's/^ro\\.bootimage\\.build\\.fingerprint=//p' /system/etc/ramdisk/build.prop 2>/dev/null"
          b" | /busybox tr -d '\\r' | /busybox tail -n 1)\n"
          b"  OS=unvalidated\n"
          b"  [ -n \"$FV\" ] && [ \"$FB\" = \"$FV\" ] && OS=validated\n"
          b"  [ \"$OG\" = force ] && OS=unvalidated\n"
          b"  [ \"$OG\" = 0 ] && OS=off\n"
          b"  if [ \"$OS\" = unvalidated ]; then\n"
          b"    /busybox rm -rf /odm/vnni\n"
          b"    echo >> /odm/etc/build.prop\n"
          b"    echo \"ro.vendor.gb.ota_gate=unvalidated\" >> /odm/etc/build.prop\n"
          b"  fi\n"
          b"  { echo \"state=$OS\"; echo \"validated=$FV\"; echo \"booted=${FB:-unknown}\"; echo \"token=${OG:-none}\"; }"
          b" > /odm/etc/gb/ota_state\n"
          b"fi\n"
          # (W6, F1 + M-vlower; VNNI_GBINIT_RO below) what is left of /odm/vnni after the kill switch and the OTA gate
          # moves to its own tmpfs "gbvnni" (the same files). gb.rc relabels and binds each library at early-init and
          # then remounts this superblock read-only: every mount of it (the binds in every mount namespace and their
          # propagated copy /odm/vlower/lib64/libz.so) refuses writes. Any failure here: no /odm/vnni (no bind).
          b"if [ -d /odm/vnni ]; then\n"
          b"  /busybox rm -rf /odm/.gbvnni; /busybox mv /odm/vnni /odm/.gbvnni\n"
          b"  if /busybox mkdir -m 0755 /odm/vnni && /busybox mount -t tmpfs -o mode=0755 gbvnni /odm/vnni"
          b" && /busybox cp -a /odm/.gbvnni/. /odm/vnni/; then\n"
          b"    /busybox rm -rf /odm/.gbvnni\n"
          b"  else\n"
          b"    /busybox umount /odm/vnni 2>/dev/null\n"
          b"    /busybox rm -rf /odm/vnni /odm/.gbvnni\n"
          b"    echo \"vnni: no own tmpfs, /odm/vnni removed (no bind)\" > /odm/gbinit_vnni.log\n"
          b"  fi\n"
          b"fi\n"
          b"/busybox mkdir -p /odm/etc\n"
          b"echo \"$HW\" > /odm/etc/gb_hw\n"
          # Booted from a USB stick: keep vold from treating that stick as a removable 'USB drive' (it would offer to
          # mount the boot partition or format the disk).
          b"[ \"$USB\" = 1 ] && /busybox sed -i '/voldmanaged=usb/d' /odm/etc/fstab.android-desktop\n"
          # Overlayfs whiteouts (char dev 0,0 in the /vendor overlay upperdir) to MASK the Trusty-based
          # security HALs that can't run without a Trusty VM: the strongbox keymint apex (its VINTF fragment
          # declares IKeyMintDevice/strongbox -> keystore2.earlyBootEnded fails NAME_NOT_FOUND -> vold
          # reboot_on_failure -> RescueParty reboot loop) + the secretkeeper-trusty VINTF fragment & rc
          # (panics SIGABRT). Masking them makes keystore2 skip strongbox so earlyBootEnded succeeds.
          b"/busybox mkdir -p /odm/vup/apex /odm/vup/etc/vintf/manifest /odm/vup/etc/init\n"
          b"/busybox mknod /odm/vup/apex/com.android.hardware.keymint.strongbox.desktop.apex c 0 0\n"
          b"/busybox mknod /odm/vup/etc/vintf/manifest/android.hardware.security.secretkeeper.trusty.xml c 0 0\n"
          b"/busybox mknod /odm/vup/etc/init/android.hardware.security.secretkeeper.trusty.desktop.rc c 0 0\n"
          # Whiteout the Trusty Gatekeeper init .rc so the service NEVER STARTS. It is GSC/Trusty-backed
          # (/dev/trusty-ipc-dev0, absent here) and SIGABRTs on connect (status=-3) ~81x -> the crash storm
          # (crash_dump/tombstoned/logd churn) blocks system_server's android.bg thread ~25s -> Watchdog kills
          # system_server AFTER boot_completed -> SystemUI+launcher die -> display freezes on the splash. Its
          # VINTF is already undeclared (gb.rc early-init), so LockSettings skips it (enroll fails fast, no
          # lock password) and nothing waits on it. (gbos-x86 hook.sh empties this same .rc unconditionally.)
          b"/busybox mknod /odm/vup/etc/init/android.hardware.gatekeeper-service.trusty.rc c 0 0\n"
          # ESP bring-up config (WAVE3_PLAN item 12, contract 2.3): off unless this boot's command line carries both
          # gb.hw=native and gb.overlay=1 (never in a shipped entry). /gbbringup.sh exists only in the native cpio and
          # may only apply a whitelisted gb\gb.cfg + overlay from this boot's ESP to the /odm tmpfs. Whatever it does
          # (an error, a hang: timeout 40), this script goes on to exec /init. No cmdline module-option forwarding
          # (lead decision D-d: i915.gb_ignore_user_gamma is set only through gb.cfg GB_MODOPTS_i915).
          b"if [ \"$HW\" = native ] && [ \"$HWTOK\" = 1 ] && [ \"$OV\" = 1 ] && [ -f /gbbringup.sh ]; then\n"
          b"  /busybox timeout 40 /busybox sh /gbbringup.sh >> /odm/gbinit_bringup.log 2>&1\n"
          b"  echo \"bringup rc=$?\" >> /odm/gbinit_bringup.log\n"
          b"  for m in /gbbu/esp /gbbu/sys /gbbu/proc; do /busybox umount -l $m 2>/dev/null; done\n"
          b"fi\n"
          # Native: load our modules (signed GKI replacements for lapis system_dlkm, Surface Aggregator stack, i915,
          # sensors, IPTS ...) right here, before Android init and ueventd. Otherwise lapis's vendor_dlkm copies of
          # video/wmi/drm helpers autoload first by modalias and i915 cannot link against them (other symbol CRCs),
          # and lapis's Google-signed libarc4/rfkill/bluetooth... would be tried (and refused) before ours.
          b"if [ \"$HW\" = native ] && [ -f /odm/lib/modules/modules.load ]; then\n"
          b"  while read -r m a; do\n"
          b"    case \"$m\" in ''|'#'*) continue;; esac\n"
          b"    if /busybox insmod /odm/lib/modules/$m $a >> /odm/gbinit_modules.log 2>&1; then echo \"ok $m\" >> /odm/gbinit_modules.log\n"
          b"    else echo \"FAILED $m\" >> /odm/gbinit_modules.log; fi\n"
          b"  done < /odm/lib/modules/modules.load\n"
          b"fi\n"
          b"exec /init\n")
# /gbbringup.sh (native cpio only; WAVE3_PLAN item 12, contract 2.3, [W3R-01/02/03/17]). Run by GBINIT as above; its
# output is /odm/gbinit_bringup.log, which gb_bootlog's summary shows. Busybox ash only, every command through /busybox
# (there is no PATH at rdinit). The test harness (tests\w3a\bringup) replaces only the line before #@@config-end.
GB_BRINGUP_SH = rb"""#!/busybox sh
# gbbringup (native, rdinit): apply ESP:\gb\gb.cfg (first line exactly "# gb.cfg v1") and ESP:\gb\overlay\ to the
# /odm tmpfs before Android starts. GBINIT runs this only for the explicit tokens gb.hw=native and gb.overlay=1, under
# "timeout 40", and goes on to exec /init whatever happens here (a failure only leaves log lines).
# Trust: Secure Boot is off for this project, so whoever can write this boot's ESP already controls vmlinuz and the
# initrd; this adds nothing beyond that. It reads only THIS boot's ESP (the one disk whose GPT holds the partition GUID
# of androidboot.boot_part_uuid; its partition PARTNAME=EFI-SYSTEM), mounted ro,nosuid,nodev,noexec; it executes
# nothing from it. It may write only: module options and commented-out lines in the tmpfs modules.load (GB_MODOPTS_<m>,
# GB_SKIPMOD), property lines appended to /odm/etc/build.prop (GB_PROP_<n>, a deny list), and input/audio
# configuration files (overlay whitelist), each created 0644 by root. Never rc files, SELinux policy, VINTF,
# libraries, firmware, modules or binaries. A format error anywhere in gb.cfg or the overlay = nothing is applied.
# Single process at rdinit, read-only mount: the one exception to INTERFACES 5.4 (no ESP lock user exists yet).
# Design idea: gbOS86 (gbos86.com/docs/how-it-works, github.com/linuxkid473/gbos-x86 @f8a5c1a); code ours, Apache-2.0.
B=/busybox; R=
#@@config-end
set -f
umask 022
G=$R/gbbu; S=$G/sys; ESP=$G/esp; PLAN=$G/plan; ODM=$R/odm; ML=$ODM/lib/modules/modules.load; BP=$ODM/etc/build.prop
CR=$(printf '\r')
log() { echo "gbbringup: $*"; }
fin() {
  $B umount $ESP 2>/dev/null; $B umount $S 2>/dev/null; $B umount $G/proc 2>/dev/null
  $B rm -rf $G/dev $PLAN
}
out() { fin; log "$*"; exit 0; }
rev2() { v=$1; o=; while [ -n "$v" ]; do o=$o${v#"${v%??}"}; v=${v%??}; done; printf '%s' "$o"; }
le() { x=$(rev2 "$1"); echo $((0x${x:-0})); }
okn() { case $1 in ''|.|..|*[!A-Za-z0-9_.-]*) return 1;; esac; }

$B mkdir -p $G/proc $S $G/dev $ESP $PLAN || { log "cannot create $G"; exit 1; }
$B mount -t proc proc $G/proc || out "cannot mount proc: skipped"
$B mount -t sysfs sysfs $S || out "cannot mount sysfs: skipped"
U=
for a in $($B cat $G/proc/cmdline); do case $a in androidboot.boot_part_uuid=*) U=${a#androidboot.boot_part_uuid=};; esac; done
[ -n "$U" ] || out "no androidboot.boot_part_uuid on the command line (a --boot-devices install): skipped"
u=$(printf '%s' "$U" | $B tr 'A-F' 'a-f')
case $u in ????????-????-????-????-????????????) ;; *) out "androidboot.boot_part_uuid is not a GUID: skipped";; esac
case $u in *[!0-9a-f-]*) out "androidboot.boot_part_uuid is not a GUID: skipped";; esac
g1=${u%%-*}; r=${u#*-}; g2=${r%%-*}; r=${r#*-}; g3=${r%%-*}; r=${r#*-}; g45=${r%%-*}${r#*-}
W=$(rev2 $g1)$(rev2 $g2)$(rev2 $g3)$g45

# ---- the boot disk: the one whole disk whose GPT holds the GUID W (bytes_le hex), private nodes under $G/dev
gpt_count() {   # $1 node, $2 disk name: how often W is a partition GUID in its primary GPT (0 = none or no GPT)
  lbs=$($B cat $S/class/block/$2/queue/logical_block_size 2>/dev/null)
  case $lbs in 512|4096) ;; *) lbs=512;; esac
  h=$($B dd if=$1 bs=$lbs skip=1 count=1 2>/dev/null | $B od -A n -t x1 -v | $B tr -d ' \n')
  case $h in 4546492050415254*) ;; *) echo 0; return;; esac
  case $h in *[!0-9a-f]*) echo 0; return;; esac
  [ ${#h} -ge 176 ] || { echo 0; return; }
  lba=$(le ${h:144:16}); cnt=$(le ${h:160:8}); esz=$(le ${h:168:8})
  if [ $lba -lt 2 ] || [ $cnt -lt 1 ] || [ $cnt -gt 256 ] || [ $esz -lt 128 ] || [ $esz -gt 1024 ] || \
     [ $((esz % 8)) != 0 ]; then echo 0; return; fi
  nb=$(((cnt * esz + lbs - 1) / lbs))
  $B dd if=$1 bs=$lbs skip=$lba count=$nb 2>/dev/null | $B hexdump -v -e "$esz/1 \"%02x\" \"\n\"" | $B head -n $cnt |
    $B cut -c33-64 | $B grep -c -x "$W"
}
scan() {        # DISK = the matching disk, NHIT = matches over all disks (1 = found), SIG = every whole disk's name,
  DISK=; NHIT=0; SIG=   # device number, size and match count (what the settle loop below compares between scans)
  for k in $($B ls $S/class/block 2>/dev/null); do
    case $k in ''|*[!a-z0-9]*|loop*|ram*|zram*) continue;; esac
    [ -e $S/class/block/$k/partition ] && continue
    d=$($B cat $S/class/block/$k/dev 2>/dev/null)
    case $d in ''|*[!0-9:]*|:*|*:|*:*:*) continue;; esac
    [ -e $G/dev/$k ] || $B mknod -m 0600 $G/dev/$k b ${d%%:*} ${d#*:} 2>/dev/null || continue
    c=$(gpt_count $G/dev/$k $k)
    case $c in ''|*[!0-9]*) c=0;; esac
    sz=$($B cat $S/class/block/$k/size 2>/dev/null)
    case $sz in *[!0-9]*) sz=x;; esac
    SIG="$SIG $k:$d:$sz:$c"
    [ $c -gt 0 ] && { NHIT=$((NHIT + c)); DISK=$k; }
  done
}
# (wave 4, wave 3 audit LOW-1) a disk may appear after the first match (USB storage enumerates late, usb-storage waits
# delay_use first): rescan every 0.5 s until the whole-disk set (names, device numbers, sizes, matches) has not changed
# over 4 rescans (2 s), and only then require exactly one match over all disks. At most 30 rescans (15 s); GBINIT's
# "timeout 40" bounds everything.
i=0; last=-; same=0
while :; do
  scan
  if [ "$SIG" = "$last" ]; then same=$((same + 1)); else same=0; last=$SIG; fi
  [ $NHIT -ge 1 ] && [ $same -ge 4 ] && break
  [ $i -ge 30 ] && break
  $B sleep 0.5; i=$((i + 1))
done
[ $NHIT -ge 1 ] && [ $same -lt 4 ] && out "the block devices did not settle within 15 s: skipped"
[ $NHIT = 1 ] || out "the boot partition GUID was found $NHIT times (one disk needed): skipped"
P=; NP=0
for p in $($B ls $S/class/block/$DISK 2>/dev/null); do
  case $p in "$DISK"*) ;; *) continue;; esac
  case $p in *[!a-z0-9]*) continue;; esac
  [ -e $S/class/block/$DISK/$p/partition ] || continue
  $B grep -q -x 'PARTNAME=EFI-SYSTEM' $S/class/block/$DISK/$p/uevent 2>/dev/null || continue
  NP=$((NP + 1)); P=$p
done
[ $NP = 1 ] || out "boot disk $DISK has $NP EFI-SYSTEM partitions (one needed): skipped"
d=$($B cat $S/class/block/$DISK/$P/dev 2>/dev/null)
case $d in ''|*[!0-9:]*|:*|*:|*:*:*) out "no device number for $P: skipped";; esac
$B mknod -m 0600 $G/dev/esp b ${d%%:*} ${d#*:} || out "cannot create the $P node: skipped"
$B mount -t vfat -o ro,nosuid,nodev,noexec,uid=0,gid=0,fmask=0133,dmask=0022 $G/dev/esp $ESP ||
  out "cannot mount $DISK/$P read-only: skipped"
log "boot disk $DISK, ESP $P mounted read-only"

# ---- phase 1: check everything, record the plan; a format error = fatal (nothing is applied)
fatal=
$B sed -n 's/^\([^#[:space:]][^[:space:]]*\)\.ko\([[:space:]].*\)\{0,1\}$/\1/p' $ML 2>/dev/null | $B tr - _ > $PLAN/mods
inload() { $B grep -q -x "$1" $PLAN/mods; }
modopts() {   # $1 GB_MODOPTS_<m>, $2 the options
  m=${1#GB_MODOPTS_}
  case $m in ''|*[!a-z0-9_]*) fatal="line $n: $1: module names are [a-z0-9_]"; return 1;; esac
  [ ${#2} -le 200 ] || { fatal="line $n: $1: options longer than 200 characters"; return 1; }
  c=0; o=
  for t in $2; do
    tn=${t%%=*}; tv=${t#*=}
    case $t in *=*) ;; *) fatal="line $n: $1: '$t' is not name=value"; return 1;; esac
    case $tn in ''|*[!a-z0-9_]*) fatal="line $n: $1: bad option name"; return 1;; esac
    case $tv in ''|*[!A-Za-z0-9_.,:-]*) fatal="line $n: $1: bad option value"; return 1;; esac
    if [ "$m" = i915 ] && [ "$tn" = gb_ignore_user_gamma ]; then
      case $tv in [0-7]) ;; *) fatal="line $n: $1: gb_ignore_user_gamma must be 0-7"; return 1;; esac
    fi
    c=$((c + 1)); o="$o $t"
  done
  [ $c -ge 1 ] && [ $c -le 8 ] || { fatal="line $n: $1: 1 to 8 options needed"; return 1; }
  if inload "$m"; then printf '%s\n' "${o# }" > $PLAN/opt.$m; log "line $n: $1 accepted"
  else log "line $n: $1 ignored: $m is not in modules.load"; fi
  return 0
}
skipmod() {   # $1 comma list of modules.load names
  case $1 in ''|*[!a-z0-9_.,-]*) fatal="line $n: GB_SKIPMOD: names are [a-z0-9_-], comma separated"; return 1;; esac
  oi=$IFS; IFS=,; set -- $1; IFS=$oi
  for x in "$@"; do
    x=${x%.ko}; x=$(printf '%s' "$x" | $B tr - _)
    case $x in ''|*[!a-z0-9_]*) fatal="line $n: GB_SKIPMOD: bad name"; return 1;; esac
    if inload "$x"; then : > $PLAN/skip.$x; log "line $n: GB_SKIPMOD $x accepted"
    else log "line $n: GB_SKIPMOD $x ignored: not in modules.load"; fi
  done
  return 0
}
prop() {      # $1 GB_PROP_<n>, $2 <prop>=<value>
  ix=${1#GB_PROP_}
  case $2 in *=*) ;; *) fatal="line $n: $1 is not <prop>=<value>"; return 1;; esac
  p=${2%%=*}; pv=${2#*=}
  case $p in ''|*[!a-z0-9_.]*) fatal="line $n: $1: property names are [a-z0-9_.]"; return 1;; esac
  case $pv in *[!A-Za-z0-9_.,:/+=-]*) fatal="line $n: $1: bad value characters"; return 1;; esac
  [ ${#pv} -le 91 ] || { fatal="line $n: $1: value longer than 91 characters"; return 1; }
  case $ix in [1-9]|1[0-6]) ;; *) log "line $n: $1 refused: the index must be 1-16"; return 0;; esac
  case $p in ro.?*|persist.?*|vendor.?*|debug.?*) ;; *) log "line $n: $1 refused: $p (ro. persist. vendor. debug. only)"; return 0;; esac
  case $p in
    ro.hardware.vulkan*|ro.hardware.egl*|ro.hwui.use_vulkan*|debug.hwui.renderer*|ro.vendor.gb.gfx_default*|\
    persist.vendor.gb.gfx_mode*|persist.vendor.gb.nouveau*|ro.gfx.*|ro.vendor.gb.*|persist.vendor.gb.*|\
    ro.debuggable*|ro.secure*|ro.adb.*|persist.adb.*|service.adb.*|ro.boot.*|ro.build.*|ro.product.*|ro.crypto.*|\
    ro.system.build.*|ro.vendor.build.*|ro.*.build.fingerprint*|\
    persist.sys.usb.*|sys.usb.*|ro.zygote*|ro.control_privapp_permissions*)
      log "line $n: $1 refused: $p is a denied property"; return 0;;
  esac
  printf '%s\n' "$p=$pv" > $PLAN/prop.$ix; log "line $n: $1 accepted ($p)"
  return 0
}
C=$ESP/gb/gb.cfg
if [ -e $C ]; then
  if [ -L $C ] || [ ! -f $C ]; then fatal="gb.cfg is not a regular file"
  else
    sz=$($B wc -c < $C); bad=$($B tr -d '\n\r -~' < $C | $B wc -c)
    case $sz$bad in ''|*[!0-9]*) fatal="cannot read gb.cfg";; esac
    [ -z "$fatal" ] && [ $sz -gt 4096 ] && fatal="gb.cfg is larger than 4 KiB"
    [ -z "$fatal" ] && [ $bad != 0 ] && fatal="gb.cfg has $bad byte(s) that are not printable ASCII"
  fi
  if [ -z "$fatal" ]; then
    n=0
    while IFS= read -r line || [ -n "$line" ]; do
      n=$((n + 1)); line=${line%"$CR"}
      [ $n -gt 64 ] && { fatal="gb.cfg has more than 64 lines"; break; }
      case $line in *"$CR"*) fatal="line $n: a CR inside the line"; break;; esac
      if [ $n = 1 ]; then [ "$line" = "# gb.cfg v1" ] || { fatal="line 1 is not '# gb.cfg v1'"; break; }; continue; fi
      case $line in ''|'#'*) continue;; esac
      case $line in *=*) ;; *) fatal="line $n is not KEY=VALUE"; break;; esac
      k=${line%%=*}; v=${line#*=}
      case $k in ''|*[!A-Za-z0-9_]*) fatal="line $n: keys are [A-Za-z0-9_]"; break;; esac
      [ -e $PLAN/key.$k ] && { fatal="line $n: $k given twice"; break; }
      : > $PLAN/key.$k
      case $k in
        GB_MODOPTS_*) modopts "$k" "$v" || break;;
        GB_SKIPMOD) skipmod "$v" || break;;
        GB_PROP_*) prop "$k" "$v" || break;;
        *) log "line $n: unknown key $k ignored";;
      esac
    done < $C
    [ -z "$fatal" ] && [ $n = 0 ] && fatal="gb.cfg is empty"
  fi
else
  log "no gb\\gb.cfg"
fi
O=$ESP/gb/overlay
: > $PLAN/overlay
if [ -z "$fatal" ] && [ -e $O ]; then
  if [ -L $O ] || [ ! -d $O ]; then fatal="gb\\overlay is not a directory"
  else
    $B find $O > $PLAN/olist 2>/dev/null || fatal="cannot list gb\\overlay"
    nf=0; tot=0
    while [ -z "$fatal" ] && IFS= read -r f; do
      [ "$f" = "$O" ] && continue
      rel=${f#"$O"/}
      if [ -L "$f" ]; then log "overlay: refused $rel (a symlink)"; continue; fi
      [ -d "$f" ] && continue
      if [ ! -f "$f" ]; then log "overlay: refused $rel (not a regular file)"; continue; fi
      nf=$((nf + 1))
      [ $nf -gt 64 ] && { fatal="gb\\overlay has more than 64 files"; break; }
      ok=0
      case $rel in
        vup/etc/data/hal_configs/audio_default.xml) ok=1;;
        usr/idc/?*.idc) okn "${rel#usr/idc/}" && ok=1;;
        usr/keylayout/?*.kl) okn "${rel#usr/keylayout/}" && ok=1;;
        usr/keychars/?*.kcm) okn "${rel#usr/keychars/}" && ok=1;;
        vup/etc/audio/?*/?*.xml) x=${rel#vup/etc/audio/}; okn "${x%%/*}" && okn "${x#*/}" && ok=1;;
      esac
      case ${rel##*/} in .idc|.kl|.kcm|.xml) ok=0;; esac
      [ $ok = 1 ] || { log "overlay: refused $rel (not an allowed destination)"; continue; }
      s=$($B wc -c < "$f"); case $s in ''|*[!0-9]*) log "overlay: refused $rel (unreadable)"; continue;; esac
      [ $s -le 262144 ] || { log "overlay: refused $rel (larger than 256 KiB)"; continue; }
      tot=$((tot + s)); [ $tot -le 4194304 ] || { fatal="gb\\overlay holds more than 4 MiB"; break; }
      printf '%s\n' "$rel" >> $PLAN/overlay
    done < $PLAN/olist
  fi
fi
[ -z "$fatal" ] || out "refused, nothing applied: $fatal"

# ---- phase 2: apply the plan. (wave 4, wave 3 audit LOW-2) Every file is written whole to "<file>.gbbu" next to it and
# then renamed over the file (mv -f -T, one rename on the /odm tmpfs): a kill by GBINIT's "timeout 40" leaves the old
# or the new file, never a half-written modules.load, build.prop or overlay file.
na=0
if [ -n "$($B ls $PLAN | $B grep -E '^(opt|skip)\.')" ]; then
  $B rm -f $ML.gbbu; ok=1; n1=0
  : > $ML.gbbu || ok=0
  while [ $ok = 1 ] && { IFS= read -r line || [ -n "$line" ]; }; do
    set -- $line
    case ${1:-} in ''|'#'*) printf '%s\n' "$line" >> $ML.gbbu || ok=0; continue;; esac
    b=${1%.ko}; b=$(printf '%s' "$b" | $B tr - _)
    if [ -e $PLAN/skip.$b ]; then printf '#%s\n' "$line" >> $ML.gbbu || ok=0; log "modules.load: $1 commented out"; n1=$((n1 + 1)); continue; fi
    if [ -e $PLAN/opt.$b ]; then
      IFS= read -r x < $PLAN/opt.$b; line="$line $x"; log "modules.load: $1 gets options $x"; n1=$((n1 + 1))
    fi
    printf '%s\n' "$line" >> $ML.gbbu || ok=0
  done < $ML
  if [ $ok = 1 ] && $B chmod 0644 $ML.gbbu && $B mv -f -T $ML.gbbu $ML; then na=$((na + n1))
  else $B rm -f $ML.gbbu; log "modules.load: rewrite FAILED (left unchanged)"; fi
fi
props=
for ix in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do [ -f $PLAN/prop.$ix ] && props="$props $ix"; done
if [ -n "$props" ]; then
  $B rm -f $BP.gbbu; ok=1
  if [ -e $BP ]; then $B cat $BP > $BP.gbbu || ok=0; else : > $BP.gbbu || ok=0; fi
  if [ $ok = 1 ] && [ -s $BP.gbbu ] && [ "$($B tail -c 1 $BP.gbbu | $B od -A n -t x1 | $B tr -d ' \n')" != 0a ]; then
    echo >> $BP.gbbu || ok=0
  fi
  for ix in $props; do [ $ok = 1 ] && { $B cat $PLAN/prop.$ix >> $BP.gbbu || ok=0; }; done
  if [ $ok = 1 ] && $B chmod 0644 $BP.gbbu && $B mv -f -T $BP.gbbu $BP; then
    for ix in $props; do log "build.prop: appended GB_PROP_$ix"; na=$((na + 1)); done
  else $B rm -f $BP.gbbu; log "build.prop: rewrite FAILED (left unchanged)"; fi
fi
while IFS= read -r rel; do
  dst=$ODM/$rel; dd=${dst%/*}; q=$dd; sl=0
  while [ "$q" != "$ODM" ] && [ -n "$q" ]; do [ -L "$q" ] && sl=1; q=${q%/*}; done
  if [ $sl = 1 ] || [ -L "$dst" ]; then log "overlay: refused $rel (a symlink on the destination path)"; continue; fi
  if [ -e "$dst" ] && [ ! -f "$dst" ]; then log "overlay: refused $rel (the destination is not a regular file)"; continue; fi
  if $B mkdir -p "$dd" && $B rm -f "$dst.gbbu" && $B cat "$O/$rel" > "$dst.gbbu" && $B chmod 0644 "$dst.gbbu" &&
     $B mv -f -T "$dst.gbbu" "$dst"; then
    log "overlay: applied $rel"; na=$((na + 1))
  else $B rm -f "$dst.gbbu"; log "overlay: FAILED $rel"; fi
done < $PLAN/overlay
out "done: $na change(s) applied"
"""
assert b"\r" not in GB_BRINGUP_SH and GB_BRINGUP_SH.isascii()
assert GB_BRINGUP_SH.count(b"\nB=/busybox; R=\n#@@config-end\n") == 1, "bring-up config block (test harness hook)"
# Graphics crash capture (staged to /odm/gfxlog.sh, run by the gb_tomb service). STREAMS logcat in
# follow mode (not -d polling, which missed the fast SF SIGABRT), filtered to fatal-level + the SF/Skia/
# Vulkan/DEBUG tags. A *:F fatal line is emitted by the crashing process's OWN handler inline, so it
# survives even when crash_dump64 is SIGKILLed in the service-restart storm. Started before SF (post-fs-data).
GFXLOG = (b"#!/system/bin/sh\n"
          b"exec >/dev/kmsg 2>&1\n"
          b"echo GB_GFX_STREAM_START\n"
          # ROOT CAUSE suspect: /data full ("No space left on device" -> timberslide/dex2oat/system_server
          # crash loop). Dump df for /data + key mounts early and again later to confirm size/fill.
          b"/system/bin/sleep 25\n"
          b"echo \"GB_NVME $(/system/bin/toybox ls /dev/block/nvme* 2>&1 | /system/bin/toybox tr '\\n' ' ')\"\n"
          b"for m in /data / /mnt; do echo \"GB_DF $(/system/bin/toybox df -h $m 2>&1 | /system/bin/toybox tail -n1) [$m]\"; done\n"
          # Display is confirmed registered (dumpsys: logical display 0 @1280x800, activeMode id=0). Now
          # trace WHY boot never completes: system_server boot-phase progression, the TAIL of the whole
          # logcat (= where boot is currently stuck/looping), and the error/fatal stream. Two snapshots.
          # Hypothesis: boot is dexopt-bound (independent-nonpersistent disk -> /data wiped every boot -> full
          # dexopt from scratch). Poll every 30s for 10 min: boot_completed, bootanim exit, whether dex2oat is
          # running (dexopt in progress), and the system_server pid (to detect restarts). Shows if it is
          # progressing toward the launcher vs looping.
          # system_server crashes natively (pbtombstone) ~every restart, coinciding with dexopt start. Capture
          # the crash signal/backtrace from the crash buffer + the newest tombstone to find WHY it crashes.
          # Boot now reaches the launcher layer but idmap2d/SystemUI crash, and vold reboots ('Checkpoint
          # commit failed') after ~4 failed attempts (~90-120s), wiping the crash buffer. So LOOP-dump boot
          # state + the non-EC crashers every 18s (serial persists across the reboot) to catch the crash in
          # whichever iteration fires before it, plus the newest tombstones each round.
          b"n=0\n"
          b"while [ $n -lt 12 ]; do /system/bin/sleep 18; n=$((n+1)); t=$((25+n*18)); "
          b"echo \"GB_P$n t=${t}s boot=$(getprop sys.boot_completed) banim=$(getprop service.bootanim.exit)"
          b" ss=$(/system/bin/toybox pidof system_server) sysui=$(/system/bin/toybox pidof com.android.systemui)"
          b" launcher=$(/system/bin/toybox pidof com.google.android.apps.nexuslauncher 2>/dev/null)"
          b" dpct=$(/system/bin/toybox df /data 2>/dev/null | /system/bin/toybox tail -n1 | /system/bin/toybox awk '{print $5}')\"; "
          b"/system/bin/logcat -d -b crash 2>/dev/null | /system/bin/toybox grep -aE 'Fatal signal|Abort message|Cmdline:' | /system/bin/toybox grep -aivE 'timberslide|charge_control|cros_ec' | /system/bin/toybox tail -n 6 | while IFS= read -r l; do echo \"GB_CR$n $l\"; done; "
          b"T=$(/system/bin/toybox ls -t /data/tombstones 2>/dev/null | /system/bin/toybox grep tombstone_ | /system/bin/toybox head -n1); "
          b"[ -n \"$T\" ] && /system/bin/toybox grep -aE 'Cmdline:|signal |Abort message|#0[0-9]|#1[0-9]' /data/tombstones/$T 2>/dev/null | /system/bin/toybox head -n 10 | while IFS= read -r l; do echo \"GB_TB$n $l\"; done; "
          # (Full main/system logcat greps + ANR stack dumps used to run here every iteration; they cost ~25% of a
          # guest CPU. The live gb_console shell (gbsh.py) replaces them.)
          b"done\n"
          b"echo GB_DIAG_END\n"
          b"while true; do /system/bin/sleep 3600; done\n")
# Staged script (gb_showhome service runs it). The display scanout is healthy (0 'No screen target' errors)
# but shows the Googlebook bootanim frame instead of the launcher home screen. Find out why: dump bootanim/
# launcher pids, the focused window, the resumed activity, and the SurfaceFlinger layer list (shows whether
# nexuslauncher's surface exists/visible vs a lingering bootanim/splash). Then try to fix: force service
# .bootanim.exit, stop+kill a lingering bootanim (its surface can stay on top), wake + press HOME to pull the
# launcher to the front, and dump focus again. exec->/dev/kmsg so the output reaches serial (unlike the inline
# stdio_to_kmsg, which dropped the subprocess output). Runs as a file so init does no $-expansion on it.
# Host-driven root shell on ttyS1 (gb_console service). No echo, and stdin is a PIPE (cat) so mksh runs
# non-interactively: no prompt or line editor mangling the stream; gbsh.py frames each command with a marker.
GB_SLEEPER = b"#!/system/bin/sh\nwhile :; do /system/bin/sleep 3600; done\n"
# OTA vendor postinstall (fw-updater-wrapper) runs `google_desktop_firmware_updater vendor`, which flashes the ASUS
# Googlebook's AP/EC firmware (futility update --archive /postinstall/firmware/ap-ec-fw.zip). That must never run on
# other hardware (a VMware VM, a Surface Book 3); in the VM it fails, and its non-zero exit aborts every system update
# ("Couldn't update - Installation problem"). Bound over the updater: skip and succeed.
# Native only: refuse (exit 1) for flashing/EC tools; fwupd's daemon idles so init sees a healthy service.
GB_REFUSE = (b"#!/system/bin/sh\n"
             b"echo \"gb: $0 is disabled on this hardware (protects the Surface firmware and TPM)\" >&2\n"
             b"exit 1\n")
GB_FW_STUB = (b"#!/system/bin/sh\n"
              b"echo \"gb: firmware update skipped for $1 (not Googlebook hardware)\"\n"
              b"log -t fw_updater \"gb: firmware update skipped for $1 (not Googlebook hardware)\"\n"
              b"exit 0\n")
# vCPU count for interactive use. Measured (Settings scroll, busy 4-thread host): 4 vCPUs p50/p90 = 97/350 ms per
# frame, 3 = 44/150, 2 = 53/93 -- every cross-vCPU wakeup (app -> RenderThread -> SF -> HWC) costs a host reschedule
# of a halted vCPU, so fewer vCPUs = lower latency. Boot keeps all 4 (parallel init/dexopt); once it settles this
# takes the extra ones offline. Tunable: setprop persist.gb.cpus N (1..4; 0 = leave all online).
GB_CPUS_SH = (b"#!/system/bin/sh\n"
          b"exec >/dev/kmsg 2>&1\n"
          b"N=$(getprop persist.gb.cpus); [ -z \"$N\" ] && N=2\n"
          b"[ \"$N\" = 0 ] && exit 0\n"
          b"/system/bin/sleep 150\n"
          b"i=1; while [ -e /sys/devices/system/cpu/cpu$i/online ]; do\n"
          b"  if [ $i -lt $N ]; then echo 1 > /sys/devices/system/cpu/cpu$i/online; else echo 0 > /sys/devices/system/cpu/cpu$i/online; fi\n"
          b"  i=$((i+1))\n"
          b"done\n"
          b"echo \"GB_CPUS online=$(cat /sys/devices/system/cpu/online)\"\n")
GB_DEXOPT_SH = (b"#!/system/bin/sh\n"
          b"exec >/dev/kmsg 2>&1\n"
          b"M=/data/local/gb_dexopt.v1.done\n"
          b"[ -f $M ] && { echo GB_DEXOPT already done; exit 0; }\n"
          b"/system/bin/sleep 90\n"
          b"for p in com.android.systemui com.google.android.apps.nexuslauncher com.android.settings "
          b"com.google.android.inputmethod.latin com.android.launcher3; do\n"
          b"  /system/bin/pm path $p >/dev/null 2>&1 || continue\n"
          b"  echo GB_DEXOPT start $p\n"
          b"  /system/bin/toybox nice -n 10 /system/bin/cmd package compile -m speed -f $p 2>&1 | /system/bin/toybox tail -n 2\n"
          b"done\n"
          b"/system/bin/toybox touch $M\n"
          b"echo GB_DEXOPT done\n")
# UI scale, animations and the person's own user (gb_ui service, both trees, started at boot_completed; init never
# waits for it). (Wave 7, headless-settings: BACKLOG 9.4 notify O1-O3) lapis runs in headless system user mode, so
# per-user settings must name the person's FULL user (10, 11, ...), not the current user at boot_completed (the
# headless user 0, the login screen). The script polls the foreground user for the whole boot and sets up every full
# user at its first unlock: per boot its density, HOME and user_setup_complete; once ever (a marker in /data/local)
# the VM tree's keep-awake values of gb_provision and, natively, gb_sensors.sh's Auto-rotate default. User 0 keeps
# exactly what it got before (gb_provision; its density when nobody signs in within 60 s). Its own comments say more.
GB_UI_SH = (b"#!/system/bin/sh\n"
            b"# gb_ui.sh: service gb_ui (both trees; user root, seclabel u:r:shell:s0), started at sys.boot_completed=1. init only\n"
            b"# starts it and never waits for it. (Wave 7, headless-settings) lapis runs in headless system user mode: user 0 shows\n"
            b"# the login screen, and the person uses a FULL user (10, 11, ...) that comes to the foreground at the sign-in, possibly\n"
            b"# long after boot (a PIN). Settings written without --user reach the CURRENT user, i.e. user 0 at boot_completed\n"
            b"# (wave-6 notify evidence 60/61). So this script:\n"
            b"#  1. once per device (marker gb_anim.v1.done): animation scales 1.0, window blurs on (global settings);\n"
            b"#  2. for every user that comes to the foreground other than user 0 (the foreground user is always a full user or\n"
            b"#     the headless user 0; profiles never are), the first time this boot, once it is unlocked: the density for this\n"
            b"#     display (its display_density_forced + wm density), the stock launcher as its HOME, user_setup_complete 1 (the\n"
            b"#     SetupWizard is disabled, so nothing else sets it for a new user);\n"
            b"#  3. for every such user ONCE EVER, 30 polls (>= 60 s) after that first unlock (marker /data/local/gb_ui.user.<u>.v1,\n"
            b"#     created BEFORE the writes, so a marker that cannot be created never repeats them): VM tree: the keep-awake values\n"
            b"#     gb_provision writes for user 0 (screen_off_timeout, sleep_timeout); native: the sensors component's Auto-rotate\n"
            b"#     default (/odm/gb_sensors.sh user <u>). What the person changes later is never written again. The delay: on a\n"
            b"#     first boot, lapis sets a new user's system defaults again around its first unlock and initialization (measured:\n"
            b"#     gb_provision's screen_off_timeout for the first-boot user 10 was replaced by the default 600000);\n"
            b"#  4. user 0 (the login screen, whose HOME is lapis's login launcher): only its density, as before, when nobody has\n"
            b"#     signed in after 60 s.\n"
            b"# It polls the foreground user (am get-current-user) every 2 s for 10 min, then every 10 s, for the whole boot, so a\n"
            b"# late sign-in and a user created later are covered. At start, the markers of users that no longer exist are removed\n"
            b"# (pm list users must answer and list user 0), so a reused user id starts fresh.\n"
            b"# Test hooks (never set by init): GB_ROOT (prefix for /odm and /data), GB_TEST_PATH (prepended to PATH), GB_UI_LOG=-\n"
            b"# (log to stdout instead of /dev/kmsg), GB_UI_MAXPOLL (stop after that many polls; default: never stop).\n"
            b"[ \"${GB_UI_LOG:-}\" = - ] || exec > /dev/kmsg 2>&1\n"
            b"PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin\n"
            b"export PATH\n"
            b"R=${GB_ROOT:-}\n"
            b"MAX=${GB_UI_MAXPOLL:-}\n"
            b"case $MAX in *[!0-9]*) MAX= ;; esac\n"
            b"HW=$(cat \"$R/odm/etc/gb_hw\" 2> /dev/null)\n"
            b"HOMEPKG=com.google.android.apps.nexuslauncher\n"
            b"\n"
            b"M=$R/data/local/gb_anim.v1.done\n"
            b"if [ ! -f \"$M\" ]; then\n"
            b"  for k in window_animation_scale transition_animation_scale animator_duration_scale; do settings put global \"$k\" 1.0; done\n"
            b"  settings put global disable_window_blurs 0\n"
            b"  (: > \"$M\") 2> /dev/null\n"
            b"fi\n"
            b"\n"
            b"dens() { # $1 = user: 160 dpi * W/1280 keeps a 1280 dp wide desktop at any width >= 1920 (2560 -> 320 = 2 px per dp;\n"
            b"  # native Surface Book 3, 3000x2000 at 267 ppi: 320 dpi = 1500x1000 dp, like Windows at 200%); smaller modes use the\n"
            b"  # panel default (ro.sf.lcd_density). The user's own setting is written first: `wm density` alone targets\n"
            b"  # WindowManager's current user, which can still be 0 while a switch is in flight.\n"
            b"  W=$(wm size 2> /dev/null | sed -n 's/^Physical size: \\([0-9][0-9]*\\)x.*/\\1/p' | head -n 1)\n"
            b"  cur=$(settings get --user \"$1\" secure display_density_forced 2> /dev/null)\n"
            b"  if [ \"${W:-0}\" -ge 1920 ]; then\n"
            b"    D=$((W / 8))\n"
            b"    [ \"$HW\" = native ] && D=$((W * 16 / 150))\n"
            b"    [ \"$cur\" = \"$D\" ] || { settings put --user \"$1\" secure display_density_forced \"$D\"; wm density \"$D\"; }\n"
            b"  else\n"
            b"    D=default\n"
            b"    [ \"$cur\" = null ] || { settings delete --user \"$1\" secure display_density_forced; wm density reset; }\n"
            b"  fi\n"
            b"}\n"
            b"\n"
            b"show() { # $1 = user: the summary line (as before wave 7)\n"
            b"  echo \"GB_UI user=$1 width=$W density=$D (was $cur) anim=$(settings get global animator_duration_scale)\" \\\n"
            b"    \"window_blurs_off=$(settings get global disable_window_blurs) sf_blurs_off=$(getprop persist.sys.sf.disable_blurs)\" \\\n"
            b"    \"home=$(cmd role get-role-holders --user \"$1\" android.app.role.HOME 2>&1)\"\n"
            b"}\n"
            b"\n"
            b"unlocked() { # $1 = user: RUNNING_UNLOCKED. am get-current-user names the user as soon as a switch starts, before the\n"
            b"  # user has started (\"User is not started: <u>\"). An answer without a known state counts as unlocked.\n"
            b"  case \"$(timeout 20 am get-started-user-state \"$1\" 2>&1)\" in\n"
            b"    *RUNNING_UNLOCKED*) return 0 ;;\n"
            b"    *BOOTING* | *RUNNING_LOCKED* | *RUNNING_UNLOCKING* | *STOPPING* | *SHUTDOWN* | *'not started'*) return 1 ;;\n"
            b"  esac\n"
            b"  return 0\n"
            b"}\n"
            b"\n"
            b"user_ui() { # $1 = a full user in the foreground, the first time this boot\n"
            b"  dens \"$1\"\n"
            b"  [ \"$(settings get --user \"$1\" secure user_setup_complete 2> /dev/null)\" = 1 ] ||\n"
            b"    settings put --user \"$1\" secure user_setup_complete 1\n"
            b"  case \"$(cmd role get-role-holders --user \"$1\" android.app.role.HOME 2>&1)\" in\n"
            b"    *\"$HOMEPKG\"*) ;;\n"
            b"    *) cmd role add-role-holder --user \"$1\" android.app.role.HOME \"$HOMEPKG\" 2>&1 ;;\n"
            b"  esac\n"
            b"}\n"
            b"\n"
            b"user_once() { # $1 = a full user at its first unlock ever seen here\n"
            b"  O=$R/data/local/gb_ui.user.$1.v1\n"
            b"  [ -e \"$O\" ] && return 0\n"
            b"  if ! (umask 077 && : > \"$O\") 2> /dev/null; then\n"
            b"    echo \"GB_UI user=$1 first unlock: cannot create $O, nothing written (it is never retried without the marker)\"\n"
            b"    return 0\n"
            b"  fi\n"
            b"  case $HW in\n"
            b"    vm)\n"
            b"      settings put --user \"$1\" system screen_off_timeout 2147483647\n"
            b"      settings put --user \"$1\" secure sleep_timeout -1\n"
            b"      echo \"GB_UI user=$1 first unlock: screen_off_timeout=$(settings get --user \"$1\" system screen_off_timeout)\" \\\n"
            b"        \"sleep_timeout=$(settings get --user \"$1\" secure sleep_timeout) (once; gb_provision's user-0 values)\"\n"
            b"      ;;\n"
            b"    native)\n"
            b"      if [ -f \"$R/odm/gb_sensors.sh\" ]; then\n"
            b"        GB_SENSORS_LOG=- sh \"$R/odm/gb_sensors.sh\" user \"$1\"\n"
            b"      else\n"
            b"        echo \"GB_UI user=$1 first unlock: no /odm/gb_sensors.sh\"\n"
            b"      fi\n"
            b"      ;;\n"
            b"    *) echo \"GB_UI user=$1 first unlock: gb_hw '$HW': nothing to set\" ;;\n"
            b"  esac\n"
            b"}\n"
            b"\n"
            b"cleanup() { # markers of users that no longer exist\n"
            b"  _l=$(timeout 20 pm list users 2> /dev/null) || return 0\n"
            b"  case $_l in *'UserInfo{0:'*) ;; *) return 0 ;; esac\n"
            b"  for f in \"$R\"/data/local/gb_ui.user.*.v1; do\n"
            b"    [ -e \"$f\" ] || continue\n"
            b"    _u=${f##*/gb_ui.user.}\n"
            b"    _u=${_u%.v1}\n"
            b"    case $_u in '' | *[!0-9]*) continue ;; esac\n"
            b"    case $_l in *\"UserInfo{$_u:\"*) ;; *) rm -f \"$f\" && echo \"GB_UI user=$_u no longer exists: marker removed\" ;; esac\n"
            b"  done\n"
            b"}\n"
            b"\n"
            b"cleanup\n"
            b"SEEN=\n"
            b"PEND=\n"
            b"Z=0\n"
            b"n=0\n"
            b"while :; do\n"
            b"  c=$(timeout 20 am get-current-user 2> /dev/null)\n"
            b"  case $c in '' | *[!0-9]*) c= ;; esac\n"
            b"  [ ${#c} -le 5 ] || c=\n"
            b"  if [ -n \"$c\" ] && [ \"$c\" != 0 ]; then\n"
            b"    case \" $SEEN \" in\n"
            b"      *\" $c \"*) ;;\n"
            b"      *)\n"
            b"        if unlocked \"$c\"; then\n"
            b"          SEEN=\"$SEEN $c\"\n"
            b"          user_ui \"$c\"\n"
            b"          show \"$c\"\n"
            b"          PEND=\"$PEND $c:$n\"\n"
            b"        fi\n"
            b"        ;;\n"
            b"    esac\n"
            b"  elif [ \"$c\" = 0 ] && [ $Z = 0 ] && [ -z \"$SEEN\" ] && [ $n -ge 30 ]; then\n"
            b"    Z=1\n"
            b"    dens 0\n"
            b"    show 0\n"
            b"  fi\n"
            b"  _left=\n"
            b"  for _p in $PEND; do\n"
            b"    if [ $((n - ${_p#*:})) -ge 30 ]; then user_once \"${_p%%:*}\"; else _left=\"$_left $_p\"; fi\n"
            b"  done\n"
            b"  PEND=$_left\n"
            b"  n=$((n + 1))\n"
            b"  if [ -n \"$MAX\" ] && [ $n -ge \"$MAX\" ]; then break; fi\n"
            b"  if [ $n -lt 300 ]; then sleep 2; else sleep 10; fi\n"
            b"done\n"
            b"exit 0\n")
GB_CONSOLE_SH = (b"#!/system/bin/sh\n"
          b"/system/bin/toybox stty 115200 -echo -onlcr 2>/dev/null\n"
          b"export PATH=/system/bin:/system/xbin:/vendor/bin:/odm/bin HOME=/data/local/tmp\n"
          b"cd /data/local/tmp 2>/dev/null\n"
          b"while :; do echo GB_CONSOLE_READY; /system/bin/toybox cat | /system/bin/sh 2>&1; /system/bin/sleep 1; done\n")
SHOWHOME = (b"#!/system/bin/sh\n"
          b"exec >/dev/kmsg 2>&1\n"
          # Posture: the VM has no lid switch or hinge sensor, so DeviceStateManager sits in its default base
          # state 0 = 'CLOSED' (lid shut). That disabled the built-in keyboard and leaves the launcher in a
          # lid-closed context. Override it to the open-laptop state (prefer 'OPENED', else any *OPEN* state).
          b"L=$(/system/bin/cmd device_state print-states 2>&1)\n"
          b"echo \"$L\" | /system/bin/toybox head -n 20 | while IFS= read -r l; do echo \"GB_DSLIST $l\"; done\n"
          b"S=$(echo \"$L\" | /system/bin/toybox grep -iE \"name='OPENED'\" | /system/bin/toybox head -n 1 | /system/bin/toybox sed -n 's/.*identifier=\\([0-9]*\\).*/\\1/p')\n"
          b"[ -z \"$S\" ] && S=$(echo \"$L\" | /system/bin/toybox grep -iE \"name='[A-Z_]*OPEN\" | /system/bin/toybox head -n 1 | /system/bin/toybox sed -n 's/.*identifier=\\([0-9]*\\).*/\\1/p')\n"
          b"echo \"GB_DSSET target=[$S]\"\n"
          b"[ -n \"$S\" ] && /system/bin/cmd device_state state $S 2>&1 | while IFS= read -r l; do echo \"GB_DSSET $l\"; done\n"
          b"/system/bin/sleep 14\n"
          b'echo "GB_SH1 bootanim=[$(/system/bin/toybox pidof bootanimation)] launcher=[$(/system/bin/toybox pidof com.google.android.apps.nexuslauncher)] sysui=[$(/system/bin/toybox pidof com.android.systemui)]"\n'
          b"/system/bin/dumpsys window 2>/dev/null | /system/bin/toybox grep -iE 'mCurrentFocus|mFocusedApp' | /system/bin/toybox head -n 4 | while IFS= read -r l; do echo \"GB_SH1_FOCUS $l\"; done\n"
          b"/system/bin/dumpsys activity activities 2>/dev/null | /system/bin/toybox grep -iE 'ResumedActivity|topResumedActivity|mResumedActivity' | /system/bin/toybox head -n 4 | while IFS= read -r l; do echo \"GB_SH1_RES $l\"; done\n"
          b"/system/bin/dumpsys SurfaceFlinger --list 2>/dev/null | /system/bin/toybox grep -iE 'boot|nexus|launch|wallpaper|splash|gemini|setup|com.google' | /system/bin/toybox head -n 15 | while IFS= read -r l; do echo \"GB_SH1_SF $l\"; done\n"
          b"/system/bin/setprop service.bootanim.exit 1\n"
          b"/system/bin/setprop ctl.stop bootanim\n"
          b"B=$(/system/bin/toybox pidof bootanimation);[ -n \"$B\" ] && /system/bin/kill $B && echo \"GB_SH killed bootanim $B\"\n"
          b"/system/bin/input keyevent 224\n"
          b"/system/bin/input keyevent 3\n"
          b"/system/bin/sleep 6\n"
          b'echo "GB_SH2 bootanim=[$(/system/bin/toybox pidof bootanimation)]"\n'
          b"/system/bin/dumpsys window 2>/dev/null | /system/bin/toybox grep -iE 'mCurrentFocus|mFocusedApp' | /system/bin/toybox head -n 4 | while IFS= read -r l; do echo \"GB_SH2_FOCUS $l\"; done\n"
          # Input: the vendor touchpad-inhibitor (stubbed in _entries) inhibits the internal keyboard/touchpad when
          # it finds no lid switch, which is always the case in a VM. Backstop: log every input device's kernel
          # 'inhibited' flag, clear them all, log again. Then start two background watchers that echo the first raw
          # events from the keyboard (event1) and mouse (event2) to kmsg, so the serial log proves whether the
          # user's keystrokes / mouse moves reach the guest kernel. Finally dump InputReader's per-device view.
          b"for d in /sys/class/input/input*; do echo \"GB_INH_PRE $d name=[$(/system/bin/toybox cat $d/name 2>/dev/null)] inhibited=[$(/system/bin/toybox cat $d/inhibited 2>/dev/null)]\"; done\n"
          b"for f in /sys/class/input/input*/inhibited; do echo 0 > $f 2>/dev/null; done\n"
          b"for d in /sys/class/input/input*; do echo \"GB_INH_POST $d inhibited=[$(/system/bin/toybox cat $d/inhibited 2>/dev/null)]\"; done\n"
          b"/system/bin/toybox chmod 0666 /dev/input/event* 2>&1\n"
          b"(/system/bin/getevent -lt /dev/input/event1 2>&1 | /system/bin/toybox head -n 16 | while IFS= read -r l; do echo \"GB_KEV1 $l\"; done) &\n"
          b"(/system/bin/getevent -lt /dev/input/event2 2>&1 | /system/bin/toybox head -n 16 | while IFS= read -r l; do echo \"GB_KEV2 $l\"; done) &\n"
          b"/system/bin/cmd input 2>&1 | /system/bin/toybox head -n 40 | while IFS= read -r l; do echo \"GB_CMDIN $l\"; done\n"
          b"/system/bin/dumpsys input 2>&1 | /system/bin/toybox grep -iE 'Device [0-9]|^ +[0-9]+: |Classes|Enabled|disabled|ConfigurationFile|Associated|Viewport|Sources' | /system/bin/toybox head -n 50 | while IFS= read -r l; do echo \"GB_INPUT_DS $l\"; done\n"
          b"/system/bin/dumpsys device_state 2>&1 | /system/bin/toybox head -n 30 | while IFS= read -r l; do echo \"GB_DSTATE $l\"; done\n"
          # Rendering config actually in effect (lapis values vs. the emulator's), to decide on color-management and
          # composition-format overrides: SF/HWUI/EGL props + SurfaceFlinger's own summary (color mode, RE backend).
          b"/system/bin/getprop 2>&1 | /system/bin/toybox grep -iE 'surface_flinger|debug\\.sf|hwui|renderengine|egl|vulkan|angle|wide|color' | while IFS= read -r l; do echo \"GB_PROP $l\"; done\n"
          b"/system/bin/dumpsys SurfaceFlinger 2>&1 | /system/bin/toybox grep -iE 'wide|color|dataspace|renderengine|backend|composition|refresh|vsync|client' | /system/bin/toybox head -n 40 | while IFS= read -r l; do echo \"GB_SFINFO $l\"; done\n"
          # Hardware cursor check: is Android's pointer sprite composed as CURSOR on the vmwgfx cursor plane?
          b"/system/bin/dumpsys SurfaceFlinger 2>&1 | /system/bin/toybox grep -iE 'Sprite|CURSOR|composition type' | /system/bin/toybox head -n 12 | while IFS= read -r l; do echo \"GB_CURSOR sf $l\"; done\n"
          b"/system/bin/toybox mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null\n"
          b"/system/bin/toybox grep -A8 -iE 'plane\\[[0-9]+\\]: (cursor|plane-1)' /sys/kernel/debug/dri/0/state 2>&1 | /system/bin/toybox head -n 18 | while IFS= read -r l; do echo \"GB_CURSOR drm $l\"; done\n"
          b"echo GB_SHOWHOME_DONE\n"
          # PERMANENT keep-awake: the display was going to sleep after long idle (the VM reports no charger,
          # which defeats stay_on_while_plugged_in) and the sleep/resume WEDGED SurfaceFlinger -> unrecoverable
          # black screen (a manual click/keypress did NOT wake it). Prevent that by NEVER letting the display
          # idle: send KEYCODE_WAKEUP every 25s forever. WAKEUP only (no HOME) so it never disrupts whatever
          # app is in the foreground. This service intentionally never exits.
          b"while :; do /system/bin/sleep 25; /system/bin/input keyevent 224; done\n")
# W4 rehearsal finding (2026-10-26, dgpu\w4\evidence\rehearsal): init's `stdio_to_kmsg` points a service's stdout/stderr
# at /dev/kmsg_debug and falls back to /dev/null when that node is missing. lapis's init creates /dev/kmsg_debug only on
# debuggable builds; lapis is a user build (ro.debuggable=0; no /dev/kmsg_debug, measured on the rehearsal VM), so every
# stdio_to_kmsg service (gb_gatekeeper, gb_keymint, gb_bootok, gb_vkenum, ... and lapis's prng_seeder) wrote into
# /dev/null: none of their lines reached dmesg, serial0.log or gb_bootlog (GATEKEEPER.md 9.2 step 1 and 8.1 rely on
# them). Measured fix: a root-only node (0600 root:root, label kmsg_debug_device from plat_file_contexts) made a
# restarted gb_gatekeeper's "starting" / "registered" lines appear in dmesg, i.e. init opens it as root before it drops
# privileges; no world-writable node is needed. Both trees, first thing at early-init (the earliest stdio_to_kmsg
# services, gb_keymint and gb_gatekeeper, start with class early_hal at late-fs). Checked by _check_vm/_check_native
# and verify_image V29.
GB_KMSGDBG_SVC = (b"service gb_kmsgdbg /system/bin/toybox mknod -m 0600 /dev/kmsg_debug c 1 11\n"
                  b"    user root\n"
                  b"    group root\n"
                  b"    seclabel u:r:shell:s0\n"
                  b"    oneshot\n"
                  b"    disabled\n"
                  b"\n")
GB_KMSGDBG_EARLY = b"    exec_start gb_kmsgdbg\n    restorecon /dev/kmsg_debug\n"

def _with_kmsgdbg(rc):
    """gb.rc with the gb_kmsgdbg service first and its two lines right after the first `on early-init`."""
    assert b"on early-init\n" in rc and b"gb_kmsgdbg" not in rc
    return GB_KMSGDBG_SVC + rc.replace(b"on early-init\n", b"on early-init\n" + GB_KMSGDBG_EARLY, 1)

def _has_kmsgdbg(rc):
    """True if rc holds the gb_kmsgdbg service once and its two lines once, right after the first `on early-init`."""
    i = rc.find(b"on early-init\n")
    return (rc.count(GB_KMSGDBG_SVC) == 1 and rc.count(GB_KMSGDBG_EARLY) == 1 and i >= 0
            and rc[i + len(b"on early-init\n"):].startswith(GB_KMSGDBG_EARLY))

GB_RC = (
         # Software KeyMint HAL (binary + impl libs provided by the /vendor overlay below).
         b"service gb_keymint /vendor/bin/hw/android.hardware.security.keymint-service\n"
         b"    class early_hal\n"
         b"    user nobody\n"
         b"    group drmrpc\n"
         b"    seclabel u:r:hal_keymint_default:s0\n"
         b"    stdio_to_kmsg\n"
         b"\n"
         # EDID injector (debugfs edid_override). Runs as a shell-domain oneshot so init has a valid
         # domain (the bare `exec -- /system/bin/sh` form is refused: no init->shell auto-transition).
         # Driven synchronously via `exec_start gb_edid` at early-init, before the composer probes.
         b"service gb_edid /system/bin/sh /odm/gb_edid.sh\n"
         b"    user root\n"
         b"    group root system\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         # Mark the current A/B slot booted-successfully so vold's cp_needsCheckpoint()==false. Otherwise the
         # slot is never marked (no A/B bootloader on a direct boot) -> system_server tries to commit a
         # checkpoint, fails, and reboots 'Checkpoint commit failed' at ~90-120s, before boot_completed.
         b"service gb_bootok /system/bin/bootctl mark-boot-successful\n"
         b"    user root\n"
         b"    group root system\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         # Stay awake. ~36s after boot_completed Android suspends to RAM (serial shows 'Freezing user space
         # processes' -> 'Suspending console(s)'): no input -> screen off -> suspend. That looked like a VM
         # 'freeze' (console silent, display black). Hold a kernel wakelock so the suspend path aborts, keeping
         # the GUI running and visible. Written once; persists until wake_unlock.
         b'service gb_stayawake /system/bin/sh -c "echo gbstay > /sys/power/wake_lock; echo GB_WAKELOCK_RAN"\n'
         b"    user root\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         # After boot_completed: mark the device provisioned (so nexuslauncher shows unrestricted, not in
         # setup mode) and keep the DISPLAY on (max screen-off timeout + stay-on-while-plugged) so the home
         # screen stays rendered. Needs the settings provider, so runs on sys.boot_completed=1.
         b'service gb_provision /system/bin/sh -c "/system/bin/settings put global device_provisioned 1; /system/bin/settings put secure user_setup_complete 1; /system/bin/settings put global stay_on_while_plugged_in 7; /system/bin/settings put system screen_off_timeout 2147483647; /system/bin/settings put secure sleep_timeout -1; /system/bin/settings put global bluetooth_on 0; /system/bin/svc bluetooth disable; /system/bin/svc power stayon true; /system/bin/settings put global hide_error_dialogs 1; /system/bin/input keyevent 224; echo GB_PROVISION_DONE"\n'
         b"    user system\n"
         b"    group system shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         # The display scanout is healthy (0 'No screen target' errors) but shows a Googlebook splash activity
         # that sits in front of nexuslauncher even with SetupWizard disabled. After boot settles, press HOME
         # (keyevent 3) to bring the default home (the launcher) to the front, and dump what is actually focused
         # / resumed so we can see what the splash activity is and whether the launcher draws.
         b"service gb_eglprobe /system/bin/sh /odm/gb_eglprobe.sh\n"
         b"    user root\n"
         b"    group root system graphics shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         # Live root shell on COM2 (/dev/ttyS1 = vmx serial1 named pipe \\.\pipe\lapis_con), driven from the host
         # by gbsh.py, so fixes can be tested on the running guest instead of a full reboot each time.
         b"service gb_console /system/bin/sh /odm/gb_console.sh\n"
         b"    user root\n"
         b"    group root system shell log readproc graphics input\n"
         b"    seclabel u:r:shell:s0\n"
         b"    console ttyS1\n"
         b"    disabled\n"
         b"\n"
         # One-time AOT compile (speed) of the UI-critical apps -- everything else ships 'verify' only (interpreted
         # until JIT warms up = jank). /data persists now, so this runs once (marker in /data/local).
         b"service gb_vmtools /vendor/bin/gb_vmtools\n"
         b"    class core\n"
         b"    user root\n"
         b"    group root\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"\n"
         b"service gb_cpus /system/bin/sh /odm/gb_cpus.sh\n"
         b"    user root\n"
         b"    group root\n"
         b"    seclabel u:r:shell:s0\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"service gb_ui /system/bin/sh /odm/gb_ui.sh\n"
         b"    user root\n"
         b"    group root system shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"service gb_dexopt /system/bin/sh /odm/gb_dexopt.sh\n"
         b"    user root\n"
         b"    group root system shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"service gb_showhome /system/bin/sh /odm/gb_showhome.sh\n"
         b"    user root\n"
         b"    group root system shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         # Diagnostic: run a staged SCRIPT FILE (/odm/gfxlog.sh) that periodically dumps the graphics/Vulkan
         # init logcat to kmsg->serial. A script file avoids init's .rc arg parsing/$-expansion entirely
         # (the old inline `sh -c "...$(...)..."` form never produced ticks). tail -n (not head) keeps the
         # most-recent lines; targeted grep keeps the serial burst small enough not to overflow the UART FIFO.
         b"service gb_tomb /system/bin/sh /odm/gfxlog.sh\n"
         b"    user root\n"
         b"    group system log readproc\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    disabled\n"
         b"\n"
         # Diagnostic: Vulkan enumerator, run in SurfaceFlinger's own SELinux domain so it reproduces
         # exactly what SF sees (loader path + pastel ICD path). Output -> kmsg -> serial.
         b"service gb_vkenum /odm/vkenum\n"
         b"    user root\n"
         b"    group root system graphics\n"
         b"    seclabel u:r:surfaceflinger:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         # Diagnostic: print /proc/cpuinfo flags to verify the AVX-VNNI CPUID mask took effect.
         b'service gb_cpuinfo /system/bin/sh -c "echo GB_CPU_FEAT_BEGIN >/dev/kmsg; /system/bin/toybox grep -o -m1 gfni /proc/cpuinfo >/dev/kmsg 2>&1; /system/bin/toybox grep -o -m1 avx_vnni /proc/cpuinfo >/dev/kmsg 2>&1; echo GB_CPU_FEAT_END >/dev/kmsg"\n'
         b"    user root\n"
         b"    seclabel u:r:shell:s0\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"on early-init\n"
         b"    setprop trusty.security_vm.enabled 0\n"
         b"    setprop trusty.security_vm.nonsecure_vm_ready 0\n"
         # Overlay /vendor to inject the software keymint (new /vendor/bin/hw binary + /vendor/lib64 impl libs).
         b"    mkdir /odm/vlower 0755\n"
         b"    mount none /vendor /odm/vlower bind\n"
         b"    mkdir /odm/vwk 0755\n"
         b"    mount overlay gbkm /vendor lowerdir=/odm/vlower,upperdir=/odm/vup,workdir=/odm/vwk\n"
         # fstab: plain /data (encryption bypass, unchanged).
         b"    restorecon /odm/etc/fstab.android-desktop\n"
         b"    mount none /odm/etc/fstab.android-desktop /vendor/etc/fstab.android-desktop bind\n"
         b"    restorecon /vendor/etc/fstab.android-desktop\n"
         # se_manifest: drop the dead trusty keymint V5, declare the software keymint V4 /default.
         b"    restorecon /odm/etc/se_manifest.xml\n"
         b"    mount none /odm/etc/se_manifest.xml /system_ext/etc/vintf/manifest.xml bind\n"
         b"    restorecon /system_ext/etc/vintf/manifest.xml\n"
         # Display drivers.
         b"    insmod /odm/lib/modules/drm_sysfb_helper.ko\n"
         b"    insmod /odm/lib/modules/efidrm.ko\n"
         b"    insmod /odm/lib/modules/drm_ttm_helper.ko\n"
         b"    insmod /odm/lib/modules/vmwgfx.ko\n"
         # Inject the forced EDID into the vmwgfx Virtual-1 connector via debugfs edid_override, before the
         # composer starts (synchronous exec_start of the shell-domain oneshot), so hwc3-drm sees a real
         # EDID and SF stays off legacy multi-display mode.
         b"    exec_start gb_edid\n"
         # Mask the strongbox keymint apex's VINTF fragment. apexd-bootstrap activates that apex at early-init
         # (BEFORE our /vendor overlay, so the apex-file whiteout is too late) and its fragment declares
         # IKeyMintDevice/strongbox + IRemotelyProvisionedComponent/strongbox. The strongbox keymint SERVICE
         # never starts (gated on trusty.security_vm.keymint.enabled=1, which we leave off), so keystore2's
         # IKeystoreMaintenance::earlyBootEnded does waitForService(IKeyMintDevice/strongbox) and HANGS forever
         # (watchdog: earlyBootEnded overdue) -> keystore2 main thread stuck -> android.security.maintenance
         # never settles -> system_server blocks -> no boot_completed. Bind an empty manifest over the fragment
         # here (apex already activated, BEFORE servicemanager reads/caches VINTF in `on init`) so strongbox is
         # never declared and keystore2 only talks to the software /default keymint.
         b"    restorecon /odm/empty_vintf.xml\n"
         b"    mount none /odm/empty_vintf.xml /apex/com.android.hardware.keymint.strongbox.desktop/etc/vintf/android.hardware.security.keymint-service.strongbox.desktop.xml bind\n"
         # Weaver is Trusty/GSC-backed and never answers in this VM, but its VINTF declaration stays, so
         # system_server's LockSettingsService waits on IWeaver forever -> Watchdog kills system_server
         # ("System process not responding") -> boot_completed never fires (the system_server boot hang the
         # crossystem/checkpoint fix exposed). Bind an empty manifest over the Weaver VINTF fragment so it is
         # undeclared and LockSettings proceeds without it. (Path + mechanism from the gbos-x86 reference,
         # same lapis image: /vendor/etc/vintf/manifest/android.hardware.weaver-service.android-desktop.xml.)
         b"    mount none /odm/empty_vintf.xml /vendor/etc/vintf/manifest/android.hardware.weaver-service.android-desktop.xml bind\n"
         # Trusty Gatekeeper is also GSC-backed and enroll never returns; undeclare it too so nothing (LSS /
         # Settings) blocks on IGatekeeper during boot. (No lock-screen password; acceptable for reaching GUI.)
         b"    mount none /odm/empty_vintf.xml /vendor/etc/vintf/manifest/android.hardware.gatekeeper-service.trusty.xml bind\n"
         b"\n"
         b"on init\n"
         b"    setprop trusty.security_vm.enabled 0\n"
         # Disable RescueParty. The CrOS-EC HALs hal_desktop_charge_control_default + timberslide can't open
         # /dev/cros_ec (no EC in VMware) and crash-loop ~140x each -> sys.init.updatable_crashing fires ~796x
         # -> RescueParty escalates and RESTARTS the framework (zygote ~20x), so system_server never survives
         # to boot_completed. With RescueParty off, those crash loops are harmless noise and boot can finish.
         b"    setprop persist.sys.disable_rescue true\n"
         b"    setprop persist.device_config.configuration.disable_rescue_party true\n"
         b"    setprop persist.device_config.configuration.disable_rescue_party_factory_reset true\n"
         # /data is too small (recovery-image userdata, not resized) -> dex2oat fills it -> 'No space left on
         # device' crash loop. Skip/minimize dexopt so it doesn't write GBs of .oat to /data (apps run
         # interpreted; slower but boots). Belt-and-suspenders with the dex2oat filters.
         # (2026-10-24: /data is now the 31 GB grown userdata on a PERSISTENT disk, so the dexopt 'skip' overrides
         # that used to live here are gone: defaults apply, and gb_dexopt AOT-compiles the UI apps once.)
         # keystore2 (android17) blocks boot if KeyMint rejects setAdditionalAttestationInfo(MODULE_HASH);
         # the android16 software keymint lacks it. Pre-set 'sent' so keystore2 skips the call.
         b"    setprop keystore.module_hash.sent true\n"
         # Graphics: use the Skia VULKAN render engine, which drives libvulkan -> vulkan.intel.so (pastel)
         # DIRECTLY, bypassing EGL/GLES/ANGLE entirely (the SkiaGL path dies in chooseEglConfig: ANGLE's
         # Vulkan backend yields no EGLConfig under vmwgfx). SkiaVk needs no EGLConfig at all.
         b"    setprop debug.renderengine.backend skiavkthreaded\n"
         b"    setprop debug.hwui.renderer skiavk\n"
         # hwc3-drm (drm_hwcomposer) REJECTS every DRM plane that lacks the COLOR_PIPELINE property
         # ("Plane[%d] does not support COLOR_PIPELINE. Rejecting.") when color-pipeline mode is enabled.
         # vmwgfx planes have no COLOR_PIPELINE prop -> all planes rejected -> "Primary plane for CRTC not
         # found" -> "Failed to create pipeline" -> "No pipelines available. Creating null-display for
         # headless mode" -> SF gets no valid config (active mode id -1) -> framework default display null
         # -> system_server blocks at PHASE_WAIT_FOR_DEFAULT_DISPLAY. Disable the color pipeline so the
         # composer accepts plain vmwgfx planes and builds a real display pipeline.
         b"    setprop vendor.hwc.drm.enable_color_pipeline false\n"
         # vmwgfx's atomic-commit out-fence is unreliable (SF 'trackPendingFrame: Invalid present fence').
         b"    setprop ro.vendor.hwc.drm.present_fence_not_reliable true\n"
         # The per-frame atomic commit ('Failed to create AtomicCommitArgs' -> present BAD_DISPLAY, every
         # frame) likely sets color-management / overlay-plane atomic props vmwgfx's atomic_check rejects.
         # Disable the extra composition features so the commit is a bare primary-plane page-flip.
         b"    setprop vendor.hwc.drm.ctm 0\n"
         b"    setprop vendor.hwc.drm.short_circuit_ignore_ctm true\n"
         b"    setprop vendor.hwc.drm.short_circuit_ignore_geometry true\n"
         b"    setprop ro.vendor.hwc.use_overlay_planes false\n"
         b"    setprop vendor.hwc.drm.scale_with_gpu true\n"
         # ROOT CAUSE of the black screen: SF composites the client target as RGBA_8888 (=DRM ABGR8888/
         # AB24), which vmwgfx's planes DON'T support (only XRGB8888/ARGB8888/RGB565/XRGB1555) -> vmwgfx
         # 'unsupported pixel format AB24' -> AddFB2 fails -> composer can't build the atomic commit ->
         # every present fails. Make SF composite in BGRA_8888 (HAL fmt 5 = DRM ARGB8888/AR24), which
         # vmwgfx DOES support, so the scanout framebuffer is accepted.
         # (ro.surface_flinger.wcg_composition_pixel_format 5 -- BGRA -- used to be set here as the first black-screen
         # workaround; the vmwgfx ABGR8888 plane patch + GB_RGBA_DUMB made RGBA the native format, and BGRA buffers
         # now need Mesa's GB_BGRA_AS_RGBA retry, so don't steer anything to BGRA.)
         # (The boot animation used to be disabled -- debug.sf.nobootanimation 1 -- because under the SkiaVk/SwiftShader
         # SurfaceFlinger its layer outlived bootanim and covered the launcher. On the GPU path it is back on;
         # gb_showhome still stops a lingering bootanim after boot_completed.)
         # From Google's own Googlebook desktop emulator image (sdk_gdesktop_x86_64, vendor/build.prop): in a VM the
         # display's present fences are unreliable, so SurfaceFlinger's VSyncReactor must not feed them into its vsync
         # model -- bogus fence timestamps skew frame scheduling -> judder / torn or skipped frames.
         b"    setprop debug.sf.vsync_reactor_ignore_present_fences true\n"
         # Frame budgets for a VM whose GPU/host latency varies: SurfaceFlinger's default phase-offset mode gives SF one
         # vsync (15.7 ms) and it missed ~25-60 deadlines per scroll test. Durations mode with SF 25 ms / app 20 ms
         # (A/B: janky 17-21% -> 12-18%, more frames delivered; ~10 ms more latency).
         b"    setprop debug.sf.use_phase_offsets_as_durations true\n"
         b"    setprop debug.sf.late.sf.duration 25000000\n"
         b"    setprop debug.sf.late.app.duration 20000000\n"
         b"    setprop debug.sf.early.sf.duration 25000000\n"
         b"    setprop debug.sf.early.app.duration 20000000\n"
         b"    setprop debug.sf.earlyGl.sf.duration 25000000\n"
         b"    setprop debug.sf.earlyGl.app.duration 20000000\n"
         # Mesa's Android EGL platform picks the DRM render node by driver name; point it at vmwgfx.
         b"    setprop drm.gpu.vendor_name vmwgfx\n"
         # Blur on (GPU path). An `on init` setprop of a persist.* prop is replaced by any value stored in /data when the
         # persistent props load; the authoritative write is in `on post-fs-data`, before SurfaceFlinger starts.
         b"    setprop persist.sys.sf.disable_blurs 0\n"
         # First boot puts the SetupWizard (OOBE) in front of the launcher; in a VM it stalls (no Google
         # network/GMS), so you only ever see its 'Designed for Gemini Intelligence' splash. Disable it so
         # boot drops straight to nexuslauncher (the home screen); gb_provision also marks the device
         # provisioned so the launcher shows unrestricted.
         b"    setprop ro.setupwizard.mode DISABLED\n"
         b"    setprop persist.sys.setupwizard DISABLED\n"
         b"    start gb_cpuinfo\n"
         b"\n"
         b"on post-fs\n"
         b"    setprop trusty.security_vm.enabled 0\n"
         # /data is fresh every boot, so cppreopts.rc's `exec preloads_copy.sh` copied the 3.9 GB aicore model
         # preload into /data on EVERY boot, blocking init's action queue (thermal HAL -> system_server hang)
         # for minutes on a slow host. Not needed here: replace the script with a no-op.
         b"    mount none /odm/gb_noop.sh /system/bin/preloads_copy.sh bind\n"
         # No ChromeOS EC / GSC chip in VMware: timberslide exits(1) every ~5 s forever (400+ times per boot) and
         # each crash makes init run a ~3 s `exec flags_health_check UPDATABLE_CRASHING`, starving init's action
         # queue; udsattestation waits forever for IGsc (servicemanager -> ctl.interface_start spam). Replace them
         # with an idle sleeper (vendor ones: charge-control HAL, storage_metrics_service -- via the /vendor overlay).
         b"    mount none /odm/gb_sleeper.sh /system/bin/timberslide bind\n"
         b"    mount none /odm/gb_sleeper.sh /system/bin/udsattestation bind\n"
         b"    mount none /odm/gb_fw_stub.sh /system/bin/google_desktop_firmware_updater bind\n"
         b"\n"
         # Start the streaming graphics-crash capture before SurfaceFlinger (logd is already up at
         # post-fs-data); it follows the crash/fatal buffers live and catches SF's abort message.
         b"on post-fs-data\n"
         b"    start gb_tomb\n"
         b"    start gb_vkenum\n"
         b"    start gb_stayawake\n"
         # Blur on: written after load_persist_props and before SurfaceFlinger starts, so a stale persisted '1' cannot win.
         b"    setprop persist.sys.sf.disable_blurs 0\n"
         # re-assert after persist props load from /data, so RescueParty stays disabled
         b"    setprop persist.sys.disable_rescue true\n"
         b"    setprop persist.device_config.configuration.disable_rescue_party true\n"
         # mark the slot successful so no A/B checkpoint -> no 'Checkpoint commit failed' reboot
         b"    exec_start gb_bootok\n"
         b"\n"
         b"on late-fs\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"\n"
         b"on post-fs-data\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"\n"
         b"on boot\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"    start gb_console\n"
         b"\n"
         b"on property:sys.boot_completed=1\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"    start gb_stayawake\n"
         b"    start gb_provision\n"
         b"    start gb_ui\n"
         b"    start gb_showhome\n"
         b"    start gb_dexopt\n"
         b"    start gb_cpus\n")
GB_RC = _with_kmsgdbg(GB_RC)                    # stdio_to_kmsg works on the user build (see GB_KMSGDBG_SVC)

# GPU rendering switch (enable by creating inject/mesa/ENABLE_SKIAGL; delete it to roll back to SwiftShader/SkiaVk):
# system EGL = Mesa svga (host GPU via VMware SVGA3D) instead of ANGLE, SurfaceFlinger + HWUI on SkiaGL. Needs the
# GB_RGBA_DUMB vmwgfx so gralloc's 32bpp buffers are R8G8B8A8 surfaces Mesa can import and render into.
MESA_GL = bool(MESA) and os.path.exists(os.path.join(_MESA_DIR, "ENABLE_SKIAGL"))
if MESA_GL:
    ODM_BUILD_PROP += (b"# GPU path: Mesa gallium svga as the system GLES driver\n"
                       b"ro.hardware.egl=mesa\n"
                       b"ro.hardware.gralloc=minigbm\n"
                       b"ro.hwui.use_vulkan=false\n"
                       b"ro.opengles.version=196609\n")
    for _old, _new in ((b"    setprop debug.renderengine.backend skiavkthreaded\n",
                        b"    setprop debug.renderengine.backend skiaglthreaded\n"),
                       (b"    setprop debug.hwui.renderer skiavk\n",
                        b"    setprop debug.hwui.renderer skiagl\n    setprop persist.graphics.egl mesa\n")):
        assert GB_RC.count(_old) == 1, _old
        GB_RC = GB_RC.replace(_old, _new)
print(f"GPU (Mesa svga / SkiaGL): {'ON' if MESA_GL else 'off'}; absolute mouse: {'ON' if ABSMOUSE else 'off'}", flush=True)

if ABSMOUSE:
    _vm_insmod = b"    insmod /odm/lib/modules/vmwgfx.ko\n"
    assert GB_RC.count(_vm_insmod) == 1
    GB_RC = GB_RC.replace(_vm_insmod, _vm_insmod + b"    insmod /odm/lib/modules/psmouse.ko\n") + ABSMOUSE_RC

# Networking: VMware paravirtual NIC (vmx ethernet0.virtualDev = "vmxnet3"; kernel build #5 CONFIG_VMXNET3=m). It
# creates eth0, which Android's EthernetTracker (filter (usb|eth)\d+) brings up with DHCP. Without a NIC driver, Play
# Store / Play services retried endlessly after every login.
_VMXNET3 = os.path.join(HERE, "inject", "modules", "vmxnet3.ko")
if os.path.exists(_VMXNET3):
    MODS.append(("vmxnet3.ko", open(_VMXNET3, "rb").read()))
    _vm_insmod = b"    insmod /odm/lib/modules/vmwgfx.ko\n"
    assert GB_RC.count(_vm_insmod) == 1
    GB_RC = GB_RC.replace(_vm_insmod, _vm_insmod + b"    insmod /odm/lib/modules/vmxnet3.ko\n")
print(f"network (vmxnet3): {'ON' if os.path.exists(_VMXNET3) else 'off'}", flush=True)

# Display size: make DISPLAY vmwgfx's preferred mode right after the driver loads, before the composer probes it.
if LAYOUT_BIN is not None:
    _edid_exec = b"    exec_start gb_edid\n"
    assert GB_RC.count(_edid_exec) == 1
    GB_RC = GB_RC.replace(_edid_exec, b"    exec_start gb_layout\n" + _edid_exec) + (
        b"\nservice gb_layout /odm/gb_layout " + DISPLAY + b"\n"
        b"    user root\n"
        b"    group root graphics\n"
        b"    seclabel u:r:shell:s0\n"
        b"    stdio_to_kmsg\n"
        b"    oneshot\n"
        b"    disabled\n")
print(f"display: {DISPLAY.decode() if LAYOUT_BIN is not None else 'vmwgfx default'}", flush=True)

# Host battery: VMware ("Report battery information to guest", default on) exposes the laptop's AC adapter
# (\_SB_.ACAD) and batteries (\_SB_.BAT1/BAT2 -- the Surface Book 3 has two) as ACPI devices. Kernel build #6 adds
# the ACPI ac/battery drivers as modules; they create /sys/class/power_supply/{AC,BAT*}, which Android's health HAL
# (vendor.health-default) reports, so the status bar shows the real host battery like a Windows guest does.
# BUT VMware forwards only the host's first battery: on the Surface Book 3 that is the clipboard battery, which stays at
# 100% while the keyboard-base battery drains first. So when test_power.ko (kernel build #11) is staged, the ACPI
# drivers stay unloaded and gb_vmtools mirrors Windows' composite battery (guestinfo.gb.battery, written by the host
# helper gb_hostbatt.exe) into test_power's supply instead.
_TP = os.path.join(HERE, "inject", "modules", "test_power.ko")
_BATT_MODS = ("test_power.ko",) if os.path.exists(_TP) else ("ac.ko", "battery.ko")
for _m in _BATT_MODS:
    _p = os.path.join(HERE, "inject", "modules", _m)
    if os.path.exists(_p):
        MODS.append((_m, open(_p, "rb").read()))
        _vm_insmod = b"    insmod /odm/lib/modules/vmwgfx.ko\n"
        GB_RC = GB_RC.replace(_vm_insmod, _vm_insmod + b"    insmod /odm/lib/modules/" + _m.encode() + b"\n", 1)
# lapis's vendor_dlkm ships its own ACPI battery.ko, which ueventd autoloads for VMware's BAT1 before our rc runs; a
# second Battery supply would win in the health HAL (it reports the first one). Unbind it at post-fs-data, before the
# health HAL (class hal) starts on boot, so only test_power's composite battery remains.
GB_BATTUNBIND_SH = (b"#!/system/bin/sh\n"
                    b"for d in /sys/bus/acpi/drivers/battery/PNP0C0A:*; do\n"
                    b"  [ -e \"$d\" ] && echo \"${d##*/}\" > /sys/bus/acpi/drivers/battery/unbind && echo \"gb: unbound ACPI battery ${d##*/}\" > /dev/kmsg\n"
                    b"done\n"
                    b"exit 0\n")
if _BATT_MODS[0] == "test_power.ko":
    GB_RC += (b"\n"
              b"service gb_battunbind /system/bin/sh /odm/gb_battunbind.sh\n"
              b"    user root\n"
              b"    group root\n"
              b"    seclabel u:r:shell:s0\n"
              b"    oneshot\n"
              b"    disabled\n"
              b"\n"
              b"on post-fs-data\n"
              b"    exec_start gb_battunbind\n")
print(f"host battery: {'composite via gb_hostbatt + test_power' if _BATT_MODS[0] == 'test_power.ko' else 'VMware ACPI (first host battery only)'}", flush=True)

# zram for lapis's own memory daemon in the VM (W4, user decision 2026-10-26; dgpu\w4\VM_UPGRADE.md 2.2(a)). lapis's
# mmd_setup aborted on every VM boot ("zram setup failed: failed to write zram disk size": no zram device; lapis's own
# Google-signed /system/lib/modules/zram.ko is not loaded, and our kernel enforces module signatures). The VM tree now
# loads our k15-signed zsmalloc.ko + zram.ko (stage_kernel.py VM_MODS -> inject\modules) at early-init and does nothing
# else: mmd sizes zram0 itself after boot_completed (mmd.zram.size=150%), runs mkswap and swapon, and creates its 8 GiB
# writeback file on /data only while /data has >= 8 GiB free (lapis props). A disksize write here would make mmd's own
# write fail (EBUSY on an initialised device) and abort it again, so the shipped VM gb.rc never writes zram0/disksize
# (_check_vm, verify_image V28). Required: a VM build without the two staged modules stops here.
VM_ZRAM = ("zsmalloc.ko", "zram.ko")                     # load order (zram depends on zsmalloc)
for _m in VM_ZRAM:
    _p = os.path.join(HERE, "inject", "modules", _m)
    assert os.path.isfile(_p), f"inject\\modules\\{_m} missing: stage it with stage_kernel.py (VM_MODS, W4)"
    MODS.append((_m, open(_p, "rb").read()))
_vm_insmod = b"    insmod /odm/lib/modules/vmwgfx.ko\n"
assert GB_RC.count(_vm_insmod) == 1
GB_RC = GB_RC.replace(_vm_insmod, _vm_insmod + b"".join(b"    insmod /odm/lib/modules/" + _m.encode() + b"\n"
                                                        for _m in VM_ZRAM), 1)
# W4 rehearsal finding (2026-10-26, dgpu\w4\evidence\rehearsal; 1 boot of 17 on the throwaway VM): lapis's vendor service
# insmod_sh_desktop_earlyinit (/vendor/etc/init/init.insmod.rc; its early-init action runs BEFORE this file's) does
# `modprobe -a -d /system/lib/modules -d /vendor/lib/modules zram` (/vendor/etc/init.insmod.common.cfg). That is
# lapis's Google-signed zram.ko, which our kernel refuses ("zram: Cannot use protected symbol ...", err -13). While
# its attempt is in flight our early-init `insmod /odm/lib/modules/zram.ko` fails with EBUSY ("Device or resource
# busy"), no zram0 exists and mmd_setup aborts as before W4 ("failed to write zram disk size"). gb_zram, started at
# post-fs-data (that vendor service ends within about 1 s of early-init; it still waits up to 10 s for it), loads our
# zsmalloc.ko / zram.ko only if they are missing and logs one line either way. It never sizes, formats or enables zram
# (mmd's job after boot_completed). Checked by _check_vm and verify_image V28.
GB_ZRAM_SH = (b"#!/system/bin/sh\n"
              b"exec >/dev/kmsg 2>&1\n"
              b"n=0; while [ \"$(/system/bin/getprop init.svc.insmod_sh_desktop_earlyinit)\" = running ] && [ $n -lt 20 ]; do "
              b"/system/bin/sleep 0.5; n=$((n+1)); done\n"
              b"if [ -e /sys/block/zram0 ]; then echo \"gb_zram: zram0 present (early-init insmod)\"; exit 0; fi\n"
              b"echo \"gb_zram: zram0 missing at post-fs-data (lapis's own modprobe of zram raced our early-init insmod): "
              b"loading ours\"\n"
              b"for m in zsmalloc zram; do\n"
              b"  [ -d /sys/module/$m ] || /system/bin/insmod /odm/lib/modules/$m.ko || echo \"gb_zram: insmod $m.ko FAILED\"\n"
              b"done\n"
              b"if [ -e /sys/block/zram0 ]; then echo \"gb_zram: zram0 present now\"; else echo \"gb_zram: zram0 still missing\"; fi\n")
GB_ZRAM_RC = (b"\n"
              b"service gb_zram /system/bin/sh /odm/gb_zram.sh\n"
              b"    user root\n"
              b"    group root\n"
              b"    seclabel u:r:shell:s0\n"
              b"    oneshot\n"
              b"    disabled\n"
              b"\n"
              b"on post-fs-data\n"
              b"    start gb_zram\n")
GB_RC += GB_ZRAM_RC
print("VM zram (mmd): ON (zsmalloc.ko + zram.ko at early-init, gb_zram fallback at post-fs-data; lapis's mmd sizes and "
      "enables it)", flush=True)

# Sound in the VM: VMware virtual HD Audio (vmx sound.virtualDev = "hdaudio") on lapis's legacy snd-hda-intel stack.
# The lapis audio HAL (DRAS) is configured for the Googlebook's SoundWire card, so the VM tree overrides
# hal_configs/audio_default.xml with the "Generic" card and its config dir (inject\vmaudio\Generic_vmware);
# gb_vmaudio.sh loads the (autoload-blocklisted) HDA modules and unmutes the codec before the HAL starts.
VMAUDIO_DIR = os.path.join(HERE, "inject", "vmaudio")
VMAUDIO = os.path.isfile(os.path.join(VMAUDIO_DIR, "gb_vmaudio.sh"))
if VMAUDIO:
    GB_RC += (b"\n"
              b"service gb_vmaudio /system/bin/sh /odm/gb_vmaudio.sh\n"
              b"    user root\n"
              b"    group root audio\n"
              b"    seclabel u:r:shell:s0\n"
              b"    oneshot\n"
              b"    disabled\n"
              b"\n"
              b"on post-fs-data\n"
              b"    exec_start gb_vmaudio\n")
print(f"VM sound (VMware HD Audio): {'ON' if VMAUDIO else 'off'}", flush=True)

# Network ADB for scrcpy (VM screen + audio in a window on the host, host\Googlebook-scrcpy.cmd):
# gb_adb.sh authorizes the host's public ADB key (inject\adb\adb_keys) and moves adbd from localhost (lapis's
# adbd-killer) to tcp:5555 on the VM's NAT address, after boot_completed.
ADB_DIR = os.path.join(HERE, "inject", "adb")
VMADB = all(os.path.isfile(os.path.join(ADB_DIR, n)) for n in ("gb_adb.sh", "adb_keys"))
if VMADB:
    GB_RC += (b"\n"
              b"service gb_adb /system/bin/sh /odm/gb_adb.sh\n"
              b"    user root\n"
              b"    group root system shell\n"
              b"    seclabel u:r:shell:s0\n"
              b"    oneshot\n"
              b"    disabled\n"
              b"\n"
              b"on property:sys.boot_completed=1\n"
              b"    start gb_adb\n"
              # Android's Ethernet stack sometimes drops eth0 and never re-provisions it (unanswered DHCPDISCOVERs);
              # gb_netwatch cycles the link when it has carrier but no IPv4 for 30 s
              b"\n"
              b"service gb_netwatch /system/bin/sh /odm/gb_netwatch.sh\n"
              b"    user root\n"
              b"    group root\n"
              b"    seclabel u:r:shell:s0\n"
              b"    disabled\n"
              b"\n"
              b"on property:sys.boot_completed=1\n"
              b"    start gb_netwatch\n")
print(f"VM network ADB for scrcpy: {'ON' if VMADB else 'off'}", flush=True)

# A/B slot follower (source guest/gb_slotsync.c). An OTA writes the other slot and the boot_control HAL
# marks it active in the GPT, which systemd-boot cannot read; without this every update boots the old slot and rolls
# itself back. gb_slotsync rebuilds the ESP's initrd for the slot an update completed into during this boot and makes
# it the systemd-boot default; otherwise it keeps the default on the slot that actually booted.
_SLOTSYNC = os.path.join(HERE, "inject", "slotsync", "gb_slotsync")
SLOTSYNC_BIN = open(_SLOTSYNC, "rb").read() if os.path.exists(_SLOTSYNC) else None
GB_SLOTSYNC_RC = (b"\n"
                  b"service gb_slotsync /vendor/bin/gb_slotsync --watch 60\n"
                  b"    user root\n"
                  b"    group root\n"
                  b"    seclabel u:r:shell:s0\n"
                  b"    disabled\n"
                  b"\n"
                  b"on property:sys.boot_completed=1\n"
                  b"    start gb_slotsync\n") if SLOTSYNC_BIN else b""
GB_RC += GB_SLOTSYNC_RC
print(f"slot follower (gb_slotsync): {'ON' if SLOTSYNC_BIN else 'off'}", flush=True)

# ---------------- OTA gate, both trees (wave 5b) ----------------
# After a Google A/B OTA, gb_slotsync boots the new slot with THIS initrd's /odm tree (it reuses /gb/inject.gz), so every
# injection built and tested for one build also meets the next one. Design, the classification and its evidence:
# C:\GB-build\native\ota-gating\DESIGN.md. Every file of the /vendor overlay upper layer (/odm/vup), of /odm/vnni,
# /odm/etc/selinux and /odm/etc/vintf, and every rc bind source below /odm has exactly one class (first match in
# OTA_CLASSES; an unclassified one stops the build, _check_otagate):
#   skip    build-specific and not needed to boot: GBINIT removes it from /odm on a build that is not the validated one
#   keep    build-specific but needed to boot (or for the VM's display): applied whatever the build, named in
#           gb_otagate's kmsg line (skipping one would turn a possibly stale injection into a certain failure)
#   always  build-independent (our own code and configuration, LLNDK/NDK-only binaries, neutralising stubs over Google
#           binaries, masks): applied whatever the build
# The validated build is the ro.bootimage.build.fingerprint of the boot slot's init_boot ramdisk (GENERIC), packed as
# /odm/etc/gb/ota_validated; GBINIT compares it with this boot's ramdisk (the slot systemd-boot started), so the next
# ESP update re-validates. A different or unreadable build: "unvalidated" (GBINIT), tokens gb.otagate=0 (off: all as
# built) and gb.otagate=force (the unvalidated path on the validated build, for tests). The gate's files are new paths
# in both trees (ota_validated, gb_otagate.sh and its own rc file etc/init/gb_otagate.rc); gb.rc is not changed.
OTA_PROP = b"ro.bootimage.build.fingerprint"
OTA_RAMDISK_PROP = "system/etc/ramdisk/build.prop"      # in the generic (init_boot) ramdisk, i.e. at / when gbinit runs
OTA_VALIDATED = "etc/gb/ota_validated"
OTA_STATE = "etc/gb/ota_state"                           # written by GBINIT at boot (never packed)
OTA_SCRIPT = "gb_otagate.sh"
OTA_RC_FILE = "etc/init/gb_otagate.rc"
OTA_FP_SAFE = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/:+,@~")   # init/sed/sh-safe
# (C-2, fixprep keymint-wifi) the native KeyMint graft may ship with private sonames lib<x>_gbkm16.so: same class
OTA_KM_NATIVE = tuple("vup/lib64/" + _n[:-3] + "_gbkm16.so" for _n in _KMLIBS)
OTA_CLASSES = (
    # skip: removed by GBINIT on an unvalidated build (its unvalidated branch must remove exactly these roots)
    ("vnni/*", "skip", "VNNI libz patch"),               # patched Google libz; its binds are also fingerprint-gated
    # keep: build-specific, needed to boot or for the VM display
    ("vup/bin/hw/android.hardware.security.keymint-service", "keep", "KeyMint graft"),
) + tuple((_p, "keep", "KeyMint graft") for _p in tuple("vup/lib64/" + _n for _n in _KMLIBS) + OTA_KM_NATIVE) + (
    ("etc/se_manifest.xml", "keep", "system_ext VINTF"),             # bind over /system_ext/etc/vintf/manifest.xml
    ("etc/fstab.android-desktop", "keep", "fstab"),                  # bind over /vendor/etc/fstab.android-desktop
    ("etc/selinux/odm_sepolicy.cil", "keep", "SELinux"),             # OTA-robust form (ota_safe_cil)
    ("etc/selinux/precompiled_sepolicy", "keep", "SELinux"),         # 1 byte: forces init's compile
) + tuple(("vup/lib64/" + _n, "keep", "VM graphics") for _n in _VGFX) + (
    ("vup/lib64/egl/*", "keep", "VM graphics"),                      # Mesa svga (system EGL in the VM)
    # always: neutralising stubs and masks over Google files (content ours, whatever the Google file is)
    ("vup/bin/hw/android.hardware.security.secretkeeper.trusty", "always", "stub"),
    ("vup/bin/hw/android.hardware.gatekeeper-service.trusty", "always", "stub"),
    ("vup/bin/hw/touchpad-inhibitor", "always", "stub"),
    ("vup/bin/hw/vendor.google.desktop.charge-control-hal", "always", "stub"),
    ("vup/bin/hw/vendor.google.desktop.futility-executor", "always", "stub"),
    ("vup/bin/storage_metrics_service", "always", "stub"),
    ("vup/bin/crossystem", "always", "stub"),
    ("vup/bin/fwupd-binder", "always", "stub"), ("vup/bin/fwupd_blocking_update", "always", "stub"),
    ("vup/bin/fwupdmgr", "always", "stub"), ("vup/bin/flashrom", "always", "stub"), ("vup/bin/futility", "always", "stub"),
    ("vup/bin/ectool", "always", "stub"), ("vup/bin/init.adbd-killer.sh", "always", "stub"),
    ("vup/bin/android-desktop-install", "always", "stub"),
    ("empty_vintf.xml", "always", "mask"), ("gb_noop.sh", "always", "stub"), ("gb_sleeper.sh", "always", "stub"),
    ("gb_fw_stub.sh", "always", "stub"),
    # always: our own HALs, daemons and tools (static, or LLNDK/NDK-only: ota-gating evidence elf_deps.tsv)
    ("vup/bin/hw/android.hardware.gatekeeper-service.gb", "always", "Gatekeeper"),
    ("etc/vintf/manifest/android.hardware.gatekeeper-service.gb.xml", "always", "Gatekeeper"),
    ("vup/bin/gb_*", "always", "own"), ("vup/bin/tiny*", "always", "own"), ("vup/bin/iptsd*", "always", "own"),
    ("vup/bin/nvk_probe", "always", "own"), ("vup/bin/egl_probe", "always", "own"),
    ("vup/lib64/hw/vulkan.intel.so", "always", "own"),              # pastel: no private dependency
    ("vup/lib64/hw/vulkan.nouveau.so", "always", "own"),            # symlink into the dGPU payload
    # always: our own configuration (stable formats; the values are ours)
    ("vup/etc/permissions/*", "always", "config"), ("vup/usr/idc/*", "always", "config"),
    ("vup/usr/keylayout/*", "always", "config"), ("vup/etc/audio/*", "always", "config"),
    ("vup/etc/data/hal_configs/audio_default.xml", "always", "config"),
    ("vup/etc/excluded-input-devices.xml", "always", "config"), ("vup/overlay/*", "always", "config"),
)
assert all(c in ("skip", "keep", "always") for _p, c, _l in OTA_CLASSES)


def ota_safe_cil(raw):
    """(W5b OTA gate) inject\\odm_sepolicy.cil in its OTA-robust form: every `(typepermissive T)` line becomes
    `(optional gb_ota_T (typepermissive T))`. Our 1-byte /odm precompiled_sepolicy has no .sha256 companions, so init
    compiles the split policy with secilc at every boot, this file included (serial log: "Compiling SELinux policy"). In the
    plain form one type that a later Google build removes or renames fails that compile ("Failed to resolve
    typepermissive statement") -> init LOG(FATAL) -> a reboot loop on that build. In this form only that type's optional
    block is disabled. lapis .276's own secilc compiles both forms to the byte-identical policy (sha256 ab621323...,
    with or without an extra unknown type; ota-gating evidence sepolicy_secilc.txt), so the validated build is unchanged.
    Only (typepermissive T) lines are accepted (fail closed)."""
    out, seen = [], set()
    for ln in raw.split(b"\n"):
        ln = ln.rstrip(b"\r")
        if not ln.strip():
            continue
        m = re.fullmatch(rb"\(typepermissive ([A-Za-z0-9_-]+)\)", ln)
        assert m and m.group(1) not in seen, f"odm_sepolicy.cil: unexpected or repeated line {ln[:80]!r}"
        seen.add(m.group(1))
        out.append(b"(optional gb_ota_" + m.group(1) + b" (typepermissive " + m.group(1) + b"))\n")
    assert out, "odm_sepolicy.cil: no typepermissive line"
    return b"".join(out)
ODMCIL_RAW = ODMCIL                             # inject\odm_sepolicy.cil as read above
ODMCIL = ota_safe_cil(ODMCIL_RAW)               # (W5b) both trees pack the OTA-robust form (ota_safe_cil)

# (W5 final, safety audit SA-1) lapis's user build ignores androidboot.selinux=permissive: getenforce prints Enforcing,
# and only the per-type list in odm_sepolicy.cil makes the domains permissive. These domains must stay in that list:
# the VNNI relabel/bind steps (init, vendor_init), ueventd, the Gatekeeper and KeyMint grafts and their keystore/
# gatekeeperd clients, system_server, zygote and the console shell. _check_otagate refuses a cil without one of them.
OTA_REQUIRED_PERMISSIVE = ("init", "vendor_init", "ueventd", "hal_gatekeeper_default", "gatekeeperd", "keystore",
                           "hal_keymint_default", "system_server", "zygote", "shell")


def ota_cil_missing(cil):
    """(W5 final, SA-1) The OTA_REQUIRED_PERMISSIVE types that the OTA-robust cil text `cil` does not make permissive."""
    have = set(re.findall(rb"^\(optional gb_ota_([A-Za-z0-9_-]+) \(typepermissive \1\)\)$", cil, re.M))
    return [t for t in OTA_REQUIRED_PERMISSIVE if t.encode() not in have]


def _ramdisk_files(rd):
    """{path: bytes} of the regular files of a boot ramdisk: legacy LZ4 (lapis's init_boot), gzip or a plain newc cpio."""
    if rd[:4] == b"\x02\x21\x4c\x18":
        import lz4.block as _lz4b                          # (W5b) host Python's lz4 (build_esp runs on the host only)
        buf, o = bytearray(), 0
        while o + 4 <= len(rd):
            if rd[o:o + 4] == b"\x02\x21\x4c\x18":
                o += 4
                continue
            n = struct.unpack_from("<I", rd, o)[0]
            if n == 0 or o + 4 + n > len(rd):
                break
            buf += _lz4b.decompress(rd[o + 4:o + 4 + n], uncompressed_size=8 << 20)
            o += 4 + n
        buf = bytes(buf)
    elif rd[:2] == b"\x1f\x8b":
        buf = gzip.decompress(rd)
    else:
        buf = rd
    files, o = {}, 0
    while True:
        assert buf[o:o + 6] in (b"070701", b"070702"), f"ramdisk: no newc cpio header at offset {o}"
        f = [int(buf[o + 6 + 8 * i:o + 14 + 8 * i], 16) for i in range(13)]
        mode, fsz, nsz = f[1], f[6], f[11]
        name = buf[o + 110:o + 110 + nsz - 1].decode("utf-8", "replace")
        o = (o + 110 + nsz + 3) & ~3
        data = buf[o:o + fsz]
        o = (o + fsz + 3) & ~3
        if name == "TRAILER!!!":
            return files
        if (mode & 0o170000) == 0o100000:
            files[name] = data


def ota_build_fingerprint(generic):
    """The ro.bootimage.build.fingerprint of a generic (init_boot) ramdisk by GBINIT's own rule (the value of the last
    line starting with "<prop>=" in system/etc/ramdisk/build.prop, every CR removed). Fail closed: missing, empty or
    with a character outside OTA_FP_SAFE stops the build."""
    bp = _ramdisk_files(generic).get(OTA_RAMDISK_PROP)
    assert bp is not None, f"OTA gate: the boot slot's init_boot ramdisk has no {OTA_RAMDISK_PROP}"
    vals = [ln[len(OTA_PROP) + 1:].replace(b"\r", b"") for ln in bp.split(b"\n") if ln.startswith(OTA_PROP + b"=")]
    assert vals and vals[-1] and set(vals[-1]) <= OTA_FP_SAFE, \
        f"OTA gate: {OTA_PROP.decode()} of the init_boot ramdisk is {vals[-1:]!r} (non-empty, safe characters only)"
    return vals[-1]


OTA_FP = ota_build_fingerprint(GENERIC)
OTA_VALIDATED_BYTES = OTA_PROP + b"=" + OTA_FP + b"\n"
OTA_SKIP_LABELS = sorted({_l for _p, c, _l in OTA_CLASSES if c == "skip"})
OTA_KEEP_LABELS = sorted({_l for _p, c, _l in OTA_CLASSES if c == "keep"})
# GBINIT's two OTA gate pieces (literal copies, checked by _check_otagate): the token scan (while /proc is mounted) and
# the gate itself (right after the /odm copy and the VNNI kill switch, before the bring-up and the module loading)
OTA_GBINIT_TOKENS = (b"OG=; for a in $(/busybox cat /proc/cmdline); do case \"$a\" in gb.otagate=0) OG=0;; "
                     b"gb.otagate=force) OG=force;; esac; done\n")
OTA_GBINIT_UNVALIDATED = (b"  if [ \"$OS\" = unvalidated ]; then\n"
                          b"    /busybox rm -rf /odm/vnni\n"
                          b"    echo >> /odm/etc/build.prop\n"
                          b"    echo \"ro.vendor.gb.ota_gate=unvalidated\" >> /odm/etc/build.prop\n"
                          b"  fi\n")
assert GBINIT.count(OTA_GBINIT_TOKENS) == 1 and GBINIT.count(OTA_GBINIT_UNVALIDATED) == 1, "GBINIT: the OTA gate"
# gb_otagate.sh (both trees): kmsg line + the gb_update notification; nothing else. Text <= 200 printable (INTERFACES 12.1).
# Two services run it at boot_completed on an unvalidated build: gb_otagate (root) writes the kmsg line and, where
# gb_notify ships and its queue exists (native), queues the message like every other producer; gb_otagate_post (user
# shell, the identity gb_notify posts with; INTERFACES D6: posting as uid 0 is unverified) posts it directly everywhere
# else (the VM tree has no gb_notify). Notifications with the same tag replace each other, so a double post is one.
# (Wave 6, notify) lapis is in headless system user mode: a shell post without a user lands in user 0 (the login
# screen's headless "System User") and SystemUI hides it from the person's user 10 (forced OTA-gate boot: sysui
# NotCurrentUserFilter). Both paths now post ONE record for UserHandle.ALL (`cmd notification post -u all`, as
# gb_notify does), which SystemUI shows to whichever user is in the foreground, before and after the sign-in.
OTA_TITLE = b"System update not validated"
OTA_TEXT = (b"Build $B is not the build this boot setup was made for ($V). Paused until the next ESP update: "
            + ", ".join(OTA_SKIP_LABELS).encode() + b".")
assert len(OTA_TITLE) <= 40 and len(OTA_TEXT.replace(b"$B", b"x" * 24).replace(b"$V", b"x" * 24)) <= 200 \
    and all(32 <= c <= 126 for c in OTA_TITLE + OTA_TEXT), "gb_otagate: notification title/text limits (12.1)"
# (wave 7, harness-followups, OTA-F5) a bounded number of unvalidated boots, then a louder notice. gb_otagate.sh
# counts the unvalidated boots of one (booted, validated) build pair: the root service keeps the record
# /data/vendor/gb/ota_unvalidated (key, boot id, boots; 0644 in the 0751 directory native init creates, made the same
# way in the VM tree), and both services derive the same count from it and /proc/sys/kernel/random/boot_id, so the
# order in which init runs them does not matter. From the OTA_LOUD_AFTER-th boot of a pair on, the notice says how
# many and names both remedies. No boot id, no readable record or a failed write = the plain notice (fail-safe: it
# runs only after boot_completed on an unvalidated build, like the rest of the script). Same limits as 12.1.
OTA_LOUD_AFTER = b"3"
OTA_TITLE_LOUD = b"System update still not validated"
OTA_TEXT_LOUD = (b"Unvalidated boot $N of build $B (setup made for $V). Still paused: " + ", ".join(OTA_SKIP_LABELS).encode()
                 + b". Update the ESP from the host, or boot the previous slot.")
assert len(OTA_TITLE_LOUD) <= 40 and len(OTA_TEXT_LOUD.replace(b"$N", b"9" * 6).replace(b"$B", b"x" * 24)
                                          .replace(b"$V", b"x" * 24)) <= 200 \
    and all(32 <= c <= 126 for c in OTA_TITLE_LOUD + OTA_TEXT_LOUD) and OTA_LOUD_AFTER.isdigit(), \
    "gb_otagate: louder notice limits (12.1)"
GB_OTAGATE_SH = (b"#!/system/bin/sh\n"
                 b"# gb_otagate (OTA gate, wave 5b). /odm/etc/init/gb_otagate.rc starts it at boot_completed only when gbinit\n"
                 b"# found that this boot's build is not the one the ESP was built for (ro.vendor.gb.ota_gate=unvalidated;\n"
                 b"# /odm/etc/gb/ota_state). Service gb_otagate (root): one kmsg line, and the \"System update not validated\"\n"
                 b"# notification (tag gb_update, INTERFACES 12.1) queued for gb_notify where gb_notify and its queue exist\n"
                 b"# (native). Service gb_otagate_post (argument post, user shell): posts it directly everywhere else (VM).\n"
                 b"# (wave 6, notify) Posted for UserHandle.ALL (-u all): headless system user mode hides user 0's posts.\n"
                 b"# It changes nothing else. Test hooks (never set by init): GB_ROOT (path prefix), GB_TEST_PATH.\n"
                 b"# (wave 6, OTA-F3) gb_otagate then logs the stock sha256 of each bind target nothing of ours covers.\n"
                 b"LC_ALL=C; export LC_ALL\n"
                 b"PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin; export PATH\n"
                 b"R=${GB_ROOT:-}\n"
                 b"set -f\n"
                 b"S=$R/odm/etc/gb/ota_state\n"
                 b"Q=$R/data/vendor/gb/notify\n"
                 b"log1() { { printf 'gb_otagate: %s\\n' \"$*\" >> \"$R/dev/kmsg\"; } 2>/dev/null || "
                 b"log -t gb_otagate -p w \"$*\" 2>/dev/null; return 0; }\n"
                 b"val() { sed -n \"s/^$1=//p\" \"$S\" 2>/dev/null | tail -n 1 | tr -cd ' -~' | cut -c1-120; }\n"
                 b"[ -f \"$S\" ] && [ \"$(val state)\" = unvalidated ] || exit 0\n"
                 # the build id field of a fingerprint (brand/product/device:release/ID/incremental:type/tags), <= 24
                 b"bid() { _b=$(printf '%s' \"$1\" | cut -d/ -f4 | tr -cd 'A-Za-z0-9._-' | cut -c1-24); "
                 b"printf '%s' \"${_b:-unknown}\"; }\n"
                 b"B=$(bid \"$(val booted)\"); V=$(bid \"$(val validated)\")\n"
                 b"TITLE='" + OTA_TITLE + b"'\n"
                 b"TEXT=\"" + OTA_TEXT + b"\"\n"
                 # (wave 7, OTA-F5) this build pair's unvalidated boots, the same in both services: the record is read
                 # once (a rename between two reads cannot mix two records); no boot id or no matching record = the
                 # first such boot; the count is capped at 6 digits (the notice text limit)
                 b"C=$R/data/vendor/gb/ota_unvalidated\n"
                 b"K=\"$(val booted) $(val validated)\"\n"
                 b"I=$(head -c 64 \"$R/proc/sys/kernel/random/boot_id\" 2>/dev/null | tr -cd '0-9a-f-')\n"
                 b"D=$(head -c 1024 \"$C\" 2>/dev/null | tr -cd ' -~\\n')\n"
                 b"rec() { printf '%s\\n' \"$D\" | sed -n \"s/^$1=//p\" | tail -n 1; }\n"
                 b"N=$(rec boots | tr -cd 0-9 | sed 's/^0*//' | cut -c1-6)\n"
                 b"if [ -z \"$I\" ] || [ \"$(rec key)\" != \"$K\" ]; then N=1\n"
                 b"elif [ \"$(rec boot)\" != \"$I\" ]; then N=$((${N:-0} + 1)); [ \"$N\" -le 999999 ] || N=999999\n"
                 b"else N=${N:-1}; fi\n"
                 b"if [ \"$N\" -ge " + OTA_LOUD_AFTER + b" ]; then\n"
                 b"  TITLE='" + OTA_TITLE_LOUD + b"'\n"
                 b"  TEXT=\"" + OTA_TEXT_LOUD + b"\"\n"
                 b"fi\n"
                 # the gb_notify queue is used only where gb_notify ships (native tree) and its queue is a real directory
                 b"qok() { [ -f \"$R/odm/vup/bin/gb_notify.sh\" ] && [ -d \"$Q\" ] && [ ! -L \"$Q\" ]; }\n"
                 b"if [ \"${1:-}\" = post ]; then\n"
                 b"  qok && exit 0\n"
                 b"  timeout 20 cmd notification post -u all -S bigtext -t \"$TITLE\" gb_update \"$TEXT\" > /dev/null 2>&1 || "
                 b"log1 \"cmd notification post failed\"\n"
                 b"  exit 0\n"
                 b"fi\n"
                 b"log1 \"UNVALIDATED build: booted $(val booted), ESP built for $(val validated) (token $(val token)); "
                 b"skipped: " + ", ".join(OTA_SKIP_LABELS).encode() + b"; kept as built (needed to boot): "
                 + ", ".join(OTA_KEEP_LABELS).encode() + b"; update the ESP to re-validate\"\n"
                 # (wave 7, OTA-F5) the root service records this boot; never fatal, never on a validated build
                 b"if [ -n \"$I\" ] && [ ! -L \"$R/data/vendor/gb\" ]; then\n"
                 b"  [ -d \"$R/data/vendor/gb\" ] || mkdir -m 0751 \"$R/data/vendor/gb\" 2>/dev/null\n"
                 b"  rm -f \"$C.tmp\" 2>/dev/null\n"
                 b"  if [ ! -d \"$C\" ] && (umask 022; set -C; printf 'key=%s\\nboot=%s\\nboots=%s\\n' \"$K\" \"$I\" \"$N\" > \"$C.tmp\") 2>/dev/null &&\n"
                 b"     mv -f \"$C.tmp\" \"$C\" 2>/dev/null; then\n"
                 b"    log1 \"boot count: $N unvalidated boot(s) of this build pair (from " + OTA_LOUD_AFTER
                 + b" on, the notice says so)\"\n"
                 b"  else\n"
                 b"    rm -f \"$C.tmp\" 2>/dev/null; log1 \"boot count: cannot record it\"\n"
                 b"  fi\n"
                 b"fi\n"
                 b"if qok; then\n"
                 b"  ms=\"$(date +%s)000\"; f=$Q/$ms-$$-1.msg\n"
                 # (wave 6, notify) 0644 as 12.1 asks: gb_notify reads it as the shell user, and init starts this
                 # root service with umask 077 (the notify proof VM: a 0600 file gb_notify could not open)
                 b"  umask 022\n"
                 b"  set -C\n"
                 b"  if printf 'kind=notify\\ntag=gb_update\\ntitle=%s\\ntext=%s\\n' \"$TITLE\" \"$TEXT\" > \"$f.tmp\" 2>/dev/null &&\n"
                 b"     mv \"$f.tmp\" \"$f\" 2>/dev/null; then\n"
                 b"    setprop vendor.gb.notify \"$ms\" 2>/dev/null || log1 \"setprop vendor.gb.notify failed\"\n"
                 b"    log1 \"notification queued for gb_notify\"\n"
                 b"  else\n"
                 b"    rm -f \"$f.tmp\" 2>/dev/null; log1 \"cannot queue the notification\"\n"
                 b"  fi\n"
                 b"else\n"
                 b"  log1 \"no gb_notify queue: gb_otagate_post posts the notification\"\n"
                 b"fi\n"
                 # (wave 6, OTA-F3, reviewed) diagnostic only, after the notification: for each bind of our /odm rc
                 # files, the sha256 of its stock target when nothing of ours covers that path at runtime (a gated bind
                 # that did not fire, e.g. the VNNI libz on this build), to compare with the validated build's
                 # (ota-gating evidence stock_scan_slot_b.tsv, or the report of a gb.otagate=force boot of the
                 # validated build); else "hidden-by-our-bind" (mounted) or "hidden-by-overlay" (a /vendor path our
                 # overlay upper layer holds). No file of the /vendor overlay is hashed: the overlay mount also
                 # propagated onto its lower dir /odm/vlower (MEASURE_W5 M-vlower: the vendor libz bind shows up at
                 # /odm/vlower/lib64/libz.so), so no runtime path shows their stock version; they are only counted.
                 # Reads only; at most 64 bind lines.
                 b"stk() { s=$(sha256sum \"$1\" 2>/dev/null | cut -c1-64); printf '%s' \"${s:-unreadable}\"; }\n"
                 b"n=$(cd \"$R/odm/vup\" 2>/dev/null && find . -type f 2>/dev/null | wc -l | tr -cd 0-9)\n"
                 b"m=0\n"
                 b"# one word per bind line (its spaces become @), so the for loop splits lines\n"
                 b"# shellcheck disable=SC2013\n"
                 b"for x in $(grep -r -h -E '^[[:space:]]*mount none /odm/[^ ]+ /[^ ]+ bind$' \"$R/odm/etc/init\" 2>/dev/null"
                 b" | sed 's/^[[:space:]]*//; s/ /@/g' | sort -u); do\n"
                 b"  o=${x#mount@none@}; o=${o%%@*}; d=${x#mount@none@*@}; d=${d%@bind}\n"
                 b"  m=$((m + 1)); [ $m -gt 64 ] && continue\n"
                 b"  if grep -q -F \" $d \" \"$R/proc/self/mountinfo\" 2>/dev/null; then s=hidden-by-our-bind\n"
                 b"  elif [ \"${d#/vendor/}\" != \"$d\" ] && [ -e \"$R/odm/vup/${d#/vendor/}\" ]; then s=hidden-by-overlay\n"
                 b"  else s=$(stk \"$R$d\"); fi\n"
                 b"  log1 \"stock $d $s ours $(stk \"$R$o\")\"\n"
                 b"done\n"
                 b"log1 \"stock report: $m bind target(s) (at most 64 logged); ${n:-0} /vendor overlay file(s) not hashed (no "
                 b"runtime path shows their stock version); diagnostic only, compare with the validated build's report\"\n"
                 b"exit 0\n")
# /odm/etc/init/gb_otagate.rc (both trees; init parses every file of /odm/etc/init). ro.vendor.* is an actionable
# property for /odm rc files; the property comes from /odm/etc/build.prop only when gbinit appended it.
OTA_RC = (b"# ---- OTA gate (wave 5b; build_esp OTA_RC, C:\\GB-build\\native\\ota-gating\\DESIGN.md) ----\n"
          b"# gbinit appends ro.vendor.gb.ota_gate=unvalidated to /odm/etc/build.prop when this boot's build is not the\n"
          b"# one the ESP was built for; gb_otagate then logs it and the gb_update notification is posted.\n"
          b"service gb_otagate /system/bin/sh /odm/" + OTA_SCRIPT.encode() + b"\n"
          b"    user root\n"
          b"    group root shell\n"
          b"    seclabel u:r:shell:s0\n"
          b"    oneshot\n"
          b"    disabled\n"
          b"\n"
          b"service gb_otagate_post /system/bin/sh /odm/" + OTA_SCRIPT.encode() + b" post\n"
          b"    user shell\n"
          b"    group shell\n"
          b"    seclabel u:r:shell:s0\n"
          b"    oneshot\n"
          b"    disabled\n"
          b"\n"
          b"on property:sys.boot_completed=1 && property:ro.vendor.gb.ota_gate=unvalidated\n"
          b"    start gb_otagate\n"
          b"    start gb_otagate_post\n")
assert OTA_RC.count(b"\non ") == 1 and b"ro.vendor.gb.ota_gate" not in GB_RC, "OTA gate rc: one trigger, not in gb.rc"
print(f"OTA gate: ON in both trees (validated build {OTA_FP.decode()}; skip on another build: "
      f"{', '.join(OTA_SKIP_LABELS)})", flush=True)

# ---------------- Software Gatekeeper, both trees (wave 4) ----------------
# User decision 2026-10-26: a software lock-screen PIN (a convenience lock, not a hardware boundary). Design, review and
# device tests: C:\GB-build\native\dgpu\w4\GATEKEEPER.md (7: integration; 9.2/9.5: tests), REVIEW.md. ONE component
# for both trees: inject\native\components\gatekeeper.json (= the reviewed draft C:\GB-build\native\gatekeeper\
# component\gatekeeper.json except its comment). The native tree gets it through the component loader below; the VM
# tree gets the same two files and the same rc text (same "# ---- component gatekeeper ----" framing) here.
# Invariants (_check_gatekeeper, verify_image V29): the binary, the ODM VINTF fragment and the rc stanza ship together
# or not at all (a declared IGatekeeper/default without the service hangs gatekeeperd and LockSettingsService = boot;
# a service without the fragment cannot register); the binary is the accepted build (its out\android\SHA256SUMS line,
# out\ACCEPTANCE.txt version >= 0.2.0 = W4-GK-1..3), links only LLNDK libraries; the fragment is byte-identical to the
# reviewed one (override="true" on both HALs, review GK-6); exactly one "interface aidl ...IGatekeeper/default" line.
# Kept as before: the empty bind over lapis's Trusty Gatekeeper fragment (gb.rc early-init), GBINIT's whiteout of its
# .rc and SK_STUB over its binary (one declaration only: ours), Weaver undeclared (LockSettingsService then takes its
# Gatekeeper branch). No SELinux change: hal_gatekeeper_default(_exec) and gatekeeper_vendor_data_file are lapis types
# and already typepermissive in inject\odm_sepolicy.cil.
import json as _json, hashlib as _hl
GK_MANIFEST = os.path.join(HERE, "inject", "native", "components", "gatekeeper.json")
GK_BIN_DEST = "vup/bin/hw/android.hardware.gatekeeper-service.gb"
GK_VINTF_DEST = "etc/vintf/manifest/android.hardware.gatekeeper-service.gb.xml"
GK_VINTF_SHA256 = "48cd680b721996e57c4b93acce4d842d9f14e0cd3dbd9cda75e1fe3b3216b77b"   # the reviewed fragment (GK-6)
GK_MIN_VERSION = (0, 2, 0)                                # W4-GK-1 (must before a real PIN), W4-GK-2, W4-GK-3
# (W6 prep, SA-6) every accepted build after 0.2.0 must have passed the frozen on-disk vectors (gatekeeper\tests\
# test_frozen_v1.cpp: the gk_key.v1 files + handles 0.2.0 enrolled for the PIN "1234" still verify): ACCEPTANCE.txt
# "host: GK_FROZEN_V1: PASS". 0.2.0 itself predates the test; it passed it on its own sources (w6prep\safety-misc SA-6).
GK_FROZEN_PREDATES = ("505b404d0606bf80bcb41ff8a66a80c962934e3791d3b8db13645608da143d51",)
# (W7, safety audit W6-S1) ... and not on the host only: a build after 0.2.0 needs all three frozen-vector results in
# ACCEPTANCE.txt, the host run, the independent Python reference over the same vectors (frozen_check.py) and the
# Android build run on lapis's own bionic (the code that runs on the device). build.sh skips the Android run without
# LAPIS_RT/LAPIS_SYSLIB and then writes 'android-on-lapis-bionic: NOT RUN', which does not match.
GK_FROZEN_LINES = ("host: GK_FROZEN_V1: PASS", "host: GK_FROZEN_REF: PASS", "android-on-lapis-bionic: GK_FROZEN_V1: PASS")
GK_LLNDK = ("libbinder_ndk.so", "libc.so", "libdl.so", "liblog.so", "libm.so")
GK_IFACE = b"interface aidl android.hardware.gatekeeper.IGatekeeper/default"
GK_TRUSTY_BIND = (b"    mount none /odm/empty_vintf.xml "
                  b"/vendor/etc/vintf/manifest/android.hardware.gatekeeper-service.trusty.xml bind\n")
GK_WEAVER_BIND = (b"    mount none /odm/empty_vintf.xml "
                  b"/vendor/etc/vintf/manifest/android.hardware.weaver-service.android-desktop.xml bind\n")
GK_TRUSTY_WHITEOUT = b"/busybox mknod /odm/vup/etc/init/android.hardware.gatekeeper-service.trusty.rc c 0 0\n"
GK_RC_LINES = (b"service gb_gatekeeper /vendor/bin/hw/android.hardware.gatekeeper-service.gb\n", b"    class early_hal\n",
               b"    user hsm\n", b"    group hsm\n", b"    seclabel u:r:hal_gatekeeper_default:s0\n",
               b"    " + GK_IFACE + b"\n",
               b"    interface aidl android.hardware.security.sharedsecret.ISharedSecret/gatekeeper\n",
               b"    stdio_to_kmsg\n", b"    onrestart restart gatekeeperd\n",
               b"on post-fs-data\n    mkdir /data/vendor/gatekeeper 0700 hsm hsm\n")

def _elf_dynamic(data):
    """(interpreter, [DT_NEEDED names]) of an ELF64 x86-64 little-endian file, from its program headers."""
    assert data[:4] == b"\x7fELF" and data[4] == 2 and data[5] == 1 and struct.unpack_from("<H", data, 18)[0] == 62, \
        "not an x86-64 ELF64 LE file"
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    loads, dyn, interp = [], None, None
    for i in range(phnum):
        p_type, _, p_off, p_vaddr, _, p_filesz = struct.unpack_from("<IIQQQQ", data, phoff + i * phentsize)
        if p_type == 1:
            loads.append((p_vaddr, p_off, p_filesz))
        elif p_type == 2:
            dyn = (p_off, p_filesz)
        elif p_type == 3:
            interp = data[p_off:p_off + p_filesz].split(b"\0")[0].decode()
    assert dyn is not None, "no PT_DYNAMIC"
    tags = [struct.unpack_from("<qQ", data, dyn[0] + 16 * k) for k in range(dyn[1] // 16)]
    strtab = [v for t, v in tags if t == 5]
    assert len(strtab) == 1, "no single DT_STRTAB"
    off = [o + strtab[0] - va for va, o, sz in loads if va <= strtab[0] < va + sz]
    assert off, "DT_STRTAB outside every PT_LOAD"
    needed = []
    for t, v in tags:
        if t == 0:
            break
        if t == 1:
            needed.append(data[off[0] + v:data.index(b"\0", off[0] + v)].decode())
    return interp, needed

def _gatekeeper_inputs(manifest=GK_MANIFEST):
    """(binary, VINTF fragment, rc text, the gb.rc block) of the gatekeeper component after the GATEKEEPER.md 7.5 checks."""
    with open(manifest, encoding="utf-8") as fh:
        m = _json.load(fh)
    assert m.get("name") == "gatekeeper" and set(m) <= {"name", "source", "comment", "files", "rc_files"}, \
        f"{manifest}: name gatekeeper, keys name/source/comment/files/rc_files only"
    files = {e["dest"]: e for e in m["files"]}
    assert set(files) == {GK_BIN_DEST, GK_VINTF_DEST} and len(m["files"]) == 2 and len(m["rc_files"]) == 1, manifest
    assert files[GK_BIN_DEST]["mode"] in ("0o100755", "0755") and files[GK_VINTF_DEST]["mode"] in ("0644", "0o100644")
    bsrc = files[GK_BIN_DEST]["src"]
    gbin = open(bsrc, "rb").read()
    sums = {}
    for ln in open(os.path.join(os.path.dirname(bsrc), "SHA256SUMS"), "rb").read().decode("ascii").splitlines():
        if ln.strip():
            h, n = ln.split(None, 1)
            sums[n.strip().lstrip("*")] = h.lower()
    assert sums.get(os.path.basename(bsrc)) == _hl.sha256(gbin).hexdigest(), \
        f"{bsrc}: sha256 differs from its SHA256SUMS line (not the accepted build)"
    acc = open(os.path.join(os.path.dirname(os.path.dirname(bsrc)), "ACCEPTANCE.txt"), "rb").read().decode("utf-8")
    ver = re.search(r"^version: (\d+)\.(\d+)\.(\d+)$", acc, re.M)
    assert ver and tuple(int(x) for x in ver.groups()) >= GK_MIN_VERSION, \
        f"gatekeeper ACCEPTANCE.txt: version {ver.group(0) if ver else '?'} < {'.'.join(map(str, GK_MIN_VERSION))}"
    assert f"sha256: {_hl.sha256(gbin).hexdigest()}  {os.path.basename(bsrc)}" in acc.splitlines(), \
        "gatekeeper ACCEPTANCE.txt does not name this binary's sha256"
    frozen_miss = [ln for ln in GK_FROZEN_LINES if not re.search("^" + re.escape(ln), acc, re.M)]
    assert _hl.sha256(gbin).hexdigest() in GK_FROZEN_PREDATES or not frozen_miss, \
        f"gatekeeper ACCEPTANCE.txt lacks {frozen_miss} (SA-6, W6-S1: enrolled PINs must keep verifying, on the host " \
        f"and with the Android build on lapis's bionic)"
    interp, needed = _elf_dynamic(gbin)
    assert interp == "/system/bin/linker64" and needed and set(needed) <= set(GK_LLNDK), \
        f"gatekeeper binary: interpreter {interp}, NEEDED {needed} (LLNDK only: {GK_LLNDK})"
    vintf = open(files[GK_VINTF_DEST]["src"], "rb").read()
    assert _hl.sha256(vintf).hexdigest() == GK_VINTF_SHA256, "the gatekeeper VINTF fragment is not the reviewed one"
    rc = open(m["rc_files"][0], "rb").read()
    assert b"\r" not in rc and rc.isascii() and rc.endswith(b"\n"), "gb_gatekeeper.rc: LF-only ASCII"
    assert all(rc.count(s) == 1 for s in GK_RC_LINES), "gb_gatekeeper.rc lacks a GATEKEEPER.md 7.3 line"
    return gbin, vintf, rc, b"\n# ---- component gatekeeper ----\n" + rc

GK_BIN, GK_VINTF, GK_RC, GK_RC_BLOCK = _gatekeeper_inputs()
GB_RC += GK_RC_BLOCK                                      # the VM tree (native: the component loader, same block)
print(f"software Gatekeeper: ON in both trees (binary sha256 {_hl.sha256(GK_BIN).hexdigest()[:16]}..., "
      f"{len(GK_BIN):,} B)", flush=True)

# ---------------- VNNI libz patch, both trees (wave 5) ----------------
# k15 emulates VEX AVX-VNNI (#UD) on Ice Lake; ~2.48e8 emulations in the first ~7 min of a VM boot came almost all from
# libz.so's adler32_simd_ (C:\GB-build\native\vnni-perf\MEASURE.md). vnni_prepare.py (run on demand, never here) carves
# the boot slot's system/vendor from lapis.bin read-only, checks each libz.so against vnni-perf\patcher\manifest.json
# (orig_sha256), runs vnni_patch.py (a 2-byte dispatch edit: adler32 takes its scalar path) and writes
# inject\vnni\<id>\{system/lib64/libz.so, vendor/lib64/libz.so, manifest.json}. This file only PACKS every such folder
# into both trees as /odm/vnni/<id>/ and appends to both gb.rc files, after the /vendor overlay action, one init-builtin
# bind per target, gated EXACTLY on the partition's build fingerprint (init loads ro.<partition>.build.fingerprint from
# the verified partition's own build.prop before early-init): gb_slotsync reuses this initrd for an A/B OTA's new slot,
# whose fingerprint differs, so a stale patched libz is never bound over another build (INTEGRATION.md 3). No service.
# Kill switch: gb.vnni=0 on the kernel command line makes GBINIT delete /odm/vnni (the binds then fail harmlessly).
# Checks: _vnni_inputs (each file == its manifest's patched_sha256, each (orig, patched, bind, gate, reviewed gate value)
# a deployable target of the reviewed patcher manifest, gates unique, nothing else in a folder; W6: the folder's slot
# and build == this ESP's boot slot and its init_boot build), _check_vnni, verify_image V31. (W6) Read-only: GBINIT
# moves /odm/vnni onto its own tmpfs and the block's last action remounts that superblock read-only (VNNI_GBINIT_RO).
# Patched Google binaries are never published (collect.py excludes inject\vnni). Idea credit: gbOS86; code ours.
VNNI_DIR = os.path.join(HERE, "inject", "vnni")
VNNI_PATCHER_MANIFEST = os.path.join(os.environ.get("GB_BUILD") or (HERE if os.path.isdir(os.path.join(HERE, "native")) else r"C:\GB-build"), "native", "vnni-perf", "patcher", "manifest.json")
VNNI_FORMAT = "gb-vnni-1"
VNNI_ODM = "/odm/vnni"
VNNI_BINDS = {"/system/lib64/libz.so": "ro.system.build.fingerprint",     # bind target -> its gate property
              "/vendor/lib64/libz.so": "ro.vendor.build.fingerprint"}
VNNI_ID_RE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
VNNI_FP_RE = r"[^/]+/[^/]+/[^/:]+:[^/]+/([^/]+)/([^:/]+):[^/]+/[^/]+"
VNNI_PROP_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/:+,@~"   # == vnni_patch _PROP_SAFE
VNNI_RC_HEAD = (b"\n# ---- vnni libz patch (wave 5) ----\n"
                b"# Each bind fires only on the exact build it was patched for: init loads ro.<partition>.build.fingerprint\n"
                b"# from the verified partition's build.prop before early-init, and gb_slotsync reuses this initrd after an\n"
                b"# A/B OTA. gb.vnni=0 on the kernel command line makes gbinit delete /odm/vnni: every bind below then fails\n"
                b"# harmlessly and the stock libz stays under k15's emulation.\n")
# GBINIT's kill switch (pre-W5 text, W5 text): tests revert these three edits to compare with the pre-W5 GBINIT
VNNI_GBINIT_EDITS = ((b"HW=; USB=0; HWTOK=0; OV=0\n", b"HW=; USB=0; HWTOK=0; OV=0; VN=1\n"),
                     (b" gb.overlay=1) OV=1;; esac; done\n", b" gb.overlay=1) OV=1;; gb.vnni=0) VN=0;; esac; done\n"),
                     (b"/busybox cp -a $SRC/. /odm/\n",
                      b"/busybox cp -a $SRC/. /odm/\n[ \"$VN\" = 0 ] && /busybox rm -rf /odm/vnni\n"))
assert all(GBINIT.count(_new) == 1 for _old, _new in VNNI_GBINIT_EDITS), "GBINIT: the gb.vnni=0 kill switch"
# (W6, INTEGRATE_REVIEW F1 + MEASURE_W5 M-vlower) GBINIT's own tmpfs for /odm/vnni (a literal copy, checked by
# _check_vnni and V31). A per-mount `remount bind ro` reaches only the mount it names, so the copies that propagated at
# bind time (another mount namespace, /odm/vlower/lib64/libz.so) stayed rw, and the source sat on the rw /odm tmpfs.
# A superblock remount (MS_REMOUNT without MS_BIND) makes the filesystem itself read-only: every mount of it refuses
# writes (__mnt_is_readonly: MNT_READONLY or sb_rdonly), wherever it was propagated. The libraries must be relabelled
# first (restorecon needs a writable mount), so gb.rc does the remount (VNNI_RC_TAIL) after its binds; a context= mount
# here is not possible: SELinux refuses mount context options before the policy is loaded (EINVAL), and context mounts
# cannot be relabelled at all. Same files, same flags as the /odm tmpfs (no nosuid/nodev/noexec change for the binds).
VNNI_GBINIT_RO = (b"if [ -d /odm/vnni ]; then\n"
                  b"  /busybox rm -rf /odm/.gbvnni; /busybox mv /odm/vnni /odm/.gbvnni\n"
                  b"  if /busybox mkdir -m 0755 /odm/vnni && /busybox mount -t tmpfs -o mode=0755 gbvnni /odm/vnni"
                  b" && /busybox cp -a /odm/.gbvnni/. /odm/vnni/; then\n"
                  b"    /busybox rm -rf /odm/.gbvnni\n"
                  b"  else\n"
                  b"    /busybox umount /odm/vnni 2>/dev/null\n"
                  b"    /busybox rm -rf /odm/vnni /odm/.gbvnni\n"
                  b"    echo \"vnni: no own tmpfs, /odm/vnni removed (no bind)\" > /odm/gbinit_vnni.log\n"
                  b"  fi\n"
                  b"fi\n")
VNNI_GBINIT_RO_AFTER = (VNNI_GBINIT_EDITS[2][1], OTA_GBINIT_UNVALIDATED)          # the kill switch, the OTA gate
VNNI_GBINIT_RO_BEFORE = (b"if [ \"$HW\" = native ] && [ \"$HWTOK\" = 1 ]", b"exec /init\n")   # the bring-up, init


def _vnni_gbinit_ro_ok(gi):
    """(W6) GBINIT text gi holds VNNI_GBINIT_RO once, after the kill switch and the OTA gate, before the bring-up (when
    present: its own checks report it missing) and exec /init."""
    pos = [gi.find(s) for s in VNNI_GBINIT_RO_AFTER] + [gi.find(VNNI_GBINIT_RO)] + \
          [gi.find(s) for s in VNNI_GBINIT_RO_BEFORE]
    if pos[3] < 0:
        pos.pop(3)
    return gi.count(VNNI_GBINIT_RO) == 1 and min(pos) >= 0 and pos == sorted(pos)


assert _vnni_gbinit_ro_ok(GBINIT), "GBINIT: the gbvnni read-only tmpfs block (after the kill switch and the OTA gate)"
# (W6) the last action of the vnni block, on the init trigger (init runs it after every early-init action, so after every
# gated relabel + bind; the VM gb.rc keeps one `on early-init` section, V28): the gbvnni superblock goes read-only. Not
# gated (it binds nothing; if /odm/vnni is not a mount root, init logs the failure and goes on: it can never touch /odm
# itself, do_remount needs the mount root). (W7) Without /odm/vnni init logs nothing at all: AOSP init reports a
# builtin's ENOENT only at its DEBUG log level (builtins.cpp ErrnoErrorIgnoreEnoent); the wave-6 forced OTA-gate boots
# show no line for this action (w7prep\audit-lows tests\test_enoent_evidence.py).
VNNI_RC_TAIL = (b"\n# (W6) every library above is relabelled and bound (early-init): the gbvnni tmpfs (gbinit) now goes read-only\n"
                b"# for all its mounts, the binds in every mount namespace and their propagated copies included.\n"
                b"on init\n"
                b"    mount tmpfs gbvnni /odm/vnni remount ro\n")

def _vnni_action(ident, t):
    """The init action of one target: the reviewed vnni_patch.rc_block form (restorecon the source, bind it over the
    stock library, relabel, remount the bind read-only in init's mount namespace only), source /odm/vnni/<id>/<file>,
    gated on the exact fingerprint. (W6) The source is on gbinit's own gbvnni tmpfs, which VNNI_RC_TAIL then makes
    read-only as a whole (every mount of it, in every namespace)."""
    src, dst = f"{VNNI_ODM}/{ident}/{t['file']}", t["bind"]
    return (f"on early-init && property:{t['gate_prop']}={t['gate_value']}\n"
            f"    restorecon {src}\n"
            f"    mount none {src} {dst} bind\n"
            f"    restorecon {dst}\n"
            f"    mount none none {dst} remount bind ro\n").encode()

def _vnni_inputs(vdir=VNNI_DIR, patcher_manifest=VNNI_PATCHER_MANIFEST, slot=None, boot_fp=None):
    """([(path below /odm, bytes, mode)], the gb.rc block, [ids]) of every inject\\vnni\\<id>\\ folder (vnni_prepare.py's
    output); ([], b"", []) without one. Any folder that does not check out stops the build (fail closed). (W6, safety
    audit SA-2) so does a folder prepared for another slot than the one this ESP boots (slot, default SLOT) or for
    another build than that slot's (build id and incremental of its gates vs the init_boot fingerprint boot_fp, default
    OTA_FP): its binds could never fire there (it was a WARNING). (W6, SA-3) each target's gate value must also be the
    reviewed deploy.gate_value of the patcher manifest."""
    names = sorted(os.listdir(vdir)) if os.path.isdir(vdir) else []
    if not names:
        return [], b"", []
    slot = slot or SLOT
    boot_fp = boot_fp or OTA_FP.decode("ascii")
    bm = re.fullmatch(VNNI_FP_RE, boot_fp)
    assert bm, f"VNNI: the boot slot's init_boot fingerprint {boot_fp!r} is not brand/product/device:release/id/incr:type/tags"
    with open(patcher_manifest, encoding="utf-8") as fh:
        pm = _json.load(fh)
    allowed = {(t["orig_sha256"], t["patched_sha256"], t["deploy"]["bind"], t["deploy"]["gate_prop"],
                t["deploy"].get("gate_value"), t["path"]) for t in pm["targets"] if t.get("deploy")}
    files, actions, gates = [], [], set()
    for n in names:
        d = os.path.join(vdir, n)
        assert os.path.isdir(d) and re.fullmatch(VNNI_ID_RE, n), \
            f"inject\\vnni\\{n}: only <id> folders written by vnni_prepare.py belong here (move others elsewhere)"
        mraw = open(os.path.join(d, "manifest.json"), "rb").read()
        m = _json.loads(mraw.decode("utf-8"))
        assert m.get("format") == VNNI_FORMAT and m.get("id") == n and m.get("targets"), \
            f"inject\\vnni\\{n}\\manifest.json: format {VNNI_FORMAT}, id {n}, at least one target"
        assert m.get("slot") == slot, \
            (f"inject\\vnni\\{n} was prepared for slot _{m.get('slot')}, this ESP boots slot _{slot} (SA-2): run "
             f"vnni_prepare.py for this slot, or move the folder to archive\\vnni-prev to build without the patch")
        have = {os.path.relpath(os.path.join(dp, f), d).replace("\\", "/") for dp, _, fs in os.walk(d) for f in fs}
        want = {"manifest.json"} | {t["file"] for t in m["targets"]}
        assert have == want, f"inject\\vnni\\{n}: files {sorted(have)} != manifest {sorted(want)}"
        for t in m["targets"]:
            data = open(os.path.join(d, *t["file"].split("/")), "rb").read()
            assert _hl.sha256(data).hexdigest() == t["patched_sha256"] and len(data) == t["size"] and data[:4] == b"\x7fELF", \
                f"inject\\vnni\\{n}\\{t['file']}: not the patched_sha256 of its manifest"
            assert (t["orig_sha256"], t["patched_sha256"], t["bind"], t["gate_prop"], t.get("gate_value"),
                    t["file"]) in allowed, \
                (f"inject\\vnni\\{n}\\{t['file']}: not a deployable target of {patcher_manifest} (orig/patched/bind/gate "
                 f"and (SA-3) the reviewed deploy.gate_value)")
            assert VNNI_BINDS.get(t["bind"]) == t["gate_prop"] and t["file"] == t["bind"].lstrip("/"), t["bind"]
            v = t["gate_value"]
            assert (v and v != "*" and v == m["fingerprints"].get(t["gate_prop"])
                    and all(c in VNNI_PROP_SAFE for c in v) and re.fullmatch(VNNI_FP_RE, v)), \
                f"inject\\vnni\\{n}: gate value {v!r} (the partition's fingerprint, init-safe characters only)"
            fm = re.fullmatch(VNNI_FP_RE, v)
            assert fm.groups() == bm.groups(), \
                (f"inject\\vnni\\{n} was prepared for build {'/'.join(fm.groups())}, slot _{slot} runs build "
                 f"{'/'.join(bm.groups())} (SA-2): run vnni_prepare.py for this build, or move the folder to "
                 f"archive\\vnni-prev to build without the patch")
            assert (t["gate_prop"], v) not in gates, f"inject\\vnni\\{n}: a second folder for {t['gate_prop']}={v}"
            gates.add((t["gate_prop"], v))
            files.append((f"vnni/{n}/{t['file']}", data, 0o100644))
            actions.append(_vnni_action(n, t))
        files.append((f"vnni/{n}/manifest.json", mraw, 0o100644))
    return files, VNNI_RC_HEAD + b"\n".join(actions) + VNNI_RC_TAIL, names

VNNI_FILES, VNNI_RC_BLOCK, VNNI_IDS = _vnni_inputs()
GB_RC += VNNI_RC_BLOCK                  # after the gatekeeper block: the VM gb.rc's last block (native: after _COMP_RC)
_nb = VNNI_RC_BLOCK.count(b"\non early-init && property:")
print("VNNI libz patch: " + (f"ON in both trees ({', '.join(VNNI_IDS)}; {_nb} fingerprint-gated binds; slot _{SLOT}, "
                              f"build {'/'.join(re.fullmatch(VNNI_FP_RE, OTA_FP.decode('ascii')).groups())}; read-only "
                              f"gbvnni tmpfs)" if VNNI_IDS
                             else "off (no inject\\vnni\\<id> folder: run vnni_prepare.py)"), flush=True)

# ---------------- Native mode (gb.hw=native): Surface Book 3 on bare metal ----------------
# gbinit copies /gbnative instead of /gbstage. Shared with the VM: the software KeyMint graft, Trusty/GSC stubs, the
# crossystem stub, the fstab, VINTF masks and the RescueParty/checkpoint fixes. Left out: everything VMware-specific
# (vmwgfx, Mesa svga as system EGL, patched minigbm, SwiftShader Vulkan overlay, gb_vmtools/gb_layout/gb_absmouse/
# gb_edid, VM display/SF tuning). gb_console is left out on purpose: on the Surface the serial UART carries the Surface
# Aggregator (keyboard base, battery, buttons).
NATIVE_DIR = os.path.join(HERE, "inject", "native")
NATIVE_FW_DIR = os.path.join(NATIVE_DIR, "firmware")     # Surface Book 3 firmware (linux-firmware, uncompressed)
NATIVE_MOD_DIR = os.path.join(NATIVE_DIR, "modules")     # native .ko files + modules.load (load order)

def _tree(root):
    """(relative path with / separators, bytes) for every file under root."""
    out = []
    if os.path.isdir(root):
        for d, _, fs in os.walk(root):
            for n in sorted(fs):
                p = os.path.join(d, n)
                out.append((os.path.relpath(p, root).replace("\\", "/"), open(p, "rb").read()))
    return sorted(out)

NATIVE_FW = _tree(NATIVE_FW_DIR)
# Only kernel modules and their two load lists are packed (W2-13): stage_kernel.py keeps its backups in
# archive\module-backups, and any other file left in the directory (an old *.pre-<tag>, an editor backup) must never
# reach the initrd. The opt-in dGPU modules (stage_kernel.NATIVE_OPTIN) go into the payload, not the cpio (A-06).
_NATIVE_MODS_ALL = _tree(NATIVE_MOD_DIR)
assert all("/" not in _n for _n, _ in _NATIVE_MODS_ALL), "inject\\native\\modules must be flat"
_is_mod = lambda n: n.endswith(".ko") or n in ("modules.load", "modules.load.late")
NATIVE_MODS = [(_n, _d) for _n, _d in _NATIVE_MODS_ALL if _is_mod(_n)]
_stray = [_n for _n, _ in _NATIVE_MODS_ALL if not _is_mod(_n)]
print(f"native modules: {len(NATIVE_MODS)} files, {len(_stray)} other file(s) ignored"
      + (f" (e.g. {', '.join(_stray[:3])})" if _stray else ""), flush=True)

# Second-stage module pass (exec_start at early-init, before the composer starts). gbinit already loaded
# modules.load before Android init; this retries anything it could not load and then loads modules.load.late: modules
# that must link against lapis's own first-stage modules (hid-sensor-hub needs lapis's mfd-core, which also serves
# intel-lpss -> the Surface Aggregator UART, so we never load a competing mfd-core early).
GB_MODULES_SH = (b"#!/system/bin/sh\n"
                 b"exec >/dev/kmsg 2>&1\n"
                 b"D=/odm/lib/modules\n"
                 b"[ -f $D/modules.load ] || { echo 'GB_MODULES: no modules.load'; exit 0; }\n"
                 b"ok=0; bad=0\n"
                 # lapis's platform_profile (serves lapis processor_thermal and our surface_platform_profile)
                 b"[ -f $D/modules.load.late ] && /vendor/bin/modprobe --dirname=/vendor/lib/modules platform_profile\n"
                 b"for L in $D/modules.load $D/modules.load.late; do [ -f $L ] || continue\n"
                 b"while read -r m; do\n"
                 b"  case \"$m\" in ''|'#'*) continue;; esac\n"
                 b"  set -- $m; f=$D/$1; shift\n"
                 # already loaded by gbinit (the normal case): skip
                 b"  n=${f##*/}; n=${n%.ko}; n=$(echo $n | /system/bin/tr - _); [ -d /sys/module/$n ] && { ok=$((ok+1)); continue; }\n"
                 b"  if /system/bin/insmod \"$f\" \"$@\" 2>/dev/null; then ok=$((ok+1)); else echo \"GB_MODULES: insmod $f failed\"; bad=$((bad+1)); fi\n"
                 b"done < $L\n"
                 b"done\n"
                 b"echo \"GB_MODULES: loaded $ok, failed $bad\"\n")

# Native boot log: there is no serial console on real hardware, so kernel + Android logs are written to the boot
# partition (FAT32), readable from Windows: <boot partition>\gblog\boot<N>-t<T>.txt (T = 40, 90, 180, 420, 900 s).
# Each snapshot is collected in /dev first; the ESP is taken with the INTERFACES 5.4 (r3) lock and mounted rw,flush
# only for the copy (after 600 s it goes on without the lock only if the holder is another log writer, else it skips
# that snapshot); the newest 8 boots are kept and nothing is written below 128 MiB free or when the free space cannot
# be read (fail closed). (Wave 3) Each snapshot starts with a "== summary" triage block (item 8). The dgpu section is read-only
# (cached sysfs attributes, node metadata, gb_power hello/status/get-mode). The text between #@@functions-begin and
# #@@functions-end is sourced by the test harness (tests\w2a), with the GB_BOOTLOG_* overrides init never sets.
# (Wave 5, native-readiness B-1, C-3, A-L1, C-4; A-H1, A-M1, B-3, C-1, C-2) The summary reads the kernel log from a
# copy the first snapshot keeps in /dev (no avc lines) plus the current dump, counts the real threadtime drmhwc line, and
# adds the -517, buttons, posture, dsp_driver, PSR, Wi-Fi, Bluetooth, camera and linker lines; the wave 3 lines keep
# their order and prefixes (hwtest\analyze_gblog.py keys on them).
# (Wave 6, OTA gate follow-up F2) One more summary line after the gb_slotsync line: "ota gate: ..." from gbinit's
# /odm/etc/gb/ota_state (w6prep\ota-followups; hwtest\analyze_gblog.py item ota_gate).
GB_BOOTLOG_SH = rb"""#!/system/bin/sh
# gb_bootlog (native): kernel + Android logs to <boot partition>\gblog\boot<N>-t<T>.txt at T = 40, 90, 180, 420, 900 s.
# Each snapshot is collected in /dev first; the ESP is locked (INTERFACES 5.4 r3) and mounted rw,flush only for the
# copy. Lock held over 600 s: proceed without it only if the holder is gb_slotsync, gb_audiodiag or gb_bootlog; any
# other holder (a dGPU window: gb_nouveau, gb_power, gb_dgpu_report) skips that snapshot.
# Retention: the newest 8 boots of boot*/audio-b* files; nothing is written while the ESP has less than 128 MiB free
# or when df gives no number (fail closed).
# The dgpu section reads cached sysfs attributes and node metadata only (PLAN invariant 12) and sends only the
# read-only socket commands hello, status and get-mode. GB_BOOTLOG_* overrides exist for the test harness only.
P=EFI-SYSTEM
# shellcheck disable=SC2013  # /proc/cmdline is one line of blank-separated tokens: words are wanted
for a in $(cat /proc/cmdline); do case $a in gb.logpart=*) P=${a#gb.logpart=};; esac; done
D=/dev/block/by-name/$P; M=${GB_BOOTLOG_MNT:-/mnt/gb_esp}; L=${GB_BOOTLOG_LOCK:-/dev/gb_esp.lock}
T=${GB_BOOTLOG_TMP:-/dev/gb_bootlog.txt}; G=/data/vendor/gb; N=; OWN=; KEEP=8; FLOOR_KB=131072
mkdir -p "$M"
#@@functions-begin
busy() {    # 0 = a steal is in progress: a $L.stale.<pid> directory of another live pid (INTERFACES 5.4 r3)
  for s in "$L".stale.*; do
    p=${s##*.}; case $p in ''|*[!0-9]*) continue;; esac
    [ "$p" != $$ ] && [ -d "$s" ] && [ -d "/proc/$p" ] && return 0
  done
  return 1
}
steal() {   # $1 = owner line seen, $2 = inode seen (gb_nouveau.sh steal_lock, INTERFACES 5.4 r3)
  s=$L.stale.$$; rm -rf "$s" 2>/dev/null
  mv -T "$L" "$s" 2>/dev/null || return 1
  o2=; [ -r "$s/owner" ] && IFS= read -r o2 < "$s/owner"
  if [ "$(stat -c %i "$s" 2>/dev/null)" = "$2" ] && [ "$o2" = "$1" ]; then rm -f "$s/owner"; rmdir "$s" 2>/dev/null; return 0; fi
  n=0           # not the stale lock: put it back, never over an entry in the way; one without an owner goes.
  # (wave 3, wave 2b review L1) retried every 0.05 s for up to 5 s, not once per poll: newcomers no longer mkdir
  # while our .stale.<pid> exists, and a releaser waits 7 polls (unlock), longer than this budget
  while :; do
    if [ ! -e "$L" ] && mv -T "$s" "$L" 2>/dev/null; then return 1; fi
    if [ ! -e "$s/owner" ] && rmdir "$s" 2>/dev/null; then return 1; fi
    [ $n -ge 100 ] && break
    sleep 0.05; n=$((n+1))
  done
  echo "gb_bootlog: ESP lock changed while stealing it, left as ${s##*/}" > /dev/kmsg
  return 1
}
lock() {    # 1 s polls; stale = dead or non-numeric owner, our own pid (a lock we do not hold), or 5 polls without an
  w=0; miss=0; bo=0; OWN=   # owner file; after 600 s: see below
  while :; do
    ol=         # (wave 3, wave 2b review L3) the 600 s rule below judges only a holder read in this poll
    if [ $bo -lt 5 ] && busy; then
      bo=$((bo+1))   # (wave 3, L1) a steal is in progress: no mkdir at all (our directory would block its put-back)
    elif mkdir "$L" 2>/dev/null; then
      if [ $bo -lt 5 ] && busy; then
        rmdir "$L" 2>/dev/null; bo=$((bo+1))   # a steal started meanwhile: back off (rmdir only removes our empty one)
      else
        OWN="$$ gb_bootlog $(cut -d. -f1 /proc/uptime)"
        if (set -C; echo "$OWN" > "$L/owner") 2>/dev/null; then return 0; fi   # exclusive: never another's owner
        OWN=
      fi
    else
      bo=0      # (wave 3, L1) only consecutive back-off polls count: a poll that finds the lock held resets them
      i=$(stat -c %i "$L" 2>/dev/null); [ -r "$L/owner" ] && IFS= read -r ol < "$L/owner"; o=${ol%% *}; st=0
      # (wave 3, L2) our own pid while we hold nothing: a put-back that landed after our release gave up
      if [ -n "$ol" ]; then miss=0; case $o in ''|*[!0-9]*) st=1;; "$$") st=1;; *) [ -d "/proc/$o" ] || st=1;; esac
      else miss=$((miss+1)); [ $miss -ge 5 ] && st=1; fi
      if [ $st = 1 ] && [ -n "$i" ]; then miss=0; steal "$ol" "$i" && continue; fi
    fi
    if [ $w -ge 600 ]; then  # another log writer: proceed without the lock; a dGPU window or an unknown holder: skip
      n=${ol#* }; n=${n%% *}
      case $n in gb_slotsync|gb_audiodiag|gb_bootlog)
        echo "gb_bootlog: ESP lock held over 600 s ($ol), proceeding without it" > /dev/kmsg; return 0;;
      esac
      echo "gb_bootlog: ESP lock held over 600 s ($ol), not a log writer: t=$t not written" > /dev/kmsg; return 1
    fi
    sleep 1; w=$((w+1))
  done
}
unlock() {  # only our own lock (a lock stolen from us belongs to someone else now); waits while a steal is in progress
  [ -n "$OWN" ] || return 0   # (wave 3, wave 2b review L2: 7 polls, longer than a stealer's 5 s put-back budget)
  n=0
  while :; do
    ol=; [ -r "$L/owner" ] && IFS= read -r ol < "$L/owner"
    if [ "$ol" = "$OWN" ]; then rm -f "$L/owner"; rmdir "$L" 2>/dev/null; break; fi
    if [ $n -ge 7 ] || ! busy; then echo "gb_bootlog: ESP lock not ours at release ($ol), left alone" > /dev/kmsg; break; fi
    sleep 1; n=$((n+1))
  done
  OWN=
}
prune() {   # once per boot, right after N is known
  for f in "$M"/gblog/boot*-t*.txt "$M"/gblog/audio-b*; do
    [ -f "$f" ] || continue
    b=${f##*/}; b=${b#boot}; b=${b#audio-b}; b=${b%%[!0-9]*}
    case $b in ''|*[!0-9]*) continue;; esac
    [ "$b" -le $((N-KEEP)) ] && rm -f "$f"
  done
}
rdf() { v=-; [ -r "$1" ] && v=$(head -c 600 "$1"); echo "$v"; }
# (wave 5) summary helpers. nmatch: grep -c that prints 0 for a missing file too. kts: the kernel timestamps of a dmesg
# dump. probe: can bionic load and link a binary (C-1, C-2: one library that cannot link stops a HAL)? It runs the
# binary's version/usage option, which prints and exits before doing anything else (wpa_supplicant -v, hostapd -v,
# lcam-probe -h): "ok" when an output line starts with $3, else FAILED with the linker's line or the first line.
nmatch() { v=$(grep -c "$@" 2>/dev/null); echo "${v:-0}"; }
kts() { sed -n 's/^[^[]*\[ *\([0-9][0-9]*\.[0-9][0-9]*\)\].*/\1/p' "$1" 2>/dev/null; }
probe() {
  [ -x "$r$1" ] || { echo absent; return 0; }
  v=$(timeout 5 "$r$1" "$2" 2>&1 < /dev/null); e=$?
  if printf '%s\n' "$v" | grep -q -F 'CANNOT LINK'; then
    echo "FAILED: $(printf '%s\n' "$v" | grep -m1 -F 'CANNOT LINK' | cut -c1-200)"
  elif printf '%s\n' "$v" | grep -q "^$3"; then echo ok
  else echo "FAILED (exit $e): $(printf '%s\n' "$v" | head -n 1 | cut -c1-200)"; fi
}
# (wave 6, OTA gate F2) gbinit's verdict for this boot from /odm/etc/gb/ota_state (ota-gating DESIGN.md 2.2), then
# what followed from it: the gate property in /odm/etc/build.prop, /odm/vnni (the one "skip" root) and the notifier
# service. ostate: the last value of one key, printable characters only, at most 120 (as gb_otagate.sh reads it)
ostate() { sed -n "s/^$1=//p" "$r/odm/etc/gb/ota_state" 2>/dev/null | tail -n 1 | tr -cd ' -~' | cut -c1-120; }
otagate() {
  [ -f "$r/odm/etc/gb/ota_state" ] || { echo 'no state file (an image without the OTA gate)'; return 0; }
  if [ -d "$r/odm/vnni" ]; then ov=present; else ov=absent; fi
  echo "state=$(ostate state) token=$(ostate token) prop=$(getprop ro.vendor.gb.ota_gate) vnni=$ov notifier=$(getprop init.svc.gb_otagate) booted=$(ostate booted) validated=$(ostate validated)"
}
# (wave 3, item 8) one-screen triage, tied to the DEVICE-STATUS checklist. Read-only and bounded: dmesg and logcat -d
# (dump and exit, never a streaming logcat: [W3R-14]) once each into /dev, then fixed greps. It prints no serial
# numbers, SSIDs or account names (input device names only for the Surface Aggregator bus 0019 and, in the posture
# line, for the internal Surface, button, lid and IPTS devices; no Uniq/Phys lines; network interface names but no
# addresses; only named getprop properties). Section list idea: gbOS86 hwdetect.sh (names only); code ours.
# (wave 5) Besides the dumps it starts only dumpsys device_state and dumpsys input (read-only) and the three probes.
# GB_BOOTLOG_ROOT: test harness only (a fake /proc, /sys, /odm, /vendor).
summary() {
  r=${GB_BOOTLOG_ROOT:-}
  echo '== summary (triage; the full logs follow)'
  dmesg > "$T.dm" 2>/dev/null; timeout 20 logcat -d -b all > "$T.lc" 2>/dev/null     # (dmesg never blocks)
  # (wave 5, A-L1) a permissive boot floods the 1 MiB kernel ring with avc lines (thousands from getprop alone), so the
  # boot's first lines are gone within minutes. The first snapshot keeps a copy without avc lines ($T.dm0, in /dev
  # until the last snapshot); every kernel-log line below comes from K = that copy + this dump's lines it does not hold.
  [ -s "$T.dm0" ] || grep -v -F 'avc:' "$T.dm" > "$T.dm0" 2>/dev/null
  K=$T.dk
  { cat "$T.dm0"; grep -v -F 'avc:' "$T.dm" | awk -v f="$T.dm0" 'FILENAME == f { s[$0] = 1; next } !($0 in s)' "$T.dm0" -; } > "$K" 2>/dev/null
  k1=$(kts "$T.dm" | head -n 1); k2=$(kts "$T.dm0" | head -n 1); k3=$(kts "$T.dm0" | tail -n 1)
  echo "kernel log: this dump from ${k1:-?} s ($(nmatch -F 'avc:' "$T.dm") avc lines), the first snapshot's copy without avc lines ${k2:-?}-${k3:-?} s: the kernel lines below come from both"
  # item 1: 8250 / Surface Aggregator (base keyboard/touchpad, batteries, charger, detach)
  echo "serial 8250: $(grep -m1 -F 'Serial: 8250/16550 driver' "$K" || echo 'no 8250 banner')"
  echo "serial errors: dw-apb-uart -22: $(grep -c -E 'dw-apb-uart.*-22' "$K"), failed to setup IRQ: $(grep -F 'failed to setup IRQ' "$K" | grep -c -v -E -- '-517|EPROBE_DEFER')"
  b=; for x in "$r"/sys/bus/serial/drivers/surface_serial_hub/*; do
    case ${x##*/} in module|'*') continue;; esac; [ -L "$x" ] && b="$b ${x##*/}"
  done
  echo "serial surface_serial_hub bound:${b:- none}"
  echo '-- /proc/tty/driver/serial'; timeout 5 cat "$r/proc/tty/driver/serial" 2>&1 | head -n 40
  echo '-- serial bus devices'; ls -l "$r/sys/bus/serial/devices" 2>&1; ls "$r/sys/bus/serdev/devices" 2>/dev/null
  echo "inputs (Surface Aggregator HID, bus 0019): $(awk '/^I:/ { b = ($2 == "Bus=0019") } /^N:/ && b { sub(/^N: Name=/, ""); printf "%s; ", $0 }' "$r/proc/bus/input/devices" 2>/dev/null)"
  # (wave 5, A-L1) -517 = a probe deferred and retried later, not an error: on the SB3 surface_hotplug's first probe
  # (in gbinit, before lapis's pinctrl-icelake) logs it; the Surface Aggregator's deferral is silent
  echo "deferred probes (-517, retried later; expected on the SB3): $(grep -c -E -- '-517([^0-9]|$)|EPROBE_DEFER' "$K") line(s): $(grep -E -- '-517([^0-9]|$)|EPROBE_DEFER' "$K" | sed -e 's/^[^]]*\] //' -e 's/: .*//' | sort -u | head -n 6 | tr '\n' ';')"
  # (wave 5, A-H1) power button and volume keys: soc_button_array on MSHW0040 needs lapis's INT3455 pinctrl, which
  # ueventd loads after gbinit, and gb_hw binds it again at boot. Two gpio-keys inputs = power + volume work
  d=$r/sys/bus/platform/devices/MSHW0040:00; x=absent
  if [ -e "$d" ]; then x=$(readlink "$d/driver" 2>/dev/null); if [ -n "$x" ]; then x="bound to ${x##*/}"; else x='NOT bound'; fi; fi
  if [ -e "$r/sys/bus/platform/drivers/icelake-pinctrl/INT3455:00" ]; then b=bound; else b='not bound'; fi
  echo "buttons: MSHW0040 $x; INT3455 pinctrl $b; gpio-keys inputs: $(nmatch -F 'N: Name="gpio-keys"' "$r/proc/bus/input/devices"); $(grep -F 'gb_hw: buttons:' "$K" | tail -n 1 | sed 's/^[^]]*\] //')"
  # (wave 5, A-M1) posture: lapis's device-state policy disables built-in input in CLOSED; on the kernel side lapis's
  # touchpad-inhibitor and gb_power's studio mode set /sys/class/input/*/inhibited. Internal device names only
  x=$(timeout 5 dumpsys device_state 2>/dev/null | sed -n -E "s/^ *(mCommittedState|mBaseState|mOverrideState)=.*name='([^']*)'.*/\1=\2/p; s/^ *(mCommittedState|mBaseState|mOverrideState)=Optional\.empty.*/\1=none/p" | tr '\n' ' ')
  timeout 10 dumpsys input > "$T.di" 2>/dev/null
  b='n/a (no input service)'
  if grep -q -F 'Event Hub State' "$T.di"; then
    b=$(awk '/^ *(Device )?-?[0-9]+: / { n = $0; sub(/^ *(Device )?-?[0-9]+: /, "", n); next }
      /^ *Enabled: false/ && n != "" { if (n ~ /^(Microsoft Surface|gpio-keys|Lid Switch|Power Button|IPTS)/) s[n] = 1; else o[n] = 1; n = "" }
      END { for (k in s) printf "%s;", k; c = 0; for (k in o) c++; if (c) printf "+%d other;", c }' "$T.di")
  fi
  l=
  for d in "$r"/sys/class/input/input*/inhibited; do
    [ "$(cat "$d" 2>/dev/null)" = 1 ] || continue
    n=$(cat "${d%/inhibited}/name" 2>/dev/null)
    case $n in 'Microsoft Surface'*|gpio-keys|'Lid Switch'|'Power Button'|IPTS*) l="$l$n;";; *) l="$l(another device);";; esac
  done
  echo "posture: ${x:-device_state n/a }| disabled by Android: ${b:-none} | inhibited by the kernel: ${l:-none}"
  # the opt-in recovery (gb\gb.cfg GB_PROP_1=vendor.gb.posture=open with gb.overlay=1, or persist.vendor.gb.posture):
  # gb_hw's last override action, not the device_state/input report its 'posture' mode logs after it (same prefix)
  # nor its diag report ('gb_hw: diag: posture:')
  echo "posture override: vendor.gb.posture=$(getprop vendor.gb.posture) persist.vendor.gb.posture=$(getprop persist.vendor.gb.posture); $(grep -F 'gb_hw: posture: ' "$K" | grep -v -E 'gb_hw: posture: (device_state|input) ' | tail -n 1 | sed 's/^[^]]*\] //')"
  # item 2: the DRAS card id
  echo "audio cards: $(grep -F ']: ' "$r/proc/asound/cards" 2>/dev/null | tr -s ' ' | tr '\n' ';')"
  echo "audio DRAS: logcat lines naming sofhdadsp: $(grep -c -F sofhdadsp "$T.lc")"
  grep -i -E 'dras|soundcard' "$T.lc" | grep -F -e sofhdadsp -e sof-hda-dsp | tail -n 3
  # (wave 5, C-4) dsp_driver is a built-in, read-only kernel parameter: gb.cfg cannot change it, the boot menu can (=1
  # for one boot: the legacy HDA driver, FLASHING.md "No sound"); gb_audio's last lines say which path it took
  echo "audio dsp_driver=$(cat "$r/sys/module/snd_intel_dspcfg/parameters/dsp_driver" 2>/dev/null || echo -) (3 SOF, 1 legacy HDA); gb_audio: $(grep -F 'GB_AUDIO: ' "$K" | grep -v -F "GB_AUDIO: mixer '" | tail -n 2 | sed 's/^[^]]*\] //' | tr '\n' ' ')"
  # firmware
  echo '-- firmware load failures (dmesg)'; grep -i -E 'Direct firmware load.*failed|Failed to load' "$K" | tail -n 12
  echo "firmware i915 DMC: $(grep -m1 -i -E 'i915.*DMC' "$K" || echo 'no DMC line')"
  echo "swaps: $(tail -n +2 "$r/proc/swaps" 2>/dev/null | tr -s ' ' | tr '\n' ';')"
  n=0; l=
  for d in "$r"/sys/bus/pci/devices/*; do
    [ -e "$d/vendor" ] || continue; [ -e "$d/driver" ] && continue
    n=$((n+1)); l="$l ${d##*/}=$(cat "$d/vendor"):$(cat "$d/device")"
  done
  echo "pci functions without a driver: $n$l"
  # item 3: the #UD AVX-VNNI emulator counter (the k15 sysfs node, WAVE3_PLAN contract 2.2)
  echo '-- gb_vnni'; timeout 5 cat "$r/sys/kernel/gb_vnni/summary" 2>&1 | head -n 17
  # item 10: i915 white veil (the default-off fix is set only through gb.cfg GB_MODOPTS_i915). (wave 5, B-1) drmhwc logs
  # tag and text apart: threadtime gives "V drmhwc  : Missing optional ...", never "drmhwc: Missing"; B-2: the
  # composer's own switches that select that fallback
  echo "i915 white veil: drmhwc LOG_GAMMA_LUT lines: $(grep -c -E 'drmhwc[^:]*: Missing optional LOG_GAMMA_LUT property' "$T.lc"), gb_ignore_user_gamma=$(cat "$r/sys/module/i915/parameters/gb_ignore_user_gamma" 2>/dev/null || echo -), hwc: use_log_gamma_lut=$(getprop vendor.hwc.use_log_gamma_lut) persistent_hdr=$(getprop vendor.hwc.drm.persistent_hdr) force_color_mode=$(getprop vendor.hwc.drm.force_color_mode)"
  echo "i915 modules.load: $(grep -E '^#?i915\.ko' "$r/odm/lib/modules/modules.load" 2>/dev/null); gbinit: $(grep -F i915.ko "$r/odm/gbinit_modules.log" 2>/dev/null | tr '\n' ' ')"
  # (wave 5, B-3) panel self refresh / pipe underruns (flicker, ghost images): GB_MODOPTS_i915=enable_psr=0 enable_fbc=0
  echo "i915 PSR/underrun lines: $(grep -c -E 'i915 .*(FIFO underrun|Selective fetch area calculation failed|PSR.*(error|fail))' "$K")"
  # item 18: HWUI on a one-queue-per-family Vulkan backend (literal of WAVE3 2.4; gbOS86 hook.sh:489-492)
  echo "HWUI Vulkan queueCount abort lines: $(grep -c -F 'queueFamilyProperties.queueCount < kRequestedQueueCount' "$T.lc")"
  # item 13: gb_slotsync HOLD (WAVE3 2.6)
  echo "gb_slotsync HOLD: $(grep -F 'gb_slotsync: HOLD slot ' "$K" | tail -n 2 | tr '\n' ' ')"
  # (wave 6, OTA-F2) the OTA gate: validated = the build this image was made for; unvalidated = another build (after a
  # Google OTA) or gb.otagate=force, the VNNI libz patch skipped; off = gb.otagate=0. Build fingerprints only
  echo "ota gate: $(otagate)"
  # item 11: apgetty off natively; item 4: fs-verity on /data
  echo "apgetty: desktop.apgetty.enable=$(getprop desktop.apgetty.enable) init.svc.apgetty=$(getprop init.svc.apgetty)"
  echo "fs-verity: ext4 feature $([ -e "$r/sys/fs/ext4/features/verity" ] && echo present || echo absent); $(grep -E 'Enabling ext4 verity|FS_STAT_ENABLE_VERITY_FAILED|Failed to run tune2fs' "$K" | tail -n 2 | tr '\n' ' ')"
  # (wave 5, C-3) Wi-Fi, Bluetooth, cameras and linker failures (C-1, C-2 otherwise show only deep in logcat). Interface
  # names, firmware and HAL lines only: no SSID, MAC or device name. The kernel lines are printed as they are, so the
  # hwtest analyzer finds them even after the ring has wrapped
  l=; for d in "$r"/sys/class/net/wlan*; do [ -e "$d" ] && l="$l${d##*/} "; done
  echo "wifi: ${l:-no wlan interface }| wpa_supplicant=$(getprop init.svc.wpa_supplicant) wifi_hal=$(getprop init.svc.vendor.wifi_hal_legacy) | link: wpa_supplicant $(probe /vendor/bin/hw/wpa_supplicant -v 'wpa_supplicant v'); hostapd $(probe /vendor/bin/hw/hostapd -v 'hostapd v')"
  grep -E 'iwlwifi .*(loaded firmware version|no suitable firmware found|Failed to start RT ucode|Microcode SW error)' "$K" | tail -n 2
  l=; for d in "$r"/sys/class/bluetooth/*; do [ -e "$d" ] && l="$l${d##*/} "; done
  echo "bluetooth: ${l:-no controller }| hal=$(getprop init.svc.vendor.bluetooth-desktop)"
  grep -E 'Bluetooth: hci[0-9]+: (Found device firmware|Firmware loaded in|Firmware already loaded|Device booted in|Failed to load Intel firmware|Failed to send firmware)' "$K" | tail -n 3
  echo "camera: provider=$(getprop init.svc.vendor.camera-provider-2-7-legacy-wrapper), exits seen: $(grep -c -E 'init: Service .vendor\.camera-provider-2-7-legacy-wrapper. \(pid [0-9]+\) (exited|received signal|killed)' "$K"), HAL load failures: $(grep -c -F 'Could not load camera HAL module' "$T.lc") | lcam-probe link: $(probe /odm/bin/lcam-probe -h 'usage: ')"
  grep -E 'intel-ipu6 .*(hardware version|Connected [0-9]+ camera|firmware|authenticat|error|fail)|Found supported sensor' "$K" | tail -n 3
  # (a probe of a binary that cannot link adds one such logcat line per snapshot: later counts include them)
  echo "linker: CANNOT LINK lines: $(grep -c -F 'CANNOT LINK EXECUTABLE' "$T.lc") logcat, $(grep -c -F 'CANNOT LINK EXECUTABLE' "$K") kernel; dlopen failed lines outside /data: $(grep -F 'dlopen failed' "$T.lc" | grep -c -v -F '/data/')"
  echo '-- linker failures (most frequent first)'
  grep -h -E 'CANNOT LINK EXECUTABLE|dlopen failed' "$T.lc" "$K" | grep -v -F '/data/' | sed -e 's/^.*\(CANNOT LINK EXECUTABLE\)/\1/' -e 's/^.*\(dlopen failed\)/\1/' | cut -c1-240 | sort | uniq -c | sort -rn | head -n 4
  # item 12: the ESP bring-up log (absent = the feature was off this boot); stays the last block (analyze_gblog)
  echo '-- ESP bring-up (/odm/gbinit_bringup.log)'
  if [ -f "$r/odm/gbinit_bringup.log" ]; then tail -n 40 "$r/odm/gbinit_bringup.log"; else echo 'absent (gb.overlay=1 not set)'; fi
  rm -f "$T.dm" "$T.lc" "$K" "$T.di"
}
snapshot() {
  summary
  echo "== uptime $(cat /proc/uptime) boot_completed=$(getprop sys.boot_completed) hw=$(cat /odm/etc/gb_hw)"
  echo '== cmdline'; cat /proc/cmdline
  echo '== gbinit module loading'; cat /odm/gbinit_modules.log
  echo '== dmi'; cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name
  echo '== block'; ls -l /sys/block; ls -l /dev/block/by-name
  echo '== input devices'; cat /proc/bus/input/devices
  echo '== iio'; for i in /sys/bus/iio/devices/*/name; do echo "$i $(cat "$i")"; done
  echo '== asound'; cat /proc/asound/cards /proc/asound/pcm; timeout 10 tinymix -D 0 2>&1 | head -n 80
  echo '== gpu'; timeout 20 dumpsys gpu 2>&1 | head -n 60; timeout 20 cmd gpu vkjson 2>&1 | head -c 4000
  echo '== modules'; cat /proc/modules
  echo '== drm/input/power'; ls -l /dev/dri /dev/input; for u in /sys/class/power_supply/*/uevent; do echo "$u"; cat "$u"; done
  echo '== pci'; for d in /sys/bus/pci/devices/*; do echo "$d $(cat "$d/vendor"):$(cat "$d/device") driver=$(basename "$(readlink "$d/driver")" 2>/dev/null)"; done
  echo '== getprop'; getprop
  echo '== dgpu'
  for d in /sys/bus/pci/devices/*; do
    v=$(cat "$d/vendor")
    [ "$v" = 0x10de ] || [ "${d##*/}" = 0000:00:1d.4 ] || continue
    # shellcheck disable=SC2012  # iommu_group/devices holds PCI addresses (dddd:bb:dd.f) only
    echo "${d##*/} $v:$(cat "$d/device") class=$(cat "$d/class") driver=$(basename "$(readlink "$d/driver")" 2>/dev/null)" \
         "control=$(cat "$d/power/control") runtime=$(cat "$d/power/runtime_status") state=$(cat "$d/power_state")" \
         "active_ms=$(cat "$d/power/runtime_active_time") suspended_ms=$(cat "$d/power/runtime_suspended_time")" \
         "iommu_group=$(ls "$d/iommu_group/devices" 2>/dev/null | tr '\n' ' ')"
  done
  echo "nouveau refcnt=$(cat /sys/module/nouveau/refcnt 2>/dev/null || echo -)"
  for r in /sys/class/drm/card[0-9]* /sys/class/drm/renderD*; do
    [ -e "$r/device/vendor" ] && echo "${r##*/} -> $(cat "$r/device/vendor") $(basename "$(readlink "$r/device/driver")") $(stat -c '%a %U:%G' "/dev/dri/${r##*/}" 2>/dev/null)"
  done
  echo '-- gb_dgpu_check'; timeout 30 sh /vendor/bin/gb_dgpu_check.sh 2>&1 | head -n 150
  echo '-- properties'; getprop | grep -E 'vendor\.gb\.|persist\.vendor\.gb\.|init\.svc\.gb_|ro\.gfx\.driver|ro\.hardware\.(vulkan|egl)|hwui'
  echo '-- gb_power (read-only: hello, status, get-mode)'
  printf 'hello\nstatus\nget-mode\n' | timeout 5 toybox nc -q 1 -U /dev/socket/gb_power 2>&1 | head -c 8192; echo
  echo '-- settings'
  for k in all_apps production_opt_in_apps production_opt_out_apps production_allowlist production_denylist; do
    echo "updatable_driver_$k=$(timeout 10 settings get global updatable_driver_$k 2>&1 | head -c 600)"
  done
  for p in gb.sb3.gpu gb.sb3.gfxdriver; do echo "pkg $p:"; timeout 10 dumpsys package $p 2>&1 | grep -E 'codePath|pkgFlags|privateFlags|versionCode' | head -n 8; done
  echo '-- state'; ls -la /dev/gb_dgpu "$L" $G/notify 2>&1
  for s in nouveau.clean_boots gfx_unclean dgpu_unclean selftest/result; do echo "$s: $(rdf $G/$s)"; done
  for s in gfx_session dgpu_session nouveau.loading selftest/running; do
    if [ -e $G/$s ]; then echo "$s: present $(rdf $G/$s)"; else echo "$s: absent"; fi
  done
  grep -E ' /odm/dgpu | /odm/firmware/nvidia | /mnt/gb_' /proc/mounts
  dmesg | grep -iE 'nouveau|gsp|pciehp|_OSC|sdtx|surface_hotplug|acpiphp|firmware|gb_nouveau|gb_power|gb_notify|gb_slotsync|gb_audiodiag|runtime PM through ACPI _PR3|no longer present on the bus|runtime suspend failed|failed to evict VRAM|Do not have permissions to set'
  logcat -d -s gb_notify gb_gpu_ui_setup gb_gpu_compute gb_gfxdriver GbGpu 2>/dev/null | tail -n 60
  getenforce
  for k in dgpu load bind test selftest; do
    # shellcheck disable=SC2012  # newest of the fixed report names <prefix>-<N>.txt (INTERFACES 5.1)
    f=$(ls -t $G/report/$k-*.txt 2>/dev/null | head -n 1); [ -n "$f" ] && { echo "== $f"; cat "$f"; }
  done
  echo '== dmesg-live (tail)'; tail -c 262144 $G/report/dmesg-live.txt 2>/dev/null
  echo '== dmesg-live.prev (tail)'; tail -c 262144 $G/report/dmesg-live.prev.txt 2>/dev/null
  echo '== gb_nouveau.log (tail)'; tail -c 32768 $G/report/gb_nouveau.log.1 2>/dev/null; tail -c 65536 $G/report/gb_nouveau.log 2>/dev/null
  echo '== dmesg'; dmesg
  echo '== logcat'; logcat -d -b all -v threadtime | tail -n 6000
  echo '== dumpsys SurfaceFlinger'; timeout 20 dumpsys SurfaceFlinger | head -n 120
  echo '== dumpsys input'; timeout 20 dumpsys input | head -n 200
}
#@@functions-end
for t in 40 90 180 420 900; do
  while [ "$(cut -d. -f1 /proc/uptime)" -lt $t ]; do sleep 5; done
  [ $t = 40 ] && setprop vendor.gb.dgpu_report 1
  snapshot > "$T" 2>&1
  lock || { rm -f "$T"; continue; }
  if ! mount -t vfat -o rw,flush,nosuid,nodev,noexec "$D" "$M"; then
    unlock; rm -f "$T"; echo "gb_bootlog: cannot mount $D, t=$t skipped" > /dev/kmsg; continue
  fi
  mkdir -p "$M/gblog"
  if [ -z "$N" ]; then
    c=$(tr -d '\r' < "$M/gblog/bootcount" 2>/dev/null); case $c in ''|*[!0-9]*) c=0;; esac   # a CRLF edit keeps N
    N=$((c+1)); echo $N > "$M/gblog/bootcount"; prune
  fi
  # free KiB = the third field from the end of df's last line (a wrapped long device name moves the others); no
  # number = fail closed: nothing is written
  fr=; f1=; f2=; for x in $(df -k "$M" 2>/dev/null | tail -n 1); do fr=$f2; f2=$f1; f1=$x; done
  case $fr in ''|*[!0-9]*) fr=;; esac
  if [ -z "$fr" ]; then
    echo "gb_bootlog: cannot read the ESP free space, t=$t not written" > /dev/kmsg
  elif [ "$fr" -lt $FLOOR_KB ]; then
    echo "gb_bootlog: ESP free ${fr} KiB below the 128 MiB floor, t=$t not written" > /dev/kmsg
  else
    cp "$T" "$M/gblog/boot$N-t$t.txt"
  fi
  sync; umount "$M"; unlock; rm -f "$T"
done
rm -f "$T.dm0"      # (wave 5) the first snapshot's kernel-log copy (summary)
"""
assert b"\r" not in GB_BOOTLOG_SH

GB_RC_NATIVE = (
         b"service gb_keymint /vendor/bin/hw/android.hardware.security.keymint-service\n"
         b"    class early_hal\n"
         b"    user nobody\n"
         b"    group drmrpc\n"
         b"    seclabel u:r:hal_keymint_default:s0\n"
         b"    stdio_to_kmsg\n"
         b"\n"
         b"service gb_modules /system/bin/sh /odm/gb_modules.sh\n"
         b"    user root\n"
         b"    group root\n"
         b"    seclabel u:r:shell:s0\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"service gb_bootok /system/bin/bootctl mark-boot-successful\n"
         b"    user root\n"
         b"    group root system\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"service gb_bootlog /system/bin/sh /odm/gb_bootlog.sh\n"
         b"    user root\n"
         b"    group root system log readproc shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         # Provisioned (so the launcher is unrestricted) but, unlike the VM, normal screen timeout / sleep / Bluetooth.
         b'service gb_provision /system/bin/sh -c "/system/bin/settings put global device_provisioned 1; /system/bin/settings put secure user_setup_complete 1; /system/bin/settings put global hide_error_dialogs 1; echo GB_PROVISION_DONE"\n'
         b"    user system\n"
         b"    group system shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"service gb_ui /system/bin/sh /odm/gb_ui.sh\n"
         b"    user root\n"
         b"    group root system shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"service gb_dexopt /system/bin/sh /odm/gb_dexopt.sh\n"
         b"    user root\n"
         b"    group root system shell\n"
         b"    seclabel u:r:shell:s0\n"
         b"    stdio_to_kmsg\n"
         b"    oneshot\n"
         b"    disabled\n"
         b"\n"
         b"on early-init\n"
         b"    setprop trusty.security_vm.enabled 0\n"
         b"    setprop trusty.security_vm.nonsecure_vm_ready 0\n"
         b"    mkdir /odm/vlower 0755\n"
         b"    mount none /vendor /odm/vlower bind\n"
         b"    mkdir /odm/vwk 0755\n"
         b"    mount overlay gbkm /vendor lowerdir=/odm/vlower,upperdir=/odm/vup,workdir=/odm/vwk\n"
         b"    restorecon /odm/etc/fstab.android-desktop\n"
         b"    mount none /odm/etc/fstab.android-desktop /vendor/etc/fstab.android-desktop bind\n"
         b"    restorecon /vendor/etc/fstab.android-desktop\n"
         b"    restorecon /odm/etc/se_manifest.xml\n"
         b"    mount none /odm/etc/se_manifest.xml /system_ext/etc/vintf/manifest.xml bind\n"
         b"    restorecon /system_ext/etc/vintf/manifest.xml\n"
         # Surface Book 3 drivers (i915, Surface Aggregator stack, Wi-Fi, sensors, audio...) before the composer starts.
         b"    exec_start gb_modules\n"
         b"    restorecon /odm/empty_vintf.xml\n"
         b"    mount none /odm/empty_vintf.xml /apex/com.android.hardware.keymint.strongbox.desktop/etc/vintf/android.hardware.security.keymint-service.strongbox.desktop.xml bind\n"
         b"    mount none /odm/empty_vintf.xml /vendor/etc/vintf/manifest/android.hardware.weaver-service.android-desktop.xml bind\n"
         b"    mount none /odm/empty_vintf.xml /vendor/etc/vintf/manifest/android.hardware.gatekeeper-service.trusty.xml bind\n"
         b"\n"
         b"on init\n"
         b"    setprop trusty.security_vm.enabled 0\n"
         b"    setprop persist.sys.disable_rescue true\n"
         b"    setprop persist.device_config.configuration.disable_rescue_party true\n"
         b"    setprop persist.device_config.configuration.disable_rescue_party_factory_reset true\n"
         b"    setprop keystore.module_hash.sent true\n"
         b"    setprop ro.setupwizard.mode DISABLED\n"
         b"    setprop persist.sys.setupwizard DISABLED\n"
         # apgetty off natively (WAVE3_PLAN item 11, [W3R-21]; idea gbOS86 hook.sh:611-612). /system_ext/etc/init/
         # apgetty.rc (read from lapis.bin, both slots): orange boot state -> early-init `setprop
         # desktop.apgetty.enable 1`; started by `on property:desktop.apgetty.enable=1` AND by `on init &&
         # property:desktop.apgetty.enable=1`, which init runs before this file's `on init` (system_ext rc files are
         # parsed first). So: 0 here stops the property start, and `stop apgetty` right here stops the init-time
         # start ~0.1 s after it (W6 prep: lapis's init enables property triggers only after `on boot`, builtin
         # enable_property_trigger, so the init.svc triggers below never fired before `on boot` stopped it: VM-1 boot
         # log 10-29, apgetty ran 21.2-41.1 s). There is no console UART natively (the SAM UART belongs to serdev), so
         # it would only crash-loop.
         b"    setprop desktop.apgetty.enable 0\n"
         b"    stop apgetty\n"
         b"\n"
         b"on property:init.svc.apgetty=running\n"
         b"    stop apgetty\n"
         b"\n"
         b"on property:init.svc.apgetty=restarting\n"
         b"    stop apgetty\n"
         b"\n"
         b"on post-fs\n"
         b"    setprop trusty.security_vm.enabled 0\n"
         b"    mount none /odm/gb_noop.sh /system/bin/preloads_copy.sh bind\n"
         b"    mount none /odm/gb_sleeper.sh /system/bin/timberslide bind\n"
         b"    mount none /odm/gb_sleeper.sh /system/bin/udsattestation bind\n"
         b"    mount none /odm/gb_fw_stub.sh /system/bin/google_desktop_firmware_updater bind\n"
         b"\n"
         b"on post-fs-data\n"
         b"    start gb_bootlog\n"
         b"    setprop persist.sys.disable_rescue true\n"
         b"    setprop persist.device_config.configuration.disable_rescue_party true\n"
         b"    setprop persist.sys.sf.disable_blurs 0\n"
         b"    exec_start gb_bootok\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"\n"
         b"on late-fs\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"\n"
         b"on boot\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"    stop apgetty\n"
         b"\n"
         b"on property:sys.boot_completed=1\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"    start gb_provision\n"
         b"    start gb_ui\n"
         b"    start gb_dexopt\n")
GB_RC_NATIVE = _with_kmsgdbg(GB_RC_NATIVE)      # stdio_to_kmsg works on the user build (see GB_KMSGDBG_SVC)

ODM_BUILD_PROP_NATIVE = (b"# GB native (Surface Book 3) overrides (loaded after /vendor/build.prop)\n"
                         b"ro.surface_flinger.supports_background_blur=1\n"
                         # hwc3-drm rejects every plane without a COLOR_PIPELINE property; this tree's i915 has none
                         b"vendor.hwc.drm.enable_color_pipeline=false\n"
                         b"ro.sf.lcd_density=320\n"
                         b"ro.surface_flinger.has_HDR_display=false\n"
                         b"ro.surface_flinger.has_wide_color_display=false\n"
                         b"ro.surface_flinger.use_color_management=false\n"
                         # first native boots: plain primary-plane commits; re-enable after i915 plane testing
                         b"vendor.hwc.drm.ctm=0\n"
                         b"ro.vendor.hwc.use_overlay_planes=false\n"
                         # (wave 5, B-2) white veil: lapis's vendor build.prop sets the Google xe panel's colour mode
                         # (force_color_mode=13 = DISPLAY_BT2020, persistent_hdr, use_log_gamma_lut). With
                         # enable_color_pipeline=false hwc3-drm clears GAMMA_LUT, but it bakes a BT.709->BT.2020 gamut
                         # matrix into the CRTC CTM and tags eDP BT2020_RGB: pale colours on the SB3's sRGB panel. These
                         # are the composer's compiled defaults: SDR, SurfaceFlinger's (sRGB) colour mode, CTM = SF's
                         # colour matrix only, so Night Light keeps working. Opt back for one boot with gb.cfg GB_PROP_<n>.
                         b"vendor.hwc.drm.force_color_mode=-1\n"
                         b"vendor.hwc.drm.persistent_hdr=false\n"
                         b"vendor.hwc.use_log_gamma_lut=false\n")

def cpio_newc(entries):
    out = bytearray(); ino = [0]
    def emit(path, mode, data):
        ino[0] += 1
        fsz = len(data) if data is not None else 0
        nsz = len(path) + 1
        nlink = 2 if data is None else 1
        out.extend(b'070701' + b''.join(b'%08X' % x for x in [ino[0], mode, 0, 0, nlink, 0, fsz, 0, 0, 0, 0, nsz, 0]))
        out.extend(path.encode()); out.append(0)
        while len(out) % 4: out.append(0)
        if data: out.extend(data)
        while len(out) % 4: out.append(0)
    for path, mode, data in entries: emit(path, mode, data)
    nsz = len(b'TRAILER!!!') + 1
    out.extend(b'070701' + b''.join(b'%08X' % x for x in [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, nsz, 0]))
    out.extend(b'TRAILER!!!'); out.append(0)
    while len(out) % 4: out.append(0)
    return bytes(out)

_entries = [
    ('gbinit', 0o100755, GBINIT),
    ('busybox', 0o100755, BUSYBOX),
    ('gbstage', 0o040755, None),
    ('gbstage/gfxlog.sh', 0o100755, GFXLOG),
    ('gbstage/gb_showhome.sh', 0o100755, SHOWHOME),
    ('gbstage/gb_console.sh', 0o100755, GB_CONSOLE_SH),
    ('gbstage/gb_noop.sh', 0o100755, b"#!/system/bin/sh\nexit 0\n"),
    ('gbstage/gb_sleeper.sh', 0o100755, GB_SLEEPER),
    ('gbstage/gb_fw_stub.sh', 0o100755, GB_FW_STUB),
    ('gbstage/gb_dexopt.sh', 0o100755, GB_DEXOPT_SH),
    ('gbstage/gb_cpus.sh', 0o100755, GB_CPUS_SH),
    ('gbstage/gb_ui.sh', 0o100755, GB_UI_SH),
] +([('gbstage/vkenum', 0o100755, VKENUM)] if VKENUM is not None else []) + [
    ('gbstage/etc', 0o040755, None),
    ('gbstage/etc/fstab.android-desktop', 0o100644, FSTAB_VERITY),    # W4: + fsverity, as natively (2.2(b))
    ('gbstage/etc/se_manifest.xml', 0o100644, SEMAN),
    ('gbstage/etc/init', 0o040755, None),
    ('gbstage/etc/init/gb.rc', 0o100644, GB_RC),
    ('gbstage/etc/selinux', 0o040755, None),
    ('gbstage/etc/selinux/odm_sepolicy.cil', 0o100644, ODMCIL),
    ('gbstage/etc/selinux/precompiled_sepolicy', 0o100644, ODMPRE),
    ('gbstage/lib', 0o040755, None),
    ('gbstage/lib/modules', 0o040755, None),
    # /vendor overlay upperdir (becomes /odm/vup after gbinit copies gbstage -> /odm):
    ('gbstage/vup', 0o040755, None),
    ('gbstage/vup/bin', 0o040755, None),
    ('gbstage/vup/bin/hw', 0o040755, None),
    ('gbstage/vup/bin/hw/android.hardware.security.keymint-service', 0o100755, KM_BIN),
    # Neutralize the Trusty secretkeeper HAL by overlaying its binary with a block-forever stub.
    ('gbstage/vup/bin/hw/android.hardware.security.secretkeeper.trusty', 0o100755, SK_STUB),
    # Trusty Gatekeeper: overlay its binary with the same block-forever stub. Its init .rc is parsed by init
    # BEFORE our /vendor overlay mounts (so a .rc whiteout is too late), so the service DOES start -- but with
    # the binary replaced it sleeps instead of SIGABRT-looping on the absent /dev/trusty-ipc-dev0. That stops
    # the 81x crash-storm that was blocking system_server's android.bg thread -> Watchdog kill after
    # boot_completed. Its VINTF is undeclared (gb.rc early-init) so LockSettings skips it (no hang).
    ('gbstage/vup/bin/hw/android.hardware.gatekeeper-service.trusty', 0o100755, SK_STUB),
    # touchpad-inhibitor (vendor, class core) is ChromeOS lid logic: with the lid closed it inhibits the internal
    # keyboard + touchpad via /sys/class/input/*/inhibited. The VM has NO lid switch ("No lid switch device found.
    # Driver not loaded?"), so it shut off the internal AT keyboard (InputReader 'Enabled: false') and froze input.
    # Overlay it with the sleep stub so it never inhibits anything; gb_showhome.sh also clears every 'inhibited'.
    ('gbstage/vup/bin/hw/touchpad-inhibitor', 0o100755, SK_STUB),
    # No EC / snapshot service in the VM: the charge-control HAL restart-loops and storage_metrics_service waits
    # forever for ISnapshot (ctl.interface_start spam) -> idle sleepers.
    ('gbstage/vup/bin/hw/vendor.google.desktop.charge-control-hal', 0o100755, GB_SLEEPER),
    ('gbstage/vup/bin/storage_metrics_service', 0o100755, GB_SLEEPER),
    # No Bluetooth controller in the VM: com.google.android.bluetooth aborts in hci_backend_aidl every ~40 s
    # ("Bluetooth keeps stopping"). Mark the features unavailable (SystemConfig <unavailable-feature>; the feature is
    # also in the read-only /system features.xml) so BluetoothManagerService never starts the stack.
    ('gbstage/vup/etc', 0o040755, None),
    ('gbstage/vup/etc/permissions', 0o040755, None),
    ('gbstage/vup/etc/permissions/gb_no_bluetooth.xml', 0o100644,
     b'<?xml version="1.0" encoding="utf-8"?>\n<permissions>\n'
     b'    <unavailable-feature name="android.hardware.bluetooth" />\n'
     b'    <unavailable-feature name="android.hardware.bluetooth_le" />\n</permissions>\n'),
    # crossystem stub (see CROSSYSTEM_STUB): report a healthy vboot slot so the boot_control HAL can mark
    # the slot successful -> vold cp_needsCheckpoint()==false -> no 'Checkpoint commit failed' reboot loop.
    ('gbstage/vup/bin/crossystem', 0o100755, CROSSYSTEM_STUB),
    # Keyboard: lapis's /vendor/usr/idc/AT_Translated_Set_2_keyboard.idc marks the i8042 keyboard as the BUILT-IN
    # keyboard (keyboard.builtIn = 1). Android desktop disables built-in input by laptop posture (lid / hinge
    # sensors), and the VM has neither, so InputReader showed the keyboard 'Enabled: false' even with the kernel
    # not inhibiting it. Override the IDC so the VMware keyboard is an EXTERNAL keyboard, which posture logic never
    # disables (the external USB mouse stays enabled for the same reason).
    ('gbstage/vup/usr', 0o040755, None),
    ('gbstage/vup/usr/idc', 0o040755, None),
    ('gbstage/vup/usr/idc/AT_Translated_Set_2_keyboard.idc', 0o100644,
     b"# GB VM override: the VMware i8042 keyboard is an external keyboard (no lid/hinge sensors in the VM).\n"
     b"device.internal = 0\n"
     b"keyboard.builtIn = 0\n"),
    ('gbstage/vup/lib64', 0o040755, None),
]
# Forced EDID blob -> /odm/gb_edid.bin (read by gb_edid.sh to write the connector's debugfs edid_override).
if EDID is not None:
    _entries.append(('gbstage/gb_edid.bin', 0o100644, EDID))
    _entries.append(('gbstage/gb_edid.sh', 0o100755, GB_EDID_SH))
_entries.append(('gbstage/empty_vintf.xml', 0o100644, EMPTY_VINTF))
for _n, _d in MODS:
    _entries.append(('gbstage/lib/modules/' + _n, 0o100644, _d))
for _n, _d in KMLIBS:
    _entries.append(('gbstage/vup/lib64/' + _n, 0o100644, _d))
for _n, _d in VGFX:
    _entries.append(('gbstage/vup/lib64/' + _n, 0o100644, _d))
# Vulkan HAL overlay (/vendor/lib64/hw/vulkan.intel.so <- pastel) + any extra deps.
_entries.append(('gbstage/vup/lib64/hw', 0o040755, None))
if VPASTEL is not None:
    _entries.append(('gbstage/vup/lib64/hw/vulkan.intel.so', 0o100644, VPASTEL))
for _n, _d in VPDEPS:
    _entries.append(('gbstage/vup/lib64/' + _n, 0o100644, _d))
_entries.append(('gbstage/etc/build.prop', 0o100644, ODM_BUILD_PROP))
if ABSMOUSE:
    _entries.append(('gbstage/vup/bin/gb_absmouse', 0o100755, ABSMOUSE_BIN))
    _entries.append(('gbstage/vup/usr/idc/GB_Absolute_Mouse.idc', 0o100644, ABSMOUSE_IDC))
    # gb_absmouse's wheel-only cursor device (Android's touch mapper has no scroll axes): external, like the pointer.
    _entries.append(('gbstage/vup/usr/idc/GB_Mouse_Wheel.idc', 0o100644,
                     b"# GB mouse wheel (gb_absmouse): scroll events at the shared mouse pointer.\n"
                     b"device.internal = 0\n"))
if MESA:
    _entries.append(('gbstage/vup/lib64/egl', 0o040755, None))
    for _n, _d in MESA:
        _entries.append(('gbstage/vup/lib64/egl/' + _n, 0o100644, _d))
if VMTOOLS_BIN is not None:
    _entries.append(('gbstage/vup/bin/gb_vmtools', 0o100755, VMTOOLS_BIN))
if LAYOUT_BIN is not None:
    _entries.append(('gbstage/gb_layout', 0o100755, LAYOUT_BIN))
if EGLPROBE is not None:
    _entries.append(('gbstage/vup/bin/egl_probe', 0o100755, EGLPROBE))
    _entries.append(('gbstage/gb_eglprobe.sh', 0o100755, EGLPROBE_SH))
if SLOTSYNC_BIN:
    _entries.append(('gbstage/vup/bin/gb_slotsync', 0o100755, SLOTSYNC_BIN))
if _BATT_MODS[0] == "test_power.ko":
    _entries.append(('gbstage/gb_battunbind.sh', 0o100755, GB_BATTUNBIND_SH))
_entries.append(('gbstage/gb_zram.sh', 0o100755, GB_ZRAM_SH))     # (W4 rehearsal) the zram load-race fallback
if VMAUDIO:
    _have = {p for p, _, _ in _entries}
    for _d in ("gbstage/vup/etc", "gbstage/vup/etc/data", "gbstage/vup/etc/data/hal_configs", "gbstage/vup/etc/audio",
               "gbstage/vup/etc/audio/Generic_vmware"):
        if _d not in _have:
            _entries.append((_d, 0o040755, None))
    _rd = lambda *p: open(os.path.join(VMAUDIO_DIR, *p), "rb").read()
    _entries += [
        ('gbstage/gb_vmaudio.sh', 0o100755, _rd("gb_vmaudio.sh")),
        ('gbstage/vup/bin/tinymix', 0o100755, _rd("tinymix")),
        ('gbstage/vup/etc/data/hal_configs/audio_default.xml', 0o100644, _rd("audio_default.xml")),
    ] + [('gbstage/vup/etc/audio/Generic_vmware/' + _n, 0o100644, _rd("Generic_vmware", _n))
         for _n in ("audio_config.xml", "mixer_paths.xml", "volume_curves.xml")]
if VMADB:
    _entries += [('gbstage/gb_adb.sh', 0o100755, open(os.path.join(ADB_DIR, "gb_adb.sh"), "rb").read()),
                 ('gbstage/adb_keys', 0o100644, open(os.path.join(ADB_DIR, "adb_keys"), "rb").read()),
                 ('gbstage/gb_netwatch.sh', 0o100755, open(os.path.join(ADB_DIR, "gb_netwatch.sh"), "rb").read())]
# ---- native tree (/gbnative -> /odm when gb.hw=native) ----
def _add_entry(lst, full, data, mode=0o100644):
    """Append (full, mode, data) to the cpio entry list lst, creating every parent directory entry first (the
    initramfs unpacker needs them). data None = a directory (added once)."""
    have = {p for p, _, _ in lst}
    parts = full.split("/")
    for i in range(1, len(parts)):
        d = "/".join(parts[:i])
        if d not in have:
            lst.append((d, 0o040755, None)); have.add(d)
    if data is None:
        if full not in have:
            lst.append((full, 0o040755, None))
    else:
        lst.append((full, mode, data))

def _add_native(path, data, mode=0o100644):
    """Add gbnative/<path> (see _add_entry)."""
    _add_entry(_entries, "gbnative/" + path, data, mode)

def _mode_of(e):
    """Manifest mode ("0755", "0644", "0o100755") -> full cpio mode (regular file unless the type bits are given)."""
    m = int(str(e["mode"]).replace("0o", ""), 8)
    return m if m > 0o7777 else 0o100000 | m

# the software Gatekeeper's two files in the VM tree (its rc block is already in GB_RC; the native tree: components)
_add_entry(_entries, "gbstage/" + GK_BIN_DEST, GK_BIN, 0o100755)
_add_entry(_entries, "gbstage/" + GK_VINTF_DEST, GK_VINTF, 0o100644)
# (W5) the VNNI libz patch: the same /odm/vnni/<id>/ files in both trees (its rc block: GB_RC above, the native gb.rc below)
for _p, _d, _m in VNNI_FILES:
    _add_entry(_entries, "gbstage/" + _p, _d, _m)
# (W5b) the OTA gate, the same three files in both trees: the validated build, the notifier script and its rc file
for _t in ("gbstage/", "gbnative/"):
    for _p, _d, _m in ((OTA_VALIDATED, OTA_VALIDATED_BYTES, 0o100644), (OTA_SCRIPT, GB_OTAGATE_SH, 0o100755),
                       (OTA_RC_FILE, OTA_RC, 0o100644)):
        _add_entry(_entries, _t + _p, _d, _m)

# Native components built and reviewed separately under native/<name> (GB_BUILD) (iptsd touch/pen, gb_power battery/
# detach/dGPU/profile, audio, nouveau policy, NVK, compute, the GPU UI and the per-app graphics driver package). Each
# manifest inject\native\components\<name>.json lists
#   files      {src, dest, mode}: native tree files (dest relative to /odm)
#   payload    {src, dest, mode 0644}: files of the dGPU erofs payload (dest relative to the erofs root, INTERFACES 6.2)
#   symlinks   {dest, target}: native tree symlinks into the payload mount /odm/dgpu
#   rc_files / rc_text: fragments appended to the native gb.rc;  build_prop: lines of the native /odm/etc/build.prop
# Their kernel modules go through stage_kernel.py (inject\native\modules), not through the manifests.
# GB_GFXDRIVER=0 (bisect opt-out only) skips gfxdriver.json: no GbGfxDriver.apk, no ro.gfx.driver.0, no gfx_build_id.
import json as _json, glob as _glob
NATIVE_COMPONENTS, _COMP_FILES, _COMP_RC, PAYLOAD_FILES, _COMP_LINKS = [], [], b"", [], []
_KNOWN = {"name", "source", "comment", "files", "payload", "symlinks", "rc_files", "rc_text", "build_prop"}
# never in any build.prop line of a component (PLAN invariant 1, INTERFACES 4 and 14, W2G-03: no build-time default)
_BAD_PROP = ("ro.hardware.vulkan", "ro.hardware.egl", "ro.hwui.use_vulkan", "debug.hwui.renderer",
             "ro.vendor.gb.gfx_default", "persist.vendor.gb.gfx_mode", "persist.vendor.gb.nouveau")
# the ESP bring-up refuses every _BAD_PROP name as a GB_PROP (contract 2.3) plus the VNNI gate/build prefixes (F4)
assert all((_b + "*").encode() in GB_BRINGUP_SH for _b in _BAD_PROP + (
    "ro.system.build.", "ro.vendor.build.", "ro.*.build.fingerprint")), \
    "GB_BRINGUP_SH deny list lacks a _BAD_PROP or VNNI gate name"
def _xv(o):
    """Repository: expand ${GB_HOME} / ${GB_BUILD} in every string of a component manifest."""
    if isinstance(o, str):
        return os.path.expandvars(o)
    if isinstance(o, list):
        return [_xv(x) for x in o]
    if isinstance(o, dict):
        return {k: _xv(v) for k, v in o.items()}
    return o

def _rel_ok(p):
    return bool(p) and not p.startswith("/") and "\\" not in p and ".." not in p.split("/")

def _bp_import(line):
    """(W6 prep, SA-9) True for a build.prop line that init's property loader would treat as `import <file>` (AOSP
    property_service LoadProperties; INFERRED for lapis): the F4 checks only see key=value lines, so a file imported
    from /odm/etc/build.prop could set a VNNI gate or build property. Refused in component build_prop and both trees."""
    s = (line.decode("utf-8", "replace") if isinstance(line, bytes) else line).strip()
    return s == "import" or s.startswith(("import ", "import\t"))

def _load_manifest(m, mf):
    """Check one component manifest m (from file mf) and read its inputs:
    (files [(dest, bytes, mode)], payload [(dest, bytes)], symlinks [(dest, target, mode)], rc bytes, build.prop bytes)."""
    assert not set(m) - _KNOWN, f"{mf}: unknown keys {sorted(set(m) - _KNOWN)} (gfxtest_entry is retired)"
    files, payload, links, rc, prop = [], [], [], b"", b""
    for e in m.get("files", []):
        assert _rel_ok(e["dest"]), f"{mf}: bad dest {e['dest']!r}"
        assert "gb_gfxdriver" not in e["dest"], f"{mf}: gb_gfxdriver.* is retired (v1.2)"
        files.append((e["dest"], open(e["src"], "rb").read(), _mode_of(e)))
    for e in m.get("payload", []):
        assert (int(str(e["mode"]).replace("0o", ""), 8) & 0o7777) == 0o644, f"{mf}: payload files are 0644 (6.2)"
        assert _rel_ok(e["dest"]) and e["dest"].startswith(("lib64/hw/", "lib64/gpu-compute/")), e
        payload.append((e["dest"], open(e["src"], "rb").read()))
    for s in m.get("symlinks", []):
        assert _rel_ok(s["dest"]) and s["target"].startswith("/odm/dgpu/") and ".." not in s["target"], s
        links.append((s["dest"], s["target"].encode(), 0o120777))      # newc symlink: data = target
    for r in m.get("rc_files", []):
        rc += b"\n# ---- component " + m["name"].encode() + b" ----\n" + open(r, "rb").read()
    if m.get("rc_text"):
        rc += b"\n# ---- component " + m["name"].encode() + b" ----\n" + m["rc_text"].encode() + b"\n"
    for l in m.get("build_prop", []):            # native /odm/etc/build.prop additions
        assert not l.startswith(_BAD_PROP), f"{mf}: {l} is not allowed (invariant 1, W2G-03)"
        assert l.split("=", 1)[0].strip() not in VNNI_BINDS.values() and not l.startswith(("ro.system.build.", "ro.vendor.build.")), \
            f"{mf}: {l} overrides a VNNI gate/build property"
        assert not _bp_import(l), f"{mf}: {l!r}: no import lines in build_prop (SA-9)"
        prop += l.encode() + b"\n"
    return files, payload, links, rc, prop

os.environ.setdefault("GB_HOME", HERE)   # manifest paths may use ${GB_HOME} and ${GB_BUILD}
os.environ.setdefault("GB_BUILD", HERE if os.path.isdir(os.path.join(HERE, "native")) else r"C:\GB-build")
for _mf in sorted(_glob.glob(os.path.join(NATIVE_DIR, "components", "*.json"))):
    with open(_mf, encoding="utf-8") as _fh:
        _m = _xv(_json.load(_fh))
    if _m["name"] == "gfxdriver" and os.environ.get("GB_GFXDRIVER") == "0":
        print("native component gfxdriver: skipped (GB_GFXDRIVER=0)", flush=True); continue
    assert _m["name"] not in NATIVE_COMPONENTS, f"{_mf}: component {_m['name']} twice"
    _f, _p, _s, _r, _l = _load_manifest(_m, _mf)
    for _d, _ in _p:
        assert _d not in dict(PAYLOAD_FILES), f"{_mf}: payload {_d} twice"
    _COMP_FILES += _f; PAYLOAD_FILES += _p; _COMP_LINKS += _s; _COMP_RC += _r; ODM_BUILD_PROP_NATIVE += _l
    NATIVE_COMPONENTS.append(_m["name"])
# (W5, C-1) bionic maps every PT_LOAD of a library or program page-wise, so each one needs a power-of-two p_align,
# p_offset == p_vaddr modulo max(p_align, 4 KiB page) and to lie inside the file. patchelf followed by llvm-strip broke
# this in every wave-3 camera ELF (VM-1: 'cannot find "n34_0_Box_C" from verneed[0]', the provider restarting every
# 5 s); no component file or dGPU payload file may ship like that again (verify_image V12b: every ELF of the image).
def _ptload_problems(data):
    """[problem, ...] of the PT_LOAD headers of an x86-64 ELF64 executable or shared object; [] for any other file."""
    if data[:4] != b"\x7fELF" or len(data) < 64 or data[4] != 2 or data[5] != 1 or \
            struct.unpack_from("<HH", data, 16) not in ((2, 62), (3, 62)):
        return []
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    if phentsize < 56 or phoff + phnum * phentsize > len(data):
        return ["program headers outside the file"]
    probs, loads = [], 0
    for i in range(phnum):
        f = struct.unpack_from("<IIQQQQQQ", data, phoff + i * phentsize)
        p_type, p_off, p_vaddr, p_filesz, p_align = f[0], f[2], f[3], f[5], f[7]
        if p_type == 1:
            loads += 1
            if p_align & (p_align - 1) or (p_off - p_vaddr) % max(p_align, 4096) or p_off + p_filesz > len(data):
                probs.append(f"PT_LOAD {i}: offset {p_off:#x} vaddr {p_vaddr:#x} filesz {p_filesz:#x} "
                             f"align {p_align:#x}")
    return probs if loads else ["no PT_LOAD"]
for _d, _b in [(_x[0], _x[1]) for _x in _COMP_FILES] + PAYLOAD_FILES:
    _bad = _ptload_problems(_b)
    assert not _bad, f"native component file {_d}: bionic cannot map it ({'; '.join(_bad[:2])}); re-stage it (C-1)"
# ro.gfx.driver.0 comes from the native build.prop exactly when the driver package ships (INTERFACES 4, W2G-01)
assert ODM_BUILD_PROP_NATIVE.count(b"ro.gfx.driver.0=") == (1 if "gfxdriver" in NATIVE_COMPONENTS else 0), \
    "ro.gfx.driver.0 must be set exactly once, and only with the gfxdriver component"
for _l in ODM_BUILD_PROP_NATIVE.decode().splitlines():
    assert not _l.startswith(_BAD_PROP), f"native build.prop: {_l} is not allowed (invariant 1, W2G-03)"
# (wave 5, B-2) the native composer colour switches: each exactly once, checked after the component lines are added
# (a later line with the same name would win at boot). They sit in ODM_BUILD_PROP_NATIVE, i.e. before the component
# lines (camera, gfxdriver); the order does not matter while each key occurs once (W6 prep, image audit IA-2).
for _l in ("vendor.hwc.drm.force_color_mode=-1", "vendor.hwc.drm.persistent_hdr=false", "vendor.hwc.use_log_gamma_lut=false"):
    assert [x for x in ODM_BUILD_PROP_NATIVE.decode().splitlines() if x.split("=")[0] == _l.split("=")[0]] == [_l], \
        f"native build.prop: exactly one {_l} (B-2 white veil)"
# HWUI and the retired v1.1 graphics/opt-in switches (INTERFACES 3, 4, 14, 16): never in the native rc. The
# persist.vendor.gb.nouveau= trigger is the v1.1 one; finding it means a half-integrated manifest set.
for _bad in (b"debug.hwui.renderer", b"gb.gfxdriver", b"gb_gfxdriver"):
    assert _bad not in GB_RC_NATIVE + GB_SLOTSYNC_RC + _COMP_RC, f"native gb.rc must not contain {_bad.decode()}"
assert b"persist.vendor.gb.nouveau=" not in _COMP_RC, "native gb.rc: the v1.1 persist.vendor.gb.nouveau trigger"
# W4: the software Gatekeeper ships in the native tree through its component, with the VM tree's rc block byte for byte
assert "gatekeeper" in NATIVE_COMPONENTS and _COMP_RC.count(GK_RC_BLOCK) == 1, "native: the gatekeeper component"
print(f"native components: {', '.join(NATIVE_COMPONENTS) or 'none'} ({len(_COMP_FILES)} files, "
      f"{len(PAYLOAD_FILES)} payload files, {len(_COMP_LINKS)} symlinks)", flush=True)

for _p, _d, _m in [
        ("gb_modules.sh", GB_MODULES_SH, 0o100755), ("gb_bootlog.sh", GB_BOOTLOG_SH, 0o100755),
        ("gb_ui.sh", GB_UI_SH, 0o100755), ("gb_dexopt.sh", GB_DEXOPT_SH, 0o100755),
        ("gb_noop.sh", b"#!/system/bin/sh\nexit 0\n", 0o100755), ("gb_sleeper.sh", GB_SLEEPER, 0o100755), ("gb_fw_stub.sh", GB_FW_STUB, 0o100755),
        ("empty_vintf.xml", EMPTY_VINTF, 0o100644),
        ("etc/fstab.android-desktop", FSTAB_VERITY, 0o100644), ("etc/se_manifest.xml", SEMAN, 0o100644),
        ("etc/init/gb.rc", GB_RC_NATIVE + GB_SLOTSYNC_RC + _COMP_RC + VNNI_RC_BLOCK, 0o100644),   # (W5) vnni last
        ("etc/build.prop", ODM_BUILD_PROP_NATIVE, 0o100644),
        ("etc/selinux/odm_sepolicy.cil", ODMCIL, 0o100644), ("etc/selinux/precompiled_sepolicy", ODMPRE, 0o100644),
        ("vup/bin/hw/android.hardware.security.keymint-service", KM_BIN_NATIVE, 0o100755),    # (C-2) private sonames
        ("vup/bin/hw/android.hardware.security.secretkeeper.trusty", SK_STUB, 0o100755),
        ("vup/bin/hw/android.hardware.gatekeeper-service.trusty", SK_STUB, 0o100755),
        ("vup/bin/hw/touchpad-inhibitor", SK_STUB, 0o100755),
        ("vup/bin/hw/vendor.google.desktop.charge-control-hal", GB_SLEEPER, 0o100755),
        ("vup/bin/storage_metrics_service", GB_SLEEPER, 0o100755),
        ("vup/bin/crossystem", CROSSYSTEM_STUB, 0o100755),
        ("vup/bin/fwupd-binder", GB_SLEEPER, 0o100755),
        ("vup/bin/fwupd_blocking_update", b"#!/system/bin/sh\nexit 0\n", 0o100755),
        ("vup/bin/fwupdmgr", GB_REFUSE, 0o100755), ("vup/bin/flashrom", GB_REFUSE, 0o100755),
        ("vup/bin/futility", GB_REFUSE, 0o100755), ("vup/bin/ectool", GB_REFUSE, 0o100755),
        ("vup/bin/hw/vendor.google.desktop.futility-executor", GB_REFUSE, 0o100755),
        ("vup/bin/init.adbd-killer.sh", b"#!/system/bin/sh\nexit 0\n", 0o100755),
        ("vup/bin/android-desktop-install", GB_REFUSE, 0o100755)] + (
        [("vup/bin/gb_slotsync", SLOTSYNC_BIN, 0o100755)] if SLOTSYNC_BIN else []) + _COMP_FILES + VNNI_FILES:
    _add_native(_p, _d, _m)
_add_native("vup/etc", None)
for _n, _d in KMLIBS_NATIVE:        # (C-2) lib<x>_gbkm16.so: no stock /vendor/lib64 library is replaced
    _add_native("vup/lib64/" + _n, _d)
# ---- dGPU payload (INTERFACES 6; W2-08): ESP:/gb/dgpu-<krel>.erofs ----
# The TU117 firmware (+ WHENCE/LICENCE), the 5 opt-in modules, the NVK ICD and the compute libraries live in one erofs
# image on the ESP instead of the /odm tmpfs; gb_nouveau.sh copies, verifies and mounts it at /odm/dgpu before any
# insmod. Built once on the builder (make_dgpu_payload.sh needs mkfs.erofs) and cached in cache\dgpu by input hash, so
# every later build is byte-identical without the builder. Only native builds ever need it (native_cpio / slot_files
# native=True); a VM build never does.
import hashlib as _hl, io as _io, subprocess as _sp, tarfile as _tar, zipfile as _zip
_sk_spec = _ilu.spec_from_file_location("stage_kernel", os.path.join(HERE, "stage_kernel.py"))
SK = _ilu.module_from_spec(_sk_spec); _sk_spec.loader.exec_module(SK)    # constants only (main() is __main__-guarded)
OPTIN = [m + ".ko" for m in SK.NATIVE_OPTIN]                    # drm_exec drm_gpuvm gpu-sched drm_ttm_helper nouveau
NK = os.path.join(os.environ["GB_BUILD"], "native", "nouveau-kernel")   # set above (GB_BUILD)
DGPU_TOOL = os.path.join(NK, "payload", "make_dgpu_payload.sh")
DGPU_FWSHA = os.path.join(NK, "firmware_staged_sha256.txt")
DGPU_TEXTS = [("firmware/nvidia/WHENCE.tu117", os.path.join(NK, "firmware", "WHENCE.tu117")),
              ("firmware/nvidia/LICENCE.nvidia", os.path.join(NK, "firmware", "LICENCE.nvidia"))]
DGPU_CACHE = os.path.join(HERE, "cache", "dgpu")
DGPU_STAGE = "/build/native/dgpu/payload-stage"                  # builder scratch (/build, never /)
DGPU_MIN_FREE = 6 * 10**9                                         # builder / and /build must keep this much free
SIG_TRAILER = b"~Module signature appended~\n"
_sha = lambda b: _hl.sha256(b).hexdigest()

def kernel_release(bz=KERNEL):
    """krel from the bzImage setup header (kernel_version string at u16@0x20E + 0x200), first token (C-08)."""
    assert bz[0x202:0x206] == b"HdrS", "vmlinuz: no bzImage setup header"
    off = struct.unpack_from("<H", bz, 0x20E)[0] + 0x200
    return bz[off:off + 256].split(b"\0")[0].split()[0].decode()

def _vermagic(ko):
    m = re.search(rb"(?:^|\0)vermagic=([^\0]*)\0", ko)
    assert m, "module without vermagic"
    return m.group(1).decode()

def _det_tar(files, prefix=""):
    """Deterministic tar of {path: bytes}: sorted, mtime 0, uid/gid 0, directories 0755, files 0644."""
    dirs = set()
    for p in files:
        parts = p.split("/")
        dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
    bio = _io.BytesIO()
    with _tar.open(fileobj=bio, mode="w", format=_tar.GNU_FORMAT) as tf:
        for name in sorted(dirs | set(files)):
            ti = _tar.TarInfo(prefix + name); ti.mtime = 0; ti.uid = ti.gid = 0; ti.uname = ti.gname = ""
            if name in dirs:
                ti.type, ti.mode = _tar.DIRTYPE, 0o755
                tf.addfile(ti)
            else:
                ti.mode, ti.size = 0o644, len(files[name])
                tf.addfile(ti, _io.BytesIO(files[name]))
    return bio.getvalue()

def _builder(cmd, data=None):
    """One ssh call to the builder (stage_kernel's constants). GB_NO_BUILDER=1 makes every call fail."""
    if os.environ.get("GB_NO_BUILDER") == "1":
        raise RuntimeError("GB_NO_BUILDER")
    r = _sp.run(SK.SSH + [cmd], input=data, capture_output=True)
    return r.returncode, r.stdout, r.stderr.decode(errors="replace")

def _payload_inputs():
    """(krel, {erofs path: bytes}) of the payload tree (INTERFACES 6.2), checked against the staged inputs."""
    krel = kernel_release()
    assert re.fullmatch(r"[A-Za-z0-9._+-]+", krel), krel
    files = {}
    want = {}
    for _l in open(DGPU_FWSHA, "rb").read().decode("ascii").splitlines():
        if _l.strip():
            h, p = _l.split(None, 1)
            want[p.strip().lstrip("*")] = h.lower()
    assert len(want) == 30 and all(p.startswith("nvidia/tu117/") for p in want), "firmware_staged_sha256.txt: 30 tu117 files"
    fw = {n: d for n, d in NATIVE_FW if n.startswith("nvidia/")}
    assert set(fw) == set(want), f"inject\\native\\firmware\\nvidia != firmware_staged_sha256.txt: {sorted(set(fw) ^ set(want))}"
    for p, h in sorted(want.items()):
        assert _sha(fw[p]) == h, f"firmware sha256 mismatch: {p}"
        files["firmware/" + p] = fw[p]
    for dest, src in DGPU_TEXTS:
        assert os.path.isfile(src), f"missing {src} (INTERFACES 6.2, invariant 16)"
        files[dest] = open(src, "rb").read()
    mods = dict(_NATIVE_MODS_ALL)
    for m in OPTIN:
        assert m in mods, f"inject\\native\\modules\\{m} missing (stage_kernel.py stages it)"
        assert _vermagic(mods[m]).split()[0] == krel, f"{m}: vermagic {_vermagic(mods[m]).split()[0]} != vmlinuz {krel}"
        assert mods[m].endswith(SIG_TRAILER), f"{m} is not signed"
        files["lib/modules/" + m] = mods[m]
    files["lib/modules/modules.dgpu"] = "".join(m + "\n" for m in OPTIN).encode()
    assert files["lib/modules/modules.dgpu"] == b"drm_exec.ko\ndrm_gpuvm.ko\ngpu-sched.ko\ndrm_ttm_helper.ko\nnouveau.ko\n"
    for dest, data in PAYLOAD_FILES:
        assert dest not in files, dest
        files[dest] = data
    assert "lib64/hw/vulkan.nouveau.so" in files, "nvk.json must list the ICD in 'payload'"
    assert {p.split("/")[0] for p in files} <= {"firmware", "lib", "lib64"}, sorted({p.split("/")[0] for p in files})
    return krel, files

_DGPU = {}
def dgpu_payload():
    """{"name": "dgpu-<krel>.erofs", "image": bytes, "sha_name": "dgpu-<krel>.sha256", "sha_line": "<hex>  <name>\\n"}
    (memoized): from cache\\dgpu when the inputs are unchanged, else built and content-checked on the builder."""
    if _DGPU:
        return _DGPU
    krel, files = _payload_inputs()
    tool, fwsha = open(DGPU_TOOL, "rb").read(), open(DGPU_FWSHA, "rb").read()
    assert b"\r" not in tool, "make_dgpu_payload.sh has CR bytes"
    name, sha_name = f"dgpu-{krel}.erofs", f"dgpu-{krel}.sha256"
    h = _hl.sha256(b"gb-dgpu-cache-1\0")
    h.update(krel.encode() + b"\0" + _sha(tool).encode() + b"\0" + _sha(fwsha).encode() + b"\0")
    for p in sorted(files):
        h.update(p.encode() + b"\0" + _sha(files[p]).encode() + b"\n")
    key = h.hexdigest()
    cdir = os.path.join(DGPU_CACHE, key[:16])
    cimg, csha = os.path.join(cdir, name), os.path.join(cdir, sha_name)
    cinp = os.path.join(cdir, "inputs.json")
    if os.path.isfile(cimg) and os.path.isfile(csha) and os.path.isfile(cinp):
        img, line = open(cimg, "rb").read(), open(csha, "rb").read().decode("ascii", "replace")
        with open(cinp, encoding="utf-8") as fh:
            ckey = _json.load(fh).get("key")
        if ckey == key and line == f"{_sha(img)}  {name}\n" and img[1024:1028] == struct.pack("<I", 0xE0F5E1E2):
            print(f"dgpu payload: cache hit {cdir}: {name} size={len(img)} sha256={_sha(img)}", flush=True)
            _DGPU.update(name=name, image=img, sha_name=sha_name, sha_line=line)
            return _DGPU
        print(f"dgpu payload: cache entry {cdir} does not verify, rebuilding", flush=True)
    total = sum(len(v) for v in files.values())
    print(f"dgpu payload: cache miss (key {key[:16]}): {len(files)} files, {total:,} B -> builder", flush=True)
    # builder space (never below 6 GB on / or /build) and etiquette (INTERFACES 1.4: warn, do not block, on load)
    rc, out, err = _builder("df -B1 --output=avail / /build | tail -n 2; cut -d' ' -f1 /proc/loadavg; "
                            "pgrep -fa '[/]var/tmp/gbiso/[0-9]' | wc -l")
    assert rc == 0, f"builder preflight failed: {err.strip()}"
    v = out.decode().split()
    root_free, build_free, load1, iso_jobs = int(v[0]), int(v[1]), float(v[2]), int(v[3])
    need = 5 * total + 64 * 2**20          # tree + tool copy + image + two extractions, all on /build
    if root_free < DGPU_MIN_FREE or build_free - need < DGPU_MIN_FREE:
        raise RuntimeError(f"builder free space too low for the payload build: / {root_free:,} B, /build {build_free:,} B "
                           f"(needs {need:,} B and must keep {DGPU_MIN_FREE:,} B)")
    if iso_jobs or load1 >= 2:
        print(f"dgpu payload: WARNING builder busy (load {load1}, ISO jobs {iso_jobs}); continuing at nice 19 / idle I/O",
              flush=True)
    S = f"{DGPU_STAGE}/{krel}-{key[:12]}"
    tarb = _det_tar(files, "tree/")
    stage = _det_tar({"make_dgpu_payload.sh": tool, "firmware_staged_sha256.txt": fwsha})
    try:
        rc, out, err = _builder(f"set -e; rm -rf '{S}'; mkdir -p '{S}/tree' '{S}/out' '{S}/tmp'; tar -x -C '{S}'", tarb)
        assert rc == 0, f"builder: staging the tree failed: {err.strip()}"
        rc, out, err = _builder(f"set -e; tar -x -C '{S}'", stage)
        assert rc == 0, f"builder: copying the tool failed: {err.strip()}"
        rc, out, err = _builder(f"cd '{S}' && TMPDIR='{S}/tmp' nice -n 19 ionice -c3 sh ./make_dgpu_payload.sh tree "
                                f"out/{name} {krel} firmware_staged_sha256.txt")
        last = (out.decode(errors="replace").strip().splitlines() or [""])[-1]
        print(f"dgpu payload: {last}", flush=True)
        m = re.fullmatch(r"PAYLOAD ok size=(\d+) sha256=([0-9a-f]{64}) files=(\d+)", last)
        assert rc == 0 and m, f"make_dgpu_payload.sh failed (rc {rc}): {last} {err.strip()[-400:]}"
        # content proof: the image extracts to exactly the staged tree (+ VERSION, MANIFEST.sha256), which verifies
        rc, out, err = _builder(f"cd '{S}' && mkdir x && nice -n 19 ionice -c3 fsck.erofs --extract='{S}/x' out/{name} "
                                f"> /dev/null && diff -r -x VERSION -x MANIFEST.sha256 tree x && "
                                f"(cd x && sha256sum -c --quiet MANIFEST.sha256) && echo CONTENT-OK")
        res = out.decode(errors="replace").strip()
        print(f"dgpu payload: {res.splitlines()[-1] if res else '(no output)'}", flush=True)
        assert rc == 0 and res.endswith("CONTENT-OK"), f"payload content check failed (rc {rc}): {res[-400:]} {err.strip()[-400:]}"
        rc, img, err = _builder(f"cat '{S}/out/{name}'")
        assert rc == 0, f"fetching the payload failed: {err.strip()}"
        rc, line, err = _builder(f"cat '{S}/out/{name}.sha256'")
        assert rc == 0, f"fetching the payload sha256 failed: {err.strip()}"
        line = line.decode("ascii")
    finally:
        try:
            _builder(f"rm -rf '{S}'")
        except RuntimeError:
            pass
    assert len(img) == int(m.group(1)) and _sha(img) == m.group(2), "fetched payload != PAYLOAD ok line"
    assert line == f"{_sha(img)}  {name}\n", f"payload .sha256 line {line!r}"
    assert img[1024:1028] == struct.pack("<I", 0xE0F5E1E2), "not an erofs image"
    os.makedirs(cdir, exist_ok=True)
    inputs = {"key": key, "krel": krel, "tool": {"path": DGPU_TOOL, "sha256": _sha(tool)},
              "fw_sha_list": {"path": DGPU_FWSHA, "sha256": _sha(fwsha)},
              "files": [{"path": p, "size": len(files[p]), "sha256": _sha(files[p])} for p in sorted(files)],
              "payload": {"name": name, "size": len(img), "sha256": _sha(img), "files": int(m.group(3))}}
    for path, data in ((cimg, img), (os.path.join(cdir, "inputs.json"),
                                     (_json.dumps(inputs, indent=1, sort_keys=True) + "\n").encode()), (csha, line.encode())):
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    print(f"dgpu payload: built and cached in {cdir}", flush=True)
    _DGPU.update(name=name, image=img, sha_name=sha_name, sha_line=line)
    return _DGPU

# Native tree, payload layout: the NVIDIA firmware and the opt-in modules stay out of the cpio (they are in the payload);
# /odm/dgpu (erofs mount point) and /odm/firmware/nvidia (bind target) are empty directories; the manifest symlinks
# (vulkan.nouveau.so -> /odm/dgpu/...) point into the mount.
_n_nv = 0
for _n, _d in NATIVE_FW:
    if _n.startswith("nvidia/"):
        _n_nv += 1; continue
    _add_native("firmware/" + _n, _d)
_add_native("lib/modules", None)
for _n, _d in NATIVE_MODS:
    if _n in OPTIN:
        continue
    _add_native("lib/modules/" + _n, _d)
_add_native("dgpu", None)
_add_native("firmware/nvidia", None)
for _p, _t, _md in _COMP_LINKS:
    _add_native(_p, _t, _md)
print(f"native tree: {len(NATIVE_FW) - _n_nv} firmware files (+{_n_nv} NVIDIA files in the payload), "
      f"{len([n for n, _ in NATIVE_MODS if n.endswith('.ko') and n not in OPTIN])} modules (+{len(OPTIN)} in the payload)",
      flush=True)

# ---- cpio hygiene and tree asserts (W2-10, W2-13, PLAN invariants 1, 17, 19) ----
def _dedupe(ents, label):
    """Drop later copies of a path with the same mode and bytes (logged); any other duplicate is a build error."""
    seen, out, dropped = {}, [], []
    for path, mode, data in ents:
        if path in seen:
            if seen[path] == (mode, data):
                dropped.append(path); continue
            raise AssertionError(f"{label}: duplicate path {path} with different mode or content")
        seen[path] = (mode, data); out.append((path, mode, data))
    if dropped:
        print(f"{label}: {len(dropped)} identical duplicate path(s) dropped (e.g. {', '.join(dropped[:4])}"
              f"{', ...' if len(dropped) > 4 else ''})", flush=True)
    return out

_BAD_NAME = re.compile(r"\.pre-|\.merged|\.orig|\.v1\.1|\.v1\.2r1|superseded/|\.bak$|\.part$")
_VM_FORBIDDEN = ("nouveau.ko", "firmware/nvidia", "vulkan.nouveau.so", "gb_nouveau", "nvk_probe", "gb_gpu_compute",
                 "app/GbGpu", "GbGfxDriver", "gb_gfxdriver", "gfx_build_id", "gfx_app_prefs.default",
                 "gb_gfx_selftest", "dgpu")

def _check_common(ents, label):
    for path, mode, data in ents:
        assert not path.startswith(("gbnative/priv-app", "gbstage/priv-app")), f"{label}: {path} (no priv-app, W2-10)"
        assert not _BAD_NAME.search(path), f"{label}: {path} looks like a backup or a superseded file (W2-13)"

def _check_gatekeeper(ents, tree, label, present=True):
    """GATEKEEPER.md 7.5 on one tree ("gbstage" or "gbnative"): with present=True the binary (0755), the reviewed VINTF
    fragment (0644, the only ODM VINTF fragment) and the rc block (once, the only IGatekeeper/default interface line)
    are all there, byte for byte the component's; present=False (the test-only no-Gatekeeper tree) none of them. Either
    way the Trusty Gatekeeper stays masked (empty bind over its fragment, SK_STUB binary, GBINIT whiteout of its .rc)
    and Weaver undeclared."""
    d = {p: (m, x) for p, m, x in ents}
    rc = d[f"{tree}/etc/init/gb.rc"][1]
    b, v = d.get(f"{tree}/{GK_BIN_DEST}"), d.get(f"{tree}/{GK_VINTF_DEST}")
    frags = sorted(p for p, (m, x) in d.items() if p.startswith(f"{tree}/etc/vintf/") and x is not None)
    if present:
        assert b == (0o100755, GK_BIN), f"{label}: {GK_BIN_DEST} missing, not 0755 or not the accepted binary"
        assert v == (0o100644, GK_VINTF), f"{label}: {GK_VINTF_DEST} missing, not 0644 or not the reviewed fragment"
        assert rc.count(GK_RC_BLOCK) == 1 and rc.count(b"service gb_gatekeeper ") == 1, f"{label}: the gatekeeper rc block"
        assert frags == [f"{tree}/{GK_VINTF_DEST}"], f"{label}: ODM VINTF fragments {frags}"
    else:
        assert b is None and v is None and not frags and b"gb_gatekeeper" not in rc, f"{label}: a Gatekeeper part left"
    assert rc.count(GK_IFACE) == (1 if present else 0), f"{label}: {rc.count(GK_IFACE)} IGatekeeper/default lines"
    assert rc.count(GK_TRUSTY_BIND) == 1 and rc.count(GK_WEAVER_BIND) == 1, f"{label}: Trusty/Weaver VINTF binds"
    assert d.get(f"{tree}/vup/bin/hw/android.hardware.gatekeeper-service.trusty") == (0o100755, SK_STUB), \
        f"{label}: the Trusty Gatekeeper binary must stay the sleep stub"
    assert d.get("gbinit", (0, b""))[1].count(GK_TRUSTY_WHITEOUT) == 1, f"{label}: GBINIT's Trusty .rc whiteout"
    # (W4 rehearsal) gb_gatekeeper's stderr (stdio_to_kmsg) reaches dmesg only through the root-only /dev/kmsg_debug
    assert _has_kmsgdbg(rc), f"{label}: gb.rc must create /dev/kmsg_debug first thing at early-init (gb_kmsgdbg)"

def _check_vnni(ents, tree, label):
    """(W5) the VNNI libz patch in one tree ("gbstage" or "gbnative"): exactly VNNI_FILES below <tree>/vnni/ (0644, byte
    for byte); no libz.so* entry outside <tree>/vnni/<id>/{system,vendor}/lib64/libz.so; no gate/build property in
    /odm/etc/build.prop; the gb.rc block once, after the 'on early-init' /vendor overlay action, every action in it
    gated on a ro.system/vendor build fingerprint, one bind per packed library; no /odm/vnni use and no libz.so mention
    anywhere else in gb.rc (no ungated bind); GBINIT's gb.vnni=0 kill switch. (W6, F3-dir) no entry of any type (a
    directory included, or a path below one) named libz.so* outside those two regular files; (W6, F1) the block ends
    with VNNI_RC_TAIL (the gbvnni read-only remount, the one action without a gate) and GBINIT has VNNI_GBINIT_RO."""
    d = {p: (m, x) for p, m, x in ents}
    rc = d[f"{tree}/etc/init/gb.rc"][1]
    got = {p[len(tree) + 1:]: v for p, v in d.items() if p.startswith(f"{tree}/vnni/") and v[1] is not None}
    want = {p: (m, x) for p, x, m in VNNI_FILES}
    assert got == want, f"{label}: /odm/vnni files {sorted(got)} != inject\\vnni {sorted(want)}"
    allowed_libz = {f"{tree}/{p}" for p in want if p.endswith("/libz.so")
                    and re.fullmatch(rf"vnni/{VNNI_ID_RE}/(system|vendor)/lib64/libz\.so", p)}
    bad_libz = [p for p, (m, _) in d.items() if any(c.startswith("libz.so") for c in p.split("/"))
                and not (p in allowed_libz and (m & 0o170000) == 0o100000)]
    assert not bad_libz, f"{label}: libz.so* entry outside {tree}/vnni/<id>/{{system,vendor}}/lib64/libz.so: {bad_libz}"
    bp = (d.get(f"{tree}/etc/build.prop", (0, b""))[1] or b"").decode("utf-8", "replace").splitlines()
    for ln in bp:
        s = ln.strip()
        assert not _bp_import(s), f"{label}: /odm/etc/build.prop has an import line {s!r} (SA-9)"
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            assert k not in VNNI_BINDS.values() and not k.startswith(("ro.system.build.", "ro.vendor.build.")), \
                f"{label}: /odm/etc/build.prop overrides gate/build property {k}"
    rest = rc
    if VNNI_RC_BLOCK:
        assert rc.count(VNNI_RC_BLOCK) == 1, f"{label}: the vnni rc block must be in gb.rc once"
        ov = rc.find(b"    mount overlay gbkm /vendor ")
        ov_heads = [ln.strip() for ln in rc[:ov].splitlines()
                    if ln.strip() and not ln.startswith((b" ", b"\t", b"#"))] if ov >= 0 else []
        assert 0 <= ov < rc.index(VNNI_RC_BLOCK) and ov_heads and ov_heads[-1] == b"on early-init", \
            f"{label}: the vnni binds must come after the 'on early-init' /vendor overlay action"
        rest = rc.replace(VNNI_RC_BLOCK, b"", 1)
        assert VNNI_RC_BLOCK.endswith(VNNI_RC_TAIL) and VNNI_RC_BLOCK.count(b"\non init\n") == 1, \
            f"{label}: the vnni block must end with the gbvnni read-only remount (VNNI_RC_TAIL), its only ungated action"
        lines = VNNI_RC_BLOCK[:-len(VNNI_RC_TAIL)].split(b"\n")
        heads = [ln for ln in lines if ln and not ln.startswith((b"#", b" "))]
        binds = [ln for ln in lines if ln.startswith(b"    mount none /odm/vnni/") and ln.endswith(b" bind")]
        libs = [p for p in want if not p.endswith("/manifest.json")]
        assert all(re.fullmatch(rf"on early-init && property:ro\.(system|vendor)\.build\.fingerprint={VNNI_FP_RE}".encode(), h)
                   and all(chr(b) in VNNI_PROP_SAFE for b in h.split(b"=", 1)[1])
                   for h in heads) and len(heads) == len(binds) == len(libs), f"{label}: an ungated vnni action"
    assert b"/odm/vnni" not in rest and b"libz.so" not in rest, f"{label}: /odm/vnni or libz.so outside the vnni block"
    gi = d.get("gbinit", (0, b""))[1]
    assert all(gi.count(_new) == 1 for _old, _new in VNNI_GBINIT_EDITS), f"{label}: GBINIT's gb.vnni=0 kill switch"
    assert _vnni_gbinit_ro_ok(gi), \
        f"{label}: GBINIT's gbvnni read-only tmpfs block (once, after the kill switch and the OTA gate, before the bring-up)"

def _check_otagate(ents, tree, label):
    """(W5b) the OTA gate in one tree ("gbstage" or "gbnative"; OTA_CLASSES, ota-gating DESIGN.md): the validated
    build file == the boot slot's init_boot fingerprint (0644); gb_otagate.sh (0755) and its rc file (0644; no other rc
    file names the gate's services or its property); GBINIT's token scan before /proc is unmounted and its gate once,
    after the /odm copy and the VNNI kill switch, before the bring-up, the module loading and exec /init, its unvalidated
    branch removing exactly the "skip" roots; every /odm/vup, /odm/vnni, /odm/etc/selinux, /odm/etc/vintf file and every
    rc bind source below /odm classified; the SELinux cil in its OTA-robust form."""
    import fnmatch as _fnm
    d = {p: (m, x) for p, m, x in ents}
    rcs = {p: x for p, (m, x) in d.items() if p.startswith(f"{tree}/etc/init/") and x is not None}
    assert d.get(f"{tree}/{OTA_VALIDATED}") == (0o100644, OTA_VALIDATED_BYTES), f"{label}: /odm/{OTA_VALIDATED}"
    assert f"{tree}/{OTA_STATE}" not in d, f"{label}: /odm/{OTA_STATE} is written at boot, never packed"
    assert d.get(f"{tree}/{OTA_SCRIPT}") == (0o100755, GB_OTAGATE_SH), f"{label}: /odm/{OTA_SCRIPT} (0755)"
    assert d.get(f"{tree}/{OTA_RC_FILE}") == (0o100644, OTA_RC) and not any(
        s in x for p, x in rcs.items() if p != f"{tree}/{OTA_RC_FILE}" for s in (b"gb_otagate", b"ota_gate")), \
        f"{label}: the OTA gate rc file /odm/{OTA_RC_FILE} (0644, the only rc file naming the gate)"
    gi = d.get("gbinit", (0, b""))[1]
    gate = gi.find(b"if [ -f /odm/etc/gb/ota_validated ]; then\n")
    marks = [gi.find(s) for s in (b"/busybox umount /proc\n", b"/busybox cp -a $SRC/. /odm/\n",
                                  b"[ \"$VN\" = 0 ] && /busybox rm -rf /odm/vnni\n",
                                  b"if [ \"$HW\" = native ] && [ \"$HWTOK\" = 1 ]",
                                  b"if [ \"$HW\" = native ] && [ -f /odm/lib/modules/modules.load ]", b"exec /init\n")]
    tok = gi.find(OTA_GBINIT_TOKENS)
    assert gi.count(OTA_GBINIT_TOKENS) == 1 and 0 <= tok < marks[0], f"{label}: GBINIT's gb.otagate token scan"
    assert gi.count(b"/odm/etc/gb/ota_validated") == 2 and gi.count(OTA_GBINIT_UNVALIDATED) == 1 and min(marks) >= 0 \
        and marks[1] < marks[2] < gate < gi.find(OTA_GBINIT_UNVALIDATED) < marks[3] < marks[4] < marks[5], \
        f"{label}: GBINIT's OTA gate (once, after the /odm copy and the VNNI kill switch, before the bring-up and modules)"
    assert gi.rstrip(b"\n").endswith(b"\nexec /init"), f"{label}: GBINIT must end with exec /init"
    removed = set(re.findall(rb"/busybox rm -rf /odm/(\S+)\n", OTA_GBINIT_UNVALIDATED))
    skip = {(p[:-2] if p.endswith("/*") else p).encode() for p, c, _l in OTA_CLASSES if c == "skip"}
    assert removed == skip, f"{label}: GBINIT's unvalidated branch removes {sorted(removed)}, OTA_CLASSES skip {sorted(skip)}"

    def cls(rel):
        for p, c, _l in OTA_CLASSES:
            if _fnm.fnmatchcase(rel, p):
                return c
        return None
    rels = [p[len(tree) + 1:] for p, (m, x) in d.items() if x is not None and p.startswith(
        tuple(f"{tree}/{s}/" for s in ("vup", "vnni", "etc/selinux", "etc/vintf")))]
    rels += [s.decode()[len("/odm/"):] for x in rcs.values()
             for s in re.findall(rb"^\s*mount none (/odm/\S+) \S+ bind$", x, re.M)]
    unclassified = sorted({r for r in rels if cls(r) is None})
    assert not unclassified, (f"{label}: injections without an OTA class: {unclassified[:8]} -- add each to OTA_CLASSES "
                              f"(skip/keep/always; ota-gating DESIGN.md 3)")
    assert d.get(f"{tree}/etc/selinux/odm_sepolicy.cil") == (0o100644, ODMCIL) and ODMCIL == ota_safe_cil(ODMCIL_RAW) \
        and not re.search(rb"^\(typepermissive ", ODMCIL, re.M), \
        f"{label}: the SELinux cil must be the OTA-robust form (ota_safe_cil)"
    _miss = ota_cil_missing(d[f"{tree}/etc/selinux/odm_sepolicy.cil"][1])
    assert not _miss, (f"{label}: odm_sepolicy.cil does not make {_miss} permissive (SA-1: the global SELinux mode is "
                       f"enforcing; OTA_REQUIRED_PERMISSIVE)")
    assert all(_l.encode() in GB_OTAGATE_SH for _l in OTA_SKIP_LABELS + OTA_KEEP_LABELS), f"{label}: gb_otagate labels"


def _check_vm(ents, label="VM tree"):
    """The gbstage (VMware) tree carries no part of the dGPU stack (invariant 19, VM-1B-01); since W4 it carries zram
    for mmd, fs-verity in the fstab and the software Gatekeeper (dgpu\\w4\\VM_UPGRADE.md 2.2)."""
    _check_common(ents, label)
    for path, mode, data in ents:
        assert not any(s in path for s in _VM_FORBIDDEN), f"{label}: {path} (no dGPU stack in the VM tree)"
    d = {p: x for p, _, x in ents}
    assert b"ro.gfx.driver." not in d["gbstage/etc/build.prop"], f"{label}: build.prop has ro.gfx.driver."
    for s in (b"gb_nouveau", b"gb_gfx_selftest", b"nvk_probe", b"gb_gpu_compute", b"gb_gfxdriver"):
        assert s not in d["gbstage/etc/init/gb.rc"], f"{label}: gb.rc mentions {s.decode()}"
    # wave 3 items 11, 12 stay native-only: no bring-up script, no apgetty rule
    assert "gbbringup.sh" not in d, f"{label}: /gbbringup.sh is native only"
    assert b"apgetty" not in d["gbstage/etc/init/gb.rc"], f"{label}: apgetty is disabled natively only"
    # W4 2.2(b): the fstab of both trees (inject\fstab.android-desktop + fsverity on userdata)
    assert d["gbstage/etc/fstab.android-desktop"] == FSTAB_VERITY, f"{label}: the VM fstab must be FSTAB_VERITY"
    # W4 2.2(a): our staged zsmalloc + zram, loaded at early-init after vmwgfx; only mmd writes zram0/disksize
    krel, rc = kernel_release(), d["gbstage/etc/init/gb.rc"]
    for n in VM_ZRAM:
        ko = d.get("gbstage/lib/modules/" + n)
        assert ko is not None and ko == open(os.path.join(HERE, "inject", "modules", n), "rb").read(), \
            f"{label}: {n} missing or not the staged inject\\modules copy"
        assert _vermagic(ko).split()[0] == krel and ko.endswith(SIG_TRAILER), f"{label}: {n} vermagic/signature != {krel}"
    zi = b"".join(b"    insmod /odm/lib/modules/" + n.encode() + b"\n" for n in VM_ZRAM)
    assert rc.count(zi) == 1 and rc.count(b"insmod /odm/lib/modules/zram.ko") == 1, f"{label}: zram insmod lines"
    assert b"disksize" not in rc and b"swapon" not in rc and b"mkswap" not in rc, \
        f"{label}: only lapis's mmd sizes and enables zram (2.2(a))"
    # (W4 rehearsal) the fallback for lapis's racing `modprobe zram`: the script, its service and the post-fs-data start
    zs = {p: (m, x) for p, m, x in ents}.get("gbstage/gb_zram.sh")
    assert zs == (0o100755, GB_ZRAM_SH) and rc.count(GB_ZRAM_RC) == 1 and rc.count(b"service gb_zram ") == 1, \
        f"{label}: gb_zram (0755 script, service, post-fs-data start) missing or changed"
    assert not any(w in GB_ZRAM_SH for w in (b"disksize", b"swapon", b"mkswap")), f"{label}: gb_zram must not set zram up"
    for p in ("gbstage/vnni_kat", "gbstage/gb_vnni_kat.sh"):
        assert p not in d, f"{label}: {p} belongs to the throwaway KAT tree only"
    _check_gatekeeper(ents, "gbstage", label)
    _check_vnni(ents, "gbstage", label)                 # W5: the fingerprint-gated libz patch
    _check_otagate(ents, "gbstage", label)              # W5b: the OTA gate

def _check_native(ents, label="native tree"):
    _check_common(ents, label)
    d = {p: (m, x) for p, m, x in ents}
    optin = {m.replace("-", "_") for m in OPTIN}
    for path, mode, data in ents:
        base = path.rsplit("/", 1)[-1]
        if path.startswith("gbstage"):
            raise AssertionError(f"{label}: {path}")
        if data is not None and base.replace("-", "_") in optin:
            raise AssertionError(f"{label}: {path} belongs in the dGPU payload")
        if data is not None and path.startswith("gbnative/firmware/nvidia/"):
            raise AssertionError(f"{label}: {path}: NVIDIA firmware ships only in the payload (invariant 16)")
        if path.startswith("gbnative/dgpu/"):
            raise AssertionError(f"{label}: {path}: /odm/dgpu is the payload mount point")
        if path.startswith("gbnative/app/") and data is not None:
            parts = path.split("/")
            assert len(parts) == 4 and parts[3] == parts[2] + ".apk", f"{label}: {path} (app/<D>/<D>.apk only)"
            assert mode == 0o100644 and data[:4] == b"PK\x03\x04", f"{label}: {path} must be a 0644 zip"
            z = _zip.ZipFile(_io.BytesIO(data))
            assert z.testzip() is None, f"{label}: {path}: bad zip"
            if parts[2] == "GbGfxDriver":
                names = z.namelist()
                assert not any(n.rsplit("/", 1)[-1] == "allowlist.txt" for n in names), f"{path}: allowlist.txt (24.5)"
                assert "assets/sphal_libraries.txt" in names, f"{path}: no assets/sphal_libraries.txt"
                libs = [i for i in z.infolist() if re.fullmatch(r"lib/x86_64/[^/]+\.so", i.filename)]
                assert len(libs) == 2 and all(i.compress_type == _zip.ZIP_STORED for i in libs), \
                    f"{path}: the two lib/x86_64/*.so members must be STORED"
    for p in ("gbnative/dgpu", "gbnative/firmware/nvidia"):
        assert d.get(p) == (0o040755, None), f"{label}: {p} must be an empty directory"
    for p, t, md in _COMP_LINKS:
        assert d.get("gbnative/" + p) == (md, t), f"{label}: symlink gbnative/{p}"
    for p in ("gbnative/etc/gb/dgpu_payload.sha256",) + (("gbnative/etc/gb/gfx_build_id",)
                                                          if "gfxdriver" in NATIVE_COMPONENTS else ()):
        assert p in d and d[p][0] == 0o100644, f"{label}: {p} missing"
    if "gfxdriver" not in NATIVE_COMPONENTS:
        assert "gbnative/etc/gb/gfx_build_id" not in d and "gbnative/app/GbGfxDriver" not in d, f"{label}: GB_GFXDRIVER=0"
    for ll in ("gbnative/lib/modules/modules.load", "gbnative/lib/modules/modules.load.late"):
        for line in d[ll][1].decode().splitlines():
            w = line.split()
            if w and not w[0].startswith("#"):
                assert w[0].replace("-", "_") not in optin, f"{label}: {ll} names {w[0]} (opt-in only)"
    prop = d["gbnative/etc/build.prop"][1]
    assert prop.count(b"ro.gfx.driver.0=") == (1 if "gfxdriver" in NATIVE_COMPONENTS else 0), f"{label}: ro.gfx.driver.0"
    for line in prop.decode().splitlines():
        assert not line.startswith(_BAD_PROP), f"{label}: build.prop {line}"
    # wave 3: item 12 (bring-up script, gated GBINIT), item 4 (native fstab), item 11 (apgetty off)
    assert d.get("gbbringup.sh") == (0o100755, GB_BRINGUP_SH), f"{label}: /gbbringup.sh 0755 missing"
    assert d.get("gbinit") == (0o100755, GBINIT), f"{label}: gbinit"
    assert d["gbnative/etc/fstab.android-desktop"][1] == FSTAB_VERITY, f"{label}: native fstab (fsverity)"
    rc = d["gbnative/etc/init/gb.rc"][1]
    for s in (b"    setprop desktop.apgetty.enable 0\n    stop apgetty\n",
              b"on property:init.svc.apgetty=running\n    stop apgetty\n",
              b"on property:init.svc.apgetty=restarting\n    stop apgetty\n"):
        assert s in rc, f"{label}: gb.rc lacks {s!r}"
    _check_gatekeeper(ents, "gbnative", label)          # W4: the software Gatekeeper (component gatekeeper)
    _check_vnni(ents, "gbnative", label)                # W5: the fingerprint-gated libz patch
    _check_otagate(ents, "gbnative", label)             # W5b: the OTA gate

def _gfx_build_id(ents, payload):
    """/odm/etc/gb/gfx_build_id (INTERFACES 5.3, 25.1): one line from the bytes that go into this tree."""
    d = {p: x for p, _, x in ents if x is not None}
    apk = d.get("gbnative/app/GbGfxDriver/GbGfxDriver.apk")
    probe = d.get("gbnative/vup/bin/nvk_probe")
    icd = dict(PAYLOAD_FILES).get("lib64/hw/vulkan.nouveau.so")
    assert apk and probe and icd, "gfx_build_id: GbGfxDriver.apk, nvk_probe and the payload ICD are all required"
    z = _zip.ZipFile(_io.BytesIO(apk))
    info = z.getinfo("lib/x86_64/libgb_nvk.so")
    assert info.compress_type == _zip.ZIP_STORED, "GbGfxDriver.apk: lib/x86_64/libgb_nvk.so must be STORED"
    pay = payload["sha_line"].split()[0]
    line = f"apk={_sha(apk)} icd={_sha(icd)} nvk={_sha(z.read(info))} probe={_sha(probe)} pay={pay}\n"
    assert re.fullmatch(r"apk=[0-9a-f]{64} icd=[0-9a-f]{64} nvk=[0-9a-f]{64} probe=[0-9a-f]{64} pay=[0-9a-f]{64}\n", line)
    return line.encode()

# One initrd per target: the VM boot carries only /gbstage, native boots only /gbnative (the Surface firmware the VM
# never needs). gbinit falls back to /gbstage if /gbnative is missing.
_VM_ENTRIES = _dedupe([e for e in _entries if not e[0].startswith("gbnative")], "VM tree")
_check_vm(_VM_ENTRIES)
INJECT_CPIO = cpio_newc(_VM_ENTRIES)

_NATIVE = []
def native_cpio():
    """The native (gbnative) cpio, memoized: needs the dGPU payload (its sha line goes into /odm/etc/gb), so the first
    call may build it. Module attribute INJECT_CPIO_NATIVE returns this (PEP 562)."""
    if not _NATIVE:
        ents = [e for e in _entries if not e[0].startswith("gbstage")]
        ents.append(("gbbringup.sh", 0o100755, GB_BRINGUP_SH))      # item 12: native cpio only (contract 2.3)
        p = dgpu_payload()
        _add_entry(ents, "gbnative/etc/gb/dgpu_payload.sha256", p["sha_line"].encode(), 0o100644)
        if "gfxdriver" in NATIVE_COMPONENTS:
            _add_entry(ents, "gbnative/etc/gb/gfx_build_id", _gfx_build_id(ents, p), 0o100644)
        ents = _dedupe(ents, "native tree")
        _check_native(ents)
        _check_links(ents)                                   # (C-2) link-closure gate of the /vendor overlay
        _NATIVE.append(cpio_newc(ents))
        print(f"native cpio: {len(ents)} entries, {len(_NATIVE[0]):,} B", flush=True)
    return _NATIVE[0]

def __getattr__(name):
    if name == "INJECT_CPIO_NATIVE":
        return native_cpio()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

_VMGFX = []
def vm_test_gfx_cpio():
    """Test-only VM tree (plan 3.5): the gbstage cpio plus GbGpu.apk, GbGfxDriver.apk, gfx_app_prefs.default,
    gb_gpu_ui_setup.sh, the gb_gpu_ui.rc fragment and ro.gfx.driver.0 -- nothing else of the dGPU stack. For a throwaway
    VMware check of the two packages only; never used by build_esp.py itself, never written to lapis.bin."""
    if _VMGFX:
        return _VMGFX[0]
    comp = os.path.join(NATIVE_DIR, "components")
    with open(os.path.join(comp, "gpu_ui.json"), encoding="utf-8") as fh:
        ui = _xv(_json.load(fh))
    with open(os.path.join(comp, "gfxdriver.json"), encoding="utf-8") as fh:
        gd = _xv(_json.load(fh))
    add = {"gbstage/" + e["dest"]: (_mode_of(e), open(e["src"], "rb").read()) for e in ui["files"] + gd["files"]}
    want = {"gbstage/app/GbGpu/GbGpu.apk": 0o100644, "gbstage/app/GbGfxDriver/GbGfxDriver.apk": 0o100644,
            "gbstage/etc/gb/gfx_app_prefs.default": 0o100644, "gbstage/vup/bin/gb_gpu_ui_setup.sh": 0o100755}
    assert {k: m for k, (m, _) in add.items()} == want, sorted(add)
    rc = b"".join(b"\n# ---- component gpu_ui (VM test) ----\n" + open(r, "rb").read() for r in ui["rc_files"])
    assert gd["build_prop"] == ["ro.gfx.driver.0=gb.sb3.gfxdriver"], gd["build_prop"]
    ents = []
    for path, mode, data in _VM_ENTRIES:
        if path == "gbstage/etc/init/gb.rc":
            data = data + rc
        elif path == "gbstage/etc/build.prop":
            data = data + b"ro.gfx.driver.0=gb.sb3.gfxdriver\n"
        ents.append((path, mode, data))
    for path in sorted(add):
        _add_entry(ents, path, add[path][1], add[path][0])
    ents = _dedupe(ents, "VM test gfx tree")
    _check_common(ents, "VM test gfx tree")
    old, new = {p: (m, x) for p, m, x in _VM_ENTRIES}, {p: (m, x) for p, m, x in ents}
    extra = set(new) - set(old)
    assert not set(old) - set(new) and {p for p in extra if new[p][1] is not None} == set(want), sorted(extra)
    assert all(new[p] == (0o040755, None) for p in extra if p not in want), sorted(extra)
    for p in old:
        if p == "gbstage/etc/init/gb.rc":
            assert new[p][1] == old[p][1] + rc
        elif p == "gbstage/etc/build.prop":
            assert new[p][1] == old[p][1] + b"ro.gfx.driver.0=gb.sb3.gfxdriver\n"
        else:
            assert new[p] == old[p], p
    for s in (b"gb_nouveau", b"gb_power", b"nvk_probe", b"gb_gpu_compute", b"gfx_build_id", b"nouveau.ko", b"dgpu"):
        assert not any(s.decode() in p for p in extra), s
    _VMGFX.append(cpio_newc(ents))
    print(f"VM test gfx tree: {len(extra)} entries added to the VM tree", flush=True)
    return _VMGFX[0]

# ---- test-only VNNI KAT tree (WAVE3_PLAN 6.4, R-13, contract 2.7; lead decision D-e) ----
KAT_DIR = os.path.join(HERE, "inject", "kat")       # KERNEL's drop: vnni_kat (static x86-64 ELF) + SHA256SUMS
# (W4) zram is part of the shipped VM tree now (zsmalloc/zram at early-init; lapis's mmd sizes zram0 and enables the
# swap after boot_completed, VM_UPGRADE.md 2.2(a)). The KAT therefore neither loads the modules nor writes disksize (that
# would race mmd and abort it); its script waits for mmd's zram swap and reports it ("zram swap on"), so a KAT boot also
# exercises the W4 zram path under k15.
KAT_RC = (b"\n# ---- VNNI KAT (VM test only) ----\n"         # one literal: verify_image V28 reads it with ast
          b"on property:sys.boot_completed=1\n"
          b"    start gb_vnni_kat\n"
          b"\n"
          b"service gb_vnni_kat /system/bin/sh /odm/gb_vnni_kat.sh\n"
          b"    user root\n"
          b"    seclabel u:r:shell:s0\n"
          b"    oneshot\n"
          b"    disabled\n")
# the shell part lives in a file: `$` in an rc line is a property reference
GB_VNNI_KAT_SH = (b"#!/system/bin/sh\n"
                  b"# Test-only (WAVE3_PLAN 6.4, R-13): the throwaway VMware KAT image only, never a shipped tree.\n"
                  b"k() { echo \"$1\" > /dev/kmsg; }\n"
                  b"k 'gb_vnni_kat: start'\n"
                  # (W4) mmd's zram swap (the VM tree's own zram): wait at most 300 s for zram0 in /proc/swaps
                  b"z=0\n"
                  b"while [ $z -lt 300 ] && ! /system/bin/toybox grep -q zram0 /proc/swaps; do /system/bin/toybox sleep 1; z=$((z+1)); done\n"
                  b"if /system/bin/toybox grep -q zram0 /proc/swaps; then k \"gb_vnni_kat: zram swap on (mmd, after $z s)\"\n"
                  b"else k 'gb_vnni_kat: zram swap FAILED (no zram0 in /proc/swaps after 300 s: mmd set up none)'; fi\n"
                  b"/system/bin/toybox cat /proc/swaps | while IFS= read -r l; do k \"gb_vnni_kat: swaps $l\"; done\n"
                  # (wave 3 build, R-13 early run) the counters are system-wide (INTERFACES E-15): right after
                  # boot_completed lapis's own processes (system_server, dex2oat, surfaceflinger) emulate VEX VNNI in
                  # bursts, faster than the KAT's 3 quiet re-runs can dodge. Wait (at most 600 s) until the emulated
                  # total has not moved for 5 s in a row, then run; after 600 s run anyway (the KAT then reports
                  # counter_foreign itself).
                  b"q=0; w=0; last=\n"
                  b"while [ $w -lt 600 ]; do\n"
                  b"  cur=$(/system/bin/toybox head -n 1 /sys/kernel/gb_vnni/summary 2>/dev/null); cur=${cur#emulated }; cur=${cur%% *}\n"
                  b"  if [ -n \"$cur\" ] && [ \"$cur\" = \"$last\" ]; then q=$((q+1)); [ $q -ge 5 ] && break; else q=0; fi\n"
                  b"  last=$cur; /system/bin/toybox sleep 1; w=$((w+1))\n"
                  b"done\n"
                  b"k \"gb_vnni_kat: quiet wait $w s (emulated total $last, unchanged for $q s)\"\n"
                  b"/odm/vnni_kat --all > /dev/gb_vnni_kat.out 2>&1\n"
                  b"rc=$?\n"
                  b"while IFS= read -r l; do k \"$l\"; done < /dev/gb_vnni_kat.out\n"
                  b"k \"gb_vnni_kat: exit $rc\"\n"
                  b"/system/bin/toybox timeout 5 /system/bin/toybox cat /sys/kernel/gb_vnni/summary 2>&1 | "
                  b"while IFS= read -r l; do k \"gb_vnni_kat: summary $l\"; done\n"
                  b"k 'gb_vnni_kat: done'\n")
KAT_FILES = {"gbstage/vnni_kat": 0o100755, "gbstage/gb_vnni_kat.sh": 0o100755}    # (W4: zram is in the VM tree)

def _kat_inputs(kat_dir):
    """vnni_kat bytes after the contract 2.7 checks; RuntimeError names what is missing."""
    def need(c, msg):
        if not c:
            raise RuntimeError(f"VM test KAT tree: {msg}")
    exe_p, sums_p = os.path.join(kat_dir, "vnni_kat"), os.path.join(kat_dir, "SHA256SUMS")
    need(os.path.isfile(exe_p) and os.path.isfile(sums_p), f"{kat_dir} needs vnni_kat and SHA256SUMS (contract 2.7)")
    exe = open(exe_p, "rb").read()
    sums = {}
    for ln in open(sums_p, "rb").read().decode("ascii", "replace").splitlines():
        m = re.fullmatch(r"([0-9a-f]{64}) [ *]\.?/?([A-Za-z0-9_.-]+)", ln.strip())
        need(m is not None or not ln.strip(), f"SHA256SUMS: bad line {ln[:60]!r}")
        if m:
            sums[m.group(2)] = m.group(1)
    need("vnni_kat" in sums, "SHA256SUMS does not list vnni_kat")
    for n, h in sums.items():
        p = os.path.join(kat_dir, n)
        need(os.path.isfile(p), f"SHA256SUMS lists {n}, which is missing")
        need(_sha(open(p, "rb").read()) == h, f"{n} does not match SHA256SUMS")
    need(exe[:4] == b"\x7fELF" and exe[4] == 2 and exe[5] == 1 and struct.unpack_from("<H", exe, 18)[0] == 62,
         "vnni_kat is not an x86-64 ELF")
    phoff = struct.unpack_from("<Q", exe, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", exe, 54)
    need(not any(struct.unpack_from("<I", exe, phoff + i * phentsize)[0] == 3 for i in range(phnum)),
         "vnni_kat has a PT_INTERP (it must be static)")
    return exe

_VMKAT = []
def vm_test_kat_cpio(kat_dir=None):
    """Test-only VM tree (WAVE3_PLAN 6.4): the gbstage cpio plus /odm/vnni_kat (inject\\kat, checked against its
    SHA256SUMS), /odm/gb_vnni_kat.sh and the KAT rc fragment appended to gb.rc. (W4: the shipped VM tree has zram
    itself now, so the KAT no longer adds zsmalloc/zram and waits for mmd's zram swap.) For one throwaway VMware boot
    (build_usb.py --vm-test --vm-test-kat); never written to lapis.bin."""
    if _VMKAT and kat_dir is None:
        return _VMKAT[0]
    exe = _kat_inputs(kat_dir or KAT_DIR)
    ents = [(p, m, (x + KAT_RC) if p == "gbstage/etc/init/gb.rc" else x) for p, m, x in _VM_ENTRIES]
    _add_entry(ents, "gbstage/vnni_kat", exe, 0o100755)
    _add_entry(ents, "gbstage/gb_vnni_kat.sh", GB_VNNI_KAT_SH, 0o100755)
    ents = _dedupe(ents, "VM test KAT tree")
    _check_common(ents, "VM test KAT tree")
    old, new = {p: (m, x) for p, m, x in _VM_ENTRIES}, {p: (m, x) for p, m, x in ents}
    extra = set(new) - set(old)
    assert extra == set(KAT_FILES) and all(new[p][0] == m for p, m in KAT_FILES.items()), sorted(extra)
    for p in old:
        assert new[p] == ((old[p][0], old[p][1] + KAT_RC) if p == "gbstage/etc/init/gb.rc" else old[p]), p
    assert not any(any(s in p for s in _VM_FORBIDDEN) for p in new), "VM test KAT tree: dGPU parts"
    cpio = cpio_newc(ents)
    if kat_dir is None:
        _VMKAT.append(cpio)
    print(f"VM test KAT tree: {len(extra)} files added to the VM tree", flush=True)
    return cpio

# ---- test-only trees of the W4 rehearsal (dgpu\w4\VM_UPGRADE.md 2.3, GATEKEEPER.md 9.2/9.5) ----
# --vm-test-rehearsal (build_usb.py) boots EXACTLY INJECT_CPIO, the tree `GB_WRITE_LAPIS=1 build_esp.py` writes into
# lapis.bin; its gb/inject.gz sha256 (vm_tree_record) is what the user-VM preview must match (verify_image
# --expect-inject-sha256). --vm-test-nogk boots that tree minus exactly the Gatekeeper trio: the stand-in for a pre-W4
# ESP in the fix-forward / rollback-after-clear tests, on the throwaway rehearsal disk only.
GK_NOGK_DIRS = ("gbstage/etc/vintf", "gbstage/etc/vintf/manifest")    # exist only for the fragment

def vm_tree_record(cpio=None):
    """The identity of a VM tree: '<sha256>  gb/inject.gz' (the gzip is deterministic, mtime 0) + the cpio's sha256."""
    cpio = INJECT_CPIO if cpio is None else cpio
    return (f"{_hl.sha256(inject_gz(cpio)).hexdigest()}  gb/inject.gz\n"
            f"{_hl.sha256(cpio).hexdigest()}  gb/inject.cpio (uncompressed)\n").encode()

_VMNOGK = []
def vm_test_nogk_cpio():
    """Test-only VM tree: the shipped VM tree without the software Gatekeeper (no binary, no VINTF fragment, no rc
    block; the Trusty masks stay), everything else byte-identical. Never written to lapis.bin; build_usb.py --vm-test
    --vm-test-nogk only (output below D:\\gb-w4test\\)."""
    if _VMNOGK:
        return _VMNOGK[0]
    drop = {"gbstage/" + GK_BIN_DEST, "gbstage/" + GK_VINTF_DEST} | set(GK_NOGK_DIRS)
    ents = [(p, m, (x.replace(GK_RC_BLOCK, b"", 1) if p == "gbstage/etc/init/gb.rc" else x))
            for p, m, x in _VM_ENTRIES if p not in drop]
    _check_common(ents, "VM test no-Gatekeeper tree")
    _check_gatekeeper(ents, "gbstage", "VM test no-Gatekeeper tree", present=False)
    old, new = {p: (m, x) for p, m, x in _VM_ENTRIES}, {p: (m, x) for p, m, x in ents}
    assert set(old) - set(new) == drop and not set(new) - set(old), sorted(set(old) ^ set(new))
    for p in new:
        want = (old[p][0], old[p][1].replace(GK_RC_BLOCK, b"", 1)) if p == "gbstage/etc/init/gb.rc" else old[p]
        assert new[p] == want, p
    _VMNOGK.append(cpio_newc(ents))
    print(f"VM test no-Gatekeeper tree: {len(drop)} entries and the gatekeeper rc block left out", flush=True)
    return _VMNOGK[0]

BOOTCONFIG_EXTRA = (
 'androidboot.force_normal_boot = "1"\n'
 'androidboot.verifiedbootstate = "orange"\n'
 'androidboot.vbmeta.device_state = "unlocked"\n'
 'androidboot.selinux = "permissive"\n'
 'androidboot.boot_source = "internal"\n'
 'androidboot.hypervisor.protected_vm.supported = "0"\n'
 'androidboot.first_stage_console = "1"\n'
 # androidboot.boot_devices is NOT set here: it differs per boot entry (VMware NVMe, USB stick, internal NVMe), so each
 # systemd-boot entry passes it on the kernel command line (fs_mgr falls back to the cmdline when bootconfig lacks it).
).encode()
def bootconfig(slot, vbc):
    if vbc and not vbc.endswith(b"\n"): vbc += b"\n"
    return vbc + f'androidboot.slot_suffix = "_{slot}"\n'.encode() + BOOTCONFIG_EXTRA
BOOTCONFIG = bootconfig(SLOT, VBC)

_GZ = {}
def inject_gz(cpio):
    """gzip of the gb cpio, zero-padded to 4 bytes (= the initrd's first segment and the ESP's /gb/inject.gz)."""
    k = id(cpio)
    if k not in _GZ:
        gz = bytearray(gzip.compress(bytes(cpio), 9, mtime=0))
        while len(gz) % 4: gz.append(0)
        _GZ[k] = bytes(gz)
    return _GZ[k]

def slot_stamp(gz, slot):
    """CRC32 over the initrd's inputs, same order as gb_slotsync.c (its /gb/slot_<x>.stamp)."""
    generic, vendor, vbc = (GENERIC, VENDOR, VBC) if slot == SLOT else slot_ramdisks(slot)
    c = 0
    for part in (gz, BOOTCONFIG_EXTRA, generic, vendor, vbc.rstrip(b"\0")):
        c = zlib.crc32(part, c)
    return f"{c:08x}\n".encode()

def make_initrd(inject=None, slot=None):
    # gzip the (uncompressed) injected cpio segment to fit the 64 MiB ESP; the kernel's
    # initramfs loader decompresses it (RD_GZIP=y), then the lz4 vendor/generic segments.
    slot = slot or SLOT
    generic, vendor, vbc = (GENERIC, VENDOR, VBC) if slot == SLOT else slot_ramdisks(slot)
    bc = bootconfig(slot, vbc)
    blob = bytearray(inject_gz(INJECT_CPIO if inject is None else inject))
    blob += vendor; blob += generic
    while len(blob) % 4: blob.append(0)
    size = len(bc); csum = sum(bc) & 0xffffffff
    blob += bc + struct.pack("<II", size, csum) + b"#BOOTCONFIG\n"
    return bytes(blob)

# kvm.enable_virt_at_load=0: the built-in KVM otherwise does VMXON on every vCPU at boot, so VMware must run the whole
# guest as a nested hypervisor (vhv) even though no VM ever runs; VMX now turns on only if a VM is actually created.
CMDLINE = ("rdinit=/gbinit root=/dev/ram0 kvm-intel.pkvm=0 kvm.enable_virt_at_load=0 intel_iommu=igfx_off 8250.nr_uarts=4 console=ttyS0,115200 console=tty0 "
 "earlycon=uart8250,io,0x3f8,115200 ignore_loglevel printk.devkmsg=on loglevel=8 panic=0 "
 "firmware_class.path=/vendor/firmware binder.impl=c proc_mem.force_override=ptrace "
 "sched_proxy_exec=1 cgroup_disable=pressure console_msg_format=syslog log_buf_len=1M "
 "video=Virtual-1:" + DISPLAY.decode() + "e "
 "ia32_emulation=false vsyscall=none bootconfig")

# Per-target command line: hardware mode for gbinit + the disk that holds this install's Android partitions.
VM_BOOT_DEVICES = "pci0000:00/0000:00:15.2"            # VMware virtual NVMe
CMDLINE_VM = CMDLINE + " gb.hw=vm androidboot.boot_devices=" + VM_BOOT_DEVICES

def slot_files(cmdline, inject_cpio, title, default=None, timeout=3, native=False):
    """ESP files for systemd-boot: entry gb-<slot> + initrd-<slot>.img for the boot slot (the default). The other
    slot's entry is created in the guest by gb_slotsync when an OTA completes into it (from /gb/inject.gz,
    /gb/bootconfig_extra and that slot's partitions); a stale entry for a slot that was never booted is not offered.
    native=True (USB stick, internal/dual-boot, ISO objects): also the NVIDIA test entry gb-<slot>-nvtest
    (INTERFACES 16; never the default) and the dGPU payload ESP:/gb/dgpu-<krel>.erofs + .sha256 (INTERFACES 6.1)."""
    assert "gb.nouveau=" not in cmdline and "gb.gfxdriver=" not in cmdline, "the default entry never selects NVIDIA"
    default = default or SLOT
    slots = [default]
    gz = inject_gz(inject_cpio)
    files = {
        "EFI/BOOT/BOOTX64.EFI": SDBOOT,
        "loader/loader.conf": f"default gb-{default}\ntimeout {timeout}\nconsole-mode max\neditor yes\n".encode(),
        "vmlinuz": KERNEL,
        "gb/inject.gz": gz,
        "gb/bootconfig_extra": BOOTCONFIG_EXTRA,
    }
    def entry(x, suffix, opts):     # the title keeps "(slot X)" before the suffix: gb_slotsync's rewrite keys on it
        return (f"title   {title} (slot {x.upper()}){suffix}\nlinux   /vmlinuz\n"
                f"initrd  /initrd-{x}.img\noptions {opts}\n").encode()
    for x in slots:
        files[f"loader/entries/gb-{x}.conf"] = entry(x, "", cmdline)
        if native:
            files[f"loader/entries/gb-{x}-nvtest.conf"] = entry(x, " [NVIDIA test]", cmdline + " gb.nouveau=test")
        files[f"initrd-{x}.img"] = make_initrd(inject_cpio, x)
        files[f"gb/slot_{x}.stamp"] = slot_stamp(gz, x)
    if native:
        p = dgpu_payload()
        assert p["sha_line"].encode() in inject_cpio, "native=True needs the native cpio of this payload"
        files["gb/" + p["name"]] = p["image"]
        files["gb/" + p["sha_name"]] = p["sha_line"].encode()
    return files

MiB = 1 << 20
def native_min_free(files):
    """Free ESP space a native ESP must keep: the other slot's initrd (gb_slotsync builds it after an OTA, + 16 MiB
    margin) plus gb_bootlog's 128 MiB floor."""
    return max(len(v) for k, v in files.items() if k.startswith("initrd-")) + 144 * MiB

# ---------------- FAT32 writer (from tools/build_iso.py) ----------------
class Node:
    def __init__(self, name, data=None):
        self.name, self.data, self.children = name, data, ({} if data is None else None)
        self.cluster = 0
    @property
    def is_dir(self): return self.children is not None
def build_tree(files):
    root = Node('')
    for path, data in files.items():
        parts = path.split('/'); cur = root
        for d in parts[:-1]: cur = cur.children.setdefault(d, Node(d))
        cur.children[parts[-1]] = Node(parts[-1], data)
    return root
def lfn_checksum(sn):
    s = 0
    for c in sn: s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s
def short_alias(name, used):
    base, _, ext = name.rpartition('.') if '.' in name else (name, '', '')
    clean = lambda s: re.sub(r"[^A-Z0-9_$~!#%&\-{}()@'`^]", '', s.upper())
    b, e = clean(base), clean(ext)[:3]
    if re.fullmatch(r'[A-Z0-9_\-]{1,8}', b) and name == name.upper() and len(ext) <= 3 and b + ('.' + e if e else '') == name:
        return name.upper(), False
    n = 1
    while True:
        tail = '~%d' % n; cand = (b[:8 - len(tail)] + tail)
        full = cand + ('.' + e if e else '')
        if full not in used: return full, True
        n += 1
class Fat32:
    DATE = ((2026 - 1980) << 9) | (10 << 5) | 11
    def __init__(self, total_bytes, label='GBOOK_ESP', spc=1):
        self.spc, self.label = spc, label.ljust(11)[:11]
        self.total = total_bytes // 512; self.res = 32; self.nf = 2
        fz = 0
        for _ in range(50):
            clusters = (self.total - self.res - self.nf * fz) // spc
            nz = ((clusters + 2) * 4 + 511) // 512
            if nz == fz: break
            fz = nz
        self.fatsz, self.clusters = fz, clusters
        assert clusters >= 65525, 'not enough clusters for FAT32'
        self.csize = spc * 512
        self.fat = [0] * (clusters + 2); self.fat[0] = 0x0FFFFFF8; self.fat[1] = 0x0FFFFFFF
        self.data = bytearray(clusters * self.csize); self.nxt = 2
    def alloc(self, nbytes):
        n = max(1, (nbytes + self.csize - 1) // self.csize); first = self.nxt
        assert first + n <= self.clusters + 2, 'FAT image full'
        for i in range(n): self.fat[first + i] = first + i + 1
        self.fat[first + n - 1] = 0x0FFFFFFF; self.nxt += n
        return first
    def put(self, cl, data): o = (cl - 2) * self.csize; self.data[o:o + len(data)] = data
    def dir_entries(self, node, parent_cluster):
        ents = []
        if node.cluster != 2:
            for nm, cl in ((b'.          ', node.cluster), (b'..         ', parent_cluster if parent_cluster != 2 else 0)):
                ents.append(self.sfn(nm, 0x10, cl, 0))
        used = set()
        for name in sorted(node.children, key=lambda s: s.lower()):
            ch = node.children[name]; alias, need_lfn = short_alias(name, used); used.add(alias)
            b, _, e = alias.partition('.'); sn = (b.ljust(8) + e.ljust(3)).encode('ascii')
            if need_lfn:
                u = name.encode('utf-16le') + b'\0\0'; u += b'\xff\xff' * ((-len(u) // 2) % 13)
                cs = lfn_checksum(sn); chunks = [u[i:i + 26] for i in range(0, len(u), 26)]
                for i, c in reversed(list(enumerate(chunks, 1))):
                    seq = i | (0x40 if i == len(chunks) else 0)
                    ents.append(bytes([seq]) + c[0:10] + b'\x0f\x00' + bytes([cs]) + c[10:22] + b'\x00\x00' + c[22:26])
            ents.append(self.sfn(sn, 0x10 if ch.is_dir else 0x20, ch.cluster, 0 if ch.is_dir else len(ch.data)))
        return b''.join(ents)
    def sfn(self, sn, attr, cl, size):
        return (sn + bytes([attr, 0, 0]) + struct.pack('<HHHHHHHI', 0, self.DATE, self.DATE, cl >> 16, 0, self.DATE, cl & 0xFFFF, size))
    def layout(self, node, parent=0):
        n_ent = 0 if node.cluster == 2 else 2
        used = set()
        for name in node.children:
            _, need = short_alias(name, used); used.add(short_alias(name, used)[0])
            n_ent += 1 + ((len(name) + 1 + 12) // 13 if need else 0)
        node.dirbytes = max(32, n_ent * 32)
    def write_tree(self, root):
        root.cluster = self.alloc(0); assert root.cluster == 2
        order = [(root, 0)]; i = 0
        while i < len(order):
            node, par = order[i]; i += 1
            self.layout(node)
            if node is not root: node.cluster = self.alloc(node.dirbytes)
            elif node.dirbytes > self.csize: raise SystemExit('root dir too big')
            for nm, ch in node.children.items():
                if ch.is_dir: order.append((ch, node.cluster))
                else: ch.cluster = self.alloc(len(ch.data)); self.put(ch.cluster, ch.data)
        for node, par in order:
            raw = self.dir_entries(node, par); self.put(node.cluster, raw.ljust(max(len(raw), 0)))
    def image(self):
        bs = bytearray(512)
        bs[0:3] = b'\xeb\x58\x90'; bs[3:11] = b'GBOOKISO'
        struct.pack_into('<HBHBHHBHHHII', bs, 11, 512, self.spc, self.res, self.nf, 0, 0, 0xF8, 0, 63, 255, 0, self.total)
        struct.pack_into('<IHHIHH', bs, 36, self.fatsz, 0, 0, 2, 1, 6)
        bs[64] = 0x80; bs[66] = 0x29; struct.pack_into('<I', bs, 67, 0x20261011); bs[71:82] = self.label.encode(); bs[82:90] = b'FAT32   '
        bs[510:512] = b'\x55\xaa'
        fsi = bytearray(512); struct.pack_into('<I', fsi, 0, 0x41615252); struct.pack_into('<I', fsi, 484, 0x61417272)
        struct.pack_into('<II', fsi, 488, self.clusters - (self.nxt - 2), self.nxt); struct.pack_into('<I', fsi, 508, 0xAA550000)
        fatb = struct.pack('<%dI' % len(self.fat), *self.fat)
        fatb += b'\0' * (self.fatsz * 512 - len(fatb))
        res = bytearray(self.res * 512); res[0:512] = bs; res[512:1024] = fsi; res[6 * 512:7 * 512] = bs; res[7 * 512:8 * 512] = fsi
        out = bytes(res) + fatb * self.nf + bytes(self.data)
        return out.ljust(self.total * 512, b'\0')[:self.total * 512]

def make_esp(files, size=ESP_BYTES, min_free=0):
    """FAT32 image of `size` bytes holding `files` (path -> bytes), with at least `min_free` bytes left free."""
    fat = Fat32(size, spc=1)
    fat.write_tree(build_tree(files))
    img = fat.image()
    free = (fat.clusters - (fat.nxt - 2)) * fat.csize
    assert len(img) == size and free >= min_free, (len(img), size, free, min_free)
    print(f"ESP built: used={(fat.nxt-2)*fat.csize/2**20:.1f} MiB free={free/2**20:.1f} MiB of {size/2**20:.0f} MiB, "
          f"{fat.clusters:,} clusters" + (f" (min_free {min_free/2**20:.1f} MiB)" if min_free else ""), flush=True)
    return img

def vm_min_free(files):
    """(W4) Free space a VM ESP must keep: room for the other slot's initrd that gb_slotsync builds after an OTA, plus a
    16 MiB margin (no boot-log floor in the VM). verify_image V1 checks the same for every VM kind."""
    return max(len(v) for k, v in files.items() if k.startswith("initrd-")) + 16 * MiB

if __name__ == "__main__":       # build_usb.py imports this module for the shared pieces and builds its own ESP
    FILES = slot_files(CMDLINE_VM, INJECT_CPIO, "Googlebook lapis x86")
    if os.environ.get("GB_NATIVE_TEST"):
        # One-off VMware boot of the NATIVE tree as the default entry (no display driver in VMware, so it does not
        # reach a desktop): checks gbinit module loading against lapis's vendor modules, the late pass, the native rc
        # and services, via serial0.log (console kept). gb_slotsync resets the default to gb-<slot> if it gets that far;
        # a normal rebuild drops this entry.
        ntest = (f"{CMDLINE} gb.hw=native androidboot.boot_devices={VM_BOOT_DEVICES} "
                 f"firmware_class.path=/odm/firmware,/vendor/firmware snd_intel_dspcfg.dsp_driver=3")
        # the native initrd (Surface firmware, camera stack, nouveau) no longer fits next to the VM entry in the
        # 192 MiB VM ESP: this one-off ESP holds only the test entry (the next normal build restores the VM entry)
        FILES = {"EFI/BOOT/BOOTX64.EFI": SDBOOT, "vmlinuz": KERNEL,
                 "loader/loader.conf": b"default gb-ntest\ntimeout 3\nconsole-mode max\neditor yes\n",
                 "loader/entries/gb-ntest.conf": (f"title   Googlebook native tree (VM test)\nlinux   /vmlinuz\n"
                                                  f"initrd  /initrd-ntest.img\noptions {ntest}\n").encode(),
                 "initrd-ntest.img": make_initrd(native_cpio(), SLOT)}
        print("NATIVE TEST entry is the default for this build", flush=True)
        ESP = make_esp(FILES)
    else:
        ESP = make_esp(FILES, min_free=vm_min_free(FILES))     # (W4) an OTA's second initrd must still fit
        print("VM tree:", vm_tree_record().decode().replace("\n", "; "), flush=True)
    print("CMDLINE:", CMDLINE_VM, flush=True)
    print(f"boot slot: {SLOT}; entries: {sorted(k for k in FILES if k.startswith('loader/entries'))}", flush=True)
    # write ESP into lapis.bin's EFI-SYSTEM partition (only that region); the guard is checked again right before it
    if _lapis_write_refused():
        sys.exit(f"build_esp.py: refused: {_lapis_write_refused()}")
    why = _pin_path_refused(ESP)                        # (W6 prep, SA-7) after the VM-off guard, before the write;
    if why:                                             # (W7, W6-S7) once: one read of lapis.bin's ESP, one report
        sys.exit(f"build_esp.py: refused: {why}")
    with open(LAPIS, "r+b") as f:
        f.seek(ESP_OFFSET)
        f.write(ESP)
    print(f"wrote {ESP_BYTES >> 20} MiB ESP into lapis.bin at offset {ESP_OFFSET}", flush=True)
