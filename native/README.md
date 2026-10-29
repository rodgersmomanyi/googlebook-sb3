# Native Surface Book 3 components

Each component was built and reviewed on its own (original work dirs: `C:\GB-build\native\<name>` on Windows,
`/build/native/<name>` on the builder). Binaries, firmware, logs and research copies are not in the repository;
each component's scripts rebuild what it needs. **None of them has run on the real hardware yet.** Device-level
status: [docs/DEVICE-STATUS.md](../docs/DEVICE-STATUS.md).

| Component | What | Integrated in the native build | Off-device testing |
|---|---|---|---|
| [audio](audio/) | SOF (Ice Lake DSP) + ALC274 card config for lapis's DRAS HAL (`<soundcard>` = the ALSA card id `sofhdadsp`, wave 3), codec preload, diagnostics | yes (`inject/native/components/audio.json`) | module CRCs vs. the kernel; topology decoding; diagnostics script suite with GNU and toybox userland |
| [iptsd](iptsd/) | touchscreen + Surface Pen: iptsd 3.1.1 static NDK build, launcher, IDC files | yes (`iptsd.json`) | end-to-end with a fake IPTS uhid device |
| [power](power/) | `gb_power`: combined battery, charger, detach (DTX), dGPU hot-plug and runtime PM, Intel/NVIDIA graphics switching (the single writer of the graphics state), the control socket `/dev/socket/gb_power`, platform profile; kernel patches 0001-0003 | yes (`power.json`) | unit tests against a fake sysfs, fake clock and mock EC (`test/test_gb_power.c`, `test/test_gfx.c`), socket goldens (`test/test_socket.py`), ASan/UBSan |
| [sensors](sensors/) | ISH accel/gyro + APDS-9960 light presented as ChromeOS EC (`cros-ec-*`) IIO devices for lapis's sensors HAL (kernel patches in build #13), auto-rotate overlay | yes (`sensors.json`) | QEMU with a fake ISH (`test/`) |
| [hw-gap-audit](hw-gap-audit/) | CPU power limits (restore the Surface's PL1/PL2), `hid-surface` (Fn key not seen as a gamepad, build #13), SAM keyboard/touchpad IDCs (touchpad `Change_Timeout` 0.02, wave 3), input exclusions, Surface Pen button key layouts, the device audit | yes (`hw.json`) | host test of `gb_hw.sh` with a fake sysfs/MSR; static checks of the input files |
| [camera-kernel](camera-kernel/) | IPU4P (8086:8A19) port on the ipu6 driver + OV5693/OV8865/OV7251/DW9719 | yes: kernel build #13 + `modules.load` (needs your IPU4P firmware) | built in kernel #13 (no IPU4P in the VM: never probed) |
| [camera-userspace](camera-userspace/) | libcamera 0.7.2 (simple pipeline + software ISP) as the Android camera HAL `camera.sb3.so` | yes (`camera.json`, `ro.hardware.camera=sb3`) | HAL test against vimc on the builder |
| [nouveau-kernel](nouveau-kernel/) | the nouveau policy for the TU117 dGPU: `gb_nouveau.sh` (automatic load after boot with the base attached, bind path, `off`, crash-loop guard, kill switch), `gb_dgpu_report.sh` (report, test sequence, self-test), `gb_notify.sh`, the dGPU payload builder, the TU117 firmware fetcher with WHENCE/licence, and (wave 3) `tools/fw_coverage.py`, the firmware pinning and coverage check for every staged firmware file | yes (`nouveau.json`; modules and firmware in the `gb/dgpu-<krel>.erofs` payload; nouveau from kernel build #14, unchanged in #15) | mksh suite with GNU and toybox userland, rc lint, payload tool test |
| [nvk-android](nvk-android/) | Mesa NVK (Vulkan on nouveau) cross-built for Android with our patch set 8a (LINEAR for i915 buffers, render shadow, sync_fd release), `nvk_probe` (also the self-test probe) | yes (`nvk.json`; the ICD in the payload) | ELF/import checks against lapis 276.R4, bionic chroot with drm-shim, probe validated with lavapipe |
| [gfx-driver-pkg](gfx-driver-pkg/) | GbGfxDriver, the per-app driver package (`ro.gfx.driver.0`): a shim `vulkan.intel.so` that puts NVK first for opted-in apps while gb_power says `nvidia`, ANV otherwise | yes (`gfxdriver.json`) | host, ASan and bionic-chroot tests with lavapipe and drm-shim NVK; APK checks |
| [gpu-compute](gpu-compute/) | `gb_gpu_compute`: llama.cpp (Vulkan backend) on NVK through a private loader stub, OpenAI-style HTTP on a UNIX socket | yes (`gpu_compute.json`; libraries in the payload) | loader and backend ops on lavapipe, end-to-end service tests, reproducible builds |
| [gpu-ui](gpu-ui/) | GbGpu: Quick Settings tile and Settings page "Graphics", notifications, close-apps dialog, app catalog | yes (`gpu_ui.json`) | host-JVM unit/spec/protocol tests, its mock compared with the real gb_power, Android Lint, APK checks |
| [dgpu](dgpu/) | the NVIDIA interface spec (`INTERFACES.md` v1.2 r4), the per-component integration records, the wave 2 and wave 3 plans, and wave 4's software-Gatekeeper design and VM upgrade plan (`w4/`) | (documents) | - |
| [gatekeeper](gatekeeper/) | `gb_gatekeeper` 0.2.0, a software Gatekeeper HAL (lock-screen PIN; wave 4): AOSP libgatekeeper, scrypt + HMAC under a per-device key, persistent throttling, the ISharedSecret participant. Not hardware-backed: [docs/GATEKEEPER.md](../docs/GATEKEEPER.md) | yes, **both trees** (`gatekeeper.json`; build_esp also adds it to the VM tree) | host tests under ASan/UBSan and on lapis's bionic, regression proof against the reviewed 0.1.0 sources, reproducible build; rehearsed with every recovery path on a throwaway VM |
| [vnni-perf](vnni-perf/) | the VEX AVX-VNNI emulation measurement and the libz patch (wave 5): `patcher/` (sha256-pinned 2-byte edit, fingerprint-gated binds), proofs and tools. The patched libraries are made locally by `vnni_prepare.py` and never published: [docs/PERFORMANCE.md](../docs/PERFORMANCE.md) | yes, **both trees** (`build_esp.py` packs `inject/vnni/` when it exists; kill switch `gb.vnni=0`) | equivalence probe on Ice Lake without the emulator (SIGILL-checked), whole-binary reachability proof, patcher safety tests, before/after boots in a throwaway VM |
| [hwtest](hwtest/) | first-boot hardware test kit: the Windows log collector for the stick's `gblog\` and the triage analyser | separate tools | - |
| [ota-gating](ota-gating/) | the OTA gate (wave 5): the design and classification of every `/odm` addition (skip, keep, always), the host suite (106 checks), the builder harness for `gbinit`'s gate and the notifier (54 checks), the `secilc` proof that the OTA-robust SELinux list compiles to the identical policy | yes, inside `build_esp.py` (both trees) | host suite, builder harness, and the throwaway-VM boots of wave 5 (validated path; the notifier with a rewritten state file) |
| [dualboot](dualboot/) | internal install next to Windows, one-shot boot entry, uninstall, import of an installer-ISO install, ESP-only update (`-UpdateEspOnly`, slot-aware and log-preserving since wave 3) | separate tool | image-file install/uninstall cycles and edge cases, BCD parser test, read-only dry runs on a real disk |

How a component reaches the image: `build_esp.py` reads `inject/native/components/<name>.json`:

* `files`: `src` -> `dest` in the native tree, with a mode;
* `payload`: files of the dGPU erofs payload;
* `symlinks`: native tree links into the payload mount `/odm/dgpu`;
* `rc_files`: appended to the native `gb.rc`; `rc_text`: rc lines given inline;
* `build_prop`: lines for the native `/odm/etc/build.prop`.

Paths may use `${GB_HOME}` and `${GB_BUILD}`. Every listed file must exist, for VM builds too: move a manifest out
of the folder to build without that component. Kernel modules go through `stage_kernel.py`, not through the
manifests. The NVIDIA components are described for users in [docs/NVIDIA.md](../docs/NVIDIA.md). Their READMEs here
are the working documents of the development setup (`C:\GB-build\native\<x>` = `native/<x>`).
