# Licenses

This repository mixes our own work with changes to other projects. Each part keeps the license of the project it
modifies. Licensing, confirmed by the copyright holder: our own work is Apache-2.0, and everything derived from another
project keeps that project's license:

| Part | License | Notes |
|---|---|---|
| Our own code, scripts and docs: the workspace scripts (`*.py`, `*.ps1`, `*.sh` at the root), `vm/`, `host/`, `guest/`, `graphics/minigbm/`, `builder/`, `tools/`, `legacy/`, `docs/`, the `inject/` configs, and the native components' own scripts, daemons and docs (`native/*`, except the rows below) | Apache-2.0 | [LICENSE](LICENSE) |
| Kernel patches and patch generators: `kernel/patches/` (including `kernel/patches/k14/` and the `.patch` files of `kernel/patches/k15/`), `kernel/config/`, `kernel/scripts/patch_*.py`, `kernel/scripts/apply_vnni_patch.py`, `kernel/scripts/apply_vnni3.py`, `native/power/patches/`, `native/sensors/patches/` + `native/sensors/kernel/tools/`, `native/camera-kernel/kernel/`, `native/nouveau-kernel/kernel/` | GPL-2.0 | derived from Linux (AOSP kernel/common android17-6.18); [LICENSES/GPL-2.0.txt](LICENSES/GPL-2.0.txt) |
| Kernel build #15 tools (wave 3): `kernel/scripts/apply_vnni4.py`, `kernel/scripts/kbuild15_native.sh`, `kernel/patches/k15/tools/`, and in `kernel/tools/` the KAT generators and scripts (`gen_*.py`, `build_vnni_kat.sh`, `kat0_run.sh`) | Apache-2.0 (ours) | as marked in each file. The kernel code the generators emit (`arch/x86/kernel/gb_vnni_core.h`, the traps.c and i915 changes) is our own and is published as GPL-2.0 in the patches (`kernel/patches/0002`, `0005`, `k15/0110`, `0111`) |
| `kernel/tools/vnni_kat.c`, `kernel/tools/test_gb_vnni_core.c`, `kernel/tools/k14_merge_ref.h` | GPL-2.0 | ours, as marked in each file: the AVX-VNNI known-answer test and the host test of the emulator core (they build against the GPL-2.0 `gb_vnni_core.h`). Sources only; the test binary is never shipped |
| `kernel/patches/k14/0001`-`0037` | GPL-2.0 | linux-6.18.y stable backports of nouveau commits, unmodified `git format-patch` output (0022: one-line backport fix) with the upstream authorship and `Signed-off-by` lines as received; `0101`-`0103` (N1-N3) are ours. The k14 tools and `kernel/scripts/kbuild14_native.sh` are ours (Apache-2.0) |
| `native/camera-kernel/kernel/` IPU6/IPU7 import and the two ov8865 patches by their upstream author | GPL-2.0 | upstream authorship and `Signed-off-by` lines are kept as received |
| linux-surface IPTS driver (`drivers/hid/ipts`, **not included**; fetched from linux-surface `patches/6.18/0005-ipts.patch`) | GPL-2.0 | see `kernel/patches/0004-*` |
| `native/iptsd/patches/` and `native/iptsd/etc/iptsd/` (configuration and device presets from iptsd) | GPL-2.0 (iptsd's license) | presets come from linux-surface/iptsd; our IDC files, launcher, tests and rc are Apache-2.0 |
| `graphics/mesa/patches/`, `graphics/mesa/patch_svga_shader_cache.py` | MIT (Mesa) | changes to Mesa |
| `graphics/swiftshader/` patchers | Apache-2.0 (SwiftShader) | build-system changes to SwiftShader |
| `native/camera-userspace/patches/`, `native/camera-userspace/build-scripts/patchers/` | LGPL-2.1-or-later (libcamera) | changes to libcamera |
| `native/camera-userspace/config/*.yaml` | CC0-1.0 | as marked in the files |
| `native/audio/src/tinymix-setall.patch` | BSD-3-Clause (tinyalsa) | change to tinyalsa |
| `native/nvk-android/` build scripts and probe | Apache-2.0 (ours); builds Mesa NVK (MIT) | `native/nvk-android/patches/` are Mesa changes: MIT. `0003` (AHB, compile-checked only) rebases Mesa merge request !33700 in the layout of Project Churro's `nvk-android-hardware-buffer.patch` |
| `native/nouveau-kernel/firmware/LICENCE.nvidia` | NVIDIA's firmware licence ("License For Customer Use of NVIDIA Software") | copied unchanged from linux-firmware tag 20260916 (`LICENSES/LICENCE.nvidia`). It governs the 30 TU117 firmware files that an image you build carries in its dGPU payload. The firmware itself is not in this repository |
| `native/nouveau-kernel/firmware/WHENCE.tu117` | linux-firmware's WHENCE (excerpt) | the WHENCE entries of those 30 files at tag 20260916, extracted by `whence_tu117.py`; shipped next to the firmware in the payload |
| `native/gfx-driver-pkg/` (shim, APK tools, tests) | Apache-2.0 (ours) | `gen_trampolines.py` reads Khronos `vk.xml` (Apache-2.0 OR MIT) from the Mesa tree at build time. The APK it builds also contains NVK (`libgb_nvk.so`, Mesa: MIT) |
| `native/gpu-compute/` (service, loader stub, launcher, tests) | Apache-2.0 (ours) | builds llama.cpp/ggml (MIT, tag `b11552`, fetched by `scripts/10_fetch_llama.sh`), linked statically into `libgb_gpu_compute.so` with llama.cpp's bundled nlohmann/json (MIT); the tests use llama.cpp's `gguf-py` (MIT). Keep those licence notices with any copy of the built library |
| `native/gpu-ui/` (GbGpu app), `native/power/` (gb_power), `native/dgpu/` (spec and integration notes) | Apache-2.0 (ours) | |
| `iso/` (installer ISO build, installer, tests, design) | Apache-2.0 (ours) | the ISO it builds also contains GRUB (GPL-3.0-or-later), an Ubuntu kernel and modules (GPL-2.0), busybox (GPL-2.0), Python (PSF-2.0), fonts from Ubuntu packages, and **Google's proprietary lapis partitions**: it is for personal use and must never be redistributed |
| `native/hw-gap-audit/kernel/` (`hid-surface` patch and source) | GPL-2.0-or-later | based on linux-surface's hid-surface patch, as marked in the file |
| `native/power/uapi/linux/surface_aggregator/dtx.h` | GPL-2.0+ WITH Linux-syscall-note | copy of the Linux uAPI header (Surface DTX, by Maximilian Luz), SPDX line and copyright kept |
| `kernel/manifest_16302624.xml` | AOSP repo manifest (pins public AOSP kernel projects) | as published by AOSP for build 16302624 |
| `inject/fstab.android-desktop`, `inject/se_manifest.xml` | Apache-2.0 | edited versions of the AOSP-format lapis fstab and system_ext VINTF manifest |
| `native/hw-gap-audit/tree/etc/excluded-input-devices.xml` | Apache-2.0 | our overlay of the AOSP-format input exclusion list; keeps lapis's one entry ("Power Button") |
| `inject/odm_sepolicy.cil` | Apache-2.0 | generated: one `typepermissive` statement per SELinux type name in lapis's policy |
| `kernel/patches/0005-i915-optional-ignore-user-gamma.patch` (wave 3) | GPL-2.0 | ours, derived from Linux (i915 display code) |
| Wave 3 tools and changes: `native/nouveau-kernel/tools/fw_coverage.py`, the DRAS (`audio_default.xml`, `audio_config.xml`) and touchpad IDC edits, the ESP bring-up parser (`build_esp.py` `/gbbringup.sh`), the gb_slotsync kernel check, the `-UpdateEspOnly` checks (`native/dualboot/`, `gbdisk dump`, `tools/esp_extract.py`, the test helpers) | Apache-2.0 | ours. Ideas from gbOS86 (gbos-x86 @f8a5c1a) are credited in each file; **no gbos-x86 code is included in them** (gbos-x86 has no top-level licence, so its unlicensed files are not usable here; the only gbos-x86 file with its own licence that we adapted is `soft_gk.c`, next row) |
| `native/gatekeeper/` (wave 4: `src/`, `tests/`, `tools/`, `rc/`, `vintf/`, `component/`, `build.sh`) | Apache-2.0 (ours) | the software Gatekeeper. `src/aidl_service.cpp` and `src/binder_api.h` adapt the AIDL parcelable framing and meta-transaction codes of gbOS86's `gatekeeper/soft_gk.c` (SPDX Apache-2.0, gbos-x86 commit `03a6a46`), credited in [native/gatekeeper/NOTICE](native/gatekeeper/NOTICE) and in both files. The binary built from it (never in this repository) also links BoringSSL (Apache-2.0, fetched by `build.sh`) and the NDK's libc++ (Apache-2.0 WITH LLVM-exception) statically |
| `native/gatekeeper/third_party/aosp/` | Apache-2.0 (AOSP) | unmodified copies with their licence headers: libgatekeeper (`platform/system/gatekeeper`), `hw_auth_token.h` (`platform/hardware/libhardware`) and three libbinder_ndk platform headers (`platform/frameworks/native`, used only by a compile-time ABI check); branches and commits in `NOTICE` |
| `native/dgpu/w4/` (wave 4 design, VM upgrade plan, review, `tools/`) | Apache-2.0 (ours) | working documents; evidence is not published |
| `native/vnni-perf/` (wave 5: `patcher/`, `tests/`, `tools/`, `component/`, `w5/tools/`, docs), `vnni_prepare.py` | Apache-2.0 (ours) | the libz patcher, its proofs and measurement tools. `patcher/manifest.json` holds only sha256 values, offsets, a few original bytes around each edit and the 2-byte edits of Google's `libz.so` (no part of the library beyond those bytes). Idea credit: gbOS86; no gbos-x86 code was copied |
| `native/hwtest/` (collector, analyser, README) | Apache-2.0 (ours) | first-boot log collection and triage tools |
| `native/ota-gating/` (wave 5: `DESIGN.md`, `tests/`, `tools/`) | Apache-2.0 (ours) | the OTA gate's design, host suite, builder harness and evidence tools. `tests/sepolicy/*.cil` are copies of `inject/odm_sepolicy.cil` (the generated list of lapis's SELinux type names) in its plain and OTA-robust forms, with and without one test type |
| `link_closure.py`, `linkref_prepare.py`, `keymint_native_graft.sh`, `verify_keymint_graft.py` (wave 5) | Apache-2.0 (ours) | the native tree's link gate and the KeyMint graft with private sonames; their outputs (`inject/keymint/native/`: renamed AOSP binaries and the link data of your lapis image) are never committed |

## What is deliberately not here

* Google's OS images (the lapis recovery image, `lapis.bin`, partition images), any file extracted from them
  (vendor libraries, modules, configs such as the AOSP `volume_curves.xml`, decompiled HAL code), and our modified
  copies of them (patched minigbm: we ship the patcher instead).
* Android emulator / AOSP binaries (the software KeyMint graft), SwiftShader/Mesa/NVK/iptsd/tinyalsa/llama.cpp build
  outputs, our kernel and module builds, the GbGpu and GbGfxDriver APKs, the dGPU payload, the installer ISO and its
  payload, the built `gb_slotsync` binary, the test-only `vnni_kat` known-answer test binary, and the built
  `gb_gatekeeper` binary (`native/gatekeeper/out/`).
* The patched `libz.so` copies of the VNNI patch (`inject/vnni/`, made by `vnni_prepare.py` from your own lapis image)
  and anything carved or extracted from lapis for it (`native/vnni-perf/out/`, its evidence and disassembly).
* The native KeyMint graft with private sonames and the lapis link data (`inject/keymint/native/`, made by
  `keymint_native_graft.sh` and `linkref_prepare.py`).
* Code from gbOS86 (gbos-x86): none, except the adapted parts of its Apache-2.0 `gatekeeper/soft_gk.c` in
  `native/gatekeeper` (credited in its `NOTICE`). Its findings and ideas are credited in the README and in each file
  that uses them; gbos-x86 has no top-level licence, so none of its other files is copied here.
* Firmware (Intel graphics, Wi-Fi, Bluetooth, SOF audio, the proprietary IPU4P camera firmware, Realtek USB
  Ethernet, the NVIDIA TU117 firmware), ACPI table dumps and Windows driver INF files.
* Keys and personal data: the ADB key used for scrcpy, the builder's SSH key, the signing keys of the sensors
  overlay and of the two NVIDIA packages, build and test logs, test evidence, compute models.
* Third-party documentation copied for research (e.g. the Surface Pro 7 IPU4P camera notes): see their sources.

Redistributing Google's images or vendor binaries is not permitted by this repository's licenses and must not be done
through it; every user must obtain the lapis image themselves.
