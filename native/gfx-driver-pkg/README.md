> **Repository note.** This is the component's working README, copied from the development tree. Paths
> such as `C:\GB-build\native\gfx-driver-pkg` mean `native/gfx-driver-pkg` in this repository, `C:\GB-build\native\dgpu`
> is [`native/dgpu`](../dgpu/), `C:\GB-x86` is the workspace (the repository root) and `builder:` paths are
> on the Linux builder. Build outputs, logs and older versions (`out/`, `logs/`, `superseded/`, `*.v1.1`, ...)
> are not published. What the NVIDIA support does for a user: [docs/NVIDIA.md](../../docs/NVIDIA.md).

# gfx-driver-pkg: per-app NVIDIA Vulkan driver package (GbGfxDriver), INTERFACES v1.2

**v1.2 r2 (review fix pass, INTERFACES section 29, 2026-10-25):** the ANGLE-HWUI guard takes exactly `skiavk`
(R-13). New APK `15a22bfd...` (shim `7675b988...`); all GD-1B suites re-run (host 68/68, ASan 68/68, chroot 69/69 +
18/18, APK checks); logs in `out\logs\v12r2\`, details in `integration\nvk-gfxdriver.md` section 8; the pre-fix shim,
test and APK are in `superseded\v1.2r1\`.

Component `nvk-gfxdriver` (INTERFACES.md 1.1, row 4). This directory holds the **per-app driver package**; the NVK
build itself (patches, `nvk_probe`, payload ICD) lives in `C:\GB-build\native\nvk-android\` (its README.md and
INTEGRATION_NOTES.txt). Spec: `C:\GB-build\native\dgpu\INTERFACES.md` **v1.2** sections 13.3, 14, 24 (and 22.1 D3/D5);
tasks and acceptance tests: `C:\GB-build\native\dgpu\1b\TASKS_1B.md` v1.2 section 3. Hand-off, hashes, evidence and the
Wave 2 edits: `C:\GB-build\native\dgpu\integration\nvk-gfxdriver.md` section 7 ("v1.2 (wave 1b)").

## v1.2 in one paragraph (graphics switching, user decisions D3/D5)

GbGfxDriver ships on **every native image** (never in the VM image). `ro.gfx.driver.0=gb.sb3.gfxdriver` comes from the
native `/odm/etc/build.prop` (`gfxdriver.json` `build_prop`; GpuService reads it once in its constructor). Which apps
load the package is decided by Android's own updatable-driver rules from three `Settings.Global` keys that only
gb_power decides and only gb_notify writes (`updatable_driver_all_apps` 0/3, `..._production_opt_in_apps`,
`..._production_opt_out_apps`; INTERFACES 24). This package's part of that mechanism is negative: **the APK contains no
`assets/allowlist.txt`** (24.5), so GpuService keeps `updatable_driver_production_allowlist` empty and every app that
is not opted in gets the system ANV driver directly. Inside an app that loads the package, the shim decides per Vulkan
instance: NVK **first** and ANV second while gb_power says `vendor.gb.gfx_active=nvidia`, ANV only otherwise. The
v1.1 boot switch (`gb_gfxdriver.sh`, `gb_gfxdriver.rc`, `gb.gfxdriver=1`, the gfxtest boot entry, `vendor.gb.gfxdriver`)
is retired.

## What is in the APK

| Piece | File in the APK | What it is |
|---|---|---|
| Shim | `lib/x86_64/vulkan.intel.so` | Named after `ro.hardware.vulkan` (`intel` on native lapis and in the VM, INTERFACES D12). Exports only `HMI`. Loads the real platform HAL through `android_load_sphal_library()`. |
| NVK | `lib/x86_64/libgb_nvk.so` | The same Mesa 26.3.0-rc2 NVK build as the payload ICD (patch set 8a = 0001 0002 0004 0005 0006), SONAME `libgb_nvk.so`. Unchanged in v1.2 (`6bb6dc80...`). |
| Namespace list | `assets/sphal_libraries.txt` | Empty (NVK's `libhardware.so` comes in through GraphicsEnv's formerly-VNDK-SP list, the rest is LLNDK). |
| (never) | `assets/allowlist.txt` | **Forbidden** (INTERFACES 24.5): `build_apk.sh` creates the assets dir with only `sphal_libraries.txt` and fails if anything else appears, `apk_tool.py add-libs` refuses an input APK that has one, `apk_tool.py check` and `check_apk.sh` fail on any member named `allowlist.txt`. |

Manifest: package `gb.sb3.gfxdriver`, `android:label="NVIDIA app graphics"`, `hasCode=false`,
`extractNativeLibs=false`, min/target SDK 36, meta-data `com.android.graphics.driver.build_time` = `L<epoch s>`, no
permissions. Libraries STORED, 16 KiB aligned (`zipalign -P 16`), signed v2 + v3.

## Shim rules (`shim/gb_shim.c`, INTERFACES 14 v1.2)

1. **Read point (D5).** `vkCreateInstance` reads `vendor.gb.gfx_active` once and keeps `want_nvk` = (value is exactly
   `nvidia`) in the instance. A later property change never affects an existing instance; a new instance reads again.
   `vendor.gb.gfxdriver` is ignored.
2. **Engine rule.** `pApplicationInfo` NULL, or `pEngineName`/`pApplicationName` `android framework` (HWUI,
   `a17 VulkanManager.cpp:133-136`): platform driver only, NVK never loaded.
3. **ANGLE-HWUI guard** (`gb_hwui_vulkan()`, process-wide, `pthread_once` at the process's first `vkCreateInstance`):
   `debug.hwui.renderer` set and not starting with `skiavk`, or unset with `ro.hwui.use_vulkan` != `true` -> no
   instance of the process ever gets NVK (logged once). Native lapis has `ro.hwui.use_vulkan=true`, so it is inert
   there.
4. **Gate** at the first `vkEnumeratePhysicalDevices*` of the instance: `want_nvk`, rule 3, then
   `/sys/bus/pci/drivers/nouveau/0000:*` exists and a nouveau render node opens **as the app's uid** (0666 only while
   gb_power itself derived `nvidia`, INTERFACES 23.5; a spoofed property gains nothing).
5. **Order.** Gate open and NVK enumerated >= 1 device: NVK devices first, then the platform devices;
   `vkEnumeratePhysicalDeviceGroups` lists one group per NVK device first, then the platform driver's groups,
   respecting `*count` and `VK_INCOMPLETE` (a short array gets the NVK device). Otherwise platform devices only.
6. **Never fatal.** Any NVK failure (library missing, instance, 0 devices, registry full, gate) leaves the instance
   with the platform devices only and `VK_SUCCESS`; only `vkCreateDevice` on an NVK physical device returns NVK's own
   error. NVK is created lazily (opening the shim never wakes the dGPU) and `libgb_nvk.so` is unloaded when the last
   NVK instance is gone.
7. **Trampolines** (unchanged): the shim returns its own `VkInstance` (first word `HWVULKAN_DISPATCH_MAGIC`) and the
   drivers' own physical-device and device handles; 89 physical-device-level and 24 instance-level commands are
   generated trampolines (`shim/gen_trampolines.py` from Mesa's `vk.xml`) that find the owner in a registry;
   `vkGetDeviceProcAddr` hands out the owning driver's functions directly.

Logcat tag `gb_gfxdriver`: `instance: platform driver only (vendor.gb.gfx_active=<v>)`, `NVK: <n> NVIDIA physical
device(s) first, then <m> platform device(s)`, `NVK: not offered (<why>)`, `HWUI [not] on Vulkan (...)`. Property
values in logs are cut to 24 printable characters.

## Files

```
shim/gb_shim.c              the shim (Android production build, or with -DGB_SHIM_TEST_HOOKS for tests)
shim/gen_trampolines.py     generates gb_shim_tramp.inc from vk.xml (python3 -I)
apk/AndroidManifest.xml.in  package gb.sb3.gfxdriver, label "NVIDIA app graphics", hasCode=false, build_time meta-data
apk/build_apk.sh            aapt2 link + STORED libs + zipalign -P 16 + apksigner v2/v3; never an allowlist.txt
apk/apk_tool.py             adds the libraries STORED; check: storage, 16 KiB alignment, REQUIRED and FORBIDDEN members
apk/check_apk.sh            GD-03 / GD-1B-11 static checks
scripts/build_shim.sh       NDK build of the shim + host and bionic test builds
scripts/run_host_tests.sh   GD-1B-01..08 host run, GD-1B-10 ASan/UBSan run, GD-1B-12 shim reproducibility
test/test_shim.c            the v1.2 unit tests: plays Android's libvulkan (HMI, dispatch-magic overwrite, ...)
test/fake_icd.c             tiny ICD with fixed vendor IDs and failure injection (tests only)
test/run_chroot_tests.sh    GD-1B-09: the bionic runs in the lapis 276.R4 chroot with drm-shim NVK
test/test_apk_negative.sh   GD-1B-11 negative cases: the allowlist guards fire
test/check_manifest.py      GD-1B-12: gfxdriver.json == TASKS_1B 3.3 byte for byte, out/ has no gb_gfxdriver.*
gfxdriver.json              component manifest (identical copy in out/, the INTERFACES 1.3 location)
out/                        GbGfxDriver.apk (+ .sha256), gfxdriver.json, apk-libs/ (the two libraries as packed),
                            gen/ (generated trampolines), logs/ (wave-1 evidence) and logs/v12/ (v1.2 evidence)
superseded/                 v1.1 files kept by hash (see superseded/README.txt): gb_gfxdriver.sh/.rc, GD-04 test,
                            v1.1 gfxdriver.json, shim and test sources, APK aed28e62 and shim 694760ab
```

## Build (builder, `/build/native/dgpu/gfx-driver-pkg`, `nice -n 19 ionice -c3`)

1. NVK: unchanged (`nvk-android` `results/8a-1/libgb_nvk.so`, `6bb6dc80...`).
2. `bash scripts/build_shim.sh` builds `out/android/vulkan.intel.so` and the test builds.
3. Copy `out/android/vulkan.intel.so` and `libgb_nvk.so` to `out/apk-libs/`, then `bash apk/build_apk.sh` (key
   `~/keys/gbgfxdriver.p12`, backed up in `C:\GB-build\keys\`; never in a repository or an image).
4. `bash apk/check_apk.sh`, `bash test/test_apk_negative.sh`, `python3 -I test/check_manifest.py . <TASKS_1B.md>`.

Toolchain: NDK r27c (API 35 target), build-tools 36.0.0 and `platforms/android-36/android.jar` already on the builder
(read only), JDK 17. No Gradle, no Maven, no downloads.

## Tests (v1.2, 2026-10-25; logs in `out/logs/v12/`)

| Id | Command (builder) | Result |
|---|---|---|
| GD-1B-01..08 host | `bash scripts/run_host_tests.sh` (lavapipe as the platform driver + fake NVK 0x10de) | PASS 67/67 (`gd1b_host.log`) |
| GD-1B-10 ASan/UBSan | same script | PASS 67/67, 0 sanitizer reports (`gd1b_host_asan.log`) |
| mutation check | the v1.2 test against the v1.1 shim source | FAIL 50/67 as required (`gd1b_mutation_v11shim.log`) |
| GD-1B-09 bionic | `bash test/run_chroot_tests.sh` (276.R4 chroot, drm-shim TU117, real `libgb_nvk.so`) | PASS 68/68 and 18/18 (`gd1b_chroot.log`) |
| GD-1B-11 | `bash apk/check_apk.sh`; `bash test/test_apk_negative.sh` | PASS (`gd1b_apk.log`); PASS 8/8 (`gd1b_apk_negative.log`) |
| GD-1B-12 | `check_manifest.py`; two `build_shim.sh` runs | PASS; identical `19b3fe6f...` (`gd1b_repro.log`) |
| GD-1B-13 | wave-1 GD-01..03 assertions inside the above (GD-02 rewritten as GD-1B-02, GD-03 label), GD-04 retired | PASS |

Findings worth knowing (unchanged from wave 1): the VM's `/vendor/lib64/hw/vulkan.intel.so` is SwiftShader (0x1ae0);
without a sphal namespace `dlopen("vulkan.intel.so")` returns the shim itself, which the shim rejects; bionic may keep
`libgb_nvk.so` mapped after the shim's `dlclose` (TLS destructors), while all NVK fds are closed (checked 8 -> 8).

Current artifacts: `out/GbGfxDriver.apk` `fd9bd6f711d7c231bbea0b750f064cdd51d2b71d62aec7835031443f005c6cd7`
(versionCode 29882228, build_time L1792933682), `out/apk-libs/vulkan.intel.so` `19b3fe6f...`,
`out/apk-libs/libgb_nvk.so` `6bb6dc80...`.

## Hardware-only checks (HW, not run: no NVIDIA hardware)

* H-1B-03: vkcube and an ANGLE GLES game opted in (a game, or "Use NVIDIA") with `gfx_active=nvidia`: logcat
  `gb_gfxdriver: NVK: 1 NVIDIA physical device(s) first, then 1 platform device(s)`, the app renders on NVIDIA,
  HWUI stays on ANV (`dumpsys gfxinfo`).
* H-1B-04: an app that is not opted in (or Always Intel) has no `vulkan.intel.so` from the APK in `/proc/<pid>/maps`.
* H-1B-05/07: after detach or Intel mode, newly started instances are platform-only; open apps keep their instance.
* H-1B-12: apps with an old targetSdk (`untrusted_app_25..32`; r2: these domains are permissive too, INTERFACES 17):
  a plain functional check that such a game with "Use NVIDIA" reaches NVK (the shim stays on ANV on any denial).
* H-1B-13: zygote preload / unload / reload of the driver namespace; repeated app start/stop.
