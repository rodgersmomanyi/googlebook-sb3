# Native boot on the Surface Book 3

**Status: untested on hardware.** Everything native was built and checked off-device (VMware boot of the native tree,
QEMU, builder-side tests, disk-image tests). The first real boots are still to come; please report what happens.
Device by device: [DEVICE-STATUS.md](DEVICE-STATUS.md). Flashing and the first boot: [FLASHING.md](FLASHING.md).

## What changes natively

The same ESP layout and initrd format as the VM, but the boot entries pass `gb.hw=native`, so `gbinit` copies the
native tree (`/gbnative`). See [ARCHITECTURE.md](ARCHITECTURE.md) for the tree and [DRIVERS.md](DRIVERS.md) for every
device. In short:

* **Kernel:** native images run kernel build #14 (build #13 plus nouveau-only patches; the same config and KMI, so
  every module and lapis vendor module behaves as with #13). The VM keeps whatever kernel its ESP was last built with.
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
  sensors, auto-rotate overlay), the libcamera camera HAL (`camera.sb3.so`), the NVIDIA stack (`gb_nouveau`,
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
* `snd_intel_dspcfg.dsp_driver=3` (SOF; the kernel's built-in table has no Ice Lake entry).
* NVIDIA: nouveau is never part of the boot (no load list), loads only after Android is up, and every boot that
  loads it sets `kernel.panic=30` (the base command line keeps `panic=0`). The crash-loop guard and the `gb.nouveau=0`
  kill switch recover a bad start without a shell ([NVIDIA.md](NVIDIA.md#safety-rails-and-recovery)).

## Known risks / open points

* **NX_COMPAT**: linux-surface reverts the kernel's PE NX_COMPAT flag because some Surface firmware hangs with it
  (seen with GRUB). We boot through systemd-boot with the flag set. If the boot stops right after the menu, clear the
  flag in the ESP's copy of the kernel: `native/dualboot/build_internal.py --clear-nx-compat` does that for the
  internal install; `build_usb.py` has no such option yet.
* lapis's boot_control HAL rewrites the GPT of the disk that holds `vbmeta_a/b` when it updates the slot bits.
* **AVX-IFMA**: our #UD emulator covers AVX-VNNI only; lapis's libinput is reported to use VEX AVX-IFMA too, which
  Ice Lake lacks (prior-art finding, unverified here).
* An OTA to a lapis build with a different kernel KMI could make lapis's vendor modules incompatible with our
  kernel ([OTA.md](OTA.md)).
* The NVIDIA stack (automatic driver start, per-app NVK rendering, detach gating, compute) has never run on a real
  GTX 1650: the first boot with the base attached is its first real test. Expect to need the recovery steps in
  [NVIDIA.md](NVIDIA.md). In the VM, VMware itself renders on the host GPU.
* **Cameras** need the IPU4P firmware (Intel's, from your Windows driver; [INPUTS.md](INPUTS.md#firmware-native-only)).
  Without it the ipu6 driver probes without firmware and no camera appears; everything else is unaffected.
