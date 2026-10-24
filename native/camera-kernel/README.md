# camera-kernel (experimental, not supported)

The Surface Book 3 cameras (front OV5693, rear OV8865 + DW9719 focus motor, IR OV7251) sit behind Intel's **IPU4P**
(8086:8A19), for which Linux has **no upstream driver**. This is an out-of-tree attempt to run them on the mainline
ipu6 driver with IPU4P support, for the android17-6.18 GKI tree. It is not part of the build and nothing here has
run on the hardware.

* `kernel/0001-camera-ipu4p-sb3.patch` (squashed) and `kernel/series/0001-0006` (the same as a series): import of
  the ipu6 driver from v7.3-rc4 with the IPU7-in-ipu6 series, 6.18 compatibility, an IPU4P port timing fix, and two
  upstream ov8865 fixes (their author's attribution kept). GPL-2.0.
* `kernel/camera.config`, `kernel/bazel_modules.txt`: config lines and Kleaf module outputs; `odm/modules.load.camera`:
  load order.
* `tools/cpd_check.py`: offline check of an IPU4P firmware file against the driver's CPD validation.
* `analysis/downstream-ipu4p-rejected/gki_compat.py`: why the downstream IPU4/IPU4P series was not used (it changes
  core V4L2 structs, i.e. the GKI KMI).
* Not included: the IPU4P firmware (from the Windows driver package), ACPI dumps, INF files, build logs.

Status: compiles against the GKI tree; **not supported**.
