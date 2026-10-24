# Licenses

This repository mixes our own work with changes to other projects. Each part keeps the license of the project it
modifies. **Proposed** licensing (to be confirmed by the copyright holder before publishing):

| Part | License | Notes |
|---|---|---|
| Our own code, scripts and docs: the workspace scripts (`*.py`, `*.ps1`, `*.sh` at the root), `vm/`, `host/`, `guest/`, `graphics/minigbm/`, `builder/`, `tools/`, `legacy/`, `docs/`, the `inject/` configs, and the native components' own scripts, daemons and docs (`native/*`, except the rows below) | Apache-2.0 | [LICENSE](LICENSE) |
| Kernel patches and patch generators: `kernel/patches/`, `kernel/config/`, `kernel/scripts/patch_*.py`, `kernel/scripts/apply_vnni*.py`, `native/power/patches/`, `native/sensors/patches/` + `native/sensors/kernel/tools/`, `native/camera-kernel/kernel/`, `native/nouveau-kernel/kernel/` | GPL-2.0 | derived from Linux (AOSP kernel/common android17-6.18); [LICENSES/GPL-2.0.txt](LICENSES/GPL-2.0.txt) |
| `native/camera-kernel/kernel/` IPU6/IPU7 import and the two ov8865 patches by their upstream author | GPL-2.0 | upstream authorship and `Signed-off-by` lines are kept as received |
| linux-surface IPTS driver (`drivers/hid/ipts`, **not included**; fetched from linux-surface `patches/6.18/0005-ipts.patch`) | GPL-2.0 | see `kernel/patches/0004-*` |
| `native/iptsd/patches/` and `native/iptsd/etc/iptsd/` (configuration and device presets from iptsd) | GPL-2.0 (iptsd's license) | presets come from linux-surface/iptsd; our IDC files, launcher, tests and rc are Apache-2.0 |
| `graphics/mesa/patches/`, `graphics/mesa/patch_svga_shader_cache.py` | MIT (Mesa) | changes to Mesa |
| `graphics/swiftshader/` patchers | Apache-2.0 (SwiftShader) | build-system changes to SwiftShader |
| `native/camera-userspace/patches/`, `native/camera-userspace/build-scripts/patchers/` | LGPL-2.1-or-later (libcamera) | changes to libcamera |
| `native/camera-userspace/config/*.yaml` | CC0-1.0 | as marked in the files |
| `native/audio/src/tinymix-setall.patch` | BSD-3-Clause (tinyalsa) | change to tinyalsa |
| `native/nvk-android/` build scripts and probe | Apache-2.0 (ours); builds Mesa NVK (MIT) | `native/nvk-android/patches/` are Mesa changes: MIT |
| `native/hw-gap-audit/kernel/` (`hid-surface` patch and source) | GPL-2.0-or-later | based on linux-surface's hid-surface patch, as marked in the file |
| `kernel/manifest_16302624.xml` | AOSP repo manifest (pins public AOSP kernel projects) | as published by AOSP for build 16302624 |
| `inject/fstab.android-desktop`, `inject/se_manifest.xml` | Apache-2.0 | edited versions of the AOSP-format lapis fstab and system_ext VINTF manifest |
| `inject/odm_sepolicy.cil` | Apache-2.0 | generated: one `typepermissive` statement per SELinux type name in lapis's policy |

## What is deliberately not here

* Google's OS images (the lapis recovery image, `lapis.bin`, partition images), any file extracted from them
  (vendor libraries, modules, configs such as the AOSP `volume_curves.xml`, decompiled HAL code), and our modified
  copies of them (patched minigbm: we ship the patcher instead).
* Android emulator / AOSP binaries (the software KeyMint graft), SwiftShader/Mesa/iptsd/tinyalsa build outputs, our
  kernel and module builds.
* Firmware (Intel graphics, Wi-Fi, Bluetooth, SOF audio, IPU4, NVIDIA), ACPI table dumps and Windows driver INF files.
* Third-party documentation copied for research (e.g. the Surface Pro 7 IPU4P camera notes): see their sources.

Redistributing Google's images or vendor binaries is not permitted by this repository's licenses and must not be done
through it; every user must obtain the lapis image themselves.
