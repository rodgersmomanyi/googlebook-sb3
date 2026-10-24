# sensors (native)

lapis's only sensors sub-HAL (the Desktop IIO sub-HAL) accepts ChromeOS EC (and Semtech) IIO devices only. Instead of
a new HAL, two kernel patches make the Surface Book 3's sensors look like what it accepts (cros-ec emulation):

* `patches/0001-iio-hid-sensors-android-cros-ec-accel-gyro.patch`: the ISH (8086:34FC) HID accelerometer and
  gyroscope register as `cros-ec-accel` / `cros-ec-gyro` with the expected scale and axes.
* `patches/0002-iio-apds9960-android-cros-ec-light.patch`: the APDS-9960 (ACPI MSHW0184) gets a `cros-ec-light`
  (lux) companion device.
* `kernel/tools/`: the generators of those patches (`mkpatches.py`, `patch_*.py`, `upd_axes.py`).
* `gb_sensors.sh` (+ `gb_sensors.rc`, carried as `rc_text` in `inject/native/components/sensors.json`): wait
  (bounded) at early-boot until the IIO devices exist, because the sub-HAL scans once; diagnostics after boot.
* `gb_sb3_sensors.xml`: the sensor features lapis does not declare; `overlay/`: a static resource overlay
  (`config_supportAutoRotation`), built with `overlay/build_overlay.sh` (generates its own signing key locally).
* `test/`: QEMU test with a fake ISH HID sensor collection (`fake_ish.c`), `hal_probe.c` (re-implements the
  sub-HAL's device discovery; shipped as `/odm/gb_hal_probe` for on-device checks), `run_qemu_test.sh`.

Integration: both patches are in kernel build #13 (`kernel/scripts/kbuild13_native.sh`), the HID sensor modules load
in the late pass (`modules.load.late`), and `sensors.json` puts the script, feature XML, overlay and probe into the
native tree. Status: untested on hardware; QEMU-tested.
