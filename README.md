# googlebook-sb3

Run **Googlebook OS** ("lapis", Google's Android 17 desktop build for the ASUS Googlebook, x86_64) on a
**Microsoft Surface Book 3 13.5"** (i7-1065G7 Ice Lake): inside **VMware Workstation** on the laptop's own Windows 11,
and natively from a **USB stick** or an **internal partition** next to Windows.

This repository holds our own build scripts, kernel patches, guest daemons, Android init/overlay configuration and
documentation. It does **not** contain Google's OS images, vendor binaries, firmware blobs or anything built from
them. You have to get the lapis image yourself (see [Requirements](#requirements)).

> Unofficial hobby project. Not affiliated with or endorsed by Google, ASUS, Microsoft, NVIDIA or VMware.
> Running a modified OS on your hardware is at your own risk. Everything marked *untested on hardware* below has
> only been checked in the VM, in QEMU, on the builder or against disk-image files.

## Status

Build tested: lapis **CL3B.260622.271.R1** (installed from the recovery image) updated in the VM by OTA to
**CL3B.260622.276.R4**; our GKI kernel **android17-6.18 (6.18.21)**, built with Kleaf: build #13, and for native images
build #14 (#13 plus nouveau backports and hardening, same config and KMI).

| Area | VMware VM | Native Surface Book 3 |
|---|---|---|
| Boot to the desktop, user sign-in | **works** (first boot is slow: dexopt) | untested on hardware (the native tree's module loading and services were boot-tested in VMware) |
| Display | **works**: vmwgfx (patched), 2560x1600 @ 320 dpi | untested: i915 (Gen11 Iris Plus 8086:8A52) + DMC firmware, stock lapis composer |
| GPU / 3D | **works**: VMware SVGA3D through Mesa gallium `svga` 26.3 (patched) as the system GLES driver, SkiaGL; Vulkan is SwiftShader (CPU) | untested: lapis's own Mesa ANV (Vulkan) + ANGLE on i915 |
| Pointer / keyboard | **works**: seamless absolute mouse (`gb_absmouse` + vmmouse + mini VMware Tools `gb_vmtools`), wheel, VMware keyboard | untested: SAM base keyboard + touchpad (`surface_hid`, `hid-surface` drops the Fn "gamepad" button), IDC files |
| Touchscreen + pen | n/a | untested: IPTS (linux-surface driver, MEI 8086:34E4) + `iptsd` 3.1.1 for Android (end-to-end tested on the builder with a fake HID device); pen top button key layouts |
| Batteries | **works**: Windows' combined level (both packs), updated live, via `gb_hostbatt` -> guestinfo -> `gb_vmtools` -> `test_power` | untested: `gb_power` sums clipboard + base battery into one Android battery (unit-tested) |
| Charger / AC | **works** (host AC state, live) | untested: `surface_charger` mirrored by `gb_power` |
| Detach (DTX) | n/a | untested: `gb_power` handles detach requests like `surface-dtx-daemon` (with the NVIDIA driver loaded it moves apps to Intel, unbinds and removes the GPU first, and refuses while apps still use it); tablet mode from `surface_dtx` |
| NVIDIA GTX 1650 Max-Q (TU117, in the base) | not visible to the guest; VMware uses it on the host to render the VM | untested: **graphics switching**, default mode **Auto**. With the base attached, nouveau loads after boot and games plus the apps you choose render on NVK (Mesa's NVIDIA Vulkan driver) through a per-app driver package; detached, everything runs on Intel. The system UI always stays on Intel. Quick Settings tile and Settings page "Graphics" (Auto / Intel only / NVIDIA, per-app **Use NVIDIA** / **Always Intel**), an automatic self-test, detach refused with "Close apps and detach" while apps use the GPU, D3cold when idle, a local LLM compute service on NVK, and recovery with `gb.nouveau=0` or the crash-loop guard. [NVIDIA.md](docs/NVIDIA.md) |
| Audio | **works**: VMware HD Audio through lapis's HDA drivers (low-latency `sound.*` VMX settings); or the guest's sound in the scrcpy window | untested: SOF (Ice Lake DSP 8086:34C8) + ALC274 with lapis's vendor modules and our card configs |
| Wi-Fi / Bluetooth / network | VM networking via vmxnet3 (NAT) with a link watchdog (`gb_netwatch`); Bluetooth disabled | untested: AX201 (iwlwifi, firmware -77) and Intel BT (8087:0026) with our signed GKI modules; USB / SurfaceDock Ethernet (r8152) |
| Remote window | **works** (optional): network ADB + `host\Googlebook-scrcpy.cmd` (screen, keyboard/mouse, clipboard, audio) | n/a |
| Sensors | n/a | untested: ISH accelerometer/gyro and APDS-9960 light sensor presented as ChromeOS EC (`cros-ec-*`) IIO devices, the only kind lapis's sensors HAL accepts; auto-rotate overlay (QEMU-tested) |
| Cameras (IPU4P, 8086:8A19) | n/a | untested: our IPU4P port of the Linux ipu6 driver + OV5693 (front), OV8865 + DW9719 (rear), and libcamera's simple pipeline with the software ISP as the Android camera HAL (tested against vimc only). Needs Intel's IPU4P firmware from your own Windows driver package ([INPUTS.md](docs/INPUTS.md#firmware-native-only)) |
| Power / volume buttons, lid | VMware power button = clean shutdown | untested: `soc_button_array` (MSHW0040), `surface_gpe` |
| Platform profile, CPU power limits | n/a | untested: `surface_platform_profile`, set by `gb_power` (`persist.vendor.gb.perf_profile`); `gb_hw.sh` restores the Surface firmware's PL1/PL2 |
| OTA system updates | **works** (one update so far, .271 -> .276): the A/B update installs and the VM boots the new slot; `gb_slotsync` switches the boot entry (see [OTA.md](docs/OTA.md) for what was observed) | same mechanism; untested on hardware |
| Screen lock PIN / hardware-backed keys | **no**: software KeyMint, no Gatekeeper/Weaver (no Trusty/GSC) | same |

Details per device, with the kernel config, firmware and userspace pieces behind each line:
[docs/DRIVERS.md](docs/DRIVERS.md); every Surface Book 3 device from the hardware audit, with its status and a
first-boot checklist: [docs/DEVICE-STATUS.md](docs/DEVICE-STATUS.md); the NVIDIA dGPU, its modes, detach behaviour,
recovery and limits: [docs/NVIDIA.md](docs/NVIDIA.md).

## How it works (short)

systemd-boot on an ESP starts **our own GKI kernel** with an initrd we assemble: a small cpio of our overlay tree
(`/gbinit` + the VM tree `/gbstage` or the native tree `/gbnative`), then lapis's vendor ramdisk, its generic ramdisk
and a bootconfig. `gbinit` (busybox) copies the selected tree to a tmpfs `/odm`, loads the native modules, and
`exec`s Android's init. Our `/odm/etc/init/gb.rc` overlays `/vendor` (overlayfs, upper dir `/odm/vup`), stubs the
Googlebook-only security/firmware services that cannot work on this hardware, and starts our daemons. Android's own
system, vendor and product partitions are used unmodified. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Requirements

* **The lapis disk image**, which you obtain yourself: the official Googlebook (ASUS, codename lapis) recovery image,
  `android-desktop_signed_recovery_image.bin` (build CL3B.260622.271.R1 here). This repository does not distribute
  it, links to no copy of it, and contains no file from it: no binaries, libraries, modules or verbatim configs.
  The only lapis-derived text is small and listed in [LICENSES.md](LICENSES.md): our edited fstab and VINTF
  manifest (`inject/`), the generated list of SELinux type names in `inject/odm_sepolicy.cil`, and the one lapis
  entry kept in our input exclusion list (`native/hw-gap-audit/tree/etc/excluded-input-devices.xml`).
* A few further inputs you build or fetch yourself (busybox, systemd-boot, the AOSP software KeyMint from an Android
  emulator image, linux-firmware files, Mesa, SwiftShader, lapis's own minigbm patched by our script ...):
  [docs/INPUTS.md](docs/INPUTS.md) lists every one, where it comes from and its checksum.
* **Windows 10/11 x64** with Python 3.10+ (`python` on PATH, or set `GB_PYTHON`), **VMware Workstation 17.5+** for
  the VM, and the in-box .NET Framework `csc.exe` for the two small C# helpers.
* A **Linux x86_64 builder** (we use an Ubuntu 24.04 VM with ~120 GB of disk) for the kernel (Kleaf/Bazel), the
  Android NDK r27c builds and image work: [builder/README.md](builder/README.md).
* Native: a Surface Book 3 13.5" with **Secure Boot disabled** (our loader and kernel are unsigned), a USB stick of
  32 GB or more, and for dual boot free space on the internal disk. For the cameras, the IPU4P firmware file from
  the laptop's own Intel camera driver (Windows).

## Getting started

| Goal | Guide |
|---|---|
| Run it in VMware on Windows | [docs/VMWARE.md](docs/VMWARE.md) |
| Boot the Surface Book 3 natively from USB (`build_usb.py`) | [docs/NATIVE.md](docs/NATIVE.md) |
| Check, flash and boot the USB stick image (`verify_image.py`, Rufus) | [docs/FLASHING.md](docs/FLASHING.md) |
| What each Surface Book 3 device needs, and what to test first | [docs/DEVICE-STATUS.md](docs/DEVICE-STATUS.md) |
| Use the NVIDIA GPU: graphics modes, detach, self-test, compute service, recovery | [docs/NVIDIA.md](docs/NVIDIA.md) |
| Install next to Windows on the internal NVMe | [native/dualboot/README_DUALBOOT.md](native/dualboot/README_DUALBOOT.md) |
| Build the bootable installer ISO (whole disk / dual boot / uninstall) | [docs/INSTALLER-ISO.md](docs/INSTALLER-ISO.md) |
| System updates (OTA) and the A/B slot follower | [docs/OTA.md](docs/OTA.md) |
| Build the kernel | [kernel/README.md](kernel/README.md) |

## Repository layout

The repository root is also the **workspace**: the scripts look for `lapis.bin`, `boot/`, `debs/` and `inject/` next
to themselves (override with `GB_HOME`). Everything you add there that must not be published (images, binaries,
firmware, keys, logs) is excluded by [.gitignore](.gitignore).

```
build_esp.py         ESP + initrd builder (VM and native trees); writes the ESP into lapis.bin (only with GB_WRITE_LAPIS=1)
build_usb.py         raw GPT image for a USB stick (+ partition export for the dual-boot installer)
verify_image.py      read-only check of a built stick image before flashing (GPT, slots, boot entry, contents)
extract_slot.py      A/B slot detection and ramdisk extraction from lapis.bin
stage_kernel.py      fetch a kernel build from the builder, check module dependencies/vermagic, stage modules
carve_lp.py          extract dynamic partitions (e.g. vendor_dlkm_b) from super
grow_esp.py, grow_userdata.py, wipe_data.py, gen_edid.py     one-time lapis.bin preparation / maintenance
gbsh.py, gbnet.py    host side of the VM's serial root console, fast file transfer over the VM network
boot_live.ps1, cycle.ps1, vmx_set.ps1, wait_boot.sh, measure.sh    VM run / test loop helpers
vm/                  VMware .vmx template, lapis.vmdk descriptor generator
inject/              our text configs for the overlay tree (fstab, VINTF manifest, sepolicy CIL, audio, ADB, native manifests)
guest/               guest daemons in C (gb_vmtools, gb_absmouse, gb_layout, gb_slotsync, vkenum) + NDK build script
host/                Windows helpers (gb_hostbatt battery bridge, scrcpy launcher)
kernel/              kernel patches (incl. patches/k14: nouveau backports), config deltas, Kleaf build scripts, pinned manifest
graphics/            Mesa svga build + patches, SwiftShader build notes, minigbm patcher, EGL/AHB probes
native/              Surface Book 3 components: audio, iptsd, power, sensors, hw-gap-audit, dualboot, camera-*, and the
                     NVIDIA stack: nouveau-kernel, nvk-android, gfx-driver-pkg, gpu-compute, gpu-ui, dgpu (spec)
iso/                 installer ISO: builder scripts, the installer (Python on a small Linux), VMware test suite, design
builder/             builder VM cloud-init template and helpers
tools/               frametime (host-side frame cadence measurement)
legacy/              the first bring-up ISO builder (historical)
docs/                architecture, VMware, native, flashing, device status, NVIDIA, installer ISO, OTA, drivers, inputs
```

### Environment variables

| Variable | Default | Used by |
|---|---|---|
| `GB_HOME` | the folder of the script (repository root) | all host scripts: workspace with `lapis.bin`, `boot/`, `debs/`, `inject/` |
| `GB_BUILD` | the workspace if it has `native/`, else `C:\GB-build` | `stage_kernel.py`, component manifests (`${GB_BUILD}`) |
| `GB_PYTHON` | `python` | the PowerShell/bash helpers |
| `GB_BUILDER`, `GB_BUILDER_KEY` | `builder@192.168.115.132`, `C:\GB-build\builder_key` | `stage_kernel.py` (ssh to the builder) |
| `GB_PIPE` | `\\.\pipe\lapis_con` | `gbsh.py` (the VM's serial console pipe) |
| `GB_WRITE_LAPIS` | unset | `build_esp.py`: running it writes the ESP into `lapis.bin` only with `GB_WRITE_LAPIS=1`, and never while VMware has the VM open (`build_usb.py` does not need it) |
| `GB_SLOT` | read from lapis.bin's GPT | `build_esp.py`: force slot a/b |
| `GB_NATIVE_TEST` | unset | `build_esp.py`: `1` writes a one-off VM ESP that boots the native tree (module/service test in VMware, [NATIVE.md](docs/NATIVE.md)) |
| `GB_NO_BUILDER` | unset | `build_esp.py` / `build_usb.py`: `1` forbids builder calls (the dGPU payload must then come from `cache\dgpu\`) |
| `GB_GFXDRIVER` | unset | `build_esp.py` / `build_usb.py`: `0` leaves out the NVIDIA app-graphics package (for bisecting only) |
| `NDK` | `/build/ndk` | builder-side build scripts |

The original development setup used `C:\GB-x86` as the workspace and `C:\GB-build` for builder-side sources and
component work; a few comments and defaults still name those folders. The component READMEs under `native/` and the
spec in `native/dgpu/` are the working documents of that setup and cite those paths (`C:\GB-build\native\<x>` is
`native/<x>` here). The installer ISO tooling in `iso/` keeps its fixed default paths, which its test guards rely on
([INSTALLER-ISO.md](docs/INSTALLER-ISO.md#building-the-iso)).

## Safety

Native mode is built to leave the Surface's firmware, TPM and Windows alone:

* **Firmware updaters are stubbed.** lapis's OTA postinstall would flash the *Googlebook's* AP/EC firmware
  (`google_desktop_firmware_updater`); it is replaced by a script that skips and succeeds. `fwupdmgr`, `flashrom`,
  `futility`, `ectool`, the futility executor and lapis's disk installer `android-desktop-install` (which would
  erase the whole disk) are replaced by scripts that refuse; `fwupd` idles.
* **No TPM / SPI flash / EFI NVRAM writes.** The native command line blacklists the TPM, SPI-NOR/`spi-intel` and
  `gsmi` modules and sets `efi_pstore.pstore_disable=1`, so kernel panics are not written into UEFI variables.
  `efivarfs` is not built.
* **Nothing from the VM's /data is copied** into USB or internal images: userdata, metadata, misc and the security
  partitions are created blank, with fresh partition GUIDs.
* The dual-boot installer is a dry run unless `-Apply`, backs up the GPT and BCD first, never moves C:, the Windows
  ESP, MSR or Recovery, and was exercised against disk images only. Read its README before using it.
* Known hazard: lapis's boot_control HAL rewrites the GPT of the disk holding `vbmeta_a/b` (A/B slot bits). On an
  internal install that is the Windows disk. In the VM those writes were correct and limited to the vbmeta entries.
* **The NVIDIA driver is never part of the boot.** nouveau is in no load list. It loads only after Android is up,
  with a crash-loop guard, a boot-menu kill switch (`gb.nouveau=0`) and `kernel.panic=30` on those boots, and the
  display always stays on Intel ([NVIDIA.md](docs/NVIDIA.md#safety-rails-and-recovery)).
* Security model: SELinux runs **permissive**, there is no hardware-backed keystore (software KeyMint) and no lock
  screen credential storage (Gatekeeper/Weaver are absent). Do not keep sensitive data in it.

## Licenses

Our own code and docs: **Apache-2.0** ([LICENSE](LICENSE)). Kernel patches are **GPL-2.0** (derived from Linux);
parts derived from linux-surface, iptsd, Mesa, libcamera, tinyalsa and AOSP keep their licenses. The NVIDIA firmware
licence text and WHENCE excerpt come from linux-firmware. See [LICENSES.md](LICENSES.md).

## Acknowledgements and prior art

[linux-surface](https://github.com/linux-surface/linux-surface) (Surface Aggregator, IPTS, DTX knowledge),
[iptsd](https://github.com/linux-surface/iptsd), [Mesa](https://mesa3d.org) (svga and NVK), the Linux nouveau
driver and [linux-firmware](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git),
[llama.cpp](https://github.com/ggml-org/llama.cpp), [SwiftShader](https://github.com/google/swiftshader),
[libcamera](https://libcamera.org), and the other projects that boot Googlebook OS on non-Googlebook PCs:
[gbOS86 / gbos-x86](https://github.com/linuxkid473/gbos-x86), [Project Churro](https://github.com/alesimula/project-churro)
and gbrunch.
