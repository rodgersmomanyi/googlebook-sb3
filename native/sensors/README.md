# sensors (native)

lapis's only sensors sub-HAL (the Desktop IIO sub-HAL) accepts ChromeOS EC (and Semtech) IIO devices only. Instead of
a new HAL, two kernel patches make the Surface Book 3's sensors look like what it accepts:

* `patches/0001-iio-hid-sensors-android-cros-ec-accel-gyro.patch`: the ISH (8086:34FC) HID accelerometer and
  gyroscope register as `cros-ec-accel` / `cros-ec-gyro` with the expected scale and axes.
* `patches/0002-iio-apds9960-android-cros-ec-light.patch`: the APDS-9960 (ACPI MSHW0184) gets a `cros-ec-light`
  (lux) companion device.
* `kernel/tools/`: the generators of those patches (`mkpatches.py`, `patch_*.py`, `upd_axes.py`).
* `gb_sensors.sh` + `gb_sensors.rc`: wait (bounded) at early-boot until the IIO devices exist, because the sub-HAL
  scans once; diagnostics and auto-rotate enable after boot.
* `gb_sb3_sensors.xml`: the sensor features lapis does not declare; `overlay/`: a static resource overlay
  (`config_supportAutoRotation`), built with `overlay/build_overlay.sh` (generates its own signing key locally).
* `test/`: QEMU test with a fake ISH HID sensor collection (`fake_ish.c`), `hal_probe.c`, `run_qemu_test.sh`.

Status: untested on hardware; QEMU-tested; **not yet in the default build** (no component manifest, patches not in
the kernel build).
