"""Build a 64 MiB EFI System Partition (systemd-boot + GKI bzImage + reconstructed initrd)
and write it into lapis.bin's EFI-SYSTEM partition so the single NVMe disk boots directly."""
import os, re, struct, sys, gzip, zlib

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
BOOT = os.path.join(HERE, "boot")
LAPIS = os.path.join(HERE, "lapis.bin")
ESP_OFFSET = 142606336          # EFI-SYSTEM partition start (bytes)
ESP_BYTES  = 201326592          # 192 MiB (enlarged into unused KERN-A via grow_esp.py, for vulkan.pastel)

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
          b"HW=; USB=0\n"
          b"for a in $(/busybox cat /proc/cmdline); do case \"$a\" in gb.hw=*) HW=${a#gb.hw=};; gb.boot=usb) USB=1;; esac; done\n"
          b"if [ -z \"$HW\" ]; then\n"
          b"  case \"$(/busybox cat /sys/class/dmi/id/sys_vendor 2>/dev/null)\" in *VMware*) HW=vm;; *) HW=native;; esac\n"
          b"fi\n"
          b"SRC=/gbstage\n"
          b"[ \"$HW\" = native ] && [ -d /gbnative ] && SRC=/gbnative\n"
          b"/busybox umount /sys\n"
          b"/busybox umount /proc\n"
          b"/busybox mount -t tmpfs tmpfs /odm\n"
          b"/busybox cp -a $SRC/. /odm/\n"
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
# UI scale + animations (gb_ui service, on boot_completed). Density is per user, so wait for the signed-in user to take
# over from the headless system user 0 first. 160 dpi * W/1280 keeps a 1280 dp wide desktop at any width >= 1920
# (2560 -> 320 = 2 physical px per dp); smaller modes fall back to the panel default (ro.sf.lcd_density). The
# animation/blur settings are set once (marker), so later changes in Settings / Developer options stick.
GB_UI_SH = (b"#!/system/bin/sh\n"
          b"exec >/dev/kmsg 2>&1\n"
          b"n=0; while [ \"$(/system/bin/am get-current-user 2>/dev/null)\" = 0 ] && [ $n -lt 30 ]; do /system/bin/sleep 2; n=$((n+1)); done\n"
          b"W=$(/system/bin/wm size | /system/bin/toybox grep -o 'Physical size: [0-9]*' | /system/bin/toybox grep -o '[0-9]*$')\n"
          # Write the user's own setting first: `wm density` alone targets WindowManager's current user, which can still be
          # 0 while the switch is in flight (then the switch reloads the new user's unset value -> back to 260).
          b"U=$(/system/bin/am get-current-user)\n"
          b"cur=$(/system/bin/settings get --user $U secure display_density_forced)\n"
          b"if [ \"${W:-0}\" -ge 1920 ]; then D=$((W / 8))\n"
          # native Surface Book 3 (3000x2000, 267 ppi): 320 dpi = 1500x1000 dp, like Windows at 200%
          b"  [ \"$(cat /odm/etc/gb_hw 2>/dev/null)\" = native ] && D=$((W * 16 / 150))\n"
          b"  [ \"$cur\" = \"$D\" ] || { /system/bin/settings put --user $U secure display_density_forced $D; /system/bin/wm density $D; }\n"
          b"else D=default; [ \"$cur\" = null ] || { /system/bin/settings delete --user $U secure display_density_forced; /system/bin/wm density reset; }; fi\n"
          # Stock Googlebook launcher = HOME of the signed-in user. Never user 0: the headless system user's HOME is the
          # login screen (com.google.android.desktop.identity.login/.LoginLauncherActivity).
          b"if [ -n \"$U\" ] && [ \"$U\" != 0 ]; then\n"
          b"  /system/bin/settings put --user $U secure user_setup_complete 1\n"
          b"  case \"$(/system/bin/cmd role get-role-holders --user $U android.app.role.HOME 2>&1)\" in\n"
          b"    *com.google.android.apps.nexuslauncher*) ;;\n"
          b"    *) /system/bin/cmd role add-role-holder --user $U android.app.role.HOME com.google.android.apps.nexuslauncher 2>&1 ;;\n"
          b"  esac\n"
          b"fi\n"
          b"M=/data/local/gb_anim.v1.done\n"
          b"if [ ! -f $M ]; then\n"
          b"  for k in window_animation_scale transition_animation_scale animator_duration_scale; do /system/bin/settings put global $k 1.0; done\n"
          b"  /system/bin/settings put global disable_window_blurs 0\n"
          b"  /system/bin/toybox touch $M\n"
          b"fi\n"
          b"echo \"GB_UI user=$U width=$W density=$D (was $cur) anim=$(/system/bin/settings get global animator_duration_scale)"
          b" window_blurs_off=$(/system/bin/settings get global disable_window_blurs) sf_blurs_off=$(/system/bin/getprop persist.sys.sf.disable_blurs)"
          b" home=$(/system/bin/cmd role get-role-holders --user $U android.app.role.HOME 2>&1)\"\n")
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
NATIVE_MODS = _tree(NATIVE_MOD_DIR)

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
# partition (FAT32), readable from Windows: <boot partition>\gblog\bootN-tSECONDS.txt.
GB_BOOTLOG_SH = (b"#!/system/bin/sh\n"
                 b"P=EFI-SYSTEM\n"
                 b"for a in $(cat /proc/cmdline); do case $a in gb.logpart=*) P=${a#gb.logpart=};; esac; done\n"
                 b"D=/dev/block/by-name/$P; M=/mnt/gb_esp; N=\n"
                 b"mkdir -p $M\n"
                 b"for t in 40 90 180 420; do\n"
                 b"  while [ $(cut -d. -f1 /proc/uptime) -lt $t ]; do sleep 5; done\n"
                 b"  mount -t vfat -o rw,nosuid,nodev,noexec $D $M || { echo \"gb_bootlog: cannot mount $D\" > /dev/kmsg; exit 1; }\n"
                 b"  mkdir -p $M/gblog\n"
                 b"  if [ -z \"$N\" ]; then N=$(( $(cat $M/gblog/bootcount 2>/dev/null || echo 0) + 1 )); echo $N > $M/gblog/bootcount; fi\n"
                 b"  F=$M/gblog/boot$N-t$t.txt\n"
                 b"  { echo \"== uptime $(cat /proc/uptime) boot_completed=$(getprop sys.boot_completed) hw=$(cat /odm/etc/gb_hw)\"\n"
                 b"    echo '== cmdline'; cat /proc/cmdline\n"
                 b"    echo '== gbinit module loading'; cat /odm/gbinit_modules.log\n"
                 b"    echo '== dmi'; cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name\n"
                 b"    echo '== block'; ls -l /sys/block; ls -l /dev/block/by-name\n"
                 b"    echo '== input devices'; cat /proc/bus/input/devices\n"
                 b"    echo '== iio'; for i in /sys/bus/iio/devices/*/name; do echo \"$i $(cat $i)\"; done\n"
                 b"    echo '== asound'; cat /proc/asound/cards /proc/asound/pcm; tinymix -D 0 2>&1 | head -n 80\n"
                 b"    echo '== gpu'; dumpsys gpu 2>&1 | head -n 60; cmd gpu vkjson 2>&1 | head -c 4000\n"
                 b"    echo '== modules'; cat /proc/modules\n"
                 b"    echo '== drm/input/power'; ls -l /dev/dri /dev/input; for u in /sys/class/power_supply/*/uevent; do echo $u; cat $u; done\n"
                 b"    echo '== pci'; for d in /sys/bus/pci/devices/*; do echo \"$d $(cat $d/vendor):$(cat $d/device) driver=$(basename $(readlink $d/driver) 2>/dev/null)\"; done\n"
                 b"    echo '== getprop'; getprop\n"
                 b"    echo '== dmesg'; dmesg\n"
                 b"    echo '== logcat'; logcat -d -b all -v threadtime | tail -n 6000\n"
                 b"    echo '== dumpsys SurfaceFlinger'; dumpsys SurfaceFlinger | head -n 120\n"
                 b"    echo '== dumpsys input'; dumpsys input | head -n 200\n"
                 b"  } > $F 2>&1\n"
                 b"  sync; umount $M\n"
                 b"done\n")

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
         b"\n"
         b"on property:sys.boot_completed=1\n"
         b"    stop android.system.desktop.security.gscd\n"
         b"    start gb_provision\n"
         b"    start gb_ui\n"
         b"    start gb_dexopt\n")

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
                         b"ro.vendor.hwc.use_overlay_planes=false\n")

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
    ('gbstage/etc/fstab.android-desktop', 0o100644, FSTAB),
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
def _add_native(path, data, mode=0o100644):
    """Add gbnative/<path>, creating every parent directory entry first (the initramfs unpacker needs them)."""
    full = "gbnative/" + path
    have = {p for p, _, _ in _entries}
    parts = full.split("/")
    for i in range(1, len(parts)):
        d = "/".join(parts[:i])
        if d not in have:
            _entries.append((d, 0o040755, None)); have.add(d)
    if data is None:
        if full not in have:
            _entries.append((full, 0o040755, None))
    else:
        _entries.append((full, mode, data))

# Native components built and reviewed separately under native/<name> (GB_BUILD) (iptsd touch/pen, gb_power battery/
# detach/dGPU/profile, audio, ...). Each manifest inject\native\components\<name>.json lists its files for the native
# tree ({src, dest, mode}) and its rc fragments, appended to the native gb.rc. Their kernel modules go through
# stage_kernel.py (inject\native\modules), not through the manifests.
import json as _json, glob as _glob
NATIVE_COMPONENTS, _COMP_FILES, _COMP_RC = [], [], b""
os.environ.setdefault("GB_HOME", HERE)   # manifest paths may use ${GB_HOME} and ${GB_BUILD}
os.environ.setdefault("GB_BUILD", HERE if os.path.isdir(os.path.join(HERE, "native")) else r"C:\GB-build")
for _mf in sorted(_glob.glob(os.path.join(NATIVE_DIR, "components", "*.json"))):
    _m = _json.load(open(_mf, encoding="utf-8"))
    for _e in _m.get("files", []):
        _mode = int(str(_e["mode"]).replace("0o", ""), 8)
        _COMP_FILES.append((_e["dest"], open(os.path.expandvars(_e["src"]), "rb").read(), _mode if _mode > 0o7777 else 0o100000 | _mode))
    for _r in _m.get("rc_files", []):
        _COMP_RC += b"\n# ---- component " + _m["name"].encode() + b" ----\n" + open(os.path.expandvars(_r), "rb").read()
    NATIVE_COMPONENTS.append(_m["name"])
print(f"native components: {', '.join(NATIVE_COMPONENTS) or 'none'} ({len(_COMP_FILES)} files)", flush=True)

for _p, _d, _m in [
        ("gb_modules.sh", GB_MODULES_SH, 0o100755), ("gb_bootlog.sh", GB_BOOTLOG_SH, 0o100755),
        ("gb_ui.sh", GB_UI_SH, 0o100755), ("gb_dexopt.sh", GB_DEXOPT_SH, 0o100755),
        ("gb_noop.sh", b"#!/system/bin/sh\nexit 0\n", 0o100755), ("gb_sleeper.sh", GB_SLEEPER, 0o100755), ("gb_fw_stub.sh", GB_FW_STUB, 0o100755),
        ("empty_vintf.xml", EMPTY_VINTF, 0o100644),
        ("etc/fstab.android-desktop", FSTAB, 0o100644), ("etc/se_manifest.xml", SEMAN, 0o100644),
        ("etc/init/gb.rc", GB_RC_NATIVE + GB_SLOTSYNC_RC + _COMP_RC, 0o100644), ("etc/build.prop", ODM_BUILD_PROP_NATIVE, 0o100644),
        ("etc/selinux/odm_sepolicy.cil", ODMCIL, 0o100644), ("etc/selinux/precompiled_sepolicy", ODMPRE, 0o100644),
        ("vup/bin/hw/android.hardware.security.keymint-service", KM_BIN, 0o100755),
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
        [("vup/bin/gb_slotsync", SLOTSYNC_BIN, 0o100755)] if SLOTSYNC_BIN else []) + _COMP_FILES:
    _add_native(_p, _d, _m)
_add_native("vup/etc", None)
for _n, _d in KMLIBS:
    _add_native("vup/lib64/" + _n, _d)
for _n, _d in NATIVE_FW:
    _add_native("firmware/" + _n, _d)
_add_native("lib/modules", None)
for _n, _d in NATIVE_MODS:
    _add_native("lib/modules/" + _n, _d)
print(f"native tree: {len(NATIVE_FW)} firmware files, {len([n for n, _ in NATIVE_MODS if n.endswith('.ko')])} modules", flush=True)

# One initrd per target: the VM boot carries only /gbstage, native boots only /gbnative (67 MB of Surface firmware the
# VM never needs). gbinit falls back to /gbstage if /gbnative is missing.
INJECT_CPIO = cpio_newc([e for e in _entries if not e[0].startswith("gbnative")])
INJECT_CPIO_NATIVE = cpio_newc([e for e in _entries if not e[0].startswith("gbstage")])

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

def slot_files(cmdline, inject_cpio, title, default=None, timeout=3):
    """ESP files for systemd-boot: entry gb-<slot> + initrd-<slot>.img for the boot slot (the default). The other
    slot's entry is created in the guest by gb_slotsync when an OTA completes into it (from /gb/inject.gz,
    /gb/bootconfig_extra and that slot's partitions); a stale entry for a slot that was never booted is not offered."""
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
    for x in slots:
        files[f"loader/entries/gb-{x}.conf"] = (f"title   {title} (slot {x.upper()})\nlinux   /vmlinuz\n"
                                               f"initrd  /initrd-{x}.img\noptions {cmdline}\n").encode()
        files[f"initrd-{x}.img"] = make_initrd(inject_cpio, x)
        files[f"gb/slot_{x}.stamp"] = slot_stamp(gz, x)
    return files

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

def make_esp(files, size=ESP_BYTES):
    """FAT32 image of `size` bytes holding `files` (path -> bytes)."""
    fat = Fat32(size, spc=1)
    fat.write_tree(build_tree(files))
    img = fat.image()
    assert len(img) == size, (len(img), size)
    print(f"ESP built: used={(fat.nxt-2)*fat.csize/2**20:.1f} MiB of {size/2**20:.0f} MiB, {fat.clusters:,} clusters", flush=True)
    return img

if __name__ == "__main__":       # build_usb.py imports this module for the shared pieces and builds its own ESP
    FILES = slot_files(CMDLINE_VM, INJECT_CPIO, "Googlebook lapis x86")
    if os.environ.get("GB_NATIVE_TEST"):
        # One-off VMware boot of the NATIVE tree as the default entry (no display driver in VMware, so it does not
        # reach a desktop): checks gbinit module loading against lapis's vendor modules, the late pass, the native rc
        # and services, via serial0.log (console kept). gb_slotsync resets the default to gb-<slot> if it gets that far;
        # a normal rebuild drops this entry.
        ntest = (f"{CMDLINE} gb.hw=native androidboot.boot_devices={VM_BOOT_DEVICES} "
                 f"firmware_class.path=/odm/firmware,/vendor/firmware snd_intel_dspcfg.dsp_driver=3")
        FILES["loader/entries/gb-ntest.conf"] = (f"title   Googlebook native tree (VM test)\nlinux   /vmlinuz\n"
                                                 f"initrd  /initrd-ntest.img\noptions {ntest}\n").encode()
        FILES["initrd-ntest.img"] = make_initrd(INJECT_CPIO_NATIVE, SLOT)
        FILES["loader/loader.conf"] = FILES["loader/loader.conf"].replace(f"default gb-{SLOT}".encode(), b"default gb-ntest")
        print("NATIVE TEST entry is the default for this build", flush=True)
    ESP = make_esp(FILES)
    print("CMDLINE:", CMDLINE_VM, flush=True)
    print(f"boot slot: {SLOT}; entries: {sorted(k for k in FILES if k.startswith('loader/entries'))}", flush=True)
    # write ESP into lapis.bin's EFI-SYSTEM partition (only that region)
    with open(LAPIS, "r+b") as f:
        f.seek(ESP_OFFSET)
        f.write(ESP)
    print(f"wrote {ESP_BYTES >> 20} MiB ESP into lapis.bin at offset {ESP_OFFSET}", flush=True)
