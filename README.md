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
**CL3B.260622.276.R4**; our GKI kernel **android17-6.18 (6.18.21)**, built with Kleaf. The current kernel is
**build #15** (wave 3, 2026-10-26) for both the VM and the native tree: build #13 plus the nouveau backports and
hardening of build #14, the reworked `#UD` AVX-VNNI emulator and an opt-in i915 gamma switch, with the same config and
KMI as #13 ([kernel/K15.md](kernel/K15.md)). Build #15 was boot-tested in throwaway VMware VMs: the VM tree, where
its emulator passed a known-answer test of 9,235 cases, and the native tree. The long-running VM was upgraded to
build #15 in wave 4 (2026-10-26), together with zram, fs-verity for /data and the software Gatekeeper (lock-screen
PIN), after a full backup and a rehearsal on a throwaway VM
([native/dgpu/w4/VM_UPGRADE.md](native/dgpu/w4/VM_UPGRADE.md)). Wave 5 (2026-10-29/30) added the build-gated libz
patch that takes zlib's share of the AVX-VNNI emulation off the trap path (in a throwaway VM: boot to the desktop
587.7 s -> 313.6 s, emulations at boot 211 million -> 756; [PERFORMANCE.md](docs/PERFORMANCE.md)), an OTA gate that
skips build-specific parts on an unvalidated system update ([OTA.md](docs/OTA.md#the-ota-gate-wave-5)), and the fixes
of a native-readiness audit: the power and volume buttons, the camera HAL, Wi-Fi's link to lapis's libcrypto, the
composer's colour settings, posture diagnostics and a longer boot-log summary
([DEVICE-STATUS.md](docs/DEVICE-STATUS.md#readiness-audit-wave-5-what-the-image-proves-and-what-needs-the-laptop)).
The long-running VM got the wave 5 boot partition after a verified backup of it. The native USB image, the dual-boot
export and the installer ISO were rebuilt with the Gatekeeper, the libz patch, the OTA gate and those fixes; the
native image passed its VMware boot test and the ISO its VMware install tests. Wave 6 (2026-10-30) worked through
the hardware-independent backlog: NVIDIA only on the 13.5" model's TU117, rotation per posture, read-only VNNI
copies, OTA-gate image checks and a boot-log line, notifications that reach the signed-in user (lapis runs in
headless system user mode, so notices sent to user 0 were never shown), a debug boot for early crashes, and checks
that keep the lock-screen PIN working across ESP updates. The native outputs were rebuilt and tested in VMware
(VM-1, a forced OTA-gate VM boot, and on the final ISO T0-T4b, T8 and T9: 402 checks, 0 failed). Wave 7
(2026-10-30) finished the hardware-independent items: settings for your user after any sign-in (the headless
system user mode again), a louder update notice from the third unvalidated boot, a retry for notices that a
future build would silently refuse, and the wave 6 audit follow-ups, among them a PIN-path check for dual-boot ESP
updates. The native outputs were rebuilt and tested in VMware again (a rehearsal of the VM build, VM-1, and on
the ISO T0, T1a/T1b, T3-T4b, T8 and T9: 378 checks, 0 failed), and the long-running VM got the wave 7 boot
partition after a verified backup of it ([CHANGELOG.md](CHANGELOG.md)). Nothing has run on the laptop yet.

| Area | VMware VM | Native Surface Book 3 |
|---|---|---|
| Boot to the desktop, user sign-in | **works** (first boot is slow: dexopt) | untested on hardware (the native tree's module loading and services were boot-tested in VMware) |
| Display | **works**: vmwgfx (patched), 2560x1600 @ 320 dpi | untested: i915 (Gen11 Iris Plus 8086:8A52) + DMC firmware, stock lapis composer; the expected grey, washed-out "white veil" (the composer's BT.2020 mode for Google's panel) is avoided by the native build.prop from wave 5 on, with Night Light kept; the kernel switch `i915.gb_ignore_user_gamma` stays as an opt-in fallback ([FLASHING.md](docs/FLASHING.md#7-troubleshooting-symptom-log-line-boot-option)) |
| GPU / 3D | **works**: VMware SVGA3D through Mesa gallium `svga` 26.3 (patched) as the system GLES driver, SkiaGL; Vulkan is SwiftShader (CPU) | untested: lapis's own Mesa ANV (Vulkan) + ANGLE on i915 |
| Pointer / keyboard | **works**: seamless absolute mouse (`gb_absmouse` + vmmouse + mini VMware Tools `gb_vmtools`), wheel, VMware keyboard | untested: SAM base keyboard + touchpad (`surface_hid`, `hid-surface` drops the Fn "gamepad" button), IDC files (touchpad `Change_Timeout` 0.02, so quick flicks move the pointer). The Surface Aggregator's serial link needs `8250.nr_uarts=4` on the kernel command line; native entries carry it since wave 3 (older images dropped it: [FLASHING.md](docs/FLASHING.md#images-built-before-wave-3-no-keyboard-base)) |
| Touchscreen + pen | n/a | untested: IPTS (linux-surface driver, MEI 8086:34E4) + `iptsd` 3.1.1 for Android (`DisableOnStylus = true` suppresses touch while the pen hovers; end-to-end tested on the builder with a fake HID device); pen top button key layouts |
| Batteries | **works**: Windows' combined level (both packs), updated live, via `gb_hostbatt` -> guestinfo -> `gb_vmtools` -> `test_power` | untested: `gb_power` sums clipboard + base battery into one Android battery (unit-tested) |
| Charger / AC | **works** (host AC state, live) | untested: `surface_charger` mirrored by `gb_power` |
| Detach (DTX) | n/a | untested: `gb_power` handles detach requests like `surface-dtx-daemon` (with the NVIDIA driver loaded it moves apps to Intel, unbinds and removes the GPU first, and refuses while apps still use it); tablet mode from `surface_dtx` |
| NVIDIA GTX 1650 Max-Q (TU117, in the base) | not visible to the guest; VMware uses it on the host to render the VM | untested: **graphics switching** (13.5" model only; on a 15" Surface Book 3 the dGPU stays off, wave 6), default mode **Auto**. With the base attached, nouveau loads after boot and games plus the apps you choose render on NVK (Mesa's NVIDIA Vulkan driver) through a per-app driver package; detached, everything runs on Intel. The system UI always stays on Intel. Quick Settings tile and Settings page "Graphics" (Auto / Intel only / NVIDIA, per-app **Use NVIDIA** / **Always Intel**), an automatic self-test, detach refused with "Close apps and detach" while apps use the GPU, D3cold when idle, a local LLM compute service on NVK, and recovery with `gb.nouveau=0` or the crash-loop guard. [NVIDIA.md](docs/NVIDIA.md) |
| Audio | **works**: VMware HD Audio through lapis's HDA drivers (low-latency `sound.*` VMX settings); or the guest's sound in the scrcpy window | untested: SOF (Ice Lake DSP 8086:34C8) + ALC274 with lapis's vendor modules and our card configs (DRAS `<soundcard>` = the ALSA card id `sofhdadsp`); legacy HDA fallback `snd_intel_dspcfg.dsp_driver=1` at the boot menu ([FLASHING.md](docs/FLASHING.md#7-troubleshooting-symptom-log-line-boot-option)) |
| Wi-Fi / Bluetooth / network | VM networking via vmxnet3 (NAT) with a link watchdog (`gb_netwatch`); Bluetooth disabled | untested: AX201 (iwlwifi, firmware -77) and Intel BT (8087:0026) with our signed GKI modules; USB / SurfaceDock Ethernet (r8152). Since wave 5 the native tree keeps lapis's own `libcrypto`, so `wpa_supplicant` and `hostapd` can start (earlier native images would not have connected to Wi-Fi) |
| Remote window | **works** (optional): network ADB + `host\Googlebook-scrcpy.cmd` (screen, keyboard/mouse, clipboard, audio) | n/a |
| Sensors | n/a | untested (upstream linux-surface: `?`): ISH accelerometer/gyro and APDS-9960 light sensor presented as ChromeOS EC (`cros-ec-*`) IIO devices, the only kind lapis's sensors HAL accepts; auto-rotate overlay (QEMU-tested): landscape-locked with the base attached, Auto-rotate with the clipboard detached (Android's laptop policy; untested on the laptop) |
| Cameras (IPU4P, 8086:8A19) | n/a | untested: our IPU4P port of the Linux ipu6 driver + OV5693 (front), OV8865 + DW9719 (rear), and libcamera's simple pipeline with the software ISP as the Android camera HAL (tested against vimc only; since wave 5 its libraries load under lapis's linker, which refused the earlier builds). Needs Intel's IPU4P firmware from your own Windows driver package ([INPUTS.md](docs/INPUTS.md#firmware-native-only)) |
| Power / volume buttons, lid | VMware power button = clean shutdown | untested: `soc_button_array` (`MSHW0040:00`, rebound by `gb_hw.sh buttons` once lapis's `pinctrl-icelake` drives `INT3455:00`), lid wake via `surface_gpe` |
| Platform profile, CPU power limits | n/a | untested: `surface_platform_profile`, set by `gb_power` (`persist.vendor.gb.perf_profile`); `gb_hw.sh` restores the Surface firmware's PL1/PL2 |
| OTA system updates | **works** (one update so far, .271 -> .276): the A/B update installs and the VM boots the new slot; `gb_slotsync` switches the boot entry (see [OTA.md](docs/OTA.md) for what was observed). Since wave 3 it first compares the new slot's kernel interface (KMI) with our kernel's and holds the switch if they differ ([OTA.md](docs/OTA.md#kernel-check-before-the-switch)) | same mechanism, plus the notification "System update held" when it holds; untested on hardware |
| Unvalidated updates (OTA gate, wave 5) | **tested in throwaway VMs**: the image records the build it was made for; after an update to another build `gbinit` skips the build-specific libz patch and the notification "System update not validated" appears (from wave 6 for the signed-in user; a forced test boot in VMware showed it in user 10's panel; from wave 7 it says "System update still not validated" from the third boot of the same build). Tokens `gb.otagate=0` / `gb.otagate=force` ([OTA.md](docs/OTA.md#the-ota-gate-wave-5)) | same in the native tree; untested on hardware |
| AVX-VNNI (Ice Lake lacks it; parts of lapis use it) | **works**: our kernel emulates the VEX instructions on `#UD`. Build #15's reworked emulator passed a known-answer test of 9,235 cases in a throwaway VM; counter `/sys/kernel/gb_vnni/summary` | same kernel; the boot-log summary shows the counter; untested on hardware |
| Performance: AVX-VNNI emulation load | wave 5: nearly all of the ~2.5×10⁸ emulations of a VM boot came from zlib's `adler32` in `libz.so`. Both `libz.so` (system, vendor) are bound at boot with a copy that takes zlib's scalar path (a 2-byte edit, made by `vnni_prepare.py` from your own image, sha256-pinned), only on the exact lapis build it was made for; kill switch `gb.vnni=0`. **Measured** in throwaway VMs: boot to `sys.boot_completed` 587.7 s -> 313.6 s (-46.6 %), warm boot 226.0 s -> 118.0 s, emulations at boot 211,368,597 -> 756 ([PERFORMANCE.md](docs/PERFORMANCE.md)) | same binds in the native tree (its measured load is mostly BoringSSL ML-KEM, which this does not change); untested on hardware |
| Boot logs and triage | serial console log (`serial0.log`) and `gbsh.py` | `gblog\` on the stick's boot partition at 40 s to 15 min, even with a black screen; each file starts with a one-screen `== summary`. Symptom table and issue template: [FLASHING.md](docs/FLASHING.md#7-troubleshooting-symptom-log-line-boot-option) |
| Hardware bring-up without a rebuild | n/a | opt-in `gb\gb.cfg` on the boot partition, read only on a boot with `gb.overlay=1`: module options, Android properties, input/audio config files, each checked against an allow-list (tested in VMware): [NATIVE.md](docs/NATIVE.md#bring-up-configuration-on-the-esp-gbgbcfg) |
| Google apps | lapis's own Play services are used unchanged. Google sign-in and Play installs are expected to work, and on-device AI (AICore: Magic Pointer, on-device Gemini) is expected to be refused, because the device cannot pass hardware key attestation: both from gbOS86's report on lapis, not tested separately here | same expectations; untested on hardware. [NATIVE.md](docs/NATIVE.md#google-apps) |
| Lock-screen PIN | **works** (wave 4): PIN, pattern or password through our **software Gatekeeper** `gb_gatekeeper` (scrypt + HMAC under a per-device key on /data, persistent throttling that a reboot cannot shorten). A convenience lock: **not hardware-backed**, and /data is not encrypted. Tested with every recovery path on a throwaway VM: [GATEKEEPER.md](docs/GATEKEEPER.md) | the same service is in the native images since wave 5 (it starts and registers in the VMware boot test); untested on hardware |
| Hardware-backed keys / attestation | **no**: software KeyMint, no Trusty/GSC, no Weaver | same |

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
| First boot: what to check in the boot-log summary, symptom table, reporting a result | [docs/FLASHING.md](docs/FLASHING.md#first-boot-checklist), [issue template](.github/ISSUE_TEMPLATE/native-boot-report.md) |
| Try a module option, property or input/audio config for one boot, without a rebuild (`gb\gb.cfg`) | [docs/FLASHING.md](docs/FLASHING.md#8-bring-up-configuration-gbgbcfg) |
| What each Surface Book 3 device needs, and what to test first | [docs/DEVICE-STATUS.md](docs/DEVICE-STATUS.md) |
| Use the NVIDIA GPU: graphics modes, detach, self-test, compute service, recovery | [docs/NVIDIA.md](docs/NVIDIA.md) |
| Install next to Windows on the internal NVMe | [native/dualboot/README_DUALBOOT.md](native/dualboot/README_DUALBOOT.md) |
| Build the bootable installer ISO (whole disk / dual boot / uninstall) | [docs/INSTALLER-ISO.md](docs/INSTALLER-ISO.md) |
| System updates (OTA) and the A/B slot follower | [docs/OTA.md](docs/OTA.md) |
| Set a lock-screen PIN; what the software Gatekeeper protects; recover from a lock-out | [docs/GATEKEEPER.md](docs/GATEKEEPER.md) |
| AVX-VNNI emulation load and the libz patch (`vnni_prepare.py`, kill switch `gb.vnni=0`) | [docs/PERFORMANCE.md](docs/PERFORMANCE.md) |
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
vnni_prepare.py      makes the sha256-pinned, 2-byte-patched libz copies (inject/vnni/, never committed) from your lapis.bin
linkref_prepare.py   records lapis's vendor/system link data (inject/keymint/native/, never committed) for build_esp.py's link gate
link_closure.py      bionic link-closure model + the native tree's link gate (imported by build_esp.py)
keymint_native_graft.sh, verify_keymint_graft.py   make / check the KeyMint graft with private sonames (native tree)
grow_esp.py, grow_userdata.py, wipe_data.py, gen_edid.py     one-time lapis.bin preparation / maintenance
gbsh.py, gbnet.py    host side of the VM's serial root console, fast file transfer over the VM network
boot_live.ps1, cycle.ps1, vmx_set.ps1, wait_boot.sh, measure.sh    VM run / test loop helpers
vm/                  VMware .vmx template, lapis.vmdk descriptor generator
inject/              our text configs for the overlay tree (fstab, VINTF manifest, sepolicy CIL, audio, ADB, native manifests)
guest/               guest daemons in C (gb_vmtools, gb_absmouse, gb_layout, gb_slotsync, vkenum) + NDK build script
host/                Windows helpers (gb_hostbatt battery bridge, scrcpy launcher)
kernel/              kernel patches (incl. patches/k14: nouveau backports, patches/k15: build #15), config deltas, Kleaf
                     build scripts, pinned manifest, tools/ (the AVX-VNNI emulator's known-answer test, sources only)
graphics/            Mesa svga build + patches, SwiftShader build notes, minigbm patcher, EGL/AHB probes
native/              Surface Book 3 components: audio, iptsd, power, sensors, hw-gap-audit, dualboot, camera-*, the
                     NVIDIA stack: nouveau-kernel, nvk-android, gfx-driver-pkg, gpu-compute, gpu-ui, dgpu (spec and the
                     wave 3/4 plans), and gatekeeper (software Gatekeeper, both trees), vnni-perf (libz patcher, proofs,
                     measurements), ota-gating (the OTA gate's design and tests), hwtest (first-boot log collector
                     and analyser)
iso/                 installer ISO: builder scripts, the installer (Python on a small Linux), VMware test suite, design
builder/             builder VM cloud-init template and helpers
tools/               frametime (host-side frame cadence measurement)
legacy/              the first bring-up ISO builder (historical)
docs/                architecture, VMware, native, flashing, device status, NVIDIA, installer ISO, OTA, drivers, inputs,
                     lock-screen PIN (GATEKEEPER), performance (AVX-VNNI / libz)
.github/             issue template for native boot reports
```

### Environment variables

| Variable | Default | Used by |
|---|---|---|
| `GB_HOME` | the folder of the script (repository root) | all host scripts: workspace with `lapis.bin`, `boot/`, `debs/`, `inject/` |
| `GB_BUILD` | the workspace if it has `native/`, else `C:\GB-build` | `stage_kernel.py`, component manifests (`${GB_BUILD}`) |
| `GB_PYTHON` | `python` | the PowerShell/bash helpers |
| `GB_BUILDER`, `GB_BUILDER_KEY` | `builder@192.168.115.132`, `C:\GB-build\builder_key` | `stage_kernel.py`, `vnni_prepare.py`, `linkref_prepare.py` (ssh to the builder) |
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
* Security model: SELinux's global mode is enforcing, but every type of this build is permissive through our
  `inject/odm_sepolicy.cil` (lapis's user build ignores `androidboot.selinux=permissive`), there is no hardware-backed
  keystore (software KeyMint), and /data is not encrypted. The lock-screen PIN (wave 4) comes from our software Gatekeeper. Its key lives on /data, so the PIN
  stops someone at the lock screen but not root or anyone who can read the disk
  ([GATEKEEPER.md](docs/GATEKEEPER.md#what-it-does-not-protect)). Do not keep sensitive data in it.
* The libz patch (wave 5) binds Google's own `libz.so` with a 2-byte edit. The bind applies only when the exact build
  fingerprint and sha256 it was made for match; it never touches a verified partition. The patched copies are made
  locally from your image and never published. Kill switch: `gb.vnni=0` ([PERFORMANCE.md](docs/PERFORMANCE.md)).
* The OTA gate (wave 5) never removes anything needed to boot: on an unvalidated build it skips only the libz patch,
  and every failure path of the check still starts Android ([OTA.md](docs/OTA.md#the-ota-gate-wave-5)).
* The optional bring-up file `gb\gb.cfg` is read only on a boot whose command line has `gb.overlay=1` (no shipped
  entry has it), only from the boot disk's own ESP, read-only, and every setting is checked against an allow-list;
  nothing from the ESP is executed ([NATIVE.md](docs/NATIVE.md#bring-up-configuration-on-the-esp-gbgbcfg)).

## Licenses

Our own code and docs: **Apache-2.0** ([LICENSE](LICENSE)). Kernel patches are **GPL-2.0** (derived from Linux);
parts derived from linux-surface, iptsd, Mesa, libcamera, tinyalsa and AOSP keep their licenses. The NVIDIA firmware
licence text and WHENCE excerpt come from linux-firmware. See [LICENSES.md](LICENSES.md).

## Credits

### gbOS86

[gbOS86 / gbos-x86](https://github.com/linuxkid473/gbos-x86) boots Googlebook OS on other x86 PCs. Waves 3-5 of
this project (2026-10) compared notes with it (at commit `f8a5c1a`) and took the findings and ideas below. **Ideas and
facts only.** gbos-x86 has no top-level licence, so its files are not reusable here, and the implementation of each
item is our own. The one exception is a file that carries its own licence: the software Gatekeeper
(`native/gatekeeper`) adapts the parcelable framing and the meta-transaction codes of gbOS86's Apache-2.0
`gatekeeper/soft_gk.c`. That is credited in [native/gatekeeper/NOTICE](native/gatekeeper/NOTICE) and in the two
files that use it. No other gbos-x86 code is included in this repository or in anything it builds. Each file we
touched names its source.

| From gbOS86 | What we did with it | Where |
|---|---|---|
| `8250.nr_uarts`: with `CONFIG_SERIAL_8250_RUNTIME_UARTS=0` the LPSS UART fails with `-22` (their Surface Laptop 7 fix) | native entries keep `8250.nr_uarts=4`; on the Surface Book 3 that UART carries the Surface Aggregator (base keyboard, touchpad, batteries, charger, detach) | `build_usb.py`, [NATIVE.md](docs/NATIVE.md) |
| DRAS matches `<soundcard>` against the ALSA card id, not the name | `sofhdadsp`, plus the Mic field set of lapis's own configs | [native/audio](native/audio/) |
| A review of AVX-VNNI `#UD` emulation (IRQ state, VEX upper bits, XSAVE init state) | three defects confirmed in our emulator and fixed in kernel build #15, with a known-answer test | [kernel/K15.md](kernel/K15.md) |
| The i915 "white veil" report and their colour-pipeline kernel fix | the opt-in `i915.gb_ignore_user_gamma` (off by default); from wave 5 the native build.prop removes the cause (the composer's BT.2020 mode) | [kernel/patches/0005](kernel/patches/), [FLASHING.md](docs/FLASHING.md#7-troubleshooting-symptom-log-line-boot-option) |
| Touchpad `gestureProp.Change_Timeout` (issue #17) | 0.02 in our touchpad IDC | `native/hw-gap-audit` |
| Userdata fs-verity for Play updates (issue #17) | the native fstab names `fsverity` (Android already enables it by itself) | [INPUTS.md](docs/INPUTS.md#userdata-fs-verity) |
| The HWUI-on-Vulkan abort signature | counted by the NVK self-test and the boot-log summary | [NVIDIA.md](docs/NVIDIA.md#status-and-what-to-test-first) |
| Firmware pinning | `fw_coverage.py` and the pinned-source table | [INPUTS.md](docs/INPUTS.md#firmware-native-only) |
| Their update flow and ESP configuration (gbos86.com docs) | `install_dualboot.ps1 -UpdateEspOnly` checks; the opt-in `gb\gb.cfg` | [README_DUALBOOT.md](native/dualboot/README_DUALBOOT.md), [NATIVE.md](docs/NATIVE.md#bring-up-configuration-on-the-esp-gbgbcfg) |
| Checking the kernel interface before trusting a new slot (porting notes) | gb_slotsync's KMI check, "System update held" | [OTA.md](docs/OTA.md#kernel-check-before-the-switch) |
| `i915.enable_psr=0 enable_fbc=0` for flicker on an Intel Surface panel (their notes; wave 5) | the flicker row of the symptom table, through `GB_MODOPTS_i915=` | [FLASHING.md](docs/FLASHING.md#7-troubleshooting-symptom-log-line-boot-option) |
| Their log and install pages, their boot snapshot's section list (names only), apgetty off natively | the boot-log `== summary`, the symptom table, the issue template, `desktop.apgetty.enable=0` | [FLASHING.md](docs/FLASHING.md) |
| What Google's apps do on lapis; the recovery-image checksums | the Google apps expectations; the `.271` checksum (re-hashed by us) and the `.276` one (marked third-party) | [NATIVE.md](docs/NATIVE.md#google-apps), [INPUTS.md](docs/INPUTS.md) |
| A software Gatekeeper served through a hand-written stable-AIDL server over the libbinder_ndk C API (`gatekeeper/soft_gk.c`, Apache-2.0, at `03a6a46`; wave 4) | our own `gb_gatekeeper`, built from AOSP libgatekeeper with a per-device key and persistent throttling. Its AIDL parcelable framing and meta-transaction codes are adapted from `soft_gk.c` with credit. Its fixed keys, self-signed tokens and missing throttling were not taken over | [native/gatekeeper](native/gatekeeper/), [GATEKEEPER.md](docs/GATEKEEPER.md) |
| `libz.so`'s `adler32` as the dominant AVX-VNNI trap source, and the branch after the CPU-feature test that gates its SIMD path (`je` → `nop; jmp`; wave 5) | measured on our own boots; our own sha256-pinned patcher, manifest generator, equivalence and reachability proofs, and build-fingerprint-gated binds (clean-room, no code copied; only the idea and gbOS86's published sha256 values were used) | [native/vnni-perf](native/vnni-perf/), [PERFORMANCE.md](docs/PERFORMANCE.md) |

### Other projects and prior art

[linux-surface](https://github.com/linux-surface/linux-surface) (Surface Aggregator, IPTS, DTX knowledge),
[iptsd](https://github.com/linux-surface/iptsd), [Mesa](https://mesa3d.org) (svga and NVK), the Linux nouveau
driver and [linux-firmware](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git),
[llama.cpp](https://github.com/ggml-org/llama.cpp), [SwiftShader](https://github.com/google/swiftshader),
[libcamera](https://libcamera.org), [patchelf](https://github.com/NixOS/patchelf) (the KeyMint graft's private
sonames, the camera staging), linux-surface's supported-devices matrix (the wave 5 readiness audit), and the other projects that boot Googlebook OS on non-Googlebook PCs:
[Project Churro](https://github.com/alesimula/project-churro) and gbrunch.
