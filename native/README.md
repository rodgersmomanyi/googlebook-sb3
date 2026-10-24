# Native Surface Book 3 components

Each component was built and reviewed on its own (original work dirs: `C:\GB-build\native\<name>` on Windows,
`/build/native/<name>` on the builder). Binaries, firmware, logs and research copies are not in the repository;
each component's scripts rebuild what it needs. **None of them has run on the real hardware yet.**

| Component | What | Integrated in the native build | Off-device testing |
|---|---|---|---|
| [audio](audio/) | SOF (Ice Lake DSP) + ALC274 card config for lapis's DRAS HAL, codec preload, diagnostics | yes (`inject/native/components/audio.json`) | module CRCs vs. kernel #11; topology decoding |
| [iptsd](iptsd/) | touchscreen + Surface Pen: iptsd 3.1.1 static NDK build, launcher, IDC files | yes (`iptsd.json`) | end-to-end with a fake IPTS uhid device |
| [power](power/) | `gb_power`: combined battery, charger, detach (DTX), dGPU power-off, platform profile; kernel patches 0001-0003 | yes (`power.json`) | unit tests against a fake sysfs (`test/test_gb_power.c`) |
| [sensors](sensors/) | ISH accel/gyro + APDS-9960 light for lapis's IIO sensors HAL (kernel patches), auto-rotate overlay | not yet | QEMU with a fake ISH (`test/`) |
| [dualboot](dualboot/) | internal install next to Windows, one-shot boot entry, uninstall | separate tool | image-file install/uninstall cycles, read-only dry runs on the real disk |
| [nouveau-kernel](nouveau-kernel/) | opt-in nouveau for the TU117 dGPU (GSP firmware), coordination with `gb_power` | no (experimental) | out-of-tree compile test |
| [nvk-android](nvk-android/) | Mesa NVK (Vulkan on nouveau) cross-built for Android + a standalone probe; own manifest `nvk-android.json` | no (experimental) | probe validated with lavapipe on the builder; bionic chroot test |
| [camera-kernel](camera-kernel/) | IPU4P (8086:8A19) port on the ipu6 driver + OV5693/OV8865/OV7251/DW9719 | no (experimental; IPU4 is not supported upstream) | compiles against the GKI tree |
| [camera-userspace](camera-userspace/) | libcamera 0.7.2 (simple pipeline + software ISP) as an Android camera HAL | no (experimental) | HAL test against vimc on the builder |
| [hw-gap-audit](hw-gap-audit/) | CPU power limits (restore the Surface's PL1/PL2), `hid-surface` (Fn key not seen as a gamepad), SAM keyboard/touchpad IDCs, Surface Pen button key layouts | not yet | host test of `gb_hw.sh` with a fake sysfs/MSR |

How a component reaches the image: `build_esp.py` reads `inject/native/components/<name>.json` (`files`: `src` ->
`dest` in the native tree with a mode; `rc_files`: appended to the native `gb.rc`). Paths may use `${GB_HOME}` and
`${GB_BUILD}`. Kernel modules go through `stage_kernel.py`, not through the manifests.
