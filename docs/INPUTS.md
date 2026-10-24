# External inputs

`build_esp.py` and `build_usb.py` combine this repository's sources with files that are **not** in it, because they
are Google's, third-party binaries, firmware, or build outputs. This page lists every one: where it goes in the
workspace, where it comes from, and the checksum of the copy we use (md5), so you can tell whether yours matches.
Some optional VM inputs (Mesa, SwiftShader, patched minigbm, `gb_vmtools`, `gb_absmouse`, `gb_slotsync`, the EDID,
network ADB) switch their feature off when missing (`build_esp.py` prints `... off`). Everything else must be
present, or `build_esp.py` stops with a missing-file error. That covers the OS image, kernel and modules, busybox,
systemd-boot and the KeyMint graft. It also covers `tinymix` and `volume_curves.xml` for VM sound (on as soon as
`inject/vmaudio/gb_vmaudio.sh` exists, and it is in the repository). Finally it covers every file listed in
`inject/native/components/*.json`: native component binaries (iptsd, gb_power, tinyalsa, the camera HAL tree, the
sensors overlay and probe, NVK and its probe), the test tone, `volume_curves.xml`, the SOF firmware and the Realtek
USB Ethernet firmware. Those manifests are read for every build, VM builds included: to build without a component
(for example a VM-only workspace), move its manifest out of `inject/native/components/`.

## The OS image (required, obtain it yourself)

| Workspace path | What | Notes |
|---|---|---|
| `lapis.bin` | Googlebook (ASUS, "lapis") recovery image `android-desktop_signed_recovery_image.bin`, 19,069,493,760 bytes, build **CL3B.260622.271.R1** (ab/16452207); zip entry CRC-32 `52bcf144` | Google's software. Not distributed here or anywhere by this project. Rename it to `lapis.bin`, then `grow_esp.py` and `grow_userdata.py` once ([VMWARE.md](VMWARE.md)). Later builds come by OTA. |
| `boot/generic_ramdisk.lz4`, `boot/vendor_ramdisk.lz4`, `boot/vendor_bootconfig.txt` | slot A ramdisks | optional fallback only: normally read from lapis.bin (`extract_slot.py`) |

## Built by you from this repository

| Workspace path | Built by |
|---|---|
| `boot/kernel.bin`, `inject/modules/*.ko` (VM), `inject/native/modules/*.ko` + `modules.load` / `modules.load.late` (native) | Kleaf on the builder ([kernel/README.md](../kernel/README.md)), staged by `stage_kernel.py` |
| `inject/vmtools/gb_vmtools`, `inject/vmtools/gb_layout`, `inject/mouse/gb_absmouse`, `inject/slotsync/gb_slotsync`, `inject/vkenum` (diagnostic) | [guest/build.sh](../guest/build.sh) (NDK r27c; reproduces our binaries bit for bit) |
| `inject/edid_1280x800.bin` | `gen_edid.py` |
| `inject/mesa/*.so` (+ the empty marker file `inject/mesa/ENABLE_SKIAGL` to make Mesa svga the system GLES driver), `inject/mesa/egl_probe` | [graphics/mesa/build_mesa.sh](../graphics/mesa/build_mesa.sh) + our Mesa patch ([graphics/README.md](../graphics/README.md)) |
| `inject/vulkan.pastel.so` (SwiftShader Vulkan HAL for the VM) | [graphics/swiftshader/](../graphics/swiftshader/) (notes + patchers) |
| `inject/vmaudio/tinymix`, native `tree/vup/bin/tiny*` | `native/audio/src/build_tinyalsa.sh` (tinyalsa v2.0.0 + our patch, static) |
| `native/audio/tree/gb_test_tone.wav` | `native/audio/tools/gen_test_tone.py` (bit-identical to ours) |
| native component binaries: `native/iptsd/bin/iptsd*`, `native/power/bin/gb_power` (static) | each component's build script, see [native/README.md](../native/README.md) |
| `native/camera-userspace/odm/` (`lib64/hw/camera.sb3.so`, libcamera, the soft IPA + its signature, `lcam-probe`, `etc/libcamera/*.yaml`) | `native/camera-userspace/build-scripts/` (deps, configure, patches, `stage.sh`) on the builder |
| `native/sensors/overlay/GbSb3SensorsOverlay.apk`, `native/sensors/bin/gb_hal_probe` | `native/sensors/overlay/build_overlay.sh` (signs with a key it generates locally), a static build of `native/sensors/test/hal_probe.c` |
| `native/nvk-android/bin/vulkan.nouveau.so`, `nvk_probe` | `native/nvk-android/scripts/` (Mesa 26.3.0-rc2 + our patches, NDK r27c) |
| `host/gb_hostbatt.exe` | `csc -nologo -target:winexe -out:gb_hostbatt.exe gb_hostbatt.cs` (in-box .NET Framework compiler) |

## Third-party binaries you fetch

| Workspace path | Source | md5 of ours |
|---|---|---|
| `inject/busybox` | static x86_64 BusyBox **1.35.0** (busybox.net binaries) | `ebce43017d2cb316ea45e08374de7315` |
| `debs/systemd-bootx64.efi` | Debian `systemd-boot-efi_262.1-1_amd64.deb` (md5 `f15173a633976071a3ae10d8048814e2`), extracted with `debs/extract_sdboot.py` | `5ff67a57d269f2dfcefb5b5778eb7b8b` |

## Taken from Google/AOSP images you obtain yourself

| Workspace path | Source | md5 of ours |
|---|---|---|
| `inject/keymint/bin/android.hardware.security.keymint-service` | AOSP pure-software KeyMint from the **Android 16 (API 36) x86_64 emulator system image** `x86_64-36_r02.zip` (Android SDK manager), `/vendor/bin/hw/` of its vendor image | `5d00dedd8fe091d9309786188eb7ab52` |
| `inject/keymint/lib64/` `lib_android_keymaster_keymint_utils.so`, `libcppbor.so`, `libcppcose_rkp.so`, `libkeymaster_messages.so`, `libkeymaster_portable.so`, `libkeymint.so`, `libpuresoftkeymasterdevice.so`, `libsoft_attestation_cert.so` | same image, `/vendor/lib64/` | `b3732033...`, `c5afcb34...`, `b6d52563...`, `c16c431a...`, `7145fd71...`, `f3d6ad5f...`, `2956888f...`, `a7835a9e...` |
| `inject/keymint/lib64/libcrypto.so` | the Android 16 BoringSSL matching that KeyMint (same emulator image) | `f23f33b71a1b08d13f5ced2ad3db7846` |
| `inject/vendor_gfx/libminigbm_gralloc.so`, `libgbm.so` (VM only) | **your lapis vendor partition** (`/vendor/lib64/`), patched by [graphics/minigbm/patch_minigbm.py](../graphics/minigbm/patch_minigbm.py) | stock `86508927...` / `af2b19c9...` -> patched `a6b29a58...` / `805fdf74...` |
| `inject/vmaudio/Generic_vmware/volume_curves.xml`, `native/audio/tree/vup/etc/audio/{sof-hda-dsp_sb3,PCH_sb3_legacy}/volume_curves.xml` | **your lapis vendor partition**: `/vendor/etc/audio/sof-soundwire_cs42l43_cs35l56/volume_curves.xml` (AOSP-licensed, copied unchanged) | `dd0d9898d2b2cf108c753381412447ba` |
| `native/audio/modules/*.ko` (8 HDA codec modules, native) | **your lapis vendor_dlkm** (`carve_lp.py` + `native/audio/src/refresh_modules.sh`) | must match the booted build |
| `inject/adb/adb_keys` (optional, VM scrcpy) | **your own** `%USERPROFILE%\.android\adbkey.pub` | never commit it |

To get files out of your vendor image: `carve_lp.py OUTDIR vendor_a` (or the active slot) writes `vendor_a.img`
(EROFS); mount it read-only on the builder (`sudo mount -o ro,loop vendor_a.img /mnt/v`).

## Firmware (native only)

Staged uncompressed under `inject/native/firmware/` (-> `/odm/firmware`). From
[linux-firmware](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git) unless noted:

| Files | Device |
|---|---|
| `i915/icl_dmc_ver1_09.bin`, `i915/icl_guc_70.1.1.bin`, `i915/icl_huc_9.0.0.bin` | Iris Plus (display DMC, GuC/HuC) |
| `iwlwifi-Qu-b0-hr-b0-77.ucode`, `iwlwifi-Qu-c0-hr-b0-77.ucode`, `iwlwifi-QuZ-a0-hr-b0-77.ucode` | AX201 Wi-Fi |
| `intel/ibt-19-*.sfi` / `.ddc` (0-0, 0-1, 0-3, 0-4, 16-4, 240-1, 240-4, 32-0, 32-1, 32-4) | Intel Bluetooth 8087:0026 |
| `intel/sof/sof-icl.ri` (+ `intel-signed/sof-icl.ri`, `sof-icl.ldc`), `intel/sof-tplg/sof-hda-generic*.tplg` | SOF audio DSP (from [sof-bin](https://github.com/thesofproject/sof-bin)) |
| `intel/ipu/ipu4p_fw.bin` | IPU4P camera ISP. **Intel's proprietary firmware, not in linux-firmware and not distributable here.** Take it from the laptop's own Windows camera driver: `cpd_component_signed.bin` in `C:\Windows\System32\DriverStore\FileRepository\iacamera64.inf_amd64_*\`, copied under the new name. Check it with `python -I native\camera-kernel\tools\cpd_check.py <file>`. Without it the cameras do not appear; nothing else depends on it |
| `nvidia/tu117/...` (30 files) | only for the opt-in nouveau: `native/nouveau-kernel/firmware/fetch_tu117_firmware.sh` |

The Realtek USB Ethernet / SurfaceDock PHY firmware goes to `native/hw-gap-audit/tree/firmware/rtl_nic/` instead
(the `hw.json` manifest copies it to `/odm/firmware/rtl_nic/`): `rtl8153a-2/-3/-4.fw`, `rtl8153b-2.fw`,
`rtl8153c-1.fw`, `rtl8156a-2.fw`, `rtl8156b-2.fw` from linux-firmware's `rtl_nic/`, checked with
`sha256sum -c ../rtl_nic.sha256` inside that folder.
