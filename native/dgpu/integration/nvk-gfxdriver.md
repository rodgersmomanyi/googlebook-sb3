# nvk-gfxdriver: Wave 2 integration notes

Component 4 (INTERFACES.md **v1.1**, 1.1). **v1.2 (wave 1b, graphics switching) is section 7; where it differs from
sections 1-6, section 7 wins** (the superseded parts are marked "(superseded by v1.2, 7.x)" in place). This Wave 1 step
covered:

* plan Phase 0 item 1: `apply_patches.py` and patches 0001/0002 synced to the builder tree;
* Phase 0 item 3: the NVK import/loader checks re-run against lapis 276.R4;
* Phase 0 item 4: INTEGRATION_NOTES corrections;
* Phase 8 code: `nvk_probe` modes `ahb-i915-readback` and `a17-swapchain-model`;
* Mesa patches 0003-0006 and the new `apply_patches.py`;
* the rebuilt `vulkan.nouveau.so`;
* the per-app driver package `C:\GB-build\native\gfx-driver-pkg\` (ANV-first shim, GbGfxDriver.apk,
  `gb_gfxdriver.sh/.rc`, `gfxdriver.json`);
* tests.

v1.1 fix pass (INTERFACES 21: C2, C7, C11): `gb_nvk.rc` now runs the probe as user shell in group graphics and
creates `/data/vendor/nvk` 0770 shell:shell (review R2-1: a root probe ignores the chmod 000 of the refcnt gate, and
root wrote through a shell-controlled directory); the gfxdriver gate of gb_nouveau.sh and gb_power is now the property
AND `/dev/gb_dgpu/gfxdriver`, which `gb_gfxdriver.sh` already creates (no change to gb_gfxdriver.sh, the shim, Mesa
or the APK). No NVK, shim or APK binary changed in v1.1.

Nothing in the shared image pipeline was edited. READMEs: `C:\GB-build\native\nvk-android\README.md`,
`C:\GB-build\native\gfx-driver-pkg\README.md`; background and blockers: `C:\GB-build\native\nvk-android\INTEGRATION_NOTES.txt`.

**(v1.1; superseded by v1.2, 7.4)** **The per-app driver ships DISABLED.** `gfxdriver.json` is not a normal component.
It is loaded only in `GB_GFXDRIVER=1` builds, and even then `ro.gfx.driver.0` is set only in boots from the
`gb-<x>-gfxtest.conf` entry (`gb.gfxdriver=1`), and only for apps listed in
`Settings.Global updatable_driver_production_opt_in_apps`. v1.2: a normal native component on every native image,
`ro.gfx.driver.0` from build.prop, the app selection from gb_power's Settings (INTERFACES 24).

## 1. Artifacts (sha256)

(v1.1 hand-off. The rows for `GbGfxDriver.apk`, `gb_gfxdriver.sh`, `gb_gfxdriver.rc`, `gfxdriver.json`,
`apk-libsulkan.intel.so`, `nvk_probe`, `nvk_probe.c` and `gb_shim.c` are superseded by v1.2, 7.1.)

### Consumed by Wave 2 / other components

| File | sha256 | Notes |
|---|---|---|
| `C:\GB-build\native\nvk-android\bin\vulkan.nouveau.so` | `625314e09c8f9adb82d57b8abfd6ea2e855308a1ea79c59f6670783e52f2409b` | Payload ICD, patch set 8a. Replaces `f7e9154f...`, which is in `superseded\`. Component 5 uses this file. |
| `...\nvk-android\bin\vulkan.nouveau.unstripped.so` | `63b0aa686dc340747e341f87b6ef973d07cd53220318759fb0aa2bbf7d5d5377` | Debug copy, not packed. |
| `...\nvk-android\bin\nvk_probe` | `412e080aca1a03d3220a8ef7da1aef4a78897c52c6e5cadd30097f3e07043054` | 61504 bytes (was 42688). Goes to `vup/bin/nvk_probe`. |
| `...\nvk-android\gb_nvk.rc` | `748963f3630b641869aa862523c1d2b9ee26213437633551d8ff5b81c733d666` | `rc_files` of nvk.json (INTERFACES 15.5 v1.1: `user shell`, `group shell graphics`, `/data/vendor/nvk 0770 shell shell`; v1 file `7af5199f...`). |
| `C:\GB-build\native\gfx-driver-pkg\out\GbGfxDriver.apk` | `aed28e628718ddfed68fbcbb98864ac484c16256684968c350fcd41891d844f3` | versionCode 29881838, build_time L1792910285, 16499048 bytes. Only with `GB_GFXDRIVER=1`. |
| `...\gfx-driver-pkg\out\gb_gfxdriver.sh` | `c28f4d8c9cc00cf6c4677a26d056a15502be70edc877a02e13e4e85f7d501ad1` | `vup/bin/gb_gfxdriver.sh`, 0755. |
| `...\gfx-driver-pkg\out\gb_gfxdriver.rc` | `a49ed94bcc8b8869ef1599520f202734f4f239b972705e87a4880762781873c1` | INTERFACES 15.6 verbatim. |
| `...\gfx-driver-pkg\out\gfxdriver.json` | `d87a9bab3d9b41b074e55c88843ee1dddcfa59285fe931835c034aeaa1c5cf18` | Manifest. An identical copy is at `gfx-driver-pkg\gfxdriver.json`. |

### Contents of the APK and reference builds

| File | sha256 | Notes |
|---|---|---|
| `...\gfx-driver-pkg\out\apk-libs\vulkan.intel.so` | `694760ab45d95515b823889c6d7380048b5bf037ba72a865a2dd7ff8ddb3fac0` | The shim inside the APK. NEEDED libdl, liblog, libc. Exports `HMI` only. 16 KiB segments. Two builds gave identical bytes. |
| `...\gfx-driver-pkg\out\apk-libs\libgb_nvk.so` | `6bb6dc80afac7a73c3131b6c70e322bf3c322f0c0057adf2b0581152f673cfb9` | NVK inside the APK: the same 8a build relinked with SONAME `libgb_nvk.so`. |
| builder `results/8b/vulkan.nouveau.so` | `eabae2d442ea48be4596ed093ecf8ca88b8439d7481cac530efb8edcca6b5eb2` | 8b (AHB, 0003). Compile and API check only, **not shipped**. |

### Patches and sources

| File | sha256 |
|---|---|
| `...\nvk-android\patches\0001-nvk-android-reject-foreign-gralloc-modifiers.patch` | `c0bd217d6651160a0232803fa3d55a1ddfa19825e24e658fe08391a8ff3004ec` (unchanged) |
| `...\patches\0002-nvk-allow-building-without-libelf.patch` | `f3e874c6fa48f929a5e9d5f36868397f777d4f9fbd8cad89953b29ccaf26c254` (unchanged) |
| `...\patches\0003-nvk-android-hardware-buffer.patch` | `750daf7f0ff0d38b386d9c2b37f87054a78aa0cacc71f092c1f60df3bf2119f9` |
| `...\patches\0004-nvk-android-force-linear-for-foreign-gralloc.patch` | `261393d5f2181fc2ca1f0b65a44436619504c7f87f143e36e7ebfefa01d9394c` |
| `...\patches\0005-nvk-linear-tiled-shadow-for-android-images.patch` | `24ad95db012791b80d04fe300f7c31dd47bad658a3d1cd84b792994f648de9ab` |
| `...\patches\0006-nvk-android-sync-fd-release.patch` | `693ddc44ad5587a5a3bd62a1b9f6a43f80d1be187b5d4d6e44011582aabf085b` |
| `...\nvk-android\scripts\apply_patches.py` | `46aa2a54f5dc628ec47fd703ad38fede17ea721164ae401ae052c2723eefa734` |
| `...\nvk-android\src\nvk_probe.c` | `5c7a47ac42d27fc6d6bfc6185acf7ac0cca01bf9dbc3787a80f5f62a33555e32` |
| `...\gfx-driver-pkg\shim\gb_shim.c` | `ef329edd41d4cdc2411ba233cd2c33e5ca1db288a62aec949cae1114895a1b3b` |
| `...\gfx-driver-pkg\out\gen\gb_shim_tramp.inc` (generated) | `4689c5f71acd358799e86b143145bfe43fc4ba5a69d058b7845adf4c4c64d62c` |

Signing key: builder `~/keys/gbgfxdriver.p12` and `gbgfxdriver.pass` (0600, dir 0700) were generated by
`apk/build_apk.sh` (RSA 3072, PKCS12, 10000 days). They are backed up to `C:\GB-build\keys\` under the same names
(content hashes match), and are in no repository, component directory or image. Wave 2 may want to restrict
`C:\GB-build\keys\` to the user's account with Windows ACLs.

Builder state left behind:

* The `/build/native/nvk-android/src/mesa` tree is at patch set 8a. Before 8a, the old `apply_patches.py` and the old
  0002 are kept in `superseded/` on the builder.
* `results/{nv01,8a-1,8a-2,8b,base-new}` are kept. `b-nvk-8a` (8a build dir) is kept, and so is `b-nvk`, the base
  build dir that holds the test-only drm-shim `src/nouveau/drm-shim/libnouveau_noop_drm_shim.so` used by every
  check. The other build and stage dirs were removed.
* `/build/native/dgpu/nvk-sync/rootfs-276R4` (972 MB, the read-only adb copy of 276.R4 libraries) and
  `/build/native/dgpu/gfx-driver-pkg/` (shim builds and test runs) remain.
* All of these can be deleted when Wave 2 no longer needs to re-run the checks (section 3 has the commands).

## 2. Edits to shared files (Wave 2)

### 2.1 `C:\GB-x86\inject\native\components\nvk.json` (W2-01, W2-11)

Use README W2-01's legacy-layout JSON unchanged (`bin\vulkan.nouveau.so` -> `vup/lib64/hw/vulkan.nouveau.so` 0644,
`bin\nvk_probe` -> `vup/bin/nvk_probe` 0755, `"rc_files": ["C:\\GB-build\\native\\nvk-android\\gb_nvk.rc"]`,
`"rc_text": ""`). That is nouveau-policy's "Option B". `gb_nvk.rc`'s trigger block is exactly the one
nouveau-policy.md 2.2 asks for:

```
on property:vendor.gb.nvk_probe=1
    setprop vendor.gb.nvk_probe 0
    setprop vendor.gb.nouveau.foreign 1
    write /dev/gb_dgpu/foreign 1
    start gb_nvk_probe
```

The payload layout (with W2-08) is also unchanged from W2-01. `vulkan.nouveau.so` goes to `"payload"`
(`lib64/hw/vulkan.nouveau.so`, 0644), plus the symlink `vup/lib64/hw/vulkan.nouveau.so` ->
`/odm/dgpu/lib64/hw/vulkan.nouveau.so`. `nvk_probe` stays in `files` (D7). `nvk_probe` still tries
`/vendor/lib64/hw/vulkan.nouveau.so` first and then `/odm/dgpu/lib64/hw/vulkan.nouveau.so`, so both layouts work.

Verify:

* the native `gb.rc` has `service gb_nvk_probe ` followed by `user shell` and `group shell graphics`, the
  `mkdir /data/vendor/nvk 0770 shell shell` line, and the four-line trigger block above;
* `gbnative/vup/lib64/hw/vulkan.nouveau.so` (legacy) has the sha256 in section 1;
* the VM cpio contains none of it.

### 2.2 `gfxdriver.json`: only with `GB_GFXDRIVER=1` (W2-01, W2-03) (superseded by v1.2, 7.4: a normal component, `gfx-switching.md` W2G-01/W2G-03/W2G-06; in particular the "no `ro.gfx.driver.` line in any build.prop" check below is inverted)

* Canonical manifest: `C:\GB-build\native\gfx-driver-pkg\out\gfxdriver.json` (INTERFACES 1.3). An identical copy
  is at `C:\GB-build\native\gfx-driver-pkg\gfxdriver.json`, the path README W2-01 names. Do **not** copy it into
  `inject\native\components\`.
* build_esp: when `os.environ.get("GB_GFXDRIVER") == "1"`, load this one extra manifest after the components
  directory. It contains:
  * `out\GbGfxDriver.apk` -> `app/GbGfxDriver/GbGfxDriver.apk` (0644);
  * `out\gb_gfxdriver.sh` -> `vup/bin/gb_gfxdriver.sh` (0755);
  * `rc_files` `out\gb_gfxdriver.rc`;
  * no `build_prop` lines.
* In the same builds only, emit `loader/entries/gb-<x>-gfxtest.conf` exactly as README W2-03: title suffix
  `[NVIDIA app graphics test]`, options `<base options> gb.nouveau=once gb.gfxdriver=1`.
* Without `GB_GFXDRIVER=1` nothing from this package may appear: no `GbGfxDriver`, no `gb_gfxdriver`, no gfxtest
  entry.

Verify (proposed `verify_image.py` additions, W2-07):

* **Without `GB_GFXDRIVER=1`:** no cpio path contains `GbGfxDriver` or `gb_gfxdriver`, and no
  `loader/entries/*-gfxtest.conf` exists.
* **With it:**
  * `gbnative/app/GbGfxDriver/GbGfxDriver.apk` has the sha256 from section 1;
  * the native `gb.rc` contains `service gb_gfxdriver_setup ` and `exec_start gb_gfxdriver_setup`;
  * the gfxtest entry exists.
* **Always:**
  * no `ro.gfx.driver.` line in any build.prop. `ro.gfx.driver.0` is set at run time only (INTERFACES 4);
  * no `gbnative/priv-app` or `gbstage/priv-app` path (invariant 17);
  * the VM tree never contains the package.

### 2.3 nouveau node permissions in gfxdriver boots (component 1 interplay, nouveau-policy Q2) (superseded by v1.2: renderD 0666 only while gb_power's own derivation is `nvidia`, INTERFACES 23.5; the flag file is retired)

`gb_gfxdriver.sh` also creates the root-only flag `/dev/gb_dgpu/gfxdriver` when it switches the experiment on.
Neither an app nor the shell user can create that file, while any app can set `vendor.gb.gfxdriver`.

* v1.1 (C2): `gb_nouveau.sh` and `gb_power` both open the render node to 0666 only with "property `on` **and** flag
  file present"; `gb_nouveau.sh` no longer reads the `gb.gfxdriver=1` token, so INTERFACES 3's "gb_gfxdriver.sh is the
  only reader of the token" holds. Resolves nouveau-policy Q2.
* The shim needs the node to open as the app's uid. With 0660 root:graphics (normal boots) the gate "render node
  opens" fails for apps, so NVK is never offered outside gfxdriver boots, even if an app sets `vendor.gb.gfxdriver=on`.

### 2.4 gpu-compute (component 5): shader cache

The payload ICD now has the Mesa disk cache compiled in, but Mesa keeps it **off on Android** unless
`MESA_SHADER_CACHE_DISABLE=false` is set (`disk_cache_os.c`, `DETECT_OS_ANDROID`). For the cache to work,
`gb_gpu_compute.rc` (INTERFACES 15.3) needs a second `setenv` next to `MESA_SHADER_CACHE_DIR`:

```
    setenv MESA_SHADER_CACHE_DISABLE false
```

### 2.5 SELinux (W2-15) (v1.2: no `gb_gfxdriver_setup`; old-targetSdk app domains are W2G-09 / H-1B-12)

No new type or domain. `gb_gfxdriver_setup` runs in `u:r:shell:s0` (permissive) and does
`setprop ro.gfx.driver.0 ...`. App processes that use the package run in their own `untrusted_app` domain (permissive)
and open `/dev/dri/renderD<n>` and the APK's libraries. Whether `setprop` of `ro.gfx.driver.0` from the shell domain
is logged as a permissive denial is unverified (VM/HW check).

### 2.6 Docs (W2-14) (superseded by v1.2: `gfx-switching.md` W2G-08)

Add to `docs\NVIDIA.md` an "app graphics experiment" section:

* the gfxtest boot entry;
* `settings put global updatable_driver_production_opt_in_apps <pkg>`;
* what the user sees: ANV is still device 0 and NVK is device 1 only for that app;
* HWUI, system and privileged apps never use it;
* NVK has no ETC2/ASTC;
* the base must stay attached while an NVK app runs.

Copy the B4/B8/fall-through facts from INTEGRATION_NOTES 4 and 6.

## 3. Tests run (Wave 1, builder; no NVIDIA hardware)

Log paths are relative to `C:\GB-build\native\nvk-android\logs\wave1\` (NV, P0) or
`C:\GB-build\native\gfx-driver-pkg\out\logs\` (GD).

### Phase 0, builds and NVK checks (NV)

| Id | Result | Evidence |
|---|---|---|
| P0-1 sync | PASS | The builder had the old `apply_patches.py` (sha `0a9c6f8e`) and 0002 (`1e902932`). It got the Windows copies (`ec0415f0`, `f3e874c6`); the old ones are in builder `nvk-android/superseded/`. |
| NV-01 | PASS | Patch set base (0001+0002) after the sync. `vulkan.nouveau.so` = `f7e9154f2e049cdfbb8fc3c1d92815eb96a4b22b5105a04f625eeb41fadf0723` and unstripped = `4d8ea4037914e8f3c16d8af50f14e4e8c455a9de453d43c63b5d9b0ce98e70e9`, both identical to the previous `bin\` files (`nv01_build.log`). The same check with the new profile scripts (`PATCHSET=base`, `build_base-new.log`) also gives exactly f7e9154f... and 4d8ea403..., with 0 compiler warnings. |
| P0-3 vs 276.R4 | PASS | `p03_276R4_checks.log`, run on the shipped f7e9154f ICD. `import_check.py`: NEEDED within the 276.R4 LLNDK list + libhardware, 292/292 ICD imports and 44/44 nvk_probe imports resolved. Bionic chroot: vk_icd path and HMI path both give a 1.4.363 instance with 0 devices, exit 3. hmi_check PASS. drm-shim TU117: device, NAK pipeline in 2.4 ms. |
| NV-04 | PASS | `apply_patches.py --check` passes for base, 8a and 8b, on a pristine mini tree and on the real tree. A real apply 8b -> 8a -> base restores the pristine files. 0001/0002 diffs are byte-identical to the shipped patches, and 0004/0005/0006 are identical in sets 8a and 8b. Builds 8a and 8b: `build exit=0` and **0** compiler warnings (`build_8a-1.log`, `build_8b.log`). |
| NV-05 | PASS | Two clean 8a builds in different build directories (`b-nvk-8a`, `b-nvk-8a-r`) gave identical `vulkan.nouveau.so` 625314e0..., unstripped 63b0aa68... and `libgb_nvk.so` 6bb6dc80... (`build_8a-1.log`, `build_8a-2.log`, `cmp`). |
| NV-06 | PASS | `checks_8a-1.log`. `vulkan.nouveau.so` and `libgb_nvk.so`: NEEDED libhardware, liblog, libnativewindow, libsync, libm, libdl, libc; 317/317 imports resolved against 276.R4; SONAMEs `vulkan.nouveau.so` / `libgb_nvk.so`. zlib is static, so there is no `libz.so`. `nvk_probe`: NEEDED libdl, libnativewindow, libc, 47/47 resolved. 8b: 318/318 (`checks_8b.log`). |
| NV-02 | PASS | 8a and 8b in the 276.R4 chroot: `nvk_probe` on both entry paths gives NO_DEVICE exit 3; hmi_check PASS (`checks_8a-1.log`, `checks_8b.log`). |
| NV-03 | PASS (criterion: every mode reaches its first device call) | `checks_8a-1.log`, drm-shim TU117. hmi_check: 179 device extensions, +ANB, -AHB, GrallocUsage/Usage2 present, 3/4 NULL. `offscreen`: device and NAK in 3.6 ms; pixels 0, expected with no GPU. `a17-swapchain-model`: GPDIFP2(AHB) returns `VK_ERROR_FORMAT_NOT_SUPPORTED` as expected, then GrallocUsage2 returns consumer 0x100 / producer 0x200, then the pattern pipeline compiles; the allocation blocks (no allocator service in a chroot) and the new `-T` watchdog ends it after 20 s. `ahb-i915-readback`: the pattern pipeline compiles in 5.2 ms, then the same allocator block. `anb-*`: GrallocUsage2 and pipeline, then the same. 8b: AHB extension advertised (180 extensions, `+ANDROID_external_memory_android_hardware_buffer`), and the AHB GPDIFP2 path blocks in `AHardwareBuffer_isSupported` the same way. |
| 0004 logic | PASS | `checks_8a-1_0004.log`. With the drm-shim, NVK *is* renderD128, so auto-detection says "native" (producer 0x200). The override `NVK_ANB_LINEAR=1` gives producer **0x220**, native usage 0x320 (+CPU_WRITE); `=0` gives 0x200. An auto-detection test with a test-only drm-shim on renderD129 was inconclusive: NVK enumerated 0 devices because the shim's fake sysfs assumes minor 128. The stat comparison itself is code-reviewed and is a HW check. |
| probe host | PASS | `probe_host_validation.log`. lavapipe offscreen PASS (31.75 % coverage). An Android-only mode on the host build prints SKIP. An unknown `-t` value exits 2. Host NVK (Ubuntu 25.2.8) gives NO_DEVICE 3. |
| rebase script | PASS (refuses) | `rebase_check.log`. `50_rebase_mesa.sh --check` returns `REFUSED: mesa-26.3.0 does not exist`; latest tag mesa-26.3.0-rc2; exit 3. |

### Per-app driver package (GD)

| Id | Result | Evidence |
|---|---|---|
| GD-01 host (glibc, lavapipe as "ANV" + fake NVK 0x10de) | PASS 39/39 | `gd01_host.log`. Checks: HMI contract; libvulkan-style magic overwrite on every handle; ANV first and NVK appended; `android framework` engine or app name or NULL appinfo gives no NVK and no library load; each nouveau gate; NVK library missing, instance failure, 0 devices and device failure; lazy load; unload after the last instance; 2-instance refcount; VK_INCOMPLETE; groups order; GIPA surface. |
| GD-01 host ASan+UBSan | PASS 39/39, no reports | `gd01_host_asan.log` (LSan on, lavapipe/LLVM suppressed). |
| GD-01 bionic run 1 (fake Intel base + fake NVK + real NVK on drm-shim TU117) | PASS 44/44 | `gd01_chroot.log`. The full list on bionic, plus the real `libgb_nvk.so`: appended as `NVK` 0x10de, device created through the shim, **no fd left open after destroy (8 -> 8)**, HWUI instance never loads it. bionic kept `libgb_nvk.so` mapped after the shim's dlclose. This is reported only; see the README and the test comment: TLS destructors, no device held. |
| GD-01 bionic run 2 (the shim's own platform loader) | PASS 16/16 | `gd01_chroot.log`. With no base override the shim loaded `/vendor/lib64/hw/vulkan.intel.so`: the first `dlopen("vulkan.intel.so")` returned the shim itself, was rejected, and the sphal-path fallback was used. NVK (drm-shim) was appended after it, the device created, fds 8 -> 8, and the HWUI engine got the platform device only. The VM's `vulkan.intel.so` reports itself as **SwiftShader** (vendor 0x1ae0); relevant for GD-05. |
| GD-02 | PASS | Part of GD-01: `vendor.gb.gfxdriver` unset, `off` and `ON` all give platform-only, and the library is never loaded. |
| GD-03 | PASS | `gd03.log`. Badging: package `gb.sb3.gfxdriver`, versionCode 29881838, min/target 36, native-code x86_64, no permissions. Manifest: hasCode=false, extractNativeLibs=false, build_time `L1792910285`. `zipalign -c -P 16 4` passes. apksigner verifies v3 at min SDK 36; the v2 block is present and valid (verify at 24); v1 absent. Both `.so` STORED at 16 KiB-aligned offsets (16384, 16449536). SONAMEs match. |
| GD-04 | PASS 14/14 | `gd04.log` (mksh). Only native + last `gb.gfxdriver=1` + APK present sets `ro.gfx.driver.0=gb.sb3.gfxdriver`, `vendor.gb.gfxdriver=on` and `/dev/gb_dgpu/gfxdriver`. Each of these gives `off`: missing APK, missing token, `0`, vm, missing gb_hw, last-wins-off, `11`, `ON`, `$(...)`, `;`, glob and a 300-character value. The injection canary was never created. `shellcheck -s sh`: 0 findings. |
| shim reproducibility | PASS | `vulkan.intel.so` 694760ab... from four separate `build_shim.sh` runs. |
| GD-05 | not run | Wave 2 / VM, needs the user's approval; see Q1. |

### v1.1 fix pass re-run (2026-10-25 10:20-10:21 UTC, builder)

No NVK, shim, probe or APK binary changed in v1.1, so nothing was rebuilt; the checks were re-run on the same
artifacts (`vulkan.nouveau.so` 625314e0..., `libgb_nvk.so` 6bb6dc80..., `nvk_probe` 412e080a..., `GbGfxDriver.apk`
aed28e62..., shim `vulkan.intel.so` 694760ab...). Logs: `C:\GB-build\native\nvk-android\logs\fixpass\`
(`nvk_rerun.log` is the summary).

| Id | Result | Evidence |
|---|---|---|
| GD-01 host / ASan+UBSan | PASS | `RESULT: PASS (39 passed, 0 failed)` both; 0 sanitizer report lines. |
| GD-01 bionic | PASS | `RESULT: PASS (44 passed, 0 failed)`, `RESULT: PASS (16 passed, 0 failed)`. |
| GD-03 | PASS | `check_apk.sh`: `RESULT: PASS`. |
| GD-04 | PASS | `RESULT: PASS (14 passed, 0 failed)` under mksh; `shellcheck -s sh gb_gfxdriver.sh` 0. |
| NV-06 | PASS | `RESULT: PASS`, `import_check_exit=0`: ICD and `libgb_nvk.so` `317 undefined dynamic symbols, 0 unresolved`, `nvk_probe` `47 ... 0 unresolved`. |
| NV-02 | PASS | both entry paths `RESULT: NO_DEVICE`, `exit=3`; hmi_check `RESULT: PASS (0 failed checks)`. |
| NV-03 | PASS (same criterion as above) | drm-shim TU117: hmi_check `PASS (0 failed checks)`; every probe mode reaches its first device call (`NVK vendor=0x10de device=0x1004`, `3072 MiB DEVICE_LOCAL`, 179 extensions, pipelines compiled in 2.3-3.3 ms, GrallocUsage2 `consumer=0x100 producer=0x200`, GPDIFP2(AHB) `VK_ERROR_FORMAT_NOT_SUPPORTED` as expected). The four probe runs end `RESULT: FAIL exit=1` (no pixels from the noop shim, allocator blocks in the chroot), exactly as in Wave 1. |
| `gb_nvk.rc` v1.1 lint | PASS | `rc_lint: .../gb_nvk.rc: 1 services, 0 findings` (sha `748963f3...`). |

## 4. Deviations from the plan / spec (with reasons)

| # | Plan / task text | What was done | Why |
|---|---|---|---|
| NV-D1 | P0-3: "carve 276.R4 system+vendor read-only from `C:\GB-x86\lapis.bin` with `carve_lp.py`" | Did **not** read `lapis.bin`. The needed 276.R4 directories (`system/lib64`, `apex/com.android.runtime`, `vendor/lib64`, `system/bin/{sh,toybox,...}`, `llndk.libraries.txt`) were streamed read-only from the running VM over the allowed read-only adb (`adb exec-out "cd / && tar -cf - ..."`) into builder scratch. | The hard rules say never to touch `lapis.bin` (the running VM's disk). The adb stream gives the same files and the same build id (`getprop ro.build.id` = CL3B.260622.276.R4) without opening the VM disk. `/system/build.prop` and `/vendor/build.prop` were not readable as shell. |
| NV-D2 | G0 step 1: AHB usage `CPU_WRITE_RARELY|GPU_COLOR_OUTPUT|GPU_SAMPLED_IMAGE` | `ahb-i915-readback` adds `CPU_READ_RARELY` | The NDK contract for `AHardwareBuffer_lock` requires a CPU read usage at allocation for read locks (hardware_buffer.h). Both are SW bits, so minigbm's i915 LINEAR choice is the same. |
| NV-D3 | "official SDK tools via sdkmanager" | Used the existing official SDK copy on the builder read only (build-tools 36.0.0, platforms android-36 rev 2), as INTERFACES 1.4 allows. Nothing new was downloaded. | Same official tools; no licence prompt had to be accepted on anyone's behalf. `zipalign -P 16` is supported by this version. |
| NV-D4 | Patch order 0003, 0004, 0005, 0006 | The series is applied as 0001 0002 0004 0005 0006 **0003**. 0003 is the last diff and only in set 8b. | 0003 (AHB) is the 8b-only patch. Putting it last keeps 0004-0006 identical whether or not 0003 is applied (verified: same bytes in set 8a and 8b). |
| NV-D5 | 0003 = "rebased !33700" | Rebased into Churro's structure, not the MR's | The MR calls `vk_image_is_android_hardware_buffer()` before `vk_image_init()` (always false), spoofs the tiling, and runs the full `nvk_image_init()` (with `vk_image_init()`) a second time at bind. Churro double-frees the deferred info on 26.3. 0003 adds `nvk_image_init_layout()` instead. |
| NV-D6 | 0004 "when the gralloc device is foreign" | Foreign = `/dev/dri/renderD128` is not NVK's own render node, with the override `debug.mesa.nvk.anb.linear=0/1` | u_gralloc does not expose its DRM device. minigbm opens the first node it can drive, and the system rule is i915 = renderD128 (INTERFACES 2.2). |
| NV-D7 | 0004 "GrallocUsage2/3/4" | Usage (gralloc0) and Usage2 are wrapped. Usage3/4 are not exposed by NVK 26.3 (none in vk_common), so A17 libvulkan uses Usage2. | Nothing to wrap. `a17-swapchain-model` would report a 3/4 entry point if one appeared. |
| NV-D8 | (spec) payload ICD build | 8a also gets `-z max-page-size=16384` and statically linked NDK zlib (shader cache). The `base` set keeps the old link line. | 16 KiB segments are needed for the APK copy (INTERFACES 13.3), and the same link step makes both files. Static zlib keeps NEEDED within LLNDK + libhardware (NV-06). `libz.so` is not LLNDK on lapis. |
| NV-D9 | (spec 14) shim loads `android_load_sphal_library("vulkan.intel.so")` | Same call, but the library name is built at run time from `ro.hardware.vulkan` (fallback `ro.board.platform`, then `intel`), like libvulkan does. A loader result that is the shim itself is rejected, and then `/vendor/lib64/hw/<name>` and `/odm/lib64/hw/<name>` are tried through the same sphal loader. `libvndksupport` is resolved with dlopen, not linked. | Without a sphal namespace (chroot, odd configs), `dlopen("vulkan.intel.so")` returns the shim itself. The bionic chroot test exercises exactly that path. Runtime resolution keeps NEEDED at libc/libdl/liblog. |
| NV-D10 | Mesa option names | `NVK_ANB_LINEAR` -> `debug.mesa.nvk.anb.linear`, `NVK_ANB_WAITIDLE` -> `debug.mesa.nvk.anb.waitidle` | `vendor.mesa.nvk.android.waitidle` would have been 32 characters. The new names fit 31 with both prefixes. |
| NV-D11 | Plan 7 "GbGfxDriver.apk: VM-specific build for fallback test" | One build (`vulkan.intel.so`) for native and VM | INTERFACES D12: the VM's `ro.hardware.vulkan` is also `intel` (getprop re-checked 2026-10-25). `build_shim.sh HAL=<x>` can build another name if ever needed. |

## 5. Spec questions

(v1.2 status of Q1-Q3: section 7.6.)

* **Q1 (GD-05 on the VM).** INTERFACES 3 says everything acts only when `/odm/etc/gb_hw` is `native`, so
  `gb_gfxdriver.sh` refuses on the VM. GraphicsEnvironment also only accepts a **system** driver package
  (`MATCH_SYSTEM_ONLY`), so `adb install` alone cannot test it. GD-05 therefore needs a VM test image that contains
  the APK in `/odm/app` and sets `ro.gfx.driver.0`. Options:
  * (a) a VM-test-only build flag that installs `gfxdriver.json` and a one-line variant of `gb_gfxdriver.sh` without
    the `native` check (never in a shipped VM image);
  * (b) accept that GD-05 is mostly covered by the bionic chroot run 2. That run loads the VM's own platform HAL
    through the shim's loader (the VM's `vulkan.intel.so` is SwiftShader, vendor 0x1ae0) and shows NVK absent or
    failing -> platform device only, no crash. The zygote preload/unload/reload part would then be tested on
    hardware.
* **Q2.** `gfxdriver.json` location: INTERFACES 1.3 says `gfx-driver-pkg\out\`, README W2-01 says
  `gfx-driver-pkg\gfxdriver.json`. Both are present and identical. Wave 2 should pick `out\`.
* **Q3.** INTERFACES 14 says NVK is enumerated only when "the nouveau render node opens". The shim checks that as the
  app's uid, which in normal boots (0660 root:graphics) fails for every app. That is intended (2.3), but it means the
  experiment depends on nouveau-policy's 0666 in gfxdriver boots. Please confirm.

## 6. Hardware-only checks (not run)

* **Gate G0:** `nvk_probe -t ahb-i915-readback` on the GTX 1650 with i915 gralloc. Both read-backs must be bit-exact.
* **`nvk_probe -t a17-swapchain-model`:** PASS with 0004, and the printed modifier must be LINEAR. This confirms
  that Google's minigbm build follows upstream's SW-usage rule.
* **The 0006 sync_fd** consumed by SurfaceFlinger's RenderEngine (ANV): no tearing, no stalls.
  `debug.mesa.nvk.anb.waitidle=1` is the fallback.
* **Opted-in vkcube / ANGLE app in a gfxtest boot:**
  * device order ANV, then NVK;
  * HWUI on ANV (`dumpsys gfxinfo`);
  * the app's NVK device renders;
  * `ro.gfx.driver.0` set from the shell domain.
* **Base detached:** the opted-in app works on ANV.
* **Detach while running:** gb_power refuses.
* **Zygote preload / unload / reload** of the driver namespace; repeated app start and stop.
* **dEQP / CTS:** dEQP-VK.wsi.android.*, android_hardware_buffer.* (8b), CtsGraphicsTestCases.
* **Frame time and PCIe copy cost** at 3000x2000.
* **Kernel fence stall test** (plan 8).

## 7. v1.2 (wave 1b): graphics switching

Component 4 against INTERFACES.md **v1.2** (sections 13.3, 14, 24, 25.3; decisions D3, D4, D5) and `1b\TASKS_1B.md`
v1.2 section 3, rebased on the v1.1 code of TASKS_1B 0.1 (`gb_shim.c` `ef329edd`, `nvk_probe.c` `5c7a47ac`, ...; all
baseline shas re-checked before editing). Work done 2026-10-25 on the Windows tree and the builder
(`/build/native/dgpu/gfx-driver-pkg`, `/build/native/nvk-android`), `nice -n 19 ionice -c3`, light jobs only (no NVK
rebuild); free space during the work: `/` 27 GB, `/build` 12 GB. No shared image file was edited.

### 7.1 Artifacts (sha256)

| File | sha256 | Notes |
|---|---|---|
| `C:\GB-build\native\gfx-driver-pkg\out\GbGfxDriver.apk` | `fd9bd6f711d7c231bbea0b750f064cdd51d2b71d62aec7835031443f005c6cd7` | 16499048 bytes, versionCode 29882228, build_time `L1792933682`, label `NVIDIA app graphics`. Members exactly: `AndroidManifest.xml`, `resources.arsc`, `assets/sphal_libraries.txt` (0 bytes), `lib/x86_64/libgb_nvk.so` (16425512, STORED @16384), `lib/x86_64/vulkan.intel.so` (44872, STORED @16449536). **No `assets/allowlist.txt`.** v2 + v3, v1 off. Replaces `aed28e62...` (now in `superseded\`). |
| `...\gfx-driver-pkg\out\GbGfxDriver.apk.sha256` | `01b6fe6a3767235026fe80fcb51aedeb188dfb7dd160e5755e78675b827bdbf0` | sha256sum line of the APK. |
| `...\gfx-driver-pkg\out\apk-libs\vulkan.intel.so` | `19b3fe6f0ed0dce4af885f65970093f487d0d62dcdfce2818f16e7bd6c7c8aa1` | v1.2 shim as packed (= the APK member). NEEDED libdl, liblog, libc; exports `HMI` only; 16 KiB LOAD alignment. Two NDK builds identical. Unstripped (builder only) `8cbcbd34...`. Replaces `694760ab...`. |
| `...\gfx-driver-pkg\out\apk-libs\libgb_nvk.so` | `6bb6dc80afac7a73c3131b6c70e322bf3c322f0c0057adf2b0581152f673cfb9` | Unchanged NVK 8a (= the APK member; the `nvk=` field of `gfx_build_id`). |
| `...\gfx-driver-pkg\out\gfxdriver.json` | `840d0666ef670616fb5c05a518d0c34ba8e160e08a207c83831cd2fabdd07690` | Exactly `TASKS_1B.md` 3.3 (byte check PASS); identical copy `gfx-driver-pkg\gfxdriver.json`. |
| `...\gfx-driver-pkg\out\gb_gfxdriver.sh`, `.rc` | removed | Retired (T3.9); sources in `gfx-driver-pkg\superseded\` (`c28f4d8c`, `a49ed94b`) with `README.txt`. |
| `C:\GB-build\native\nvk-android\bin\nvk_probe` | `3d53a7e942ca67a82c1b4934299b5e60524eb7a7f1f10cc8d14b089cb06addbd` | v1.2 probe (supervision, 7.2). NEEDED libdl, libnativewindow, libc (unchanged); 58 imports, all resolved against 276.R4. Goes to `vup/bin/nvk_probe` as before. Replaces `412e080a...` (`nvk-android\superseded\`). The `probe=` field of `gfx_build_id`. |
| `...\nvk-android\bin\nvk_probe_host` | `1fe8c49d21fb65ea623a4defcd793640a83228e52f04965dc96507704f0c7c6c` | glibc twin (validation only; replaces `53d024f8...`). |
| `...\nvk-android\bin\vulkan.nouveau.so` | `625314e09c8f9adb82d57b8abfd6ea2e855308a1ea79c59f6670783e52f2409b` | Unchanged payload ICD (the `icd=` field of `gfx_build_id`). |
| `...\nvk-android\gb_nvk.rc` | `748963f3630b641869aa862523c1d2b9ee26213437633551d8ff5b81c733d666` | Unchanged (INTERFACES 15.5 has no v1.2 change). |

Sources (sha256, first 8): `gfx-driver-pkg\shim\gb_shim.c` `c49560ed`, `test\test_shim.c` `fe59f52b`, `test\fake_icd.c`
`14742e95` (unchanged), `test\run_chroot_tests.sh` `35781188`, `test\test_apk_negative.sh` `22a443cf` (new),
`test\check_manifest.py` `5f9b1ceb` (new), `apk\AndroidManifest.xml.in` `00985ef9`, `apk\apk_tool.py` `ac2d8686`,
`apk\build_apk.sh` `623727ed`, `apk\check_apk.sh` `f87a9318`, `scripts\build_shim.sh` `0e73c0b3` (unchanged),
`scripts\run_host_tests.sh` `32dca1f7` (new), `out\gen\gb_shim_tramp.inc` `4689c5f7` (unchanged);
`nvk-android\src\nvk_probe.c` `dcaeadca`, `verify\selftest_invocation.sh` `b588495d` (new),
`verify\probe_host_checks.sh` `89562d2b` (new). The builder copies are byte-identical (checked).

### 7.2 What changed (TASKS_1B 3.1)

| # | Done |
|---|---|
| T3.1 | `struct gb_instance` gains `want_nvk`; `pds[]` is now kept in the app's order (NVK first). |
| T3.2 | `gb_CreateInstance()`: after the base `CreateInstance`, `want_nvk` = `vendor.gb.gfx_active` exactly `nvidia` and `gb_hwui_vulkan()`; the framework rule is unchanged. One info line per instance when `gfx_active` is not `nvidia`. |
| T3.3 | `gb_hwui_vulkan()`: process-wide `pthread_once`, decided at the process's first `vkCreateInstance` of any kind (GD-1B-D1); `debug.hwui.renderer` set and not `skiavk*`, or unset with `ro.hwui.use_vulkan` not `true` -> no NVK in this process; logged once. |
| T3.4 | `gb_nvk_enumerate()`: the `vendor.gb.gfxdriver` check is gone (`!si->want_nvk` returns); it fills a separate array and returns the count; `gb_nouveau_ready()` unchanged. |
| T3.5 | `gb_enumerate_locked()` orders NVK first, then platform; `gb_EnumeratePhysicalDeviceGroups()` writes one group per NVK device first, then the base groups (or synthesised ones) into the remaining capacity, `VK_INCOMPLETE` when short, the caller's `sType`/`pNext` kept. Registry unchanged. |
| T3.6 | Header comment: the five v1.2 rules; the HAL module name is now `GbGfxDriver shim (NVK first while vendor.gb.gfx_active=nvidia)`. |
| T3.7 | Manifest label `NVIDIA app graphics` (+ comment); `build_apk.sh` asserts that the assets dir holds only `sphal_libraries.txt` and fails on any `allowlist.txt` in the result; `apk_tool.py` `FORBIDDEN = ["assets/allowlist.txt"]` (plus any member named `allowlist.txt`) and `add-libs` refuses such an input; `check_apk.sh` checks the label, the absence, `sphal_libraries.txt` and the exact member list. |
| T3.8 | `gfxdriver.json` and `out\gfxdriver.json` = 3.3. |
| T3.9 | `gb_gfxdriver.sh`, `gb_gfxdriver.rc`, `test\test_gb_gfxdriver.sh` moved to `superseded\`; `out\` has no `gb_gfxdriver.*`. |
| T3.10 | `test_shim.c` rewritten for GD-1B-01..09 (guard cases in forked children before the process's first instance; as root, a uid-9999 child for the closed-node case); `run_chroot_tests.sh` header; new `run_host_tests.sh`, `test_apk_negative.sh`, `check_manifest.py`. |
| T3.11 | `gfx-driver-pkg\README.md` rewritten; this section. |
| probe | "The self-test probe invocations nvk_probe needs" (GD-1B-D2): in `nvk_probe.c`, TERM/INT/HUP SIGKILL the running test child, reap it (at most 5 s) and then end the probe with that signal; `PR_SET_PDEATHSIG(SIGTERM)` on the probe and `PR_SET_PDEATHSIG(SIGKILL)` on each test child; a parent-side `-T` deadline (+5 s); a test asked for by name but missing from the build, or a run with no test, is never a PASS (exit 1); an explicit `-d`/`-A` that does not exist is exit 2 (it used to fall back silently to `$NVK_ICD`/the defaults). See `nvk-android\README.md` "Self-test invocations" and `INTEGRATION_NOTES.txt` 10. |

### 7.3 Tests run (2026-10-25, builder; logs in `gfx-driver-pkg\out\logs\v12\` and `nvk-android\logs\wave1b\`)

| Id | Result | Evidence |
|---|---|---|
| GD-1B-01 | PASS | `gd1b_host.log`: `count query: 2 device(s) (platform 1 + NVK 1)`, `NVK first ('fake NVIDIA TU117 (test ICD)' 0x10de), platform after ('llvmpipe (LLVM 20.1.2, 256 bits)' 0x10005)`, `capacity 1: VK_INCOMPLETE, the one device is NVK (0x10de)`, `groups capacity 1: VK_INCOMPLETE, the NVK group`, `groups capacity 2: NVK group then the first platform group (VK_SUCCESS)`, `groups full: NVK first, same order as vkEnumeratePhysicalDevices, sType kept`. |
| GD-1B-02 | PASS | `intel`, unset, empty, `NVIDIA`, `nvidia ` (trailing space), ` nvidia`, `nvidia1`, `nvidi` and a 91-character value starting with `nvidia`: each `-> platform only`, `libgb_nvk` never mapped. |
| GD-1B-03 | PASS | `pEngineName 'android framework' (HWUI)`, `pApplicationName 'android framework'`, `pApplicationInfo NULL`: platform only, NVK not loaded; `engine 'ANGLE' with HWUI on Vulkan -> NVK first`. |
| GD-1B-04 | PASS | `A created with nvidia, property then intel, A enumerated -> NVK first`; `B created after the change (intel) -> platform only`; and the reverse direction. |
| GD-1B-05 | PASS (11 cases, each in a fresh child process) | `skiagl` -> platform only; unset + `false` -> platform only; unset + unset -> platform only; unset + `true` -> NVK first; `skiavkthreaded` -> NVK first; `skiavk` + `false` -> NVK first; `TRUE` -> platform only; process-wide: `skiagl` first, `skiavk` later -> both platform only (also when the first instance had `gfx_active=intel`); Vulkan first, `skiagl` later -> both NVK first; the app's own engine under GL HWUI -> platform only. |
| GD-1B-06 | PASS | library missing, NVK `vkCreateInstance` fails, 0 devices, no `nouveau/0000:*`, no render node, render node not openable (host: mode 0000 as the owner; chroot: `root test: child dropped to uid 9999`): platform only, `VK_SUCCESS`, `fds 6 -> 6` (host) / `8 -> 8` (chroot); NVK `vkCreateDevice` fails -> only that call fails (-3). |
| GD-1B-07 | PASS | `gfxdriver=on + gfx_active=intel -> platform only`; `gfxdriver=off + gfx_active=nvidia -> NVK first`. |
| GD-1B-08 | PASS | `vkCreateDevice + queue + vkDestroyDevice on device 0 (NVK) and on the platform device`; lazy load; `libgb_nvk unloaded when the last instance is destroyed`; two instances share one library, unloaded after the last. |
| GD-1B-01..08 host total | PASS | `RESULT: PASS (67 passed, 0 failed)` (lavapipe + fake NVK). |
| mutation check | FAIL as required | The same test against the v1.1 shim source `ef329edd`: `RESULT: FAIL (50 passed, 17 failed)` (`gd1b_mutation_v11shim.log`): the ordering, snapshot, guard and property cases discriminate. |
| GD-1B-09 | PASS | `gd1b_chroot.log`, 276.R4 chroot, drm-shim `NOUVEAU_CHIPSET=167`. Run 1 `RESULT: PASS (68 passed, 0 failed)`, including `NVK listed first: [0] 'NVK ' 0x10de`, `no file descriptor left open after vkDestroyDevice + vkDestroyInstance (8 -> 8)`, `HWUI engine with gfx_active=nvidia: platform device(s) only, NVK not loaded by this instance, fds unchanged`. Run 2 (the shim's own loader; the VM HAL is SwiftShader) `RESULT: PASS (18 passed, 0 failed)`: `[0] 'NVK ' vendor 0x10de`, `[1] 'SwiftShader Device (LLVM 10.0.0)' vendor 0x1ae0`, `gfx_active=intel: platform device(s) only (1), NVK never loaded`. |
| GD-1B-10 | PASS | `gd1b_host_asan.log`, ASan + UBSan (`-fno-sanitize-recover=all`, LSan on, lavapipe/LLVM leaks suppressed): `rc=0 sanitizer_reports=0 RESULT: PASS (67 passed, 0 failed)`. |
| GD-1B-11 | PASS | `gd1b_apk.log`: `ok label 'NVIDIA app graphics' (v1.2)`, `ok no assets/allowlist.txt (no member named allowlist.txt at all)`, `ok assets/sphal_libraries.txt present`, `ok only the expected members`, `ok zipalign -c -P 16 4`, `ok apksigner verify --min-sdk-version 36: verifies, v3`, `ok ... v2 block present and valid, v1 absent`, badging `package: name='gb.sb3.gfxdriver'`, min/target 36, `RESULT: PASS`. Negative cases (`gd1b_apk_negative.log`): APK + `assets/allowlist.txt` -> `apk_tool check` rc 1 naming the member; a nested `allowlist.txt` rc 1; `check_apk.sh` rc 1 on the allowlist check; `add-libs` refuses and writes nothing: `RESULT: PASS (8 passed, 0 failed)`. |
| GD-1B-12 | PASS | `check_manifest.py`: both manifests equal TASKS_1B 3.3 byte for byte (309 bytes), `out/ has no gb_gfxdriver.*`; `gd1b_repro.log`: two `build_shim.sh` runs -> `19b3fe6f...` `identical`. |
| GD-1B-13 | PASS | The wave-1 GD-01 checks are kept in the suites above (HAL contract, magic overwrite, lazy load/unload, gates, failures, GIPA surface); GD-02 is rewritten as GD-1B-02/07; GD-03 = `check_apk.sh` with the v1.2 label; GD-04 is retired with `gb_gfxdriver.sh` (TASKS_1B 9). |
| shellcheck | PASS | `build_apk.sh`, `check_apk.sh`, `run_host_tests.sh`, `build_shim.sh`, `run_chroot_tests.sh`, `test_apk_negative.sh`, `selftest_invocation.sh`, `probe_host_checks.sh`: 0 findings. |
| self-test invocations | PASS | `nvk-android\logs\wave1b\selftest_invocation.log` (276.R4 chroot, its toybox `timeout`, drm-shim TU117; in a chroot the gralloc allocation blocks, i.e. a probe stuck in the stack): S1 `timeout 150 nvk_probe -t ahb-i915-readback -T 60` `rc=1 after 60 s verdict='fail ahb-i915-readback' left=0`; S2 (a17) `rc=1 after 61 s`, `left=0`; S3 `timeout 5` `rc=124`, `left=0`; S4 `-s KILL` `rc=137`, `left=0`; S5 `timeout` SIGKILLed: `left=0`; S6 TERM to the probe `rc=143`, `left=0`; S9 exactly `gb_dgpu_report.sh` `st_probe()`/`st_interrupted()` (`kill $PROBE_PID` = the `timeout` pid): `left=0 after <= 400 ms`; S7 no drm-shim `rc=3 'error nodevice'`; S8 missing `-d` `rc=2 'error icd'`. The same paths with the v1.1 probe: S5-v11 `left=2`, S6-v11 `left=1` (orphan, ppid 1), S9-v11 `left=2`. |
| probe host | PASS | `probe_host_v12.log` `RESULT: PASS (11 passed, 0 failed)`: lavapipe offscreen 0; an Android-only mode by name on the host build 1 (`RESULT: FAIL (a requested test is not in this build)`); `-t all` 0; unknown 2; host NVK 3; missing `-d` with a valid `$NVK_ICD` 2; TERM 143 and SIGKILL with no process left. |
| NV-02 / NV-03 / NV-06 (new probe) | PASS | `checks_8a-1_v12probe.log`: import check `317 ... 0 unresolved` (ICD, `libgb_nvk.so`) and `58 undefined dynamic symbols, 0 unresolved` (`nvk_probe`), `RESULT: PASS`; both entry paths `RESULT: NO_DEVICE`, `exit=3`; hmi_check `PASS (0 failed checks)`; with the drm-shim every mode reaches its first device call (`[0] NVK vendor=0x10de device=0x1004`, pipelines compiled in 2.7-6.5 ms, `vkGetSwapchainGrallocUsage2ANDROID -> VK_SUCCESS consumer=0x100 producer=0x200`), then the allocator block -> `TIMED OUT`, `exit=1`, as in wave 1. |
| NV-01 / NV-04 / NV-05 | not re-run | NVK, patches and `apply_patches.py` are unchanged in v1.2 (the 7.1 hashes equal section 1's). |
| GD-05 (VM) | SKIP | No VM install or boot (needs the user's approval); v1.2 ships no package in the VM image at all (VM-1B-01); see 7.6 Q1. |

### 7.4 Wave 2 (shared files; recorded, not edited) - supersedes 2.2, 2.3 and 2.6

* **W2G-01** `inject\native\components\gfxdriver.json` = a copy of `gfx-driver-pkg\out\gfxdriver.json`
  (`840d0666...`): the APK only (`app/GbGfxDriver/GbGfxDriver.apk` 0644), no rc, `build_prop`
  `ro.gfx.driver.0=gb.sb3.gfxdriver`. Never in the VM tree.
* **nvk.json** (2.1): the layout is unchanged, but `bin\nvk_probe` is now `3d53a7e9...`. nouveau-policy's `selftest`
  verb depends on the v1.2 probe: its trap kills the `timeout` pid (S9), which with the v1.1 probe left the probe and
  its test child running with the device open after the ESP lock was released (S9-v11). Do not pair the 1b
  `gb_dgpu_report.sh` with `412e080a...`.
* **W2G-02** `/odm/etc/gb/gfx_build_id` for this build: `apk=fd9bd6f711d7c231bbea0b750f064cdd51d2b71d62aec7835031443f005c6cd7`
  `icd=625314e09c8f9adb82d57b8abfd6ea2e855308a1ea79c59f6670783e52f2409b` `nvk=6bb6dc80afac7a73c3131b6c70e322bf3c322f0c0057adf2b0581152f673cfb9`
  (the APK member, = `apk-libs\libgb_nvk.so`) `probe=3d53a7e942ca67a82c1b4934299b5e60524eb7a7f1f10cc8d14b089cb06addbd`
  `pay=<build_esp>`. build_esp should compute every value from the bytes it puts into the tree (W2G-06 item 3), not
  copy them from here.
* **W2G-06** item 2 can reuse the component check: `python3 -I C:\GB-build\native\gfx-driver-pkg\apk\apk_tool.py check
  <the APK in the tree>` (exit 1 on any member named `allowlist.txt`, a missing required member, or compressed or
  unaligned libraries). The APK in the tree must be `fd9bd6f711d7...`.
* **W2G-03**: the HWUI asserts matter to the shim's guard: with `debug.hwui.renderer` set to `skiagl` (or
  `ro.hwui.use_vulkan` not `true`) every app process stays on ANV (GD-1B-05).
* **W2G-11** pre-flight: `gfxdriver.json` and `nvk.json` must point at the 7.1 files; section 1's hashes for the APK,
  the shim, `nvk_probe` and `gb_gfxdriver.*` are stale.

### 7.5 Deviations (`GD-1B-Dn`)

| # | Spec / task | Done | Why |
|---|---|---|---|
| GD-1B-D1 | T3.3 "process-wide, `pthread_once`" (the trigger point is not specified) | The guard is decided at the process's first `vkCreateInstance` of any kind, also when `gfx_active` is not `nvidia`. | As close to process start as the shim gets, so it sees the `debug.hwui.renderer` that HWUI read at its own start; a later change of the debug property cannot flip the decision (GD-1B-05 process-wide cases). |
| GD-1B-D2 | TASKS_1B 3.1 lists no `nvk_probe` change; the task asked for "the self-test probe invocations nvk_probe needs" | `nvk_probe.c` supervision, exit 1 for a missing or unrun test, exit 2 for a missing explicit ICD (7.2). Exit codes 0/1/2/3 and every mode's own logic are unchanged; new: the probe ends with the stop signal it got. | 25.3 runs the probe unattended as root (root ignores the node's chmod 000), and the 1b script's trap kills only the `timeout` pid: without these changes a stopped self-test left processes holding nouveau (S5-v11, S6-v11, S9-v11). The `access()` fallback could have run a different ICD than the one named. |
| GD-1B-D3 | (robustness) | Registry full -> the NVK instance is destroyed and the library released (it used to stay until `vkDestroyInstance`); a failed base registration unregisters its partial entries. | Never fatal, nothing held without devices. |
| GD-1B-D4 | (logs) | Property values in logs are cut to 24 printable characters; the NVK line reads `NVK: <n> NVIDIA physical device(s) first, then <m> platform device(s)`. | Any app can set `vendor.*`; H-1B-03 greps `first`. |
| GD-1B-D5 | GD-1B-11 | `check_apk.sh` also asserts the exact member list. | Stricter than asked: any new asset fails the check. |
| GD-1B-D6 | T3.9 "sources kept under `superseded\`" | Kept by sha256 prefix with `superseded\README.txt`, together with the v1.1 shim and test sources, manifest, APK and packed shim. | Traceability, as in `nvk-android\superseded\`. |

### 7.6 Spec questions and notes for other components

* **Q1 (wave 1) -> resolved by v1.2.** The package is native-only (VM-1B-01); in the VM only the zygote path would be
  left to test, which is H-1B-13. GD-05 is replaced by GD-1B-09 run 2 (the VM's own SwiftShader HAL through the
  shim's loader: platform only with `intel`, NVK first with `nvidia`, no crash).
* **Q2 (wave 1)**: still two identical copies; Wave 2 uses `out\gfxdriver.json`.
* **Q3 (wave 1) -> answered by v1.2 23.5**: the node is 0666 only while gb_power itself derived `nvidia`; the shim's
  "node opens as the app's uid" gate depends on exactly that (GD-1B-06 closed-node case).
* **Q-1B-1 (nouveau-policy, `gb_dgpu_report.sh` `st_interrupted()`).** The trap kills `$PROBE_PID` (the `timeout`
  pid) and then removes the marker and releases the ESP lock at once. The v1.2 probe is gone within 400 ms (S9), but
  the script does not wait for it. Suggest a bounded wait (for example up to 6 s until `pgrep -x nvk_probe` is empty)
  before `st_unmark`/`unlock_esp`. A test child stuck in a kernel ioctl cannot be killed by anyone (25.6's
  `selftest-hang` covers that).
* **Q-1B-2 (nouveau-policy, gpu-ui 27.6).** With `-T 60` inside `timeout 150`, a hung probe test is ended by the
  probe's own alarm: exit 1 and verdict `fail <mode>` (the report shows `TIMED OUT`), not `fail <mode> timeout`
  (S1/S2). The 124/137 path happens only if the probe's parent itself hangs. This matches 25.3 step 7 as written; it
  only matters if the UI wants to say "timed out".
* **Q-1B-3 (nouveau-policy NP-1B-D11, consistent).** `ctl.stop` of a service without `gentle_kill` SIGKILLs its whole
  process group/cgroup (AOSP init, INFERRED), which also kills the probe and its test child; the probe changes are
  for the TERM paths (the script's trap, shutdown) and for a dying `timeout`.
* **Q-1B-4 (gate strength).** `a17-swapchain-model` passes without the ANV pixel check when the ANV leg cannot load
  (wave-1 design), but `ahb-i915-readback` fails when ANV is unavailable, so the two-probe verdict cannot pass without
  a bit-exact read-back on Intel. Recorded, no change.
* **Q-1B-5 (Wave 2).** `nvk_probe` is not bit-reproducible (`__DATE__`/`__TIME__` in its banner, as in wave 1); its
  hash is part of the self-test identity, so Wave 2 should package `bin\nvk_probe` as delivered (a rebuild only costs
  one extra self-test).

* **Q-1B-6 (INTERFACES 14 rule 3, GD-1B-05; for the spec owner).** The guard treats any `debug.hwui.renderer` value
  that *starts with* `skiavk` as "HWUI on Vulkan" (implemented exactly so; `skiavkthreaded` -> NVK first is a
  GD-1B-05 case). But lapis 276.R4 `/system/lib64/libhwui.so` (the read-only rootfs copy on the builder) contains only
  the renderer strings `skiagl` and `skiavk` (`strings -a | grep '^skia(gl|vk)'`; no `skiavkthreaded`), which fits
  AOSP HWUI comparing the value for equality with `skiavk` and falling back to SkiaGL otherwise (INFERRED from AOSP,
  `Properties.cpp` is not in the fetched a17 sources). Then `skiavkthreaded` (or any other `skiavk...` value) would
  put HWUI on GL, i.e. on ANGLE with engine `ANGLE`, while the guard lets NVK in: exactly the case rule 3 exists for.
  Inert on lapis (the property is unset and W2G-03 asserts that no rc sets it), but only an exact match is safe.
  Proposed v1.3 change: "set and not exactly `skiavk`" (`gb_shim.c` `gb_hwui_once()`: `!strcmp(r, "skiavk")` instead
  of `!strncmp(r, "skiavk", 6)`; GD-1B-05 `skiavkthreaded` -> platform only). The `ro.hwui.use_vulkan` rule is
  fail-closed already: HWUI's boolean sysprop parser also accepts `1`/`yes`/`on` (INFERRED), for which the shim (exactly `true`) keeps NVK off.

### 7.7 Hardware-only checks (HW, not run)

* G0 on the TU117 + i915: `nvk_probe -t ahb-i915-readback` and `-t a17-swapchain-model` PASS as the automatic
  self-test (H-1B-01: `selftest-<N>.txt` in gblog, record `verdict=pass`).
* H-1B-02/03: HWUI on ANV (`ro.hwui.use_vulkan=true`, `debug.hwui.renderer` empty) while a game renders on NVK;
  logcat `gb_gfxdriver: NVK: 1 NVIDIA physical device(s) first, then 1 platform device(s)`; an ANGLE GLES game on
  NVK (not covered by the self-test).
* H-1B-04: an app that is not opted in (or is Always Intel) does not map the APK's `vulkan.intel.so`; `all_apps=3`
  removes the package from every newly started app.
* H-1B-05/06/07: after a detach, Intel mode or a fault, new instances are platform-only; open apps keep their GPU until
  restarted; a detach with an NVK app open is refused by gb_power (the shim holds the node only through NVK).
* H-1B-12: `untrusted_app_25..32` (r2 correction: permissive too, INTERFACES 17 R-14): a plain functional check, an
  old-targetSdk game with "Use NVIDIA" reaches NVK; the shim would stay on ANV on a denial.
* H-1B-13: zygote preload / unload / reload of the driver namespace; repeated app start/stop.
* Still open from wave 1: the 0006 sync_fd consumed by RenderEngine, dEQP/CTS, frame time, the kernel fence stall test.

## 8. v1.2 r2: review fix pass (INTERFACES 29)

**This section supersedes the APK and shim shas of 7.1 and the test results of 7.3** (everything else in section 7
stands). Applies R-13 (both reviews and Q-1B-6: the ANGLE-HWUI guard takes exactly `skiavk`) and adds the R-11 S10
check of the self-test stop path. The pre-fix shim, test, APK and packed shim are in
`gfx-driver-pkg\superseded\v1.2r1\`; `nvk-android\verify\selftest_invocation.sh.v1.2r1` is the pre-fix script.

### 8.1 Changes

| Fix | Where | What |
|---|---|---|
| R-13 | `shim\gb_shim.c` `gb_hwui_once()` | `g_hwui_vk = !strcmp(r, "skiavk")` (was `!strncmp(r, "skiavk", 6)`); comment cites INTERFACES 14 rule 3 r2. |
| R-13 | `test\test_shim.c` GD-1B-05 | `skiavkthreaded` -> platform only; new case exact `skiavk` -> NVK first (68 host cases, 69 bionic). |
| R-11 | `nvk-android\verify\selftest_invocation.sh` | S10: gb_dgpu_report.sh r2's TERM path (`kill $!`, then `pgrep -x nvk_probe` polls, max 12 x 0.5 s) with the real bionic probe in the 276.R4 chroot. |

nvk_probe (`3d53a7e9`), `libgb_nvk.so` (`6bb6dc80`), the ICD (`625314e0`), `gb_nvk.rc` and `gfxdriver.json`
(`840d0666`) are unchanged.

### 8.2 Artifacts (sha256)

| File | sha256 |
|---|---|
| `gfx-driver-pkg\out\GbGfxDriver.apk` (16499048 bytes, versionCode 29882322, build_time `L1792939375`, same key, same members, no `allowlist.txt`) | `15a22bfd19180b45943c68fd5fdae31129d5b94d748fb94152a3e980d20271cd` |
| `gfx-driver-pkg\out\apk-libs\vulkan.intel.so` (= the APK member; two NDK builds identical) | `7675b988b86aa13eaa0c7bed8df19646fec0dfec0337fe663e4f198a6d755edf` |
| `gfx-driver-pkg\shim\gb_shim.c` | `99dc63c24ddee77eca33fd374443b592de6cd028906fc4c3fcf80d68ceebfd64` |
| `gfx-driver-pkg\test\test_shim.c` | `cd6e3d5d51e1a33f9084feebf02d822ce82af7b78e39faa93778c9a5c7b2cfe5` |
| `nvk-android\verify\selftest_invocation.sh` | `dc5f15beddd4123ddcb0406e03ce3515251c55230f7b96a945bf049a19cf55b7` |

### 8.3 Tests (builder, 2026-10-25 14:42-14:46 UTC; logs `gfx-driver-pkg\out\logs\v12r2\`)

| Id | Result | Evidence |
|---|---|---|
| GD-1B-01..08 host (lavapipe + fake NVK) | PASS | `gd1b_host.log`: `RESULT: PASS (68 passed, 0 failed)` |
| GD-1B-10 ASan + UBSan | PASS | `RESULT: PASS (68 passed, 0 failed)`, `sanitizer_reports=0` |
| GD-1B-09 bionic chroot (276.R4, drm-shim TU117, real libgb_nvk.so) | PASS | `gd1b_chroot.log`: `RESULT: PASS (69 passed, 0 failed)`, run 2 `RESULT: PASS (18 passed, 0 failed)` |
| GD-1B-11 APK | PASS | `gd1b_apk.log` `RESULT: PASS` (label, no allowlist, members, zipalign, v2+v3); negative `RESULT: PASS (8 passed, 0 failed)` |
| GD-1B-12 | PASS | `gd1b_repro.log` `identical 7675b988b86aa13e`; `check_manifest.py` `RESULT: PASS` (both json copies = TASKS 3.3 byte for byte, out/ has no `gb_gfxdriver.*`) |
| GD-R13 mutation | PASS (the r1 shim fails as required) | `gd_r13_mutation_v12r1shim.log`: the r1 shim `c49560ed` gives `RESULT: FAIL (67 passed, 1 failed)`, the one failure being the `skiavkthreaded` case |
| NV-R11 S10 | PASS | `selftest_invocation.log`: `S10 before: left=2`, `S10 R-11 wait: polls=1 (max 12) left=0`; S1-S9, S7, S8 as in wave 1b (`S9 ... left=0 after <= 400 ms`) |
| shellcheck `selftest_invocation.sh` | PASS | rc 0 |

### 8.4 Wave 2 delta

* W2G-01: unchanged (`out\gfxdriver.json`).
* The APK `gfxdriver.json` points at is the rebuilt `out\GbGfxDriver.apk` (`15a22bfd...`).
* W2G-02 `gfx_build_id`: `apk=15a22bfd19180b45943c68fd5fdae31129d5b94d748fb94152a3e980d20271cd`; `icd=`, `nvk=`,
  `probe=` unchanged (`625314e0...`, `6bb6dc80...`, `3d53a7e9...`). A device with an r1 image re-runs the self-test
  once after updating (identity change, I 25.7).
* W2G-03 unchanged; the HWUI build asserts keep the guard inert on native lapis either way.
* W2G-09: dropped (R-14; old-targetSdk domains are permissive).
