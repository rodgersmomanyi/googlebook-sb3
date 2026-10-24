# camera-userspace (native, untested on hardware)

The Android camera HAL for the IPU4P port in `../camera-kernel`: **libcamera v0.7.2** (simple pipeline + software
ISP + simple IPA) built for Android x86_64 as `camera.sb3.so` in `/odm`, replacing lapis's IPU7-only HAL via
`ro.hardware.camera=sb3` (native boots only). It is in the default native tree through
`inject/native/components/camera.json` (files staged under `odm/` by `build-scripts/stage.sh`, plus the build.prop
line).

* `patches/0001-0007` (and the combined `libcamera-v0.7.2-sb3-android.diff`): bionic thread affinity, NV12 output
  from the CPU debayer, minigbm gralloc layouts, all streams from one processed stream, firmware camera location and
  stride probing, IPU4P driver names in the simple pipeline, read-only DMA heaps. LGPL-2.1-or-later.
* `build-scripts/`: dependencies (static libjpeg-turbo, libexif), configure, `apply_patches.sh` + `patchers/`,
  `stage.sh` (renames the NDK libc++ to `libc++_lcam.so`, strips RUNPATHs, re-signs the IPA module).
* `config/`: HAL and IPA tuning files (CC0; the sensor tunings are uncalibrated starters).
* `hosttest/`: HAL test harness (`hal_test.cpp`, `fake_hw.c`) run against vimc on the builder; `tools/lcam_probe.cpp`
  (shipped as `/odm/bin/lcam-probe` to list the cameras on the device).
* `integration/`: build.prop line and optional debug rc / feature XML.
* Not included: the built `/odm` tree, test images, third-party research notes.

Status: HAL tested with vimc only; untested on hardware. Image quality will be modest (CPU debayer, no calibration).
