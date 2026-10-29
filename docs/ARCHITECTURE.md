# Architecture

## Boot chain

```
UEFI (VMware EFI, or Surface UEFI with Secure Boot off)
 └─ \EFI\BOOT\BOOTX64.EFI = systemd-boot 262.1           (ESP "EFI-SYSTEM", FAT32, written by build_esp.py)
     └─ loader/entries/gb-<slot>.conf                     default = the A/B slot to boot
        (native ESPs also: gb-<slot>-nvtest.conf "[NVIDIA test]" = the same + gb.nouveau=test; gb/dgpu-<krel>.erofs)
         linux  /vmlinuz          our GKI kernel (android17-6.18, 6.18.21, Kleaf build, EFI stub)
         initrd /initrd-<slot>.img
         options <kernel command line>  (rdinit=/gbinit ... gb.hw=vm|native androidboot.boot_devices|boot_part_uuid=...)
          └─ /gbinit (busybox sh): pick the tree, tmpfs /odm, copy, load native modules, exec /init
              └─ Android first-stage init (lapis generic ramdisk) -> super (system, vendor, product ...) -> second stage
                  └─ /odm/etc/init/gb.rc: /vendor overlay, stubs, our services
```

Google's kernel (`boot_<slot>`) is never used: lapis's GKI modules are signed with Google's key and its kernel lacks
the drivers we need (vmwgfx, Surface Aggregator, i915 ...). We rebuild the same GKI source (same KMI, so lapis's
vendor modules still load) with our patches and extra modules. Google-signed GKI modules from lapis's
`system_dlkm` are refused by our kernel ("exports protected symbol"), so every GKI-side module we need (Wi-Fi
helpers, Bluetooth, zram, USB network, ...) comes from our build too.

## The initrd

`make_initrd()` in [build_esp.py](../build_esp.py) concatenates, in this order:

1. **our cpio, gzip'd** and padded to 4 bytes: `/gbinit`, `/busybox` and one overlay tree, `/gbstage` (VM) or
   `/gbnative` (native). Each boot target gets an initrd with only its own tree (the native one carries ~70 MB of
   firmware the VM never needs).
2. **lapis's vendor ramdisk** (lz4) from `vendor_boot_<slot>`: first-stage modules and fstab.
3. **lapis's generic ramdisk** (lz4) from `init_boot_<slot>`: Android's `/init`. Later archives win, so the generic
   `/init` is the one that runs.
4. **bootconfig**: the vendor bootconfig of that slot + `androidboot.slot_suffix = "_<slot>"` + our
   `BOOTCONFIG_EXTRA` (`force_normal_boot`, `verifiedbootstate=orange`, unlocked, `selinux=permissive`, no protected
   VM), followed by size, checksum and the `#BOOTCONFIG\n` trailer the kernel looks for.

The ramdisks of a slot are read straight from the disk image's `init_boot_<x>` / `vendor_boot_<x>` partitions
([extract_slot.py](../extract_slot.py)); in the guest, [gb_slotsync](../guest/gb_slotsync.c) builds the same
initrd from the on-disk partitions after an OTA (see [OTA.md](OTA.md)).

`androidboot.boot_devices` is not in the bootconfig: each boot entry names its own disk on the command line, because
the same initrd boots from the VMware NVMe, a USB stick or the internal NVMe.

## gbinit

[`GBINIT`](../build_esp.py) runs as `rdinit` before Android:

* **hardware mode**: `gb.hw=vm|native` from the command line, else DMI (`sys_vendor` VMware -> vm);
  `gb.boot=usb` marks a USB boot.
* mounts a **tmpfs on /odm** and copies `/gbstage` or `/gbnative` into it; writes `/odm/etc/gb_hw`.
* on a USB boot, drops the `voldmanaged=usb` line from our fstab so vold does not offer to format the boot stick.
* creates **overlayfs whiteouts** (char 0:0) in `/odm/vup` that hide the Trusty/GSC-only pieces: the strongbox
  KeyMint apex, the secretkeeper VINTF fragment and rc, the Trusty Gatekeeper rc.
* **native only, opt-in** (wave 3): when the command line has both `gb.hw=native` and `gb.overlay=1`, runs
  `/gbbringup.sh` under a 40 s timeout. It mounts the boot disk's own ESP read-only and applies `gb\gb.cfg` and
  `gb\overlay\` to the tmpfs `/odm` (module options and skipped lines in `modules.load`, `build.prop` lines,
  input/audio config files), each checked against an allow-list (log: `/odm/gbinit_bringup.log`;
  [NATIVE.md](NATIVE.md#bring-up-configuration-on-the-esp-gbgbcfg)). No shipped entry has `gb.overlay=1`.
* **native only**: insmods `/odm/lib/modules/modules.load` in order (log: `/odm/gbinit_modules.log`), before ueventd
  can autoload lapis's own copies of `video`, `wmi`, DRM helpers etc. that our i915 could not link against.
* (wave 5) with the exact token `gb.vnni=0` on the command line, deletes `/odm/vnni` (the libz patch kill switch,
  [PERFORMANCE.md](PERFORMANCE.md#kill-switch)).
* `exec /init`.

## /odm and the /vendor overlay

`/odm` is a writable tmpfs holding the tree, laid out like a partition:

| path | role |
|---|---|
| `/odm/etc/init/gb.rc` | our init script (Android init language), parsed by init like any `/odm/etc/init/*.rc` |
| `/odm/etc/build.prop` | loaded after `/vendor/build.prop`, so its `ro.*` values win |
| `/odm/etc/fstab.android-desktop`, `/odm/etc/se_manifest.xml` | bind-mounted over the vendor fstab and the system_ext VINTF manifest |
| `/odm/etc/selinux/odm_sepolicy.cil` | one `typepermissive` statement for every type in lapis's policy, so every domain is permissive even where `androidboot.selinux=permissive` is not honoured (it is not: lapis's user build keeps the global mode enforcing, `getenforce` prints `Enforcing`, and every logged denial is `permissive=1`). Since wave 5 each line is packed in its own `optional` block, so a type a later build removes cannot stop the policy compile ([OTA.md](OTA.md#the-ota-gate-wave-5)), and `build_esp.py` refuses a list without the domains the image needs (`OTA_REQUIRED_PERMISSIVE`). `precompiled_sepolicy` next to it is a one-byte placeholder: with no matching `*.sha256` files, init compiles the policy from the CIL files, this one included |
| `/odm/vup/...` | **upper dir of an overlayfs mounted on /vendor** at early-init: `vup/bin/foo` adds or replaces `/vendor/bin/foo`, `vup/etc/x` -> `/vendor/etc/x` |
| `/odm/lib/modules/` | our kernel modules + `modules.load` / `modules.load.late` |
| `/odm/firmware/` | native firmware, searched first (`firmware_class.path=/odm/firmware,/vendor/firmware`) |
| `/odm/*.sh` | helper scripts started by gb.rc services |

Services run as root with `seclabel u:r:shell:s0` (permissive), output to kmsg.

### What both trees do

* **Software KeyMint**: the AOSP pure-software KeyMint 4 service + its libraries from the Android 16 emulator image,
  with the matching Android 16 `libcrypto` (the cluster smashes its stack against lapis's BoringSSL), declared in our
  `se_manifest.xml`; `keystore.module_hash.sent=true` because it lacks `setAdditionalAttestationInfo`. The native tree
  ships these libraries under private sonames (`lib<x>_gbkm16.so`, `inject/keymint/native/`), so lapis's own
  `/vendor/lib64/libcrypto.so` (needed by `libssl.so`: `wpa_supplicant`, `hostapd`) stays in place; `build_esp.py`'s
  link-closure gate (`link_closure.py`) refuses any native overlay that would leave a vendor program unable to link.
  The VM tree still overlays the stock names (no Wi-Fi in the VM).
* **Trusty/GSC stubs**: strongbox KeyMint, Weaver and the Trusty Gatekeeper are undeclared (empty VINTF fragments
  bound over theirs), secretkeeper/gatekeeper binaries replaced by sleepers, `gscd` stopped, `timberslide`,
  `udsattestation`, the charge-control HAL and `storage_metrics_service` replaced by idle sleepers (they crash-loop
  without an EC/GSC).
* **Software Gatekeeper** (wave 4): `gb_gatekeeper` (`/odm/vup/bin/hw/android.hardware.gatekeeper-service.gb`, user
  `hsm`, `class early_hal`) declares `IGatekeeper/default` and `ISharedSecret/gatekeeper` through an ODM VINTF
  fragment, so a lock-screen PIN can be set. Its state is in `/data/vendor/gatekeeper`. Not hardware-backed:
  [GATEKEEPER.md](GATEKEEPER.md).
* **libz patch** (wave 5, only when `inject/vnni/` was prepared): `/odm/vnni/<id>/` holds the 2-byte-patched system
  and vendor `libz.so`. The last block of gb.rc binds each over the original at early-init, gated on the exact
  `ro.system/vendor.build.fingerprint` it was made for, then remounts it read-only ([PERFORMANCE.md](PERFORMANCE.md)).
* **crossystem stub**: reports a healthy, successfully booted vboot slot, so the boot_control HAL can mark the slot
  successful and vold does not reboot with "Checkpoint commit failed"; `bootctl mark-boot-successful` at post-fs-data.
* **RescueParty off**, SetupWizard disabled, device provisioned, launcher made the HOME role holder (`gb_ui.sh`),
  density from the panel width, one-time AOT compile of the UI apps (`gb_dexopt.sh`).
* **firmware updater stub** for `google_desktop_firmware_updater` (OTA postinstall), `preloads_copy.sh` no-op.
* **gb_slotsync**, the A/B slot follower.

### VM tree only (`/gbstage`)

vmwgfx (+ `drm_ttm_helper`, `efidrm`), `psmouse` with vmmouse, `vmxnet3`, `test_power` (or ACPI `ac`/`battery` on
an older kernel); forced EDID through debugfs `edid_override`; `gb_layout` (2560x1600 preferred mode); Mesa svga as
the system EGL (`ro.hardware.egl=mesa`, SkiaGL) with patched minigbm; SwiftShader as the Vulkan HAL; hwcomposer and
SurfaceFlinger tuning for a VM; `gb_vmtools`, `gb_absmouse`, the AT keyboard IDC marked external, touchpad-inhibitor
stubbed; VMware HD Audio; Bluetooth features marked unavailable; `gb_console` root shell on COM2; stay-awake and
diagnostics; optional network ADB for scrcpy, together with the eth0 watchdog `gb_netwatch`.

### Native tree only (`/gbnative`)

Our module set (two passes: `gbinit` before Android, `gb_modules.sh` at early-init for modules that must link against
lapis's first-stage `mfd-core`/`platform_profile`; never a copy of a module lapis loads in its first stage, see
[NATIVE.md](NATIVE.md)), the Surface Book 3 firmware, the native `build.prop` (320 dpi, no colour pipeline in the
composer for i915, `ro.gfx.driver.0=gb.sb3.gfxdriver`), refusing stubs for every firmware/EC/SPI flashing tool and
the disk installer, `gb_bootlog` (writes `gblog\bootN-tSECONDS.txt` to the boot partition at 40/90/180/420/900 s,
since there is no serial console), and the components from `inject/native/components/*.json` (audio, iptsd, power,
hw, sensors, camera, nouveau, nvk, gfxdriver, gpu_compute, gpu_ui). Manifests list `files` for the tree, `payload`
files for the dGPU erofs, `symlinks` into the payload mount, `rc_files`/`rc_text` for `gb.rc` and native
`build.prop` lines (e.g. `ro.hardware.camera=sb3`). Nothing VMware-specific; `gb_console` is left out because the
LPSS UART carries the Surface Aggregator.

## NVIDIA dGPU (native)

The GTX 1650 in the keyboard base is never needed to boot or to draw the screen. Its stack sits beside the Intel one
([NVIDIA.md](NVIDIA.md); the normative spec is [native/dgpu/INTERFACES.md](../native/dgpu/INTERFACES.md)):

```
ESP gb/dgpu-<krel>.erofs ──copy+verify──> /data/vendor/gb/payload/dgpu.erofs ──mount──> /odm/dgpu
   firmware/nvidia/tu117/* (+ WHENCE, LICENCE)   ── bind ──> /odm/firmware/nvidia
   lib/modules/{nouveau,drm_exec,drm_gpuvm,gpu-sched,drm_ttm_helper}.ko
   lib64/hw/vulkan.nouveau.so (NVK ICD; /vendor/lib64/hw/vulkan.nouveau.so links here)
   lib64/gpu-compute/{libgb_gpu_compute.so,libgb_nvk_loader.so}

sys.boot_completed + base attached + mode auto/nvidia ──> gb_nouveau.sh auto: guards, ESP lock, marker, insmod,
   health check (30 s), clean mark (120 s)                                     (gb.nouveau=0: never)
gb_power (root, the single writer of the graphics state):
   DTX events, PCI uevents, the nouveau state  ──> vendor.gb.gfx_active / gfx_reason, render node mode,
   Settings.Global updatable_driver_* (through gb_notify), self-test start, detach decision, /dev/socket/gb_power
GbGpu (untrusted app) ── socket ──> gb_power        tile "Graphics", Settings > System > Graphics, notifications
app process with the GbGfxDriver package ──> shim vulkan.intel.so: NVK first while gfx_active=nvidia, else ANV
gb_gpu_compute (nobody) ── /dev/socket/gb_gpu_compute (HTTP) ──> llama.cpp ──> loader stub ──> NVK
```

All of these services run in permissive existing domains (`u:r:shell:s0`, apps in their own app domains). No new
SELinux type is added.

## Kernel command lines

VM (`CMDLINE_VM`): `rdinit=/gbinit root=/dev/ram0 kvm-intel.pkvm=0 kvm.enable_virt_at_load=0 intel_iommu=igfx_off
8250.nr_uarts=4 console=ttyS0,115200 console=tty0 earlycon=... firmware_class.path=/vendor/firmware binder.impl=c
... video=Virtual-1:2560x1600e ia32_emulation=false vsyscall=none bootconfig gb.hw=vm
androidboot.boot_devices=pci0000:00/0000:00:15.2`.

Native (`build_usb.native_cmdline()`): the same minus the serial console, earlycon and the virtual display mode
(`8250.nr_uarts=4` stays since wave 3: our GKI has `CONFIG_SERIAL_8250_RUNTIME_UARTS=0`, and without the token the
LPSS UART that carries the Surface Aggregator never registers), plus `firmware_class.path=/odm/firmware,/vendor/firmware efi_pstore.pstore_disable=1
iommu.passthrough=1 reboot=pci snd_intel_dspcfg.dsp_driver=3 module_blacklist=tpm,...,spi_intel,spi_nor,mtd,gsmi
gb.logpart=EFI-SYSTEM gb.hw=native gb.boot=usb|internal androidboot.boot_part_uuid=<this disk's super GUID>`.
`boot_part_uuid` makes first-stage init use exactly one disk, so a USB stick and an internal install never mix.

## Host battery bridge (VM)

VMware forwards only the host's first battery to a guest; on the Surface Book 3 that is the clipboard battery, which
stays at 100 % while the base battery drains first.

```
Windows GetSystemPowerStatus (combined level, AC, charging)
  -> host/gb_hostbatt.exe (every 10 s; writes on change and every 2 min)
     vmrun writeVariable <vmx> guestVar gb.battery "<percent> <ac 0|1> <charging|discharging|not-charging|full>"
  -> guest: gb_vmtools reads guestinfo.gb.battery over the VMware backdoor
     -> /sys/module/test_power/parameters/* (test_battery / test_ac)
  -> Android health HAL -> status bar
```

lapis's own ACPI `battery.ko` autoloads for VMware's BAT1; `gb_battunbind.sh` unbinds it at post-fs-data so only
`test_power`'s battery is left. Natively `gb_power` does the equivalent with the two real packs
(`native/power/gb_power.c`).

## Disk images

* **VM**: `lapis.bin` is the lapis recovery image as a raw disk (`lapis.vmdk` = flat descriptor), with the ESP grown
  into the unused ChromeOS `KERN-A` partition (`grow_esp.py`, 192 MiB) and userdata grown by +28 GB
  (`grow_userdata.py`). `build_esp.py` rewrites only the ESP region.
* **USB / internal**: `build_usb.py` builds a fresh GPT: ESP (512 MiB: both slots' initrds, the dGPU payload and the
  logs), the A/B `boot`/`init_boot`/`vendor_boot`/
  `vbmeta`/`pvmfw` copies **with their GPT attribute bits** (the A/B slot state lives there), blank `misc`,
  `metadata`, `frp_persist`, `desktop_security_*`, `super` copied, blank `userdata`; fresh partition GUIDs.
  It refuses to run while the other slot has a pending (not yet successful) update.
