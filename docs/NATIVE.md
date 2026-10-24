# Native boot on the Surface Book 3

**Status: untested on hardware.** Everything native was built and checked off-device (VMware boot of the native tree,
QEMU, builder-side tests, disk-image tests). The first real boots are still to come; please report what happens.

## What changes natively

The same ESP layout and initrd format as the VM, but the boot entries pass `gb.hw=native`, so `gbinit` copies the
native tree (`/gbnative`). See [ARCHITECTURE.md](ARCHITECTURE.md) for the tree and [DRIVERS.md](DRIVERS.md) for every
device. In short:

* **Kernel modules** (our build; lapis's Google-signed GKI modules are refused by our kernel), loaded by `gbinit`
  from `modules.load` before Android starts, in dependency order (`stage_kernel.py`, `NATIVE_EARLY`):
  Wi-Fi/Bluetooth helpers (`libarc4`, `pps_core`, `ptp`, `rfkill`, `bluetooth`, `btbcm`, `hidp`, `rfcomm`), zram,
  USB network adapters, the Surface Aggregator stack (`surface_aggregator`, `_registry`, `_hub`, `_cdev`,
  `surface_hid_core`, `surface_hid`, `surface_battery scope_device=1`, `surface_charger`, `surface_dtx`,
  `surface_gpe`, `surface_hotplug`), `soc_button_array`, `test_power`, i915 and its helpers, lapis's HDA codec
  modules, `apds9960`, ACPI `thermal`, `ipts`. A second pass (`gb_modules.sh`, `modules.load.late`) loads what must
  link against lapis's first-stage modules: `surface_platform_profile` and the HID sensor drivers.
* **Firmware** from `/odm/firmware` first ([INPUTS.md](INPUTS.md#firmware-native-only)).
* **lapis's own userspace drives the Intel hardware**: Mesa ANV (Vulkan) + ANGLE on i915, stock minigbm,
  drm_hwcomposer (with `vendor.hwc.drm.enable_color_pipeline=false`: our i915 has no colour pipeline), the AOSP
  Wi-Fi HAL with iwlwifi, Google's Bluetooth HAL, the desktop health HAL, the DRAS audio HAL with our SOF card config.
* **Our daemons**: `gb_power` (batteries, charger, DTX, dGPU power-off, platform profile), `iptsd` (touch + pen),
  `gb_audio`, `gb_slotsync`, `gb_bootlog`.

## Build a USB stick

```powershell
python -I -B build_usb.py                       # -> out\googlebook-sb3-usb.img (~27 GiB with --userdata-gib 14)
python -I -B build_usb.py --userdata-gib 40     # for a 64 GB stick
python -I -B build_usb.py --vm-test             # same layout with the VM tree: boot-test the image in VMware first
```

* Partitions come from `lapis.bin` (the slot that currently boots), userdata/metadata/misc/security partitions are
  **blank** (nothing from the VM's /data), every partition GUID is fresh, and the boot entry names this stick's
  `super` (`androidboot.boot_part_uuid`), so an internal install is never picked up by mistake.
* It refuses to run while the other slot has a half-finished OTA: boot the VM, let the update finish, rebuild.
* `--export DIR` also writes every partition as `DIR\<name>.img` (input of the dual-boot installer).
* Flash with Rufus (DD image mode), balenaEtcher or `dd`; 32 GB minimum.

## Boot it

1. Surface UEFI (hold **Volume Up**, press Power): Security -> Secure Boot -> **None/Disabled**; Boot configuration ->
   allow USB boot. The red bar at boot is normal without Secure Boot.
2. Shut down, plug the stick in, hold **Volume Down** and press Power. systemd-boot shows
   `Googlebook OS - Surface Book 3 (USB) (slot X)` for 4 s.
3. The first boot formats /data and compiles apps: slow.

**Logs**: there is no serial console natively. `gb_bootlog` writes `\gblog\bootN-t40.txt` ... `-t420.txt` onto the
stick's ESP (FAT32, readable on Windows): cmdline, module loading (`gbinit_modules.log`), DMI, block devices, input
devices, IIO, ALSA, GPU, PCI drivers, power supplies, getprop, dmesg, logcat. Audio has its own report
(`gblog\audio-*.txt`, `native/audio`).

## Dual boot on the internal NVMe

[native/dualboot/README_DUALBOOT.md](../native/dualboot/README_DUALBOOT.md): shrinks D:, adds a separate Googlebook
ESP and partitions between D: and Recovery, adds a one-shot "Googlebook OS" UEFI entry
(`Restart-ToGooglebook.ps1`). Dry run unless `-Apply`; tested on disk images only. Do the USB bring-up first.

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
* The NVIDIA dGPU has no Android driver; it is powered down. VMware is the way to use it (on the Windows side).
