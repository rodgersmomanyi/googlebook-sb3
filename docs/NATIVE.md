# Native boot on the Surface Book 3

**Status: untested on hardware.** Everything native was built and checked off-device (VMware boot of the native tree,
QEMU, builder-side tests, disk-image tests). The first real boots are still to come; please report what happens.
Device by device: [DEVICE-STATUS.md](DEVICE-STATUS.md). Flashing and the first boot: [FLASHING.md](FLASHING.md).

## What changes natively

The same ESP layout and initrd format as the VM, but the boot entries pass `gb.hw=native`, so `gbinit` copies the
native tree (`/gbnative`). See [ARCHITECTURE.md](ARCHITECTURE.md) for the tree and [DRIVERS.md](DRIVERS.md) for every
device. In short:

* **Kernel:** native images run kernel build #15 (wave 3): build #14 (build #13 plus nouveau-only patches) plus the
  reworked `#UD` VNNI emulator and the opt-in i915 gamma switch. The config, KMI and exports are the same as #13, so
  every module and lapis vendor module behaves as with #13 ([kernel/K15.md](../kernel/K15.md)). The VM keeps
  whatever kernel its ESP was last built with.
* **Wave 5 additions (both trees):** the software Gatekeeper (lock-screen PIN, [GATEKEEPER.md](GATEKEEPER.md)), the
  build-gated VNNI libz patch ([PERFORMANCE.md](PERFORMANCE.md)) and the OTA gate ([OTA.md](OTA.md#the-ota-gate-wave-5));
  natively also the readiness fixes ([DEVICE-STATUS.md](DEVICE-STATUS.md), the Wave 5 group).
* **Kernel modules** (our build; lapis's Google-signed GKI modules are refused by our kernel), loaded by `gbinit`
  from `modules.load` before Android starts, in dependency order (`stage_kernel.py`, `NATIVE_EARLY`):
  Wi-Fi/Bluetooth helpers (`libarc4`, `pps_core`, `ptp`, `rfkill`, `bluetooth`, `btbcm`, `hidp`, `rfcomm`), zram,
  USB network adapters, the Surface Aggregator stack (`surface_aggregator`, `_registry`, `_hub`, `_cdev`,
  `surface_hid_core`, `surface_hid`, `surface_battery scope_device=1`, `surface_charger`, `surface_dtx`,
  `surface_gpe`, `surface_hotplug`, `hid-surface`), `soc_button_array`, `test_power`, i915 and its helpers, lapis's
  HDA codec modules, `apds9960`, ACPI `thermal`, `ipts`, and the camera stack (`v4l2-cci`, `ipu-bridge`,
  `intel_skl_int3472_*`, `ov5693`, `ov8865`, `ov7251`, `dw9719`, `intel-ipu6`, `intel-ipu6-isys`). A second pass
  (`gb_modules.sh`, `modules.load.late`) loads what must link against lapis's first-stage modules:
  `surface_platform_profile` and the HID sensor drivers. nouveau and its four helpers (`NATIVE_OPTIN`) are in
  neither list. They live in the dGPU payload `gb/dgpu-<krel>.erofs` on the ESP, together with the NVIDIA firmware,
  the NVK ICD and the compute libraries. Only the nouveau policy loads them, after `sys.boot_completed`, when the
  keyboard base is attached and the graphics mode allows it ([NVIDIA.md](NVIDIA.md)).
* **Firmware** from `/odm/firmware` first ([INPUTS.md](INPUTS.md#firmware-native-only)); the NVIDIA firmware is
  bind-mounted there from the payload while it is mounted.
* **lapis's own userspace drives the Intel hardware**: Mesa ANV (Vulkan) + ANGLE on i915, stock minigbm,
  drm_hwcomposer (with `vendor.hwc.drm.enable_color_pipeline=false`: our i915 has no colour pipeline), the AOSP
  Wi-Fi HAL with iwlwifi, Google's Bluetooth HAL, the desktop health HAL, the DRAS audio HAL with our SOF card config.
* **Our daemons and components** (`inject/native/components/*.json`): `gb_power` (batteries, charger, DTX detach,
  dGPU hot-plug, graphics switching, platform profile), `iptsd` (touch + pen), `gb_audio`, `gb_hw` (CPU power limits,
  backlight permissions, keyboard/touchpad IDCs, pen key layouts, input exclusions), `gb_sensors` (cros-ec style
  sensors, auto-rotate overlays: `config_supportAutoRotation` for the framework and Auto-rotate on by default for
  new users; a read-only rotation log line per posture change), the libcamera camera HAL (`camera.sb3.so`), the NVIDIA stack (`gb_nouveau`,
  `nvk_probe` and the self-test, the per-app driver package GbGfxDriver with `ro.gfx.driver.0`, the GbGpu app with
  the "Graphics" tile and Settings page, the compute service `gb_gpu_compute`), `gb_slotsync`, `gb_bootlog`.

### Lesson: never preload lapis's first-stage modules

lapis's vendor ramdisk loads `8250_dw`, `intel-lpss-pci`, `intel-lpss`, `mfd-core`, `pinctrl-intel` and
`pinctrl-intel-platform` in **first-stage init** (its `modules.load`), and a module that fails there stops the boot.
Kernel build #13 also compiles our own `pinctrl-icelake`, `intel-lpss(-pci)` and `i2c-designware` (the camera config
asks for them). When `gbinit` preloaded our copies, lapis's `pinctrl-intel-platform` was refused against their
protected exports and first-stage init gave up (seen in the VMware test of the native tree). So:

* `stage_kernel.py` never puts a module from lapis's first-stage list into `modules.load` / `modules.load.late`, and
  refuses to stage when one is there (`LAPIS_FIRST_STAGE_LOAD`); `pinctrl-icelake` and `i2c-designware` also stay
  lapis's (vendor_dlkm). The cameras need only the buses these provide, not their symbols.
* Modules that must link against lapis's first-stage modules (HID sensors -> lapis's `mfd-core`,
  `surface_platform_profile` -> lapis's `platform_profile`) go into the late pass, after lapis has loaded them.
* Our own modules that lapis also ships (the camera stack) are loaded **before** lapis's copies, which then fail with
  `EEXIST`, harmlessly.

### Boot-testing the native tree in VMware

`$env:GB_WRITE_LAPIS=1; $env:GB_NATIVE_TEST=1; python -I -B build_esp.py` (VM powered off) makes a one-off VM ESP
whose only entry boots the **native** tree (`gb.hw=native`, serial console kept). It cannot reach a desktop (no
i915 in VMware), but `serial0.log` shows whether `gbinit` loads every module against lapis's vendor modules, the late
pass, the native rc and the services. The NVIDIA stack stays inert there (`refused:i915`, GbGpu `Unavailable`). The
next normal `build_esp.py` restores the VM entry. To test a whole USB image instead, boot a copy of it in a
throwaway VM (`build_usb.py --vm-test` builds the same layout with the VM tree).

## Build a USB stick

```powershell
python -I -B build_usb.py                       # -> out\googlebook-sb3-usb.img (~27 GiB with --userdata-gib 14)
python -I -B build_usb.py --sparse              # the same image as an NTFS sparse file (zero blocks not allocated)
python -I -B build_usb.py --userdata-gib 40     # for a 64 GB stick
python -I -B build_usb.py --vm-test             # same layout with the VM tree: boot-test the image in VMware first
python -I -B build_usb.py --esp-only esp.img    # only the ESP, checked with verify_image.py --esp
```

* The image is written to `<out>.part`, its sha256 is computed while writing, and `verify_image.py` checks it. Only
  then is it renamed, with the sidecar `<out>.sha256`. A failed check leaves the `.part` file and no sidecar.
* The dGPU payload (`gb/dgpu-<krel>.erofs`) is made on the builder (`mkfs.erofs`, by
  `native/nouveau-kernel/payload/make_dgpu_payload.sh`) and cached in `cache\dgpu\` by input hash. Later builds with
  the same inputs need no builder, and `GB_NO_BUILDER=1` forbids builder use. The native ESP gets two boot entries per
  slot: the default and `[NVIDIA test]` ([NVIDIA.md](NVIDIA.md#safety-rails-and-recovery)).

* Partitions come from `lapis.bin` (the slot that currently boots, including `pvmfw_a/b`, which vbmeta hashes; the
  vbmeta GPT attributes carry over), userdata/metadata/misc/security partitions are **blank** (nothing from the VM's
  /data), every partition GUID is fresh, and the boot entry names this stick's `super` (`androidboot.boot_part_uuid`,
  strict: only that disk's partitions get by-name links), so an internal install is never picked up by mistake.
* It refuses to run while the other slot has a half-finished OTA: boot the VM, let the update finish, rebuild.
* `--export DIR` also writes every partition as `DIR\<name>.img` (input of the dual-boot installer).
* `--vm-test` builds the same layout with the VM tree and the same strict `boot_part_uuid` selection, to boot-test
  the image structure (GPT, slots, first-boot formatting) in VMware before flashing
  (`out\googlebook-sb3-vmtest.img`).
* Check the result with `python -I verify_image.py` (read-only), then flash with Rufus (DD image mode),
  balenaEtcher or `dd`; 32 GB minimum. Step by step: [FLASHING.md](FLASHING.md).

## Boot it

1. Surface UEFI (hold **Volume Up**, press Power): Security -> Secure Boot -> **None/Disabled**; Boot configuration ->
   allow USB boot. The red bar at boot is normal without Secure Boot.
2. Shut down, plug the stick in, hold **Volume Down** and press Power. systemd-boot shows
   `Googlebook OS - Surface Book 3 (USB) (slot X)` (the default) and `... [NVIDIA test]` for 4 s.
3. The first boot formats /data and compiles apps: slow.

**Logs**: there is no serial console natively. `gb_bootlog` writes `\gblog\bootN-t40.txt` ... `-t900.txt` (at 40,
90, 180, 420 and 900 s) onto the stick's ESP (FAT32, readable on Windows). They hold the cmdline, module loading
(`gbinit_modules.log`), DMI, block devices, input devices, IIO, ALSA, GPU, PCI drivers, power supplies, getprop, the
dGPU report, dmesg and logcat. The newest 8 boots are kept, and nothing is written when the ESP has less than
128 MiB free. `gb_bootlog` mounts the ESP inside the same lock the nouveau policy takes, so the two never overlap (it
waits up to 10 minutes for the lock, then writes anyway, because logs matter most). Audio has its own report
(`gblog\audio-*.txt`, `native/audio`).

## Dual boot on the internal NVMe

[native/dualboot/README_DUALBOOT.md](../native/dualboot/README_DUALBOOT.md): shrinks D:, adds a separate Googlebook
ESP and partitions between D: and Recovery, adds a one-shot "Googlebook OS" UEFI entry
(`Restart-ToGooglebook.ps1`). Dry run unless `-Apply`; tested on disk images only. Do the USB bring-up first.

The same layout can also be installed from a bootable installer ISO that you build yourself from the USB image
([INSTALLER-ISO.md](INSTALLER-ISO.md)): it writes a whole disk (option 1) or installs and uninstalls next to Windows
(options 2 and 3), tested in VMware only.

## Safety

* Flashing/EC/firmware tools are replaced by refusing stubs (`fwupdmgr`, `flashrom`, `futility`, `ectool`, the
  futility executor), `fwupd` idles, and OTA postinstall's Googlebook firmware updater is skipped. lapis's
  `android-desktop-install` (it erases the whole disk) refuses.
* `module_blacklist=tpm,tpm_tis,tpm_tis_core,tpm_tis_spi,tpm_tis_i2c_cr50,spi_intel_pci,spi_intel,spi_nor,mtd,gsmi`:
  no TPM access, no BIOS SPI flash access, no Google SMI.
* `efi_pstore.pstore_disable=1`: panics are not written into the UEFI variable store; `efivarfs` is not built.
* `reboot=pci` (the Surface Book 3 hangs with other reboot methods, per linux-surface).
* `iommu.passthrough=1` (IPTS DMA faults under the IOMMU; replaces linux-surface's IOMMU quirk).
* `snd_intel_dspcfg.dsp_driver=3` (SOF; the kernel's built-in table has no Ice Lake entry). `=1` for one boot (boot
  menu `e`) selects the legacy HDA driver instead: `gb_audio.sh` then loads `snd_hda_intel` for the Intel HDA function
  only and points DRAS at the `PCH` card ([FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option),
  "No sound"). It cannot come from `gb\gb.cfg`: the parameter belongs to the built-in `snd_intel_dspcfg`, is
  read-only at run time and is read once, before gbinit reads the ESP.
* `8250.nr_uarts=4` is kept natively (wave 3): the GKI is built with `CONFIG_SERIAL_8250_RUNTIME_UARTS=0`, so
  without it `serial8250_init()` gives up before the 8250 driver registers, and the LPSS UART that carries the
  Surface Aggregator (base keyboard, touchpad, batteries, charger, detach) fails with `-22`. No `console=ttyS*` or
  `earlycon` is added natively (the serdev core owns that UART; the screen-only `earlycon=efifb` of the
  [debug boot](FLASHING.md#debug-boot-kernel-messages-on-the-screen) is a one-boot edit in the boot menu). Finding
  credit: gbOS86 (their Surface Laptop 7 fix,
  github.com/linuxkid473/gbos-x86 @f8a5c1a, HANDOFF.md:423).
* NVIDIA: nouveau is never part of the boot (no load list), loads only after Android is up, and every boot that
  loads it sets `kernel.panic=30` (the base command line keeps `panic=0`). The crash-loop guard and the `gb.nouveau=0`
  kill switch recover a bad start without a shell ([NVIDIA.md](NVIDIA.md#safety-rails-and-recovery)).

## Known risks / open points

* **NX_COMPAT**: linux-surface reverts the kernel's PE NX_COMPAT flag because some Surface firmware hangs with it
  (seen with GRUB). We boot through systemd-boot with the flag set. If the boot stops right after the menu, clear the
  flag in the ESP's copy of the kernel: `native/dualboot/build_internal.py --clear-nx-compat` does that for the
  internal install; `build_usb.py` has no such option yet.
* **An oops before Android** (while `gbinit` loads i915, IPTS, the camera or the Surface modules) stops the boot with
  no screen console and no `gblog\` file (`panic=0`, `CONFIG_PANIC_ON_OOPS=y`). The kernel has `CONFIG_EFI_EARLYCON`:
  the [debug boot](FLASHING.md#debug-boot-kernel-messages-on-the-screen) (`earlycon=efifb keep_bootcon` with
  `loglevel=5`, typed in the boot menu for one boot) draws the error text on the firmware frame buffer. Tested in
  VMware (Android still starts; a panic forced during `gbinit`'s module loading stays on the screen), not yet on the
  laptop, where it is inferred that i915 keeps the firmware picture until Android draws. It is not a serial
  `earlycon` and never part of an entry file (`verify_image.py` check V16 refuses `earlycon` there).
* lapis's boot_control HAL rewrites the GPT of the disk that holds `vbmeta_a/b` when it updates the slot bits.
* **AVX-IFMA**: our #UD emulator covers AVX-VNNI only; lapis's libinput is reported to use VEX AVX-IFMA too, which
  Ice Lake lacks (prior-art finding, unverified here).
* An OTA to a lapis build with a different kernel KMI could make lapis's vendor modules incompatible with our
  kernel. gb_slotsync now checks the new slot's GKI before it moves the boot default and holds the update when the
  KMI differs ([OTA.md](OTA.md#kernel-check-before-the-switch)).
* The NVIDIA stack (automatic driver start, per-app NVK rendering, detach gating, compute) has never run on a real
  GTX 1650: the first boot with the base attached is its first real test. Expect to need the recovery steps in
  [NVIDIA.md](NVIDIA.md). In the VM, VMware itself renders on the host GPU.
* **Cameras** need the IPU4P firmware (Intel's, from your Windows driver; [INPUTS.md](INPUTS.md#firmware-native-only)).
  Without it the ipu6 driver probes without firmware and no camera appears; everything else is unaffected.

## Bring-up configuration on the ESP (`gb\gb.cfg`)

For hardware bring-up without rebuilding the image: a small, opt-in configuration file on the boot partition that
gbinit applies to the `/odm` tree before Android starts (wave 3; design idea from gbOS86's
[how it works](https://gbos86.com/docs/how-it-works); the code is ours).

* **Off by default, one boot at a time.** It runs only when the kernel command line carries both `gb.hw=native` and
  `gb.overlay=1`. No shipped entry has `gb.overlay=1`: press `e` on the entry in the systemd-boot menu, press End and append
  ` gb.overlay=1` for one boot (recommended). Never put it into the default entry: gb_slotsync copies the default
  entry's options into the next slot's entry. A hand-made extra entry file (`loader\entries\gb-<x>-bringup.conf`)
  is not recommended: it names `/initrd-<x>.img`, which pins slot `<x>`, and gb_slotsync never updates it, so after
  a system update switched slots it would boot the previous build over a `/data` the newer build already used (a
  downgrade, which needs a data wipe). If you make one anyway, delete or re-make it after every system update.
* **Files.** `gb\gb.cfg` on the ESP. Its first line must be exactly `# gb.cfg v1`. `KEY=VALUE` lines, `#` comments,
  printable ASCII, at most 4 KiB and 64 lines; CRLF line endings (Notepad) are fine. A **format error** (the first
  line, a byte that is not printable ASCII, the size or line limits, a line that is not `KEY=VALUE`, a key given
  twice, a malformed value, `gb_ignore_user_gamma` outside 0-7, too many or too large overlay files): nothing is
  applied (one log line). A **refused setting** (an unknown key, a denied property, a module that is not in
  `modules.load`, an overlay path outside the list below): only that one is skipped, with a log line, and the rest
  is applied. Keys:
  * `GB_MODOPTS_<module>=<name>=<value> [...]`: options appended to that module's line in `modules.load` (1-8
    tokens, `[a-z0-9_]+=[A-Za-z0-9_.,:-]+`, <= 200 characters; the module must be in `modules.load`). Example:
    `GB_MODOPTS_i915=enable_psr=0 enable_fbc=0` (flicker or ghost images) or `GB_MODOPTS_i915=gb_ignore_user_gamma=4`
    (the kernel white-veil fallback, [FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option)).
  * `GB_SKIPMOD=<name>,<name>`: those `modules.load` lines are commented out for this boot.
  * `GB_PROP_<1..16>=<prop>=<value>`: one line appended to `/odm/etc/build.prop`. Only `ro.`, `persist.`,
    `vendor.` and `debug.` names; the graphics-switching, adb, boot, build, product, crypto, USB, zygote and
    privapp properties are refused, and so are the properties the image itself guards. Example:
    `GB_PROP_1=vendor.gb.posture=open` (forces the `OPEN` device state after `boot_completed` if Android's posture
    policy disabled the built-in keyboard in `CLOSED`; [FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option)).
  * Not possible through `gb.cfg`: parameters of drivers built into the kernel (`8250.nr_uarts`,
    `snd_intel_dspcfg.dsp_driver`, ...). The kernel reads them from the command line before gbinit runs; add them
    for one boot with `e` in the boot menu instead.
* **Overlay.** `gb\overlay\<path>` replaces `/odm/<path>` only for input and audio configuration:
  `usr/idc/*.idc`, `usr/keylayout/*.kl`, `usr/keychars/*.kcm`, `vup/etc/audio/<dir>/*.xml` and
  `vup/etc/data/hal_configs/audio_default.xml`. Regular files only, at most 64 files, 256 KiB each, 4 MiB in total;
  each is created 0644, root-owned. Everything else is refused with a log line: rc files, SELinux policy, VINTF,
  `build.prop`, fstab, libraries, binaries, firmware, modules. Nothing from the ESP is ever executed.
* **Which ESP.** Only the boot disk's own ESP: the disk whose GPT holds the partition GUID named by
  `androidboot.boot_part_uuid`, its `EFI-SYSTEM` partition, mounted read-only (`ro,nosuid,nodev,noexec`). Any doubt
  (no such token, the GUID missing or found twice) means: skipped, logged. A USB stick with a different partition
  GUID is never read (two sticks written from the same image share their GUIDs: keep only one plugged in).
* **Log and recovery.** `/odm/gbinit_bringup.log`, shown in the `gblog\` summary. The whole step runs under a 40 s
  timeout and the boot always continues. If a boot with `gb.overlay=1` misbehaves, boot the normal entry again, or
  rename `gb\gb.cfg` from Windows.
* **Trust.** Secure Boot is off for this project, so whoever can write the ESP already controls the kernel and the
  initrd; this adds nothing beyond that. If signed boot is ever adopted, `gb.cfg` becomes unsigned input and must go.
  It is the one ESP access outside the ESP lock (read-only, at rdinit, before any lock user exists; INTERFACES 5.4).
* **Updates.** `install_dualboot.ps1 -UpdateEspOnly` rewrites the whole ESP and so removes `gb\gb.cfg` and
  `gb\overlay\`; it copies them out first and says where ([README_DUALBOOT.md](../native/dualboot/README_DUALBOOT.md)).
  gb_slotsync never touches them.

## Google apps

What to expect from Google's apps on this unofficial port (lapis keeps its own Play services; nothing here adds or
changes Google software):

* **Google sign-in and Play Store installs work** (gbOS86's report on lapis; INFERRED for this port, not re-tested
  natively).
* **AICore-based features (Magic Pointer, on-device Gemini, other on-device AI) are refused.** They need hardware
  key attestation, and this device cannot pass it: the boot state is orange (unlocked, our own kernel), SELinux is
  permissive, and KeyMint is the AOSP software implementation. INFERRED from gbOS86's report (github.com/linuxkid473/
  gbos-x86 @f8a5c1a, HANDOFF.md:159-167,193-200); not re-tested here.
* **Play updates of some apps use fs-verity**: Android itself enables it on `/data` at the first boot, natively and in
  the VM (the VM's `/data` already has it); the native fstab also names `fsverity` (wave 3). See
  [INPUTS.md](INPUTS.md#userdata-fs-verity).
* Google binaries are never part of this repository or of anything it builds for distribution; the recovery-image
  checksums are in [INPUTS.md](INPUTS.md#the-os-image-required-obtain-it-yourself).
