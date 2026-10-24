# Native Surface Book 3 components

Each component was built and reviewed on its own (original work dirs: `C:\GB-build\native\<name>` on Windows,
`/build/native/<name>` on the builder). Binaries, firmware, logs and research copies are not in the repository;
each component's scripts rebuild what it needs. **None of them has run on the real hardware yet.** Device-level
status: [docs/DEVICE-STATUS.md](../docs/DEVICE-STATUS.md).

| Component | What | Integrated in the native build | Off-device testing |
|---|---|---|---|
| [audio](audio/) | SOF (Ice Lake DSP) + ALC274 card config for lapis's DRAS HAL, codec preload, diagnostics | yes (`inject/native/components/audio.json`) | module CRCs vs. the kernel; topology decoding |
| [iptsd](iptsd/) | touchscreen + Surface Pen: iptsd 3.1.1 static NDK build, launcher, IDC files | yes (`iptsd.json`) | end-to-end with a fake IPTS uhid device |
| [power](power/) | `gb_power`: combined battery, charger, detach (DTX), dGPU power-off with the nouveau opt-in, platform profile; kernel patches 0001-0003 | yes (`power.json`) | unit tests against a fake sysfs (`test/test_gb_power.c`) |
| [sensors](sensors/) | ISH accel/gyro + APDS-9960 light presented as ChromeOS EC (`cros-ec-*`) IIO devices for lapis's sensors HAL (kernel patches in build #13), auto-rotate overlay | yes (`sensors.json`) | QEMU with a fake ISH (`test/`) |
| [hw-gap-audit](hw-gap-audit/) | CPU power limits (restore the Surface's PL1/PL2), `hid-surface` (Fn key not seen as a gamepad, build #13), SAM keyboard/touchpad IDCs, input exclusions, Surface Pen button key layouts, the device audit | yes (`hw.json`) | host test of `gb_hw.sh` with a fake sysfs/MSR; static checks of the input files |
| [camera-kernel](camera-kernel/) | IPU4P (8086:8A19) port on the ipu6 driver + OV5693/OV8865/OV7251/DW9719 | yes: kernel build #13 + `modules.load` (needs your IPU4P firmware) | built in kernel #13 (no IPU4P in the VM: never probed) |
| [camera-userspace](camera-userspace/) | libcamera 0.7.2 (simple pipeline + software ISP) as the Android camera HAL `camera.sb3.so` | yes (`camera.json`, `ro.hardware.camera=sb3`) | HAL test against vimc on the builder |
| [nouveau-kernel](nouveau-kernel/) | nouveau for the TU117 dGPU (GSP firmware), loaded on request | opt-in (`nouveau.json`; kernel build #13; `persist.vendor.gb.nouveau=1`) | out-of-tree compile test |
| [nvk-android](nvk-android/) | Mesa NVK (Vulkan on nouveau) cross-built for Android + a standalone probe | shipped, inactive (`nvk.json`): the system Vulkan loader only loads ANV | probe validated with lavapipe on the builder; lapis bionic chroot test with drm-shim |
| [dualboot](dualboot/) | internal install next to Windows, one-shot boot entry, uninstall | separate tool | image-file install/uninstall cycles and edge cases, BCD parser test, read-only dry runs on a real disk |

How a component reaches the image: `build_esp.py` reads `inject/native/components/<name>.json` (`files`: `src` ->
`dest` in the native tree with a mode; `rc_files`: appended to the native `gb.rc`; `rc_text`: rc lines given inline;
`build_prop`: lines for the native `/odm/etc/build.prop`). Paths may use `${GB_HOME}` and `${GB_BUILD}`. Every listed
file must exist, for VM builds too: move a manifest out of the folder to build without that component. Kernel
modules go through `stage_kernel.py`, not through the manifests.
