# hw-gap-audit (native)

Smaller Surface Book 3 hardware gaps found by auditing the native tree against the hardware (ACPI tables, PCI/HID
inventory). The audit's device-by-device table, updated for kernel build #13, is
[docs/DEVICE-STATUS.md](../../docs/DEVICE-STATUS.md). The kernel changes it proposed are in
[kernel/k12_kernel_changes.txt](kernel/k12_kernel_changes.txt) and went into build #13.

* `tree/gb_hw.sh` + `gb_hw.rc`: **CPU power limits**. lapis's `init.lapis.rc` writes PL1 = 25 W / PL2 = 55 W
  (tuned for the Googlebook board) unconditionally; `gb_hw.sh` snapshots the Surface firmware's limits from MSR
  0x610/0x606 at early-init (through lapis's `msr.ko`) and restores them through powercap on boot
  (`persist.vendor.gb.rapl.mode` = fw (default) | lapis | custom with `persist.vendor.gb.rapl.pl1_uw`/`pl2_uw`).
  Also gives the composer write access to every backlight, and logs a hardware summary after boot.
* `kernel/0001-HID-surface-drop-the-bogus-SAM-Fn-button.patch` (source also in `kernel/src/hid-surface.c`): HID
  driver for the SAM keyboard (045E:09AE) and touchpad (045E:09AF) that drops the firmware's Fn "button"
  (BTN_MISC range), which can stay pressed and makes Android classify the keyboard as a gamepad. Based on
  linux-surface's `0016-hid-surface.patch`; GPL-2.0-or-later. `kernel/src/Makefile` is an out-of-tree compile test.
* `tree/usr/idc/`: IDC files for the SAM keyboard and touchpad (matched by device name);
  `tree/usr/keylayout/`: key layouts for the Surface Pen / Slim Pen 2 top button (BLE keyboard chords -> stylus keys);
  `tree/etc/excluded-input-devices.xml`: hides the touchpad's vendor-collection inputs that Android would take for
  gamepads, keeping lapis's own entry.
* `tree/firmware/rtl_nic.sha256`: the Realtek USB Ethernet (r8152) firmware files the tree expects, by hash; the
  files themselves come from linux-firmware and are not included (put them in `tree/firmware/rtl_nic/`).
* `hw-gap-audit.json`: the component manifest, installed as `inject/native/components/hw.json`.
* `test/test_gb_hw.sh`: host test of `gb_hw.sh` under mksh with a fake sysfs and MSR file;
  `test/check_input_files.py`: syntax, key labels and file-name lookup rules of the IDC/KL/XML files.

Not included: the ACPI table dumps used for the audit, build and test logs. Status: in the default native build;
untested on hardware.
