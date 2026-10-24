# camera-kernel (native, untested on hardware)

The Surface Book 3 cameras (front OV5693, rear OV8865 + DW9719 focus motor, IR OV7251) sit behind Intel's **IPU4P**
(8086:8A19), for which Linux has **no upstream driver**. This is our port of the mainline ipu6 driver to IPU4P, for
the android17-6.18 GKI tree. It is part of kernel build #13 and of the native `modules.load`; nothing here has run on
the hardware yet.

* `kernel/0001-camera-ipu4p-sb3.patch` (squashed) and `kernel/series/0001-0006` (the same as a series): import of
  the ipu6 driver from v7.3-rc4 with the IPU7-in-ipu6 series, 6.18 compatibility, an IPU4P port timing fix, and two
  upstream ov8865 fixes (their author's attribution kept). GPL-2.0.
* `kernel/camera.config`, `kernel/bazel_modules.txt`: config lines (with notes on what Kleaf's `savedefconfig`
  check keeps) and Kleaf module outputs; `odm/modules.load.camera`: load order (now part of `stage_kernel.py`'s
  `NATIVE_EARLY`). `pinctrl-icelake`, `intel-lpss(-pci)` and `i2c-designware` are built from this config but never
  loaded: lapis's own copies provide the buses (docs/NATIVE.md, "never preload lapis's first-stage modules").
* `tools/cpd_check.py`: offline check of an IPU4P firmware file against the driver's CPD validation;
  `tools/cpd_verify_independent.py`: a second, independent implementation of the same checks.
* `analysis/downstream-ipu4p-rejected/gki_compat.py`: why the downstream IPU4/IPU4P series was not used (it changes
  core V4L2 structs, i.e. the GKI KMI).
* **Firmware**: the driver loads `intel/ipu/ipu4p_fw.bin`. It is Intel's proprietary firmware and is not included;
  copy `cpd_component_signed.bin` from the laptop's Windows camera driver ([docs/INPUTS.md](../../docs/INPUTS.md))
  and check it with `tools/cpd_check.py`.
* Not included: the IPU4P firmware, ACPI dumps, INF files, build logs.

Userspace (the Android camera HAL): [../camera-userspace](../camera-userspace). Status: built (#13); untested on
hardware.
